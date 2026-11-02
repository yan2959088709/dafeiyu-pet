# -*- coding: utf-8 -*-
"""独立模型通道：OpenAI 兼容 / Anthropic 协议的单一出口。

为什么需要：剥离 DSH 后没有宿主提供模型目录与凭据，碎碎念/短回复要能指向
用户自己的端点。这里只做「一次短生成」，不做流式、不做工具调用——那是聊天
主链路的职责，两套混在一起会把计费与上下文管理搅乱。

安全边界：Base URL 必须是 http(s) 完整地址（不带查询/片段）；重定向一律拒绝
（防止被跳到内网地址）；协议只有两种，请求体按协议重建，不透传调用方头。
"""
import urllib.parse

import requests

PROTOCOLS = ("openai", "anthropic")
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TIMEOUT_MS = 30000
MAX_TIMEOUT_MS = 60000
MAX_BASE_URL = 2048
MAX_MODEL = 200
MAX_KEY = 4096
READY_ERROR = "独立模型未配置或未启用"


def _valid_url(url):
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) \
        and not parsed.query and not parsed.fragment


def settings_from_cfg(cfg):
    cfg = cfg or {}
    return {
        "enabled": bool(cfg.get("aiEnabled", False)),
        "protocol": str(cfg.get("aiProtocol") or "openai"),
        "baseUrl": str(cfg.get("aiBaseUrl") or "").strip(),
        "model": str(cfg.get("aiModel") or "").strip(),
        "apiKey": str(cfg.get("aiApiKey") or ""),
        "maxTokens": int(cfg.get("aiMaxTokens") or DEFAULT_MAX_TOKENS),
        "timeoutMs": int(cfg.get("aiTimeoutMs") or DEFAULT_TIMEOUT_MS),
    }


def validate(settings):
    """返回错误列表（空 = 合法）；文案给设置页直接显示。"""
    errors = []
    if settings.get("protocol") not in PROTOCOLS:
        errors.append("协议必须是 openai / anthropic")
    base = settings.get("baseUrl") or ""
    if base:
        if not _valid_url(base):
            errors.append("Base URL 必须是 http(s) 完整地址（不带查询与片段）")
        elif len(base) > MAX_BASE_URL:
            errors.append("Base URL 过长（上限 %d）" % MAX_BASE_URL)
    model = settings.get("model") or ""
    if model and len(model) > MAX_MODEL:
        errors.append("模型名过长（上限 %d）" % MAX_MODEL)
    if len(settings.get("apiKey") or "") > MAX_KEY:
        errors.append("API Key 过长（上限 %d）" % MAX_KEY)
    tokens = settings.get("maxTokens")
    if not isinstance(tokens, int) or not (128 <= tokens <= 4096):
        errors.append("输出上限必须在 128~4096")
    timeout = settings.get("timeoutMs")
    if not isinstance(timeout, int) or not (1000 <= timeout <= MAX_TIMEOUT_MS):
        errors.append("超时必须在 1000~%d 毫秒" % MAX_TIMEOUT_MS)
    return errors


def is_ready(settings):
    return bool(settings.get("enabled") and settings.get("model")
                and settings.get("baseUrl") and settings.get("apiKey"))


def endpoint_for(settings):
    """按协议拼出唯一允许的端点（不再接受任意路径）。"""
    base = str(settings.get("baseUrl") or "").rstrip("/")
    if settings.get("protocol") == "anthropic":
        return base if base.endswith("/messages") else base + "/messages"
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


class AiProvider:
    def __init__(self, settings, post=None):
        self.settings = settings
        self._post = post or requests.post

    def chat_once(self, messages, temperature=1.0):
        settings = self.settings
        if not is_ready(settings):
            raise RuntimeError(READY_ERROR)
        proto = settings.get("protocol")
        system = ""
        turns = []
        for msg in messages or []:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "system":
                system = str(msg.get("content") or "")
            elif msg.get("role") in ("user", "assistant"):
                turns.append({"role": msg["role"], "content": str(msg.get("content") or "")})
        headers = {"Content-Type": "application/json"}
        if proto == "anthropic":
            headers["x-api-key"] = settings.get("apiKey")
            headers["anthropic-version"] = "2023-06-01"
            body = {"model": settings.get("model"),
                    "max_tokens": int(settings.get("maxTokens")),
                    "temperature": float(temperature),
                    "messages": turns}
            if system:
                body["system"] = system
        else:
            headers["Authorization"] = "Bearer %s" % settings.get("apiKey")
            body = {"model": settings.get("model"),
                    "max_tokens": int(settings.get("maxTokens")),
                    "temperature": float(temperature),
                    "messages": ([{"role": "system", "content": system}] if system else []) + turns}
        resp = self._post(endpoint_for(settings), json=body, headers=headers,
                          timeout=settings.get("timeoutMs") / 1000.0,
                          allow_redirects=False)
        code = getattr(resp, "status_code", 0)
        if code != 200:
            raise RuntimeError("独立模型调用失败：HTTP %s" % code)
        data = resp.json()
        if proto == "anthropic":
            blocks = data.get("content") or []
            text = "".join(b.get("text", "") for b in blocks
                           if isinstance(b, dict) and b.get("type") == "text")
        else:
            text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        text = str(text).strip()
        if not text:
            raise RuntimeError("独立模型没有返回文本")
        return text
