# -*- coding: utf-8 -*-
"""文件系统事件感知：轮询快照 diff（无新依赖，不用 watchdog）。

桌宠是「器灵」：下载文件夹来了新文件、工作目录被改动，它应该知道。
轮询（默认 10s，挂在桌宠既有 _tick_background_polls 上）对桌面场景足够，
且不引入后台服务或第三方依赖。事件进 win._watch_events（上限 50，超出丢
最旧），由下次心跳的提示词以【你观察到的变化】消费。

纯函数模块：snapshot/diff/describe 不碰 Qt、不碰 self；快照不落盘
（重启重新播种，首轮只播种不产事件，否则启动即把整个目录当新文件刷屏）。
"""
import os
import time

POLL_SECS = 10
MAX_FILES = 2000     # 单次快照文件数上限：巨型目录也别把 tick 拖死
MAX_EVENTS = 50

KIND_CN = {"created": "新文件", "modified": "有改动", "deleted": "消失了"}


def snapshot(dirs):
    """{abs_path: (mtime, size)}；跳过不存在/不可读目录，文件数触顶即停。"""
    out = {}
    for raw in dirs or []:
        try:
            root = os.path.realpath(os.path.expanduser(str(raw)))
        except Exception:
            continue
        if not os.path.isdir(root):
            continue
        for base, _dirs, files in os.walk(root):
            for name in sorted(files):
                if len(out) >= MAX_FILES:
                    return out
                path = os.path.join(base, name)
                try:
                    st = os.stat(path)
                    out[path] = (st.st_mtime, st.st_size)
                except Exception:
                    continue
    return out


def diff(prev, cur, now=None):
    """prev/cur 两个快照 → 事件列表（created/modified/deleted），按路径排序。"""
    now = time.time() if now is None else float(now)
    events = []
    for path, sig in cur.items():
        old = (prev or {}).get(path)
        if old is None:
            events.append({"path": path, "kind": "created", "ts": now})
        elif tuple(old) != tuple(sig):
            events.append({"path": path, "kind": "modified", "ts": now})
    for path in (prev or {}):
        if path not in cur:
            events.append({"path": path, "kind": "deleted", "ts": now})
    events.sort(key=lambda e: e["path"])
    return events


def describe(event):
    """可读一行：'23:05 新文件：…/报告.pdf'。"""
    ts = event.get("ts") or 0
    stamp = (time.strftime("%H:%M", time.localtime(ts)) + " ") if ts else ""
    kind = KIND_CN.get(str(event.get("kind") or ""), "有变化")
    return "%s%s：%s" % (stamp, kind, str(event.get("path") or ""))
