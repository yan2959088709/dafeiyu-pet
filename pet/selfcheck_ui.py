# -*- coding: utf-8 -*-
"""自检的界面侧：把 pet/selfcheck.py 的逐项结果推进工作状态气泡。

为什么一步一步走而不是一口气跑完：自检里有磁盘遍历和 DPAPI 往返，放到启动
路径上同步执行会拖慢首帧；拆成 250ms 一拍既能保持窗口响应，又能让「耗时异常」
的步骤真的出现在气泡里。

气泡只在与上游下载日志相同的两个时机说话：超过 3000ms 还没跑完（真有耗时），
或结论不是全绿（失败/降级）。全绿时不弹任何东西——上游在没有下载任务时也不会
往工作槽写日志，启动时多一条气泡属于体验打折。
"""
import time

from PySide6.QtCore import QObject, QTimer

from pet import log as pet_log
from pet import selfcheck


class SelfCheckRunner(QObject):
    def __init__(self, service, ctx=None, steps=None, interval_ms=250,
                 clock=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.ctx = ctx if ctx is not None else self._ctx()
        self.steps = tuple(steps or selfcheck.STEPS)
        self.interval_ms = interval_ms
        self._clock = clock or (lambda: int(time.monotonic() * 1000))
        self._index = 0
        self._started = None
        self._last_pub = None
        self._results = []
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    def _ctx(self):
        try:
            return selfcheck.default_ctx()
        except Exception:
            return {}

    def start(self):
        if not self.steps or self._timer.isActive():
            return
        self._started = self._clock()
        self._timer.start()
        self._tick()

    def stop(self):
        self._timer.stop()

    def _tick(self):
        if self._index >= len(self.steps):
            self._finish()
            return
        key, label, fn = self.steps[self._index]
        try:
            status, detail = fn(self.ctx)
        except Exception as exc:
            status, detail = "fail", "%s：%s" % (label, exc)
        self._results.append({"key": key, "label": label, "status": status,
                              "detail": str(detail)[:selfcheck.MAX_DETAIL]})
        self._index += 1
        pet_log.info("自检 %s：%s（%s）" % (label, status, detail))
        now = self._clock()
        if now - self._started >= selfcheck.PROGRESS_MS and \
                selfcheck.due(now, self._last_pub):
            self._last_pub = now
            self._publish(selfcheck.progress_text(self._index, len(self.steps), label))
        if self._index >= len(self.steps):
            self._finish()

    def _publish(self, text):
        work = getattr(self.service, "work", None)
        if work is None:
            return
        try:
            work.set_download(text)
        except Exception:
            pet_log.warn("自检进度写入工作槽失败")

    def _finish(self):
        self._timer.stop()
        status, text = selfcheck.summary(self._results)
        degraded = [r for r in self._results if r.get("status") == "warn"]
        bad = selfcheck.failed(self._results)
        work = getattr(self.service, "work", None)
        if work is not None:
            try:
                if status == "error":
                    work.set_external("error", text)
                elif degraded:
                    work.set_download(text)
            except Exception:
                pet_log.warn("自检结论写入工作槽失败")
        if bad:
            pet_log.error("自检未通过：%s" % "；".join(
                "%s(%s)" % (r["label"], r["detail"]) for r in bad))
        else:
            pet_log.info(text)
