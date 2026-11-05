# -*- coding: utf-8 -*-
"""动作分段：把一段素材拆成「演出段」，只循环其中的一小节。

为什么要这么拆：素材时长是固定的，而取点走位的距离是点击给定的。要让位移
配得上距离，只有两条路——放慢帧率（脚在地上滑，动作与位移脱节），或者把
素材里的循环节多跑几轮。这里选后者：帧率始终等于素材 fps，需要更久就把
loop 段按次数重复。

段表用 0 起、闭区间的帧号。以「原地左转奔跑」（100 帧 / 10fps）为例：
    0–17    once   惊喜 + 转身（原地不位移）
    18–25   loop   标准奔跑：8 帧 = 跑两步一循环，位移只发生这一节
    45–59   once   摔倒 + 惯性滑行（位移在此减速收尾）
    60–99   once   爬起 + 尴尬挠头（不位移）

由帧表直接得到时间：head/loop 的秒数都由帧数 / fps 推出，因此「不位移的
开场多长」「滑行多久」跟素材完全一致，不需要另外维护一组秒数常量。
"""

ONCE = "once"
LOOP = "loop"


def _norm_part(row):
    """段表项 → (kind, start, end)；容忍 (start, end, kind) 与 dict 两种写法。"""
    if isinstance(row, dict):
        return (str(row.get("kind") or ONCE), int(row.get("start") or 0),
                int(row.get("end") or 0))
    kind, start, end = str(row[2]), int(row[0]), int(row[1])
    return (kind, start, end)


class SegmentTimeline:
    """一段素材的分段播放表；支持把其中一节按次数重复。"""

    def __init__(self, fps, segments):
        self.fps = max(0.01, float(fps))
        self.parts = []
        for row in segments or ():
            kind, start, end = _norm_part(row)
            if kind not in (ONCE, LOOP):
                kind = ONCE
            if end < start:
                start, end = end, start
            self.parts.append({"kind": kind, "start": start, "end": end})
        if not self.parts:
            raise ValueError("段表不能为空")
        loops = [i for i, p in enumerate(self.parts) if p["kind"] == LOOP]
        if len(loops) > 1:
            raise ValueError("只支持一个循环段")
        self._loop_at = loops[0] if loops else -1
        for p in self.parts:
            p["frames"] = max(1, p["end"] - p["start"] + 1)
            p["sec"] = p["frames"] / self.fps

    # ---------------------------------------------------------- 段落时长
    @property
    def has_loop(self):
        return self._loop_at >= 0

    @property
    def head_part(self):
        """循环段之前的那一段（如奔跑前的「惊喜+转身」）。"""
        if not self.has_loop or self._loop_at == 0:
            return None
        return self.parts[self._loop_at - 1]

    @property
    def loop_part(self):
        return self.parts[self._loop_at] if self.has_loop else None

    @property
    def after_loop_parts(self):
        """循环段之后的所有段（如「摔倒滑行」「爬起挠头」）。"""
        if not self.has_loop:
            return []
        return self.parts[self._loop_at + 1:]

    @property
    def head_sec(self):
        p = self.head_part
        return p["sec"] if p else 0.0

    @property
    def loop_sec(self):
        p = self.loop_part
        return p["sec"] if p else 0.0

    @property
    def after_loop_secs(self):
        return [p["sec"] for p in self.after_loop_parts]

    def total_sec(self, loops=1):
        loops = max(1, int(loops)) if self.has_loop else 0
        return self.head_sec + loops * self.loop_sec + sum(self.after_loop_secs)

    # ---------------------------------------------------------- 取帧
    def frame_at(self, elapsed, loops=1):
        """按已播秒数取 0 起帧号；超出总时长就停在最后一帧。"""
        loops = max(1, int(loops)) if self.has_loop else 0
        t = max(0.0, float(elapsed))
        head = self.head_part
        if head is not None:
            if t < head["sec"]:
                return head["start"] + min(head["frames"] - 1, int(t * self.fps))
            t -= head["sec"]
        if self.has_loop:
            part = self.loop_part
            span = part["sec"] * loops
            if t < span:
                pos = int((t % part["sec"]) * self.fps)
                return part["start"] + min(part["frames"] - 1, pos)
            t -= span
        for part in self.after_loop_parts:
            if t < part["sec"]:
                return part["start"] + min(part["frames"] - 1, int(t * self.fps))
            t -= part["sec"]
        return self.parts[-1]["end"]


def plan_run(timeline, distance, speed):
    """按距离反推「循环几轮 + 滑行多远」。

    循环只能是整数轮（否则循环节会在半拍上断开），所以先扣掉滑行能自然吃掉
    的那一段（减速段平均速度 = 初速一半，即 speed × 滑行秒数 / 2），剩下的
    距离用整轮去凑，余量交给滑行。返回值里 run + slide 恒等于 distance。
    """
    loops_sec = timeline.loop_sec
    step = max(1.0, float(speed) * loops_sec)
    after = timeline.after_loop_secs
    slide_sec = after[0] if after else 0.0
    slide_max = 0.5 * float(speed) * slide_sec
    loops = int(round((float(distance) - slide_max) / step))
    if loops < 1:
        loops = 1
    run_dist = loops * step
    slide_dist = float(distance) - run_dist
    if slide_dist < 0.0:
        slide_dist = 0.0
    return {"loops": loops, "run_dist": run_dist, "slide_dist": slide_dist,
            "slide_sec": slide_sec, "step": step}
