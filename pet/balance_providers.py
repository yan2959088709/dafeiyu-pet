# -*- coding: utf-8 -*-
"""余额服务商注册表与四个内置取数实现。

口径照上游：内置顺序即优先级（opencode-go → commandcode → deepseek-official →
deepseek-account），单次 20s、最多 3 次、间隔 800ms；失败只暴露三种原因
（unsupported / credential-missing / fetch-error），**绝不伪造 0**。

对外提供注册函数给第三方服务商（禁止覆盖内置、禁止重复 id），与上游
`balance.register` 等价。

注：opencode / commandcode 的响应结构以实测为准，这里按防御式解析取值
（recognized 字段名优先，取不到就报 fetch-error，不猜数）。
"""
import time

import requests

TIMEOUT_S = 20
RETRIES = 3
RETRY_GAP_S = 0.8

REASON_UNSUPPORTED = "unsupported"
REASON_CREDENTIAL = "credential-missing"
REASON_FETCH = "fetch-error"

OPENCODE_URL = "https://opencode.ai/zen/go/v1/usage"
COMMANDCODE_BASE = "https://api.commandcode.ai"
DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"

_WINDOW_LABELS = ("5h", "周", "月")
_QUOTAS = (12.0, 30.0, 60.0)


def _fail(reason, message):
    return {"ok": False, "reason": reason, "message": message}


def _window(label, used, limit, reset_at=None):
    used = max(0.0, float(used))
    limit = max(0.0, float(limit))
    percent = 100.0 if limit <= 0 else min(100.0, used / limit * 100.0)
    return {"label": label, "used": used, "limit": limit,
            "percent": percent, "resetAt": reset_at}


def _windows_shape(windows):
    return {"ok": True, "shape": "windows", "rollingLabel": windows[0]["label"] if windows else "5h",
            "windows": windows}


def _get_json(url, headers, get=None, now=None):
    """带重试的 GET；401/403 视为凭证缺失，其余非 200 视为取数失败。"""
    get = get or requests.get
    last = None
    for attempt in range(RETRIES):
        try:
            resp = get(url, headers=headers, timeout=TIMEOUT_S)
            code = getattr(resp, "status_code", 0)
            if code in (401, 403):
                return None, _fail(REASON_CREDENTIAL, "余额查询失败（凭证无效或已过期）")
            if code != 200:
                last = "HTTP %s" % code
            else:
                data = resp.json()
                return data, None
        except Exception as e:
            last = str(e)
        if attempt < RETRIES - 1:
            time.sleep(RETRY_GAP_S)
    return None, _fail(REASON_FETCH, "余额查询超时（20 秒），请稍后重试：%s" % (last or ""))


def _pick_number(d, *keys):
    for key in keys:
        val = d.get(key) if isinstance(d, dict) else None
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str) and val.strip():
            try:
                return float(val.strip())
            except ValueError:
                continue
    return None


def _windows_from_payload(data):
    """把常见响应形态归一成 windows（取不到就返回 None）。"""
    if not isinstance(data, dict):
        return None
    rows = data.get("windows") or data.get("limits") or data.get("usage")
    out = []
    if isinstance(rows, list):
        for i, row in enumerate(rows[:len(_WINDOW_LABELS)]):
            if not isinstance(row, dict):
                continue
            used = _pick_number(row, "used", "usage", "spent", "consumed")
            limit = _pick_number(row, "limit", "quota", "total", "max")
            if used is None:
                continue
            if limit is None:
                limit = _QUOTAS[min(i, len(_QUOTAS) - 1)]
            out.append(_window(str(row.get("label") or _WINDOW_LABELS[min(i, 2)]),
                               used, limit, row.get("resetAt") or row.get("reset_at")))
    if out:
        return out
    remaining = _pick_number(data, "remaining", "balance", "credits")
    if remaining is not None:
        limit = _pick_number(data, "limit", "quota") or _QUOTAS[0]
        return [_window("5h", max(0.0, limit - remaining), limit)]
    return None


class Provider:
    """一个服务商：id 稳定、凭证 ref 明确、取数返回统一结果实体。"""

    builtin = True

    def __init__(self, pid, label, credential_ref=None):
        self.id = pid
        self.label = label
        self.credential_ref = credential_ref

    def matches(self, model=None):
        return True

    def query(self, credentials=None, get=None, now=None):
        raise NotImplementedError


