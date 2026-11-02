# -*- coding: utf-8 -*-
"""语音合成：四音色、500 字上限、队列 3、缓存与淘汰、15s 超时原子落盘。

音色 id 与上游 /voice/voices 完全一致（Azure Neural）。合成走 edge-tts
（与上游同源）；没装就明确报「语音合成失败」，不静默降级成无声。

缓存键 = sha1(voice \n rate \n text)：同一句话换音色/语速是不同的文件；
淘汰双上限 20MB / 200 个，按最旧先删。
"""
import asyncio
import hashlib
import os
import re
import threading
import time

VOICES = (
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓", "gender": "女", "style": "通用"},
    {"id": "zh-CN-XiaoyiNeural", "name": "晓伊", "gender": "女", "style": "活泼"},
    {"id": "zh-CN-YunxiNeural", "name": "云希", "gender": "男", "style": "年轻"},
    {"id": "zh-CN-YunjianNeural", "name": "云健", "gender": "男", "style": "沉稳"},
)
DEFAULT_VOICE = "zh-CN-XiaoyiNeural"
VOICE_IDS = tuple(v["id"] for v in VOICES)

MAX_TEXT_CHARS = 500
MAX_QUEUE = 3
TIMEOUT_S = 15
CACHE_MAX_BYTES = 20 * 1024 * 1024
CACHE_MAX_FILES = 200
RATE_RE = re.compile(r"^[+-]\d{1,3}%$")

REASON_BAD = "bad-request"
REASON_BUSY = "voice-busy"
REASON_FAIL = "generate-error"


def voices():
    return [dict(v) for v in VOICES]


def validate(text, voice=None, rate=None):
    """返回 (text, voice, rate)；非法抛 ValueError（文案与上游一致）。"""
    text = "" if text is None else str(text)
    if not text.strip():
        raise ValueError("text is required")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("text too long (max %d chars)" % MAX_TEXT_CHARS)
    voice = (voice or DEFAULT_VOICE).strip()
    if voice not in VOICE_IDS:
        raise ValueError("unknown voice; allowed: %s" % ", ".join(VOICE_IDS))
    rate = "" if rate is None else str(rate).strip()
    if rate:
        if not RATE_RE.match(rate):
            raise ValueError("rate 必须形如 +0% / -10%")
        value = int(rate[:-1])
        if value < -50 or value > 100:
            raise ValueError("rate 必须在 -50% ~ +100% 之间")
    return (text, voice, rate)


def cache_key(voice, rate, text):
    return hashlib.sha1(("%s\n%s\n%s" % (voice, rate, text)).encode("utf-8")).hexdigest()


def _edge_synth(text, voice, rate, path):
    """edge-tts 合成到指定路径（同步包装；15s 超时）。"""
    import edge_tts

    async def run():
        kwargs = {"voice": voice}
        if rate:
            kwargs["rate"] = rate
        communicate = edge_tts.Communicate(text, **kwargs)
        await asyncio.wait_for(communicate.save(path), timeout=TIMEOUT_S)

    asyncio.run(run())


class TTSService:
    def __init__(self, root, synth=None, clock=None):
        self.root = str(root)
        self._synth = synth or _edge_synth
        self._clock = clock or time.time
        self._active = 0
        self._lock = threading.Lock()

    def _cache_path(self, key):
        return os.path.join(self.root, key + ".mp3")

    def speak(self, text, voice=None, rate=None):
        try:
            text, voice, rate = validate(text, voice, rate)
        except ValueError as e:
            return {"ok": False, "reason": REASON_BAD, "message": str(e)}
        key = cache_key(voice, rate, text)
        path = self._cache_path(key)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            self._touch(path)
            return {"ok": True, "path": path, "cached": True, "voice": voice}
        with self._lock:
            if self._active >= MAX_QUEUE:
                return {"ok": False, "reason": REASON_BUSY,
                        "message": "voice queue is full, retry later"}
            self._active += 1
        tmp = path + ".part"
        os.makedirs(self.root, exist_ok=True)
        try:
            self._synth(text, voice, rate, tmp)
            if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
                raise RuntimeError("empty audio")
            os.replace(tmp, path)
            self._evict()
            return {"ok": True, "path": path, "cached": False, "voice": voice}
        except Exception as e:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return {"ok": False, "reason": REASON_FAIL,
                    "message": "voice synthesis failed: %s" % (e or "")}
        finally:
            with self._lock:
                self._active = max(0, self._active - 1)

    def _touch(self, path):
        try:
            os.utime(path, (self._clock(), self._clock()))
        except OSError:
            pass

    def _evict(self):
        try:
            files = [os.path.join(self.root, n) for n in os.listdir(self.root)
                     if n.endswith(".mp3")]
        except OSError:
            return
        entries = []
        for path in files:
            try:
                entries.append((os.path.getmtime(path), os.path.getsize(path), path))
            except OSError:
                continue
        entries.sort()
        total = sum(size for _m, size, _p in entries)
        count = len(entries)
        for _mtime, size, path in entries:
            if total <= CACHE_MAX_BYTES and count <= CACHE_MAX_FILES:
                break
            try:
                os.remove(path)
                total -= size
                count -= 1
            except OSError:
                pass
