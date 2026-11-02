# -*- coding: utf-8 -*-
"""本地 HTTP 层：只绑 127.0.0.1、Host/Origin 校验、令牌矩阵、统一 JSON 应答。

为什么需要它：剥离 DSH 后，工作状态、工具详情、第三方投喂气泡这些「宿主事件」
没有来源。等价做法是把上游的 `/dsh-pet-7340` 契约原样保留在本机，任何脚本或
DSH 侧的适配器都能按同一份协议推送事件。

鉴权矩阵照上游：公开只读（/、/state、/config 等 GET），敏感读（whisper、chat）
与所有写方法必须带 43 字符 `x-dsh-pet-token`；令牌每次启动重新生成，不落日志。
"""
import json
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from pet.petservice import MAX_BODY_BYTES

PREFIX = "/dsh-pet-7340"
DEFAULT_PORT = 7340
PORT_TRIES = 25
PUBLIC_GET = ("/state", "/config", "/config/meta", "/models", "/pose",
              "/voice/voices", "/menu/events", "/playback/")
TOKEN_REQUIRED_GET = ("/whisper", "/chat")


def new_token():
    return secrets.token_urlsafe(32)


CONTENT_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".ttf": "font/ttf", ".otf": "font/otf",
}
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff ]+$")


def safe_name(name):
    """素材名只允许单段文件名（禁 / \\ .. 与控制字符）。"""
    name = str(name or "")
    if not name or not _NAME_RE.match(name):
        return None
    if name in (".", "..") or ".." in name:
        return None
    return name


def resolve_asset(rest, paths):
    """把 /logo、/sound/x、/pic/x、/thumb/<pet>/<file> 映到实际文件。

    paths 是 {logo, sprites, dsh, stickers} 路径表；不回退到项目其他目录，
    避免一张图就能把 config.json 读出去。
    """
    rest = "/" + str(rest or "").strip("/")
    if rest == "/logo":
        return paths.get("logo")
    parts = rest.strip("/").split("/")
    if len(parts) == 2 and parts[0] == "sound":
        name = safe_name(parts[1])
        return os.path.join(paths.get("dsh") or "", name) if name else None
    if len(parts) == 2 and parts[0] == "pic":
        name = safe_name(parts[1])
        if not name:
            return None
        for root in (paths.get("sprites"), paths.get("stickers")):
            if root and os.path.exists(os.path.join(root, name)):
                return os.path.join(root, name)
        return None
    if len(parts) == 3 and parts[0] == "thumb":
        pet = safe_name(parts[1])
        name = safe_name(parts[2])
        if not pet or not name:
            return None
        return os.path.join(paths.get("sprites") or "", pet, name)
    return None


class _LoopbackHTTPServer(ThreadingHTTPServer):
    """不共享端口：Windows 的 SO_REUSEADDR 会让两个进程同时绑定同一端口，
    后绑的那个永远收不到请求（表现为令牌总是不对）。"""

    allow_reuse_address = False
    daemon_threads = True


class ApiServer:
    """把 PetService 挂到回环端口；start 返回实际端口与令牌。"""

    def __init__(self, service, port=DEFAULT_PORT, token=None, asset_paths=None):
        self.service = service
        self.token = token or new_token()
        self.asset_paths = dict(asset_paths or {})
        self.port = None
        self._httpd = None
        self._thread = None
        self._preferred = int(port) if port is not None else DEFAULT_PORT

    def start(self):
        last = None
        ports = (0,) if self._preferred <= 0 else range(
            self._preferred, self._preferred + PORT_TRIES)
        for port in ports:
            try:
                httpd = _LoopbackHTTPServer(("127.0.0.1", port), _make_handler(self))
                httpd.daemon_threads = True
                self._httpd = httpd
                self.port = httpd.server_address[1]
                self._thread = threading.Thread(target=httpd.serve_forever,
                                                name="pet-api", daemon=True)
                self._thread.start()
                return {"ok": True, "port": self.port, "token": self.token}
            except OSError as e:
                last = e
                continue
        return {"ok": False, "message": "端口 %d 起连续 %d 个都被占用：%s"
                                       % (self._preferred, PORT_TRIES, last)}

    def stop(self):
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
        self._httpd = None
        self._thread = None

    # ---------------------------------------------------------- 请求校验
    def host_ok(self, host):
        if not host:
            return False
        host = host.lower()
        return host in ("127.0.0.1:%d" % self.port, "localhost:%d" % self.port)

    def origin_ok(self, origin):
        if not origin:
            return True
        return origin.lower() in ("http://127.0.0.1:%d" % self.port,
                                 "http://localhost:%d" % self.port)

    def is_public(self, method, path):
        if method not in ("GET", "HEAD"):
            return False
        if any(path.startswith(p) for p in TOKEN_REQUIRED_GET):
            return False
        return any(path == p or path.startswith(p) for p in PUBLIC_GET)

    def token_ok(self, value):
        value = str(value or "")
        if len(value) != len(self.token):
            return False
        return secrets.compare_digest(value, self.token)

    def resolve_asset(self, rest):
        return resolve_asset(rest, self.asset_paths)

    def is_asset_path(self, rest):
        rest = "/" + str(rest or "").strip("/")
        return rest == "/logo" or rest.startswith(("/pic/", "/sound/", "/thumb/"))


