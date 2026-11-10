# -*- coding: utf-8 -*-
"""聊天头像：用户 / AI 自定义头像的落盘、圆角裁切与默认回退。

为什么预裁成 PNG 而不是在 HTML 里做圆角：消息流是 QTextBrowser 的富文本，
Qt 只认 img 标签的 width/height，不支持 border-radius。选图时就居中裁方、
缩到 96px 再加圆角蒙版，气泡里按 36px 渲染，高分屏也不糊。

为什么按文件存在与否判定而不是加配置项：头像只有「自定义」和「默认」两态，
文件即状态；默认态下 AI 回退项目 logo、用户回退「主」占位，不占配置字段。
"""
import os

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath

from pet.storage import P

KINDS = ("user", "pet")
SIZE = 96
RADIUS = 18
MAX_BYTES = 20 * 1024 * 1024


def _dir():
    path = getattr(P, "avatars", os.path.join(P.app_dir, "avatars"))
    os.makedirs(path, exist_ok=True)
    return path


def custom_path(kind):
    """自定义头像文件路径；没设置过返回 None。"""
    if kind not in KINDS:
        raise ValueError("未知头像类型：%s" % kind)
    path = os.path.join(_dir(), kind + ".png")
    return path if os.path.exists(path) else None


def effective(kind):
    """渲染用头像路径：自定义优先；AI 回退项目 logo，用户回退 None（画「主」）。"""
    path = custom_path(kind)
    if path:
        return path
    return P.logo if kind == "pet" else None


def save(kind, source):
    """读入图片 → 中心方裁 → 96px → 圆角 → 存 PNG，返回落盘路径。"""
    if kind not in KINDS:
        raise ValueError("未知头像类型：%s" % kind)
    try:
        if os.path.getsize(source) > MAX_BYTES:
            raise ValueError("图片太大（上限 20MB）")
    except OSError as e:
        raise ValueError("读不到图片：%s" % e)
    img = QImage(source)
    if img.isNull():
        raise ValueError("无法识别这张图片（支持 PNG / JPEG / WebP / GIF）")
    side = min(img.width(), img.height())
    if side <= 0:
        raise ValueError("图片尺寸无效")
    img = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
    img = img.scaled(SIZE, SIZE, Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    out = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, SIZE, SIZE), RADIUS, RADIUS)
        painter.setClipPath(clip)
        painter.drawImage(0, 0, img)
    finally:
        painter.end()
    dst = os.path.join(_dir(), kind + ".png")
    if not out.save(dst, "PNG"):
        raise ValueError("保存头像失败：%s" % dst)
    return dst


def clear(kind):
    """恢复默认头像；返回是否真的删掉了自定义文件。"""
    path = custom_path(kind)
    if not path:
        return False
    try:
        os.remove(path)
    except OSError:
        return False
    return True
