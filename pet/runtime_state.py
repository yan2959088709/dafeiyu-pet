# -*- coding: utf-8 -*-
"""本地接口的运行状态文件：让外部脚本能找到端口与令牌。

为什么必须有它：令牌只存在内存里，外部调用方无从得知；上游靠 Helper 环境变量
与设置页 URL 片段传递，本地等价物就是这份运行状态文件（0600，原子写）。

清理要 pid 与 token 双匹配：多开/重启时后启动的实例不能把前一个的状态删掉。
"""
import json
import os
import time

NAME = "api-runtime.json"


def write(path, port, token, pets=None, extra=None):
    payload = {
        "name": "dafeiyu-pet",
        "pid": os.getpid(),
        "startedAt": int(time.time() * 1000),
        "port": int(port),
        "token": str(token),
        "prefix": "/dsh-pet-7340",
        "pets": list(pets or []),
    }
    if isinstance(extra, dict):
        payload.update(extra)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return payload


def read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def clear(path, pid=None, token=None):
    """pid / token 对不上就不删（防误删另一个实例的状态）。"""
    data = read(path)
    if not data:
        return False
    if pid is not None:
        try:
            if int(data.get("pid", -1)) != int(pid):
                return False
        except (TypeError, ValueError):
            return False
    if token is not None and data.get("token") != token:
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False
