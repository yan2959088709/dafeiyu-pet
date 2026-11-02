# -*- coding: utf-8 -*-
"""对话图片附件：准入校验、魔数嗅探、预算压缩、按 sha256 存取。

上游的准入是「四类 MIME + base64 不超 90 万字符」，落盘键是内容哈希
（`sha256:<64hex>`），取图接口只校验 id 形状、不信任路径。压缩预算是
原图 ≤640KiB 且长边 ≤2048 原样发，超了走质量阶梯 0.85/0.75/0.65/0.55。

压缩需要图像库：默认用 PySide6 的 QImage（项目既有依赖，不新增），
Qt 不可用时原样返回并由调用方提示「图片过大」。
"""
import base64
import binascii
import hashlib
import os
import re

MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
MAX_BASE64_CHARS = 900000
MAX_BYTES = 640 * 1024
MAX_SIDE = 2048
QUALITIES = (0.85, 0.75, 0.65, 0.55)
PREVIEW_PX = 96
PREVIEW_QUALITY = 0.72

ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

_MSG_NOT_OBJ = "attachment 必须是对象"
_MSG_TYPE = "attachment.mediaType 必须是 %s 之一" % " / ".join(MEDIA_TYPES)
_MSG_DATA = "attachment.data 必须是非空 base64 字符串"
_MSG_TOO_LONG = "attachment.data 过长（请压缩或降低分辨率）"
_MSG_BAD_B64 = "attachment.data 不是合法的 base64"


def validate(attachment):
    """返回 (media_type, bytes)；非法抛 ValueError（文案与上游一致）。"""
    if not isinstance(attachment, dict):
        raise ValueError(_MSG_NOT_OBJ)
    media_type = str(attachment.get("mediaType") or "").strip().lower()
    if media_type not in MEDIA_TYPES:
        raise ValueError(_MSG_TYPE)
    data = attachment.get("data")
    if not isinstance(data, str) or not data.strip():
        raise ValueError(_MSG_DATA)
    if len(data) > MAX_BASE64_CHARS:
        raise ValueError(_MSG_TOO_LONG)
    raw = data.strip()
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    try:
        blob = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(_MSG_BAD_B64)
    if not blob:
        raise ValueError(_MSG_DATA)
    return (media_type, blob)


def sniff(blob):
    """按前 16 字节定类型（附件无扩展名）。"""
    head = (blob or b"")[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def attachment_id(blob):
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def is_valid_id(value):
    return bool(ID_RE.match(str(value or "")))


def path_for(root, att_id):
    """只接受合法 id，路径由哈希算出来；非法返回 None。"""
    if not is_valid_id(att_id):
        return None
    name = att_id.split(":", 1)[1] + ".bin"
    return os.path.join(root, name)


def save(root, blob):
    """按内容哈希落盘（已存在不重写）；返回附件 id。"""
    att_id = attachment_id(blob)
    os.makedirs(root, exist_ok=True)
    path = path_for(root, att_id)
    if not os.path.exists(path):
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, path)
    return att_id


def load(root, att_id):
    path = path_for(root, att_id)
    if not path or not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def needs_compress(blob, side=None):
    if len(blob) > MAX_BYTES:
        return True
    if side is not None and int(side) > MAX_SIDE:
        return True
    return False


def _qt_encoder(blob, media_type, quality, max_side):
    """PySide6 编码：等比缩到 max_side 内，按质量阶梯出 JPEG。"""
    from PySide6.QtGui import QImage
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    img = QImage.fromData(blob)
    if img.isNull():
        return None
    if max(img.width(), img.height()) > max_side:
        img = img.scaled(max_side, max_side, aspectMode=1, mode=1)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(buf, "JPEG", quality):
        return None
    return bytes(buf.data())


def prepare(blob, media_type, encoder=None):
    """超预算则压缩；压不动就原样返回并标记 too_large。"""
    if not needs_compress(blob):
        return {"blob": blob, "mediaType": media_type, "compressed": False,
                "tooLarge": False}
    enc = encoder or _qt_encoder
    for quality in QUALITIES:
        try:
            out = enc(blob, media_type, int(quality * 100), MAX_SIDE)
        except Exception:
            out = None
        if out and len(out) <= MAX_BYTES:
            return {"blob": out, "mediaType": "image/jpeg",
                    "compressed": True, "tooLarge": False}
    return {"blob": blob, "mediaType": media_type, "compressed": False,
            "tooLarge": True}


def preview_size(width, height):
    """预览缩略图尺寸（长边 96，保持比例）。"""
    if not width or not height:
        return (PREVIEW_PX, PREVIEW_PX)
    scale = PREVIEW_PX / float(max(width, height))
    return (max(1, int(width * scale)), max(1, int(height * scale)))
