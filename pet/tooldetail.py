# -*- coding: utf-8 -*-
"""工具调用 → 一句气泡详情（文案表 + 目标提取 + 命令识别）。

数据全部照抄上游 copy.ts：工具表、命令表、静默工具集、目标裁剪阈值。
为什么要把文案单独抽出来：气泡是「氛围」不是信息面板 —— 每句都带具体对象、
不出现参数值（可能含 token 或内网地址），目标太长时只说氛围话。
"""
import json
import os
import random
import re
from urllib.parse import urlparse

TARGET_MAX = 22
TARGET_DISPLAY_MAX = 20
MAX_ARGS_CHARS = 64 * 1024
CMD_NAME_MAX = 20

UNKNOWN_TOOL_LINE = "这工具是干嘛的"
COMMAND_NAME_TOO_LONG = "跑一个命令咯"
COMMAND_FALLBACK = ("跑一下 {cmd} 咯", "{cmd} 跑起来了", "让 {cmd} 干点活")

SILENT_TOOLS = frozenset((
    "todo_write", "ask_user_question", "create_goal", "get_goal",
    "update_goal", "exit_plan_mode",
))

FILE_COPY = ("翻翻 {target} 看看嘛", "{target} 里面写了啥呢", "我先瞅瞅 {target} 哈")
WRITE_COPY = ("写点东西到 {target}", "{target} 马上写出来", "给 {target} 添点料")
EDIT_COPY = ("{target} 得改改啦", "我来动动 {target} 哦", "{target} 这里要修一下")
GLOB_COPY = ("{target} 都躲哪儿了呢", "找找 {target} 在不在", "{target} 藏在哪个角落呀")
GREP_COPY = ("{target} 藏哪儿了呢", "搜搜 {target} 在哪", "哪儿有 {target} 呀")
PRESENT_COPY = ("给你看 {target} 哦", "{target} 拿来给你瞧瞧", "喏，{target} 在这儿")
SKILL_COPY = ("学个新本事 {target}", "{target} 这招得记住", "把 {target} 学起来")
SUBAGENT_COPY = ("叫人搭把手：{target}", "让帮手去看看 {target}", "{target} 交给帮手了")

TOOL_COPY = {
    "read": (FILE_COPY, "翻翻代码看看"),
    "read_image": (("这 {target} 长啥样呀", "让我瞧瞧 {target} 呗", "{target} 是张什么图呢"),
                   "看看这张图是啥"),
    "write": (WRITE_COPY, "写点东西进去"),
    "edit": (EDIT_COPY, "这里得改改"),
    "str_replace_editor": (EDIT_COPY, "这里得改改"),
    "glob": (GLOB_COPY, "找找东西在哪"),
    "grep": (GREP_COPY, "到处翻翻看"),
    "present": (PRESENT_COPY, "拿个东西给你看"),
    "skill": (SKILL_COPY, "学个新本事"),
    "subagent": (SUBAGENT_COPY, "叫人来搭把手"),
    "subagent_fork": (SUBAGENT_COPY, "叫人来搭把手"),
    "workflow": (("把活排个序，开工咯", "几件事一起安排上", "按顺序一件件来"), "把活排一排"),
    "web_search": (("我去查查 {target}", "{target} 得搜搜才知道", "上网问问 {target} 吧"),
                   "上网查查"),
    "web_fetch": (("去 {target} 瞅瞅有啥", "{target} 这页我翻翻", "看看 {target} 上写了啥"),
                  "去网上看看"),
}

CD_COPY = ("先把位置挪一挪", "切到那个目录去", "挪个地方继续干活")
LS_COPY = ("看看这儿都有啥", "这层都有什么东西", "翻翻这里有没有")
CAT_COPY = ("把内容亮出来瞧瞧", "看看里头写了什么", "读读这份内容")
MKDIR_COPY = ("建个新文件夹", "腾个地方出来", "新开一个目录")
RM_COPY = ("把不要的清掉", "这份就别留了", "清理一下这些")
MV_COPY = ("把东西挪个地儿", "换个位置放", "挪到那边去")
CP_COPY = ("抄一份留着", "复制一份备用", "再备一份")
CURL_COPY = ("去问问接口有没有人", "敲敲门看看通不通", "请求一下那个地址")
CHECKOUT_COPY = ("换到那个分支瞧瞧", "切个分支看看", "换条路继续走")

