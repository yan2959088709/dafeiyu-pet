# -*- coding: utf-8 -*-
"""落盘日志：单文件轮转 + 敏感内容脱敏。

为什么不用 logging 的 RotatingFileHandler：上游的硬指标是「超 1MiB 只留尾部
512KiB、1s 或 64 行刷盘、待写上限 1000 行」，而 RotatingFileHandler 是按份
轮转（.1/.2/...）且不留尾部，语义对不上，日志会多出一堆分片。

路径在 configure 时才从 P 取：沙箱重定向 P 之后无需再改本模块；未 configure
时写日志是空操作，测试里不会凭空在项目目录留下 logs.txt。
"""
import os
import re
import threading
import time

from pet.storage import P

MAX_BYTES = 1024 * 1024
KEEP_BYTES = 512 * 1024
FLUSH_MS = 1000
FLUSH_LINES = 64
PENDING_MAX = 1000

_REDACT = (
    re.compile(r"(?i)(x-dsh-pet-token\s*[:=]\s*)[^\s,;\"']+"),
    re.compile(r"(?i)([#?&]token=)[^\s&\"']+"),
    re.compile(r"(?i)(\btoken\s*[:=]\s*)[^\s,;\"']+"),
    re.compile(r"(?i)(DSH_PET_SITE_URL=)\S+"),
)

_lock = threading.Lock()
_buf = []
_path = None
_debug = False
_thread = None
_stop = False


def configure(path=None, debug=False):
    """绑定日志文件并启动刷盘线程；重复调用只换路径。"""
    global _path, _debug
    _path = path or P.log
    _debug = bool(debug)
    _ensure_thread()


def is_configured():
    return _path is not None


def redact(text):
    """抹掉令牌一类的敏感串，其余原样。"""
    out = str(text)
    for pat in _REDACT:
        out = pat.sub(lambda m: m.group(1) + "***", out)
    return out


def write(level, msg):
    """写一行（超过待写上限时丢最旧的，避免内存被刷屏撑爆）。"""
    if _path is None:
        return
    line = "%s [%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), level, redact(msg))
    with _lock:
        if len(_buf) >= PENDING_MAX:
            del _buf[0]
        _buf.append(line)
        full = len(_buf) >= FLUSH_LINES
    if full:
        flush()


def info(msg):
    write("INFO", msg)


def warn(msg):
    write("WARN", msg)


def error(msg):
    write("ERROR", msg)


def debug(msg):
    if _debug:
        write("DEBUG", msg)


def flush():
    """把缓冲区落到文件；失败静默（日志不能反过来拖垮主流程）。"""
    global _buf
    with _lock:
        if not _buf:
            return
        lines, _buf = _buf, []
    try:
        with open(_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        _trim()
    except Exception:
        pass


def _trim():
    """超上限时只保留尾部 KEEP_BYTES，并从行首切开避免半行。"""
    try:
        if os.path.getsize(_path) <= MAX_BYTES:
            return
        with open(_path, "rb") as f:
            f.seek(-KEEP_BYTES, os.SEEK_END)
            tail = f.read()
        cut = tail.find(b"\n")
        if cut >= 0:
            tail = tail[cut + 1:]
        with open(_path, "wb") as f:
            f.write(tail)
    except Exception:
        pass


def _ensure_thread():
    global _thread, _stop
    with _lock:
        if _thread is not None:
            return
        _stop = False
        t = threading.Thread(target=_loop, name="pet-log", daemon=True)
        _thread = t
    t.start()


def _loop():
    while not _stop:
        time.sleep(FLUSH_MS / 1000.0)
        flush()


def close():
    """退出前收尾：停线程并刷最后一次。"""
    global _stop, _thread
    _stop = True
    flush()
    _thread = None


def reset():
    """测试用：断开路径并清空缓冲（不删文件）。"""
    global _path, _buf, _debug
    with _lock:
        _buf = []
        _path = None
        _debug = False
