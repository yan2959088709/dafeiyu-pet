# -*- coding: utf-8 -*-
"""凭证字段的本地保护：Windows DPAPI（绑定当前用户，零新增依赖）。

设计取舍
- 只保护"值"，不改调用方：config 在内存里仍是明文，各处 cfg["ds_api_key"]
  的读取逻辑一行都不动；落盘时由 dump_config 统一加密。
- 密文绑定当前 Windows 用户，换机器/换用户后解不开，按"未设置"降级
  （不抛异常、不阻塞启动），用户重新填一次即可。
- 非 Windows 或 DPAPI 不可用时原样明文落盘，行为退回改造前，绝不因此启动失败。

为什么不用 keyring：会引入新依赖，而 DPAPI 是系统自带、正好匹配"仅本机
Windows 用户"这个真实威胁模型（分享目录、截图配置、误提交）。
"""
import base64
import ctypes
import json
import sys

PREFIX = "dpapi:"
SECRET_FIELDS = ("ds_api_key", "ds_platform_token",
                 "opencode_go_api_key", "commandcode_api_key", "aiApiKey")

_AVAILABLE = False
_crypt32 = None
_kernel32 = None


if sys.platform == "win32":
    try:
        from ctypes import wintypes

        class _DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
        _crypt32.CryptProtectData.restype = wintypes.BOOL
        _crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(_DataBlob)]
        _crypt32.CryptUnprotectData.restype = wintypes.BOOL
        _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        _kernel32.LocalFree.restype = ctypes.c_void_p
        _AVAILABLE = True
    except Exception:
        _AVAILABLE = False


def available():
    """DPAPI 是否可用（不可用时退化为明文存储）。"""
    return _AVAILABLE


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _take(ok, out_blob):
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI 调用失败")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


def _protect_bytes(data):
    blob_in, _keep = _blob(data)
    blob_out = _DataBlob()
    ok = _crypt32.CryptProtectData(ctypes.byref(blob_in), ctypes.c_wchar_p("dafeiyu-pet"),
                                   None, None, None, 0, ctypes.byref(blob_out))
    return _take(ok, blob_out)


def _unprotect_bytes(data):
    blob_in, _keep = _blob(data)
    blob_out = _DataBlob()
    ok = _crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None,
                                     0, ctypes.byref(blob_out))
    return _take(ok, blob_out)


def protect(text):
    """明文 → 落盘用字符串（已加密则带 dpapi: 前缀）。失败时原样返回。"""
    if not text or not _AVAILABLE:
        return text
    if str(text).startswith(PREFIX):
        return text      # 已是密文：重复加密会让下次启动只能剥一层，真 Key 永久丢失
    try:
        return PREFIX + base64.b64encode(_protect_bytes(str(text).encode("utf-8"))).decode("ascii")
    except Exception:
        return text


def unprotect(token):
    """落盘字符串 → 明文。解不开（换用户/换机器）时返回空串，按未设置处理。"""
    token = "" if token is None else str(token)
    if not token.startswith(PREFIX) or not _AVAILABLE:
        return token
    try:
        return _unprotect_bytes(base64.b64decode(token[len(PREFIX):])).decode("utf-8")
    except Exception:
        return ""


def dump_config(path, cfg):
    """写 config.json：凭证字段加密后再落盘，其余字段原样。"""
    payload = dict(cfg)
    for name in SECRET_FIELDS:
        val = str(payload.get(name) or "")
        payload[name] = protect(val) if val else ""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_secrets(cfg):
    """把内存 cfg 的凭证字段解密为明文。

    返回 True 表示文件里存在明文凭证，需要调用方回写一次完成迁移。
    """
    if not _AVAILABLE:
        return False
    migrated = False
    for name in SECRET_FIELDS:
        raw = cfg.get(name)
        if not raw:
            continue
        if str(raw).startswith(PREFIX):
            cfg[name] = unprotect(raw)
            if cfg[name] == "":
                print(f"凭证字段 {name} 无法解密（可能换了 Windows 用户），已按未设置处理")
        else:
            migrated = True
    return migrated
