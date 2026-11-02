# -*- coding: utf-8 -*-
"""把上游 dsh-pet 的 webm 动作素材转成本项目可用的皮肤素材。

上游素材是 640×360 / 24fps 的 VP9-alpha 视频（唯一发布格式，Qt 不能直接透明
播放），预览 GIF 只有 220×124 / 1bit 透明。这里用本机 ffmpeg 解码（libvpx-vp9
解码器才带 alpha），按「全动作并集包围盒」裁剪后缩到基准高度，编码成动画
WebP（Qt 自带 qwebp 插件支持逐帧读取），并生成首帧预览与 manifest。

产物：
    sprites/dsh-pet/anim/<拼音>.webp
    sprites/dsh-pet/preview/<拼音>.png
    sprites/dsh-pet/manifest.json

用法（项目根目录）：
    python tools/import_dsh_skin.py --src E:\\aitest\\dsh-pet-src\\dsh-pet\\assets
    python tools/import_dsh_skin.py --only 待机呼吸休闲   # 单个
    python tools/import_dsh_skin.py --force              # 重转已有
"""
import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DEST = os.path.join(ROOT, "sprites", "dsh-pet")
ANIM = os.path.join(DEST, "anim")
PREVIEW = os.path.join(DEST, "preview")
MANIFEST = os.path.join(DEST, "manifest.json")

FPS = 12
BASE_HEIGHT = 340
QUALITY = 72
REFERENCE_NAME = "待机呼吸休闲"
MARGIN = 10
MIN_SIDE = 64
PREVIEW_HEIGHT = 160


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for base in (os.environ.get("LOCALAPPDATA") or "",
                 os.environ.get("ProgramFiles") or ""):
        if not base:
            continue
        for name in os.listdir(base) if os.path.isdir(base) else []:
            if not name.lower().startswith("ffmpeg"):
                continue
            path = os.path.join(base, name, "bin", "ffmpeg.exe")
            if os.path.exists(path):
                return path
    return None


def load_pinyin(src_dir):
    """从上游脚本里取「中文动画名 → 拼音文件名」表。"""
    for rel in ("../../scripts/encode_preview_gifs.py", "../../../scripts/encode_preview_gifs.py"):
        path = os.path.abspath(os.path.join(src_dir, rel))
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), path)
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "PINYIN" for t in node.targets):
                return ast.literal_eval(node.value)
    return {}


def load_animations(src_dir):
    """上游 config.jsonc 的动画池（分类/事件/权重）。"""
    from pet import config as pet_config
    path = os.path.join(src_dir, "config.jsonc")
    if not os.path.exists(path):
        return {}, {}
    try:
        data = pet_config.read_jsonc(path)
    except Exception:
        return {}, {}
    return data.get("animations") or {}, data.get("animationWeights") or {}


def tag_actions(animations):
    """动画名 → 用途标签（idle / turn / move / click / action:分类 / event:事件:档位）。"""
    tags = {}
    for name in animations.get("idle") or []:
        tags.setdefault(name, []).append("idle")
    for name in animations.get("turn") or []:
        tags.setdefault(name, []).append("turn")
    drag = animations.get("drag")
    if isinstance(drag, str):
        tags.setdefault(drag, []).append("drag")
    elif isinstance(drag, list):
        for name in drag:
            tags.setdefault(name, []).append("drag")
    for name in animations.get("clicks") or []:
        tags.setdefault(name, []).append("click")
    for row in (animations.get("moves") or {}).get("actions") or []:
        if isinstance(row, dict) and row.get("name"):
            tags.setdefault(row["name"], []).append("move")
    for cat in animations.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        for name in cat.get("actions") or []:
            tags.setdefault(name, []).append("action:%s" % cat.get("id"))
    for event, raw in (animations.get("events") or {}).items():
        if isinstance(raw, str):
            tags.setdefault(raw, []).append("event:%s" % event)
        elif isinstance(raw, list):
            for index, item in enumerate(raw):
                if isinstance(item, str):
                    tags.setdefault(item, []).append("event:%s:%d" % (event, index))
                elif isinstance(item, list):
                    for name in item:
                        tags.setdefault(name, []).append("event:%s:%d" % (event, index))
    return tags


