# -*- coding: utf-8 -*-
"""日记：到点由 AI 自己写当天日记，落盘 diary/YYYY-MM-DD.md。

纯函数模块（不碰 Qt / 线程 / 网络）：指令文本、今日对话摘录、路径与追加写。
生成编排在 schedule_runner._diary_worker（与心跳同款 daemon worker 线程）。
"""
import datetime
import os
import time

from pet.storage import P

INSTRUCTION = (
    "现在到了你自己定下的写日记时间。请以第一人称写一篇今天的日记"
    "（200-400 字，Markdown）：今天和主人之间发生的事、你的心情、你的想法。"
    "结尾加一段「## 反思」：今天有一件事本可以做得更好，以及一条给明天的"
    "自己的提醒。如果有一条值得长期坚持的自我提醒，可以调用 manage_self_prompt "
    "把它记进去（可选，克制，一天最多一条）。"
    "只输出日记正文本身，不要解释你在做什么，也不要复述上面的任何内容。"
)

SYSTEM_EXTRA = (
    "【写日记】这是写给你自己的日记，不是给主人的回复。用第一人称「我」，"
    "写今天真实发生过的事和真实的感受；平淡的一天就写平淡，不要编造大事。"
)

TODAY_LIMIT = 40          # 今日对话最多带入条数
LINE_MAX_CHARS = 120      # 单条对话截断（防长粘贴撑爆提示词）


def today_conversation(hist, limit=TODAY_LIMIT):
    """最近对话摘录（主人/我逐行）。chat_history 本身有长度上限，取尾部即可。"""
    lines = []
    for item in (hist or [])[-limit * 2:]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        who = "主人" if item.get("role") == "user" else "我"
        lines.append("%s：%s" % (who, text[:LINE_MAX_CHARS]))
    return "\n".join(lines[-limit:])


def path_for(now=None):
    day = datetime.datetime.fromtimestamp(
        time.time() if now is None else float(now)).strftime("%Y-%m-%d")
    return os.path.join(P.diary, day + ".md")


def append_entry(text, now=None):
    """把一篇日记追加进当天文件；新文件带日期标题，同一天多篇以 ## 时段 分段。"""
    body = str(text or "").strip()
    if not body:
        return None
    now = time.time() if now is None else float(now)
    dt = datetime.datetime.fromtimestamp(now)
    target = path_for(now)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "a", encoding="utf-8", newline="\n") as f:
        if os.path.getsize(target) == 0:
            f.write("# %s\n" % dt.strftime("%Y-%m-%d"))
        f.write("\n## %s\n\n%s\n" % (dt.strftime("%H:%M"), body))
    return target
