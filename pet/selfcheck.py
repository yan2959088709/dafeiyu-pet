# -*- coding: utf-8 -*-
"""运行时自检：上游「Electron 下载进度写进工作气泡」的等价槽位。

上游在宿主侧下载 Electron，把 43.3.0 / npmmirror / 10 分钟超时 / 3000ms 节流
的进度日志借用工作状态的气泡槽（12s 没新日志就自动清槽）。本项目管理方式
不同——没有 Electron 运行时可下载——所以把同一槽位换成「启动自检」：Python、
Qt、皮肤注册表、素材、字体、可选依赖、凭据链路逐项过一遍，进度照样写这槽，
照样 3000ms 节流、照样 12s 清槽，出问题就等于上游下载失败那样落到 error 档。

判定分三档：ok / warn / fail。warn 是「缺了还能用」（如 edge-tts 没装 → 语音
静默降级），fail 是「跑不起来」（素材目录没了）。绝不在自检里改任何用户数据。
"""
import os
import sys

PROGRESS_MS = 3000
MAX_DETAIL = 40
_OK, _WARN, _FAIL = "ok", "warn", "fail"


def _check_python(_ctx):
    info = "%d.%d.%d" % sys.version_info[:3]
    if sys.version_info < (3, 10):
        return _WARN, "Python %s 偏低（建议 3.10+）" % info
    if getattr(sys, "frozen", False):
        return _OK, "Python %s（打包）" % info
    return _OK, "Python %s" % info


def _check_qt(_ctx):
    try:
        import PySide6
        return _OK, "Qt %s" % getattr(PySide6, "__version__", "?")
    except Exception as exc:
        return _FAIL, "Qt 导入失败：%s" % exc


def _check_skins(_ctx):
    try:
        from skins import SKIN_ORDER, SKIN_REGISTRY
    except Exception as exc:
        return _FAIL, "皮肤注册表不可用：%s" % exc
    if not SKIN_ORDER or set(SKIN_ORDER) != set(SKIN_REGISTRY):
        return _FAIL, "皮肤注册表不一致"
    return _OK, "%d 套皮肤就绪" % len(SKIN_ORDER)


def _count_files(root, suffix):
    n = 0
    for _dirpath, _dirs, files in os.walk(root):
        n += sum(1 for f in files if f.lower().endswith(suffix))
    return n


def _check_assets(ctx):
    sprites = os.path.join(ctx.get("bundle_dir") or "", "sprites")
    stickers = os.path.join(ctx.get("app_dir") or "", "stickers")
    sticker_zip = os.path.join(ctx.get("app_dir") or "", "stickers_20260523_193056.zip")
    if not os.path.isdir(sprites):
        return _FAIL, "缺少 sprites 素材目录"
    frames = _count_files(sprites, ".png")
    if frames <= 0:
        return _FAIL, "sprites 里没有帧图"
    if not os.path.isdir(stickers) and not os.path.exists(sticker_zip):
        return _WARN, "表情包缺失（%d 帧可用）" % frames
    return _OK, "%d 帧素材可用" % frames


def _check_fonts(_ctx):
    try:
        from PySide6.QtGui import QFontDatabase, QGuiApplication
        if QGuiApplication.instance() is None:
            # 无 QGuiApplication 时 QFontDatabase 会 qFatal 直接终止进程，必须先拦
            return _WARN, "无 QGuiApplication，字体检查跳过"
        families = QFontDatabase.families()
    except Exception as exc:
        return _WARN, "字体库查询失败：%s" % exc
    if not families:
        return _WARN, "未发现可用字体（气泡可能不显示）"
    return _OK, "%d 个字体族可用" % len(families)


def _check_deps(_ctx):
    missing = []
    try:
        import edge_tts  # noqa: F401
    except Exception:
        missing.append("edge-tts")
    try:
        import psutil  # noqa: F401
    except Exception:
        missing.append("psutil")
    if missing:
        return _WARN, "缺可选依赖：%s" % "、".join(missing)
    return _OK, "可选依赖齐备"


def _check_credentials(_ctx):
    try:
        from pet import credentials
        if not credentials.available():
            return _WARN, "DPAPI 不可用（凭据将明文拒绝）"
        token = credentials.protect("selfcheck")
        if credentials.unprotect(token) != "selfcheck":
            return _WARN, "凭据加解密不一致"
        return _OK, "凭据加密链路可用"
    except Exception as exc:
        return _WARN, "凭据加密不可用：%s" % exc


STEPS = (
    ("python", "Python 运行时", _check_python),
    ("qt", "Qt 运行时", _check_qt),
    ("skins", "皮肤注册表", _check_skins),
    ("assets", "素材完整性", _check_assets),
    ("fonts", "字体可用性", _check_fonts),
    ("deps", "可选依赖", _check_deps),
    ("credentials", "凭据链路", _check_credentials),
)


def default_ctx():
    from pet.storage import P
    return {"app_dir": P.app_dir, "bundle_dir": P.bundle_dir}


def run_all(ctx=None, steps=None):
    ctx = ctx if ctx is not None else default_ctx()
    out = []
    for key, label, fn in (steps or STEPS):
        try:
            status, detail = fn(ctx)
        except Exception as exc:
            status, detail = _FAIL, "%s：%s" % (label, exc)
        out.append({"key": key, "label": label,
                    "status": status, "detail": str(detail)[:MAX_DETAIL]})
    return out


def failed(results):
    return [r for r in results if r.get("status") == _FAIL]


def summary(results):
    """把结果收成一句话：进度槽只有 40 码点，说清「过没过」比罗列细节重要。"""
    total = len(results)
    bad = failed(results)
    warns = [r for r in results if r.get("status") == _WARN]
    if bad:
        return "error", "自检失败：%s（%d/%d）" % (bad[0]["label"], total - len(bad), total)
    if warns:
        return "ok", "自检完成 %d/%d，%d 项降级" % (total, total, len(warns))
    return "ok", "自检完成：%d/%d 通过" % (total, total)


def due(now_ms, last_ms):
    """节流：进度最多 3000ms 写一次（上游下载日志同参数）。"""
    return last_ms is None or int(now_ms) - int(last_ms) >= PROGRESS_MS


def progress_text(index, total, label):
    return "自检 %d/%d：%s" % (index, total, label)