def scan_bbox(frames_dir, stride=4):
    """并集包围盒（alpha > 8），按 stride 抽样，失败返回 None。"""
    from PySide6.QtGui import QImage
    minx, miny, maxx, maxy = 10 ** 9, 10 ** 9, -1, -1
    names = sorted(os.listdir(frames_dir))
    for name in names:
        img = QImage(os.path.join(frames_dir, name))
        if img.isNull():
            continue
        if not img.hasAlphaChannel():
            return None
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
        w, h = img.width(), img.height()
        for y in range(0, h, stride):
            for x in range(0, w, stride):
                if ((img.pixel(x, y) >> 24) & 0xFF) > 8:
                    if x < minx:
                        minx = x
                    if x > maxx:
                        maxx = x
                    if y < miny:
                        miny = y
                    if y > maxy:
                        maxy = y
    if maxx < 0:
        return None
    return minx, miny, maxx, maxy


def run(cmd):
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-800:])


def analyze_box(ffmpeg, src, frames_dir, fps):
    """解帧一次：返回 (包围盒, 帧数, 首帧尺寸)。"""
    if os.path.isdir(frames_dir):
        shutil.rmtree(frames_dir, ignore_errors=True)
    os.makedirs(frames_dir, exist_ok=True)
    run([ffmpeg, "-y", "-v", "error", "-c:v", "libvpx-vp9", "-i", src,
         "-vf", "fps=%d,format=rgba" % fps, os.path.join(frames_dir, "%04d.png")])
    names = sorted(n for n in os.listdir(frames_dir) if n.endswith(".png"))
    from PySide6.QtGui import QImage
    first = QImage(os.path.join(frames_dir, names[0])) if names else QImage()
    return scan_bbox(frames_dir), len(names), (first.width(), first.height())


def encode_one(ffmpeg, src, dst, tmpdir, fps=FPS, height=BASE_HEIGHT,
               quality=QUALITY, scale=None):
    frames_dir = os.path.join(tmpdir, "frames")
    box, count, (w, h) = analyze_box(ffmpeg, src, frames_dir, fps)
    if box is None:
        crop = ""
    else:
        x0 = max(0, box[0] - MARGIN)
        y0 = max(0, box[1] - MARGIN)
        x1 = min(w - 1, box[2] + MARGIN)
        y1 = min(h - 1, box[3] + MARGIN)
        cw = max(MIN_SIDE, x1 - x0 + 1)
        ch = max(MIN_SIDE, y1 - y0 + 1)
        cw = min(cw, w - x0)
        ch = min(ch, h - y0)
        crop = "crop=%d:%d:%d:%d," % (cw, ch, x0, y0)
    if scale:
        zoom = "scale=iw*%.5f:ih*%.5f:flags=lanczos," % (scale, scale)
    else:
        zoom = "scale=-1:%d:flags=lanczos," % height
    vf = "fps=%d,%s%sformat=rgba" % (fps, crop, zoom)
    run([ffmpeg, "-y", "-v", "error", "-c:v", "libvpx-vp9", "-i", src,
         "-vf", vf, "-c:v", "libwebp_anim", "-lossless", "0",
         "-q:v", str(quality), "-loop", "0", dst])
    return count, box