COMMAND_COPY = {
    "cd": CD_COPY, "set-location": CD_COPY,
    "ls": LS_COPY, "dir": LS_COPY, "get-childitem": LS_COPY,
    "cat": CAT_COPY, "get-content": CAT_COPY,
    "mkdir": MKDIR_COPY, "new-item": MKDIR_COPY,
    "rm": RM_COPY, "del": RM_COPY, "remove-item": RM_COPY,
    "mv": MV_COPY, "move-item": MV_COPY,
    "cp": CP_COPY, "copy-item": CP_COPY,
    "curl": CURL_COPY, "invoke-webrequest": CURL_COPY,
    "git": ("折腾折腾 git 仓库", "跟 git 打个交道", "git 这边动一动"),
    "git status": ("看看现在改了些什么", "查查工作区干不干净", "目前是啥状况呢"),
    "git diff": ("瞅瞅这次到底改哪了", "到底动了哪些地方呀", "对比看看都改了啥"),
    "git log": ("让我康康你的提交历史", "翻翻最近都提交了啥", "看看这些天干了什么"),
    "git add": ("先把这些攒起来", "好东西先收进去", "把改动的都登记上"),
    "git commit": ("代码得保存好才行", "打个提交，记一笔", "先存一份再说"),
    "git push": ("该推上去了吧，我来", "推送一下，交给我", "该让远端知道了"),
    "git pull": ("拉一下最新的过来", "把远端的新东西拿回来", "先同步一下再说"),
    "git fetch": ("先问问远端有没有新东西", "悄悄看一眼远端", "拉个最新状态下来"),
    "git checkout": CHECKOUT_COPY, "git switch": CHECKOUT_COPY,
    "git branch": ("看看都有哪些分支", "理理这些分支", "新建个分支备用"),
    "git merge": ("把两边合到一起", "合并一下，别打架", "这两条线该合了"),
    "git rebase": ("把历史重新捋一捋", "重新排一下顺序", "理一理提交顺序"),
    "git stash": ("先把改动收起来", "这些先搁一边", "暂时藏起来一下"),
    "git clone": ("把整个仓库搬回来", "照着一份抄下来", "把这份拿下来瞧瞧"),
    "git reset": ("把刚才的退回去", "撤销一下这几步", "回到之前的样子"),
    "git tag": ("给这次打个标记", "贴个版本标签", "记个里程碑"),
}

_COMPOUND = re.compile(r"[;|\n]|&&|\|\|")


def _first_str(args, keys):
    for key in keys:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _file_target(args):
    return _first_str(args, ("file_path", "path", "filename", "file"))


def _files_target(args):
    files = args.get("files") or args.get("file_paths")
    if isinstance(files, list) and files:
        first = files[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return _first_str(first, ("path", "file_path"))
    return _file_target(args)


def _url_host(args):
    url = _first_str(args, ("url", "uri"))
    if not url:
        return None
    try:
        host = urlparse(url).netloc or url
    except Exception:
        host = url
    return host or None


def _query_target(args):
    return _first_str(args, ("query", "q", "keywords", "term"))


TOOL_RULES = {
    "read": _file_target,
    "read_image": _file_target,
    "write": _file_target,
    "edit": _file_target,
    "str_replace_editor": _file_target,
    "glob": lambda a: _first_str(a, ("pattern", "path")),
    "grep": lambda a: _first_str(a, ("pattern", "path")),
    "web_fetch": _url_host,
    "web_search": _query_target,
    "subagent": lambda a: _first_str(a, ("description",)),
    "subagent_fork": lambda a: _first_str(a, ("description",)),
    "workflow": lambda a: _first_str(a, ("name", "description")),
    "skill": lambda a: _first_str(a, ("name",)),
    "present": _files_target,
}

_PATH_TOOLS = frozenset(("read", "read_image", "write", "edit", "present"))


def _path_like(text):
    return "/" in text or "\\" in text or bool(os.path.splitext(text)[1])


def compress_target(target, is_path=False):
    """目标裁剪：超 22 码点压成文件名；仍超 20 码点则返回 None（改说氛围话）。"""
    target = "" if target is None else str(target).strip()
    if not target:
        return None
    if len(target) <= TARGET_MAX:
        return target
    if is_path or _path_like(target):
        base = re.split(r"[/\\]", target)[-1]
        if base and len(base) <= TARGET_DISPLAY_MAX:
            return base
    return None


def parse_args(raw):
    """参数字符串超过 64KiB 就不解析（避免大对象进气泡路径）。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    if len(raw) > MAX_ARGS_CHARS:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _fill(line, target=None, cmd=None):
    if target is not None:
        line = line.replace("{target}", target)
    if cmd is not None:
        line = line.replace("{cmd}", cmd)
    return line


def command_detail(args, rng=None):
    """pwsh 命令：复合命令不猜；先查「命令 子命令」再查命令。"""
    rng = rng or random
    raw = _first_str(args, ("command", "cmd", "script"))
    if not raw:
        return None
    if _COMPOUND.search(raw):
        return None
    tokens = raw.strip().strip('"').split()
    if not tokens:
        return None
    cmd = tokens[0].strip('"').lower()
    if not cmd:
        return None
    sub = ""
    if len(tokens) > 1 and not tokens[1].startswith("-"):
        sub = tokens[1].strip('"').lower()
    lines = COMMAND_COPY.get((cmd + " " + sub).strip()) or COMMAND_COPY.get(cmd)
    if lines:
        return _fill(rng.choice(lines), cmd=cmd)
    if len(cmd) > CMD_NAME_MAX:
        return COMMAND_NAME_TOO_LONG
    return _fill(rng.choice(COMMAND_FALLBACK), cmd=cmd)


def detail(tool, raw_args, rng=None):
    """返回一句气泡文案；None = 不覆盖已有气泡（静默工具 / 取不到目标）。"""
    tool = str(tool or "").strip()
    if not tool or tool in SILENT_TOOLS:
        return None
    args = parse_args(raw_args)
    if tool in ("pwsh", "bash", "shell"):
        return command_detail(args, rng)
    copy = TOOL_COPY.get(tool)
    if not copy:
        return UNKNOWN_TOOL_LINE
    rule = TOOL_RULES.get(tool)
    target = rule(args) if rule else None
    if not target:
        return None
    target = compress_target(target, is_path=(tool in _PATH_TOOLS))
    if target is None:
        return copy[1]
    return _fill((rng or random).choice(copy[0]), target=target)
