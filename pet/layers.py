# -*- coding: utf-8 -*-
"""窗口图层协调：让二级弹窗永远压在宿主面板之上，且不被置顶看门狗埋掉。

问题根因（Windows）：`QWidget::raise_()` 落到 `SetWindowPos(HWND_TOP)`，
它会把「属主窗口 + 该属主的全部 owned 窗口」整体提到 Z 序顶部。PetWindow 每
5 秒重申一次置顶，于是聊天面板（owned）被反复提到最上，而**没有父级**的表情
浮层 / 喂食面板 / 积分弹窗（各自独立的 stays-on-top 窗口）就被压到聊天面板
底下——看不见也点不到，用户侧的表现就是「页面锁死」。

规则只有两条：
1. 有二级面板/弹窗可见时，置顶看门狗不许 raise（它们自己就是 stays-on-top，
   不需要宠物帮忙；而宠物一 raise 就会把它们埋掉）；
2. 要 raise 就按「属主 → 二级面板」的顺序来，属主永远先提。

另外把弹窗挂到宿主名下（owned window），Windows 保证 owned 窗口恒在属主
之上，这是不依赖任何 raise 顺序的结构性保证。
"""
import weakref

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget


class LayerStack(QObject):
    """宿主窗口（PetWindow / ChatPanel）的图层保镖。"""

    def __init__(self, owner):
        super().__init__(owner if isinstance(owner, QWidget) else None)
        self._owner = owner
        self._panels = []          # [(weakref, 打开序号)]，保持打开顺序

    # ------------------------------------------------------------ 登记
    def add(self, panel):
        """登记一个二级面板/弹窗（重复登记忽略）。"""
        if panel is None:
            return
        for ref, _seq in self._panels:
            if ref() is panel:
                return
        self._panels.append((weakref.ref(panel), len(self._panels)))
        try:
            panel.destroyed.connect(lambda *_a, p=panel: self.remove(p))
        except Exception:
            pass

    def remove(self, panel):
        self._panels = [(r, s) for r, s in self._panels if r() is not panel]

    def alive(self):
        """仍存活的二级面板（已销毁的滤掉），按打开顺序返回。"""
        out = []
        for ref, seq in self._panels:
            w = ref()
            if w is None:
                continue
            try:
                w.isVisible()
            except RuntimeError:
                continue
            out.append((seq, w))
        return [w for _seq, w in sorted(out, key=lambda kv: kv[0])]

    def busy(self):
        """有可见的二级面板时返回 True——此时看门狗必须让路。"""
        for w in self.alive():
            try:
                if w.isVisible():
                    return True
            except RuntimeError:
                continue
        return False

    # ------------------------------------------------------------ 排序
    def restack(self):
        """先提属主、再按打开顺序提二级面板，保证面板恒在属主之上。"""
        if self._owner is not None:
            try:
                self._owner.raise_()
            except RuntimeError:
                pass
        for w in self.alive():
            try:
                if w.isVisible():
                    w.raise_()
            except RuntimeError:
                continue

    def close_all(self):
        """宿主隐藏时收掉所有二级面板，避免留下点不到的孤儿浮层。"""
        for w in self.alive():
            try:
                w.hide()
            except RuntimeError:
                continue


class PopupCloser(QObject):
    """点击空白处关闭浮层（挂在 QApplication 上，事件照常派发不消费）。

    score_ui 早年就用的同一套路子：应用级事件过滤器返回 False，关浮层的
    同时不挡住桌面操作。这里补上「只关自己这一个、且点在自己身上不关」。
    """

    def __init__(self, popup, owner=None):
        super().__init__(popup)
        self._popup = popup
        self._owner = owner

    def _inside(self, ev):
        try:
            pos = ev.globalPosition().toPoint()
        except Exception:
            return False
        for w in (self._popup, self._owner):
            if w is None:
                continue
            try:
                if w.isVisible() and w.rect().contains(w.mapFromGlobal(pos)):
                    return True
            except RuntimeError:
                continue
        return False

    def eventFilter(self, _obj, ev):
        try:
            if ev.type() == QEvent.Type.MouseButtonPress \
                    and self._popup.isVisible() and not self._inside(ev):
                self._popup.hide()
        except RuntimeError:
            return False
        return False


def autoclose(popup, owner=None):
    """给浮层装上「点外面就关」，返回安装的过滤器（便于卸载）。"""
    app = QApplication.instance()
    if app is None:
        return None
    closer = PopupCloser(popup, owner)
    app.installEventFilter(closer)
    return closer