def _make_handler(server):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass   # 不把路径/令牌写进任何日志

        def _send(self, code, payload, extra=None):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for key, val in (extra or {}).items():
                self.send_header(key, val)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _error(self, code, message):
            self._send(code, {"ok": False, "reason": "protocol", "message": message})

        def _serve_file(self, path):
            """素材：单段 Range（206/416），带 1 小时缓存。"""
            if not path or not os.path.isfile(path):
                return self._error(404, "文件不存在")
            size = os.path.getsize(path)
            ctype = CONTENT_TYPES.get(os.path.splitext(path)[1].lower(),
                                      "application/octet-stream")
            start, end, code = 0, size - 1, 200
            rng = (self.headers.get("Range") or "").strip()
            if rng:
                m = re.match(r"bytes=(\d*)-(\d*)$", rng)
                if not m or (not m.group(1) and not m.group(2)):
                    return self._error(416, "Range 无效")
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                    end = size - 1
                if start >= size or start > end or end >= size:
                    return self._error(416, "Range 越界")
                code = 206
            with open(path, "rb") as f:
                f.seek(start)
                data = f.read(end - start + 1)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "public, max-age=3600")
            if code == 206:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _handle(self):
            if self.command == "OPTIONS":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not server.host_ok(self.headers.get("Host")):
                return self._error(403, "仅允许当前本机地址")
            if not server.origin_ok(self.headers.get("Origin")):
                return self._error(403, "不允许跨站请求")
            parsed = urlparse(self.path)
            path = parsed.path
            if not path.startswith(PREFIX):
                return self._error(404, "没有匹配的路由")
            rest = path[len(PREFIX):] or "/"
            query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            if self.command in ("GET", "HEAD") and rest == "/attachment":
                from pet import chat_attach
                from pet.storage import P
                att_id = str(query.get("id") or "")
                target = chat_attach.path_for(P.attachments, att_id)
                if not target:
                    return self._error(400, "id 必须是 sha256:<64hex>")
                return self._serve_file(target)
            if self.command in ("GET", "HEAD") and server.is_asset_path(rest):
                asset = server.resolve_asset(rest)
                if not asset:
                    return self._error(404, "文件不存在")
                return self._serve_file(asset)
            if not server.is_public(self.command, rest):
                if not server.token_ok(self.headers.get("X-DSH-Pet-Token")):
                    return self._error(403, "请从桌宠菜单重新打开设置")
            body = None
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                return self._error(413, "request body too large")
            if length:
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except Exception:
                    return self._error(400, "invalid JSON body")
            try:
                result = server.service.dispatch(self.command, rest, query, body)
            except Exception as e:
                return self._error(500, "dsh-pet: internal error (%s)" % e)
            reason = (result or {}).get("reason")
            if reason == "method":
                return self._error(405, "method not allowed")
            if reason == "not-found":
                return self._error(404, "没有匹配的路由")
            self._send(200, result)

        do_GET = _handle
        do_HEAD = _handle
        do_POST = _handle
        do_PUT = _handle
        do_DELETE = _handle
        do_OPTIONS = _handle

    return Handler