def write_preview(webp_path, preview_path):
    """首帧预览：ffmpeg 读不了动画 WebP（无视频流），交给 Qt 解码。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImageReader
    reader = QImageReader(webp_path)
    img = reader.read()
    if img.isNull():
        return False
    img = img.scaledToHeight(PREVIEW_HEIGHT, Qt.TransformationMode.SmoothTransformation)
    return img.save(preview_path, "PNG")


def webp_size(path):
    from PySide6.QtGui import QImageReader
    reader = QImageReader(path)
    size = reader.size()
    return size.width(), size.height(), reader.imageCount()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=r"E:\aitest\dsh-pet-src\dsh-pet\assets")
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--fps", type=int, default=FPS)
    parser.add_argument("--height", type=int, default=BASE_HEIGHT)
    parser.add_argument("--quality", type=int, default=QUALITY)
    args = parser.parse_args(argv)

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("未找到 ffmpeg（需要 libvpx-vp9 解码器与 libwebp_anim 编码器）")
        return 1
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    _app = QGuiApplication.instance() or QGuiApplication([])
    webm_dir = os.path.join(args.src, "webm")
    if not os.path.isdir(webm_dir):
        print("素材目录不存在：%s" % webm_dir)
        return 1
    pinyin = load_pinyin(args.src)
    animations, weights = load_animations(args.src)
    tags = tag_actions(animations)
    os.makedirs(ANIM, exist_ok=True)
    os.makedirs(PREVIEW, exist_ok=True)

    files = sorted(n for n in os.listdir(webm_dir) if n.lower().endswith(".webm"))
    if args.only:
        keep = set(args.only)
        files = [n for n in files if os.path.splitext(n)[0] in keep]
    if args.limit:
        files = files[:args.limit]

    manifest = {}
    if os.path.exists(MANIFEST) and not args.force:
        try:
            with open(MANIFEST, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            manifest = {}
    entries = {row["name"]: row for row in manifest.get("actions") or []}
    scale = manifest.get("scale")
    by_name = {os.path.splitext(n)[0]: n for n in files}
    if not scale and REFERENCE_NAME in by_name:
        # 统一缩放基准：按待机动作的包围盒定比例，避免「跳起来就变小」
        with tempfile.TemporaryDirectory(prefix="dsh_ref_") as reftmp:
            box, _n, _size = analyze_box(
                ffmpeg, os.path.join(webm_dir, by_name[REFERENCE_NAME]),
                os.path.join(reftmp, "frames"), args.fps)
            if box:
                scale = float(args.height) / max(1.0, box[3] - box[1] + 2 * MARGIN)
    if not scale:
        scale = float(args.height) / 300.0

    with tempfile.TemporaryDirectory(prefix="dsh_skin_") as tmpdir:
        for index, filename in enumerate(files, 1):
            name = os.path.splitext(filename)[0]
            slug = pinyin.get(name) or ("act%03d" % index)
            dst = os.path.join(ANIM, slug + ".webp")
            preview = os.path.join(PREVIEW, slug + ".png")
            print("[%d/%d] %s -> %s" % (index, len(files), name, slug), flush=True)
            if os.path.exists(dst) and os.path.exists(preview) and not args.force:
                w, h, frames = webp_size(dst)
            else:
                count, _box = encode_one(ffmpeg, os.path.join(webm_dir, filename), dst,
                                        tmpdir, args.fps, args.height, args.quality, scale)
                write_preview(dst, preview)
                w, h, frames = webp_size(dst)
                frames = frames or count
            entries[name] = {
                "name": name,
                "slug": slug,
                "file": "anim/%s.webp" % slug,
                "preview": "preview/%s.png" % slug,
                "frames": int(frames),
                "width": int(w),
                "height": int(h),
                "fps": args.fps,
                "tags": tags.get(name, []),
            }
            with open(MANIFEST, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "fps": args.fps, "baseHeight": args.height,
                           "scale": scale, "reference": REFERENCE_NAME,
                           "animations": animations, "weights": weights,
                           "actions": sorted(entries.values(),
                                             key=lambda r: r["name"])},
                          f, ensure_ascii=False, indent=1)
    print("完成：%d 个动作，manifest=%s" % (len(entries), MANIFEST))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