class OpencodeGo(Provider):
    def __init__(self):
        super().__init__("opencode-go", "OpenCode Go", "OPENCODE_GO_API_KEY")

    def matches(self, model=None):
        return bool(model and "opencode" in str(model).lower())

    def query(self, credentials=None, get=None, now=None):
        key = (credentials or {}).get(self.credential_ref)
        if not key:
            return _fail(REASON_CREDENTIAL, "缺少凭证 %s" % self.credential_ref)
        data, err = _get_json(OPENCODE_URL, {"Authorization": "Bearer %s" % key}, get)
        if err:
            return err
        windows = _windows_from_payload(data)
        if not windows:
            return _fail(REASON_FETCH, "余额响应结构无法识别（opencode-go）")
        return _windows_shape(windows)


class CommandCode(Provider):
    def __init__(self):
        super().__init__("commandcode", "Command Code", "COMMANDCODE_API_KEY")

    def matches(self, model=None):
        return bool(model and "command" in str(model).lower())

    def query(self, credentials=None, get=None, now=None):
        key = (credentials or {}).get(self.credential_ref)
        if not key:
            return _fail(REASON_CREDENTIAL, "缺少凭证 %s" % self.credential_ref)
        headers = {"Authorization": "Bearer %s" % key}
        merged = {}
        for path in ("/whoami?limits=1", "/credits", "/subscriptions", "/usage"):
            data, err = _get_json(COMMANDCODE_BASE + path, headers, get)
            if err:
                return err
            if isinstance(data, dict):
                merged.update(data)
        windows = _windows_from_payload(merged)
        if not windows:
            return _fail(REASON_FETCH, "余额响应结构无法识别（commandcode）")
        return _windows_shape(windows)


class DeepseekOfficial(Provider):
    def __init__(self):
        super().__init__("deepseek-official", "DeepSeek 官方", "DEEPSEEK_API_KEY")

    def matches(self, model=None):
        return True

    def query(self, credentials=None, get=None, now=None):
        key = (credentials or {}).get(self.credential_ref)
        if not key:
            return _fail(REASON_CREDENTIAL, "缺少凭证 %s" % self.credential_ref)
        data, err = _get_json(DEEPSEEK_BALANCE_URL, {"Authorization": "Bearer %s" % key}, get)
        if err:
            return err
        infos = (data or {}).get("balance_infos") or []
        rows = []
        for info in infos:
            total = _pick_number(info, "total_balance")
            if total is None:
                continue
            rows.append({"currency": str(info.get("currency") or "CNY"),
                         "total": total,
                         "granted": _pick_number(info, "granted_balance") or 0.0,
                         "toppedUp": _pick_number(info, "topped_up_balance") or 0.0})
        if not rows:
            return _fail(REASON_FETCH, "余额响应里没有可用金额")
        return {"ok": True, "shape": "money", "isAvailable": bool(data.get("is_available", True)),
                "money": rows}


class DeepseekAccount(Provider):
    def __init__(self):
        super().__init__("deepseek-account", "DeepSeek 账号", None)

    def matches(self, model=None):
        return False

    def query(self, credentials=None, get=None, now=None):
        return _fail(REASON_UNSUPPORTED, "独立模式没有 DSH 账号服务，余额不可用")


class BalanceRegistry:
    """内置顺序即优先级；第三方注册只追加在后面。"""

    def __init__(self):
        self._builtin = [OpencodeGo(), CommandCode(), DeepseekOfficial(), DeepseekAccount()]
        self._external = {}

    def builtin_ids(self):
        return [p.id for p in self._builtin]

    def register(self, provider):
        pid = getattr(provider, "id", "")
        if not pid:
            return {"ok": False, "message": "服务商 id 不能为空"}
        if pid in self.builtin_ids():
            return {"ok": False, "message": "不能覆盖内置服务商: %s" % pid}
        if pid in self._external:
            return {"ok": False, "message": "该服务商 id 已被注册: %s" % pid}
        self._external[pid] = provider
        return {"ok": True, "id": pid}

    def unregister(self, pid):
        return self._external.pop(pid, None) is not None

    def list_providers(self):
        return {"builtin": [p.id for p in self._builtin], "external": list(self._external)}

    def select(self, model=None):
        for provider in self._builtin:
            if provider.matches(model):
                return provider
        return self._builtin[-1]

    def query(self, model=None, credentials=None, get=None, now=None):
        provider = self.select(model)
        result = provider.query(credentials, get, now)
        result["provider"] = provider.id
        return result


REGISTRY = BalanceRegistry()
