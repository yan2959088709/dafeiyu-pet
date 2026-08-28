# -*- coding: utf-8 -*-
"""皮肤模块基类：定义皮肤协议（帧加载 / 状态→帧映射 / 挂件钩子）。

每套皮肤（whale-girl / fish / dsh-whale / 未来 deepseek-girl）在 skins/ 下
独立成模块，继承 SkinBase 实现各自动作体系；主程序只通过注册表
load_skin / SKIN_ORDER 路由，不感知皮肤内部实现。

协议要点：
- kind = "wander"   走动型（whale-girl / fish）：主程序走走动/待机调度
- kind = "gadget"   吸附挂件型（dsh-whale）：tick 直接走 on_tick 并 return，
                    不参与走动/待机；额外实现鼠标/进入/尺寸等挂件钩子
- frames = {(state, h): [QPixmap, ...]}   多帧容器（鲸鱼娘）
- sprites = {(name, h): QPixmap}          单帧容器（大肥鱼 / DSH 小鲸鱼）
"""


class SkinBase:
    """皮肤基类（所有皮肤共用的默认实现与协议）。"""

    name = ""                # 注册表主键（对应 config character 值）
    display_name = ""        # 界面 / 托盘显示名
    chat_title = ""          # 聊天面板标题（可含 emoji）
    kind = "wander"          # "wander" 走动型 / "gadget" 吸附挂件型
    fps_map = {}             # 多帧状态 → 播放帧率
    default_fps = 7.0
    # 素材基准朝向是否跟随移动方向镜像：
    # True  = 素材只有一套朝左（如鲸鱼娘），向右走时主程序水平镜像
    # False = 素材已分左右两套帧集（如 deepseek-girl 的 running-left/right），不镜像
    mirror_follows_facing = True

    def __init__(self):
        self.frames = {}     # {(state, h): [QPixmap, ...]}  多帧容器
        self.sprites = {}    # {(name, h): QPixmap}          单帧容器
        self._loaded = False

    # ---------- 帧加载 / 映射（子类实现） ----------
    def load(self, h_sizes):
        """加载本皮肤全部档位素材到 self.frames / self.sprites。

        h_sizes：各档位目标高度列表（如 [187, 238, 306]）。
        """
        raise NotImplementedError

    def get_frames(self, state, h):
        """返回多帧状态帧列表（无则 None）。"""
        return self.frames.get((state, h))

    def fps(self, state):
        """返回指定状态播放帧率。"""
        return self.fps_map.get(state, self.default_fps)

    def state_key(self, pet_state, dir, facing, h):
        """抽象状态（idle/walk/think...）→ 本皮肤实际帧键。

        返回 (state, h) 表示多帧容器查询（len==2）；
        返回 (name, h, facing) 表示单帧容器查询（len==3）。
        """
        raise NotImplementedError

    def width_for(self, h):
        """本皮肤在给定高度 h 下的素材宽度列表（用于窗口自适应）。"""
        widths = [pm.width() for (st, hs), fr in self.frames.items()
                  if hs == h for pm in fr]
        if not widths:
            widths = [p.width() for k, p in self.sprites.items() if k[1] == h]
        return widths

    def icon_pixmap(self, h):
        """托盘 / 窗口图标位图；返回 None 时主程序回退默认 LOGO。"""
        fr = self.frames.get(("idle", h))
        if fr:
            return fr[0]
        return self.sprites.get((self.name, h))

    # ---------- 菜单 / 文案 ----------
    def menu_filter(self):
        """需要隐藏的通用菜单项集合（如 gadget 型隐藏 "mode"/"sleep"）。"""
        return set()

    def say_map(self):
        """{skin_name: 切换台词}（兼容协议，主程序优先用 switch_line）。"""
        return {}

    def switch_line(self, pet_name):
        """切到本皮肤时的播报台词（含自定义名字时替换）。"""
        return "换了个形象～"

    def idle_pick(self, win):
        """随机待机小动作（wander 型实现；gadget 型不走）。win 为主窗口。"""
        pass

    def pick_idle_frame(self, win, frames):
        """待机 idle 帧选择策略：默认按帧率循环播放（多帧循环动画）。

        需要"定格 + 偶发眨眼"等特殊节奏的皮肤（如鲸鱼娘）可覆写。
        """
        n = len(frames)
        if n <= 1:
            return 0
        return int(win.frame_t * self.fps("idle")) % n

    # ---------- 挂件钩子（gadget 型实现，wander 型默认空实现） ----------
    def on_enter(self, win):
        """进入本皮肤时调用（切肤 / 启动吸附）。"""
        pass

    def on_tick(self, win):
        """gadget 型每帧 tick（吸附动画等）。"""
        pass

    def on_press(self, win, e):
        """gadget 型鼠标按下。"""
        pass

    def on_move(self, win, e, delta):
        """gadget 型鼠标拖动（delta 为从按下点累计位移）。"""
        pass

    def on_release(self, win, e):
        """gadget 型鼠标松开。"""
        pass

    def on_click(self, win):
        """gadget 型单击（双击判定后由主程序 _on_single_click 调用）。"""
        pass

    def on_resize(self, win):
        """gadget 型尺寸变化后重贴壁。"""
        pass
