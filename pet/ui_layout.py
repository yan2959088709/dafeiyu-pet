# -*- coding: utf-8 -*-
"""界面布局与文案的可编辑数据源（气泡 / 窗口 / 控制台）。

存在意义：这些数值原先散落成绘制函数里的字面量——气泡 viewBox 坐标在
pet/gadget.py，窗口余量在 桌宠.py，控制台尺寸在 pet/console_ui.py。改一个
位置要动代码再重启。统一收进 ui_layout.json 后，可视化编辑器
（pet/layout_editor.py）与绘制方读同一份数据，用户调完即时生效。

口径：
- 气泡坐标一律用 DSH 官方 viewBox 单位（view_w × view_h），绘制时乘
  s = 窗宽 / view_w；与官方素材同源，换皮肤、换尺寸都不漂。
- 比率一律 0~1（头顶气泡区、左右余量）。
- 文件缺失、单键缺失、类型不符、超出范围都回落 DEFAULT，绝不因缺项报错。

边界：只做数据与落盘，不含任何 Qt 依赖——沙箱里能直接跑、能单测。
"""
import copy
import json
import os
import tempfile

from pet.storage import P, backup_corrupt

VERSION = 1

DEFAULT = {
    "version": VERSION,
    "window": {
        "margin": 4.0,
        "top_h_ratio": 0.4055,
        "side_pad_ratio": 0.062,
        "side_pad_min": 6.0,
        "bottom_pad": 10.0,
    },
    "bubble": {
        "view_w": 1026.0,
        "view_h": 700.0,
        "top_offset": 4.0,
        "main": {"cx": 454.0, "cy": 248.0, "rx": 373.0, "ry": 232.0},
        "tail": {"visible": True, "cx": 357.0, "half_w": 62.0,
                 "top_inset": -6.0, "tip": 26.0},
        "decor": {
            "outer": {"visible": True, "cx": 352.0, "cy": 561.0, "rx": 37.5, "ry": 26.0},
            "inner": {"visible": True, "cx": 442.0, "cy": 646.0, "rx": 24.5, "ry": 18.0},
        },
        "fill": {"color": "#ffffff", "alpha": 245},
        "stroke": {"color": "#203170", "width": 18.0, "min_width": 2.0},
        "text": {
            "max_w": 560.0,
            "center_y": 255.0,
            "avail_h": 360.0,
            "shrink_floor": 0.55,
            "min_px": 20.0,
            "global_min_px": 18.0,
            "styles": {
                "A": {"size": 66.0, "color": "#536ba9", "weight": "DemiBold"},
                "B": {"size": 128.0, "color": "#536ba9", "weight": "DemiBold"},
                "P": {"size": 104.0, "color": "#536ba9", "weight": "DemiBold"},
                "C": {"size": 56.0, "color": "#9fb0d9", "weight": "Medium"},
            },
        },
        "image": {"max_w": 560.0, "max_h": 320.0, "top": 60.0, "caption_top": 150.0},
        "gif": {"max_w": 560.0, "max_h": 400.0, "center_y": 250.0,
                "fallback_px": 96.0, "fallback_text": "🐳 rua~"},
    },
    "texts": {
        "balance_title": "DeepSeek 余额",
        "balance_loading": "余额查询中…",
        "usage_line": "今日已用 ¥{usage:.2f}",
        "currency_cny": "¥",
        "currency_usd": "$",
        "currency_eur": "€",
        "money_title": "余额",
        "money_available": "可用",
        "money_unavailable": "不可用",
        "windows_title": "额度",
        "windows_detail_title": "明细",
        "points_title": "余额",
        "custom_empty": "（暂无内容）",
    },
    "console": {
        "window_w": 980,
        "window_h": 680,
        "nav_width": 148,
        "nav_item_h": 40,
        "nav_spacing": 2,
        "nav_top_pad": 6,
        "home_margin_l": 22,
        "home_margin_t": 20,
        "home_margin_r": 22,
        "home_margin_b": 20,
        "home_spacing": 16,
        "preview_size": 230,
        "title_pt_boost": 8,
        "page_margin": 14,
        "skin_preview_h": 170,
        "skin_grid_w": 132,
        "skin_grid_h": 100,
        "skin_icon": 64,
        "split_left": 2,
        "split_right": 5,
    },
    "console_texts": {
        "nav": ["主窗口", "界面布局", "动作绑定", "设置", "管理"],
        "home_title": "桌宠",
        "home_group": "当前状态",
        "home_buttons": ["和我聊天", "随机动作", "让它睡会儿", "显示/隐藏", "回到屏幕内"],
        "home_hint": "皮肤、动作库、宠物与服务状态都在左侧「管理」里；设置项在「设置」里。",
        "manage_skin_group": "皮肤库",
        "manage_apply": "应用这个形象",
        "manage_folder": "打开素材目录",
        "manage_tabs": ["动作库", "宠物", "服务"],
    },
}

# 画布上的热区 ↔ 数据路径。编辑器按这张表生成可拖拽矩形，属性面板按 FIELDS 生成控件。
HOTSPOTS = (
    ("bubble.main", "气泡主体", "rect"),
    ("bubble.tail", "气泡尾巴", "tail"),
    ("bubble.decor.outer", "装饰小气泡 1", "rect"),
    ("bubble.decor.inner", "装饰小气泡 2", "rect"),
    ("bubble.text", "文字区", "text"),
    ("bubble.image", "配图区", "image"),
    ("bubble.gif", "GIF 区", "image"),
)

STR_TEXT = "text"
STR_COLOR = "color"
NUM = "num"
FLAG = "flag"
WORDS = "words"
CHOICE = "choice"

# 路径 → (标签, 类型, 最小, 最大, 小数位, 选项)
FIELDS = {
    "window.margin": ("气泡与素材间距", NUM, 0.0, 40.0, 0, None),
    "window.top_h_ratio": ("头顶气泡区占比", NUM, 0.10, 0.80, 4, None),
    "window.side_pad_ratio": ("左右余量占比", NUM, 0.0, 0.30, 3, None),
    "window.side_pad_min": ("左右最小余量", NUM, 0.0, 60.0, 0, None),
    "window.bottom_pad": ("窗口底部留白", NUM, 0.0, 80.0, 0, None),

    "bubble.view_w": ("参考画布宽", NUM, 200.0, 4000.0, 0, None),
    "bubble.view_h": ("参考画布高", NUM, 200.0, 4000.0, 0, None),
    "bubble.top_offset": ("气泡顶部偏移", NUM, -40.0, 120.0, 1, None),
    "bubble.main.cx": ("主体中心 X", NUM, 0.0, 4000.0, 1, None),
    "bubble.main.cy": ("主体中心 Y", NUM, 0.0, 4000.0, 1, None),
    "bubble.main.rx": ("主体半宽", NUM, 10.0, 2000.0, 1, None),
    "bubble.main.ry": ("主体半高", NUM, 10.0, 2000.0, 1, None),
    "bubble.tail.visible": ("显示尾巴", FLAG, None, None, 0, None),
    "bubble.tail.cx": ("尾巴中心 X", NUM, 0.0, 4000.0, 1, None),
    "bubble.tail.half_w": ("尾巴半宽", NUM, 0.0, 400.0, 1, None),
    "bubble.tail.top_inset": ("尾巴上沿内缩", NUM, -200.0, 60.0, 1, None),
    "bubble.tail.tip": ("尾巴尖高度", NUM, 0.0, 300.0, 1, None),
    "bubble.decor.outer.visible": ("显示装饰泡 1", FLAG, None, None, 0, None),
    "bubble.decor.outer.cx": ("装饰泡 1 中心 X", NUM, 0.0, 4000.0, 1, None),
    "bubble.decor.outer.cy": ("装饰泡 1 中心 Y", NUM, 0.0, 4000.0, 1, None),
    "bubble.decor.outer.rx": ("装饰泡 1 半宽", NUM, 1.0, 400.0, 1, None),
    "bubble.decor.outer.ry": ("装饰泡 1 半高", NUM, 1.0, 400.0, 1, None),
    "bubble.decor.inner.visible": ("显示装饰泡 2", FLAG, None, None, 0, None),
    "bubble.decor.inner.cx": ("装饰泡 2 中心 X", NUM, 0.0, 4000.0, 1, None),
    "bubble.decor.inner.cy": ("装饰泡 2 中心 Y", NUM, 0.0, 4000.0, 1, None),
    "bubble.decor.inner.rx": ("装饰泡 2 半宽", NUM, 1.0, 400.0, 1, None),
    "bubble.decor.inner.ry": ("装饰泡 2 半高", NUM, 1.0, 400.0, 1, None),
    "bubble.fill.color": ("填充色", STR_COLOR, None, None, 0, None),
    "bubble.fill.alpha": ("填充不透明度", NUM, 0.0, 255.0, 0, None),
    "bubble.stroke.color": ("描边色", STR_COLOR, None, None, 0, None),
    "bubble.stroke.width": ("描边宽度", NUM, 0.0, 80.0, 1, None),
    "bubble.stroke.min_width": ("描边最小宽度", NUM, 0.0, 20.0, 1, None),
    "bubble.text.max_w": ("文字最大宽", NUM, 40.0, 2000.0, 1, None),
    "bubble.text.center_y": ("文字块中心 Y", NUM, 0.0, 4000.0, 1, None),
    "bubble.text.avail_h": ("文字可用高", NUM, 20.0, 2000.0, 1, None),
    "bubble.text.shrink_floor": ("缩字下限比例", NUM, 0.10, 1.00, 2, None),
    "bubble.text.min_px": ("缩字最小像素", NUM, 4.0, 120.0, 0, None),
    "bubble.text.global_min_px": ("整体缩放最小像素", NUM, 4.0, 120.0, 0, None),
    "bubble.text.styles.A.size": ("A 档字号", NUM, 8.0, 400.0, 1, None),
    "bubble.text.styles.A.color": ("A 档颜色", STR_COLOR, None, None, 0, None),
    "bubble.text.styles.A.weight": ("A 档字重", CHOICE, None, None, 0,
                                    ("Thin", "Light", "Normal", "Medium", "DemiBold", "Bold")),
    "bubble.text.styles.B.size": ("B 档字号", NUM, 8.0, 400.0, 1, None),
    "bubble.text.styles.B.color": ("B 档颜色", STR_COLOR, None, None, 0, None),
    "bubble.text.styles.B.weight": ("B 档字重", CHOICE, None, None, 0,
                                    ("Thin", "Light", "Normal", "Medium", "DemiBold", "Bold")),
    "bubble.text.styles.P.size": ("P 档字号", NUM, 8.0, 400.0, 1, None),
    "bubble.text.styles.P.color": ("P 档颜色", STR_COLOR, None, None, 0, None),
    "bubble.text.styles.P.weight": ("P 档字重", CHOICE, None, None, 0,
                                    ("Thin", "Light", "Normal", "Medium", "DemiBold", "Bold")),
    "bubble.text.styles.C.size": ("C 档字号", NUM, 8.0, 400.0, 1, None),
    "bubble.text.styles.C.color": ("C 档颜色", STR_COLOR, None, None, 0, None),
    "bubble.text.styles.C.weight": ("C 档字重", CHOICE, None, None, 0,
                                    ("Thin", "Light", "Normal", "Medium", "DemiBold", "Bold")),
    "bubble.image.max_w": ("配图最大宽", NUM, 40.0, 2000.0, 1, None),
    "bubble.image.max_h": ("配图最大高", NUM, 40.0, 2000.0, 1, None),
    "bubble.image.top": ("配图顶部偏移", NUM, 0.0, 1000.0, 1, None),
    "bubble.image.caption_top": ("配图文案高度位", NUM, 0.0, 1000.0, 1, None),
    "bubble.gif.max_w": ("GIF 最大宽", NUM, 40.0, 2000.0, 1, None),
    "bubble.gif.max_h": ("GIF 最大高", NUM, 40.0, 2000.0, 1, None),
    "bubble.gif.center_y": ("GIF 中心 Y", NUM, 0.0, 4000.0, 1, None),
    "bubble.gif.fallback_px": ("GIF 降级字号", NUM, 8.0, 400.0, 1, None),
    "bubble.gif.fallback_text": ("GIF 降级文案", STR_TEXT, None, None, 0, None),

    "texts.balance_title": ("余额标题", STR_TEXT, None, None, 0, None),
    "texts.balance_loading": ("查余额占位", STR_TEXT, None, None, 0, None),
    "texts.usage_line": ("今日已用文案", STR_TEXT, None, None, 0, None),
    "texts.currency_cny": ("人民币符号", STR_TEXT, None, None, 0, None),
    "texts.currency_usd": ("美元符号", STR_TEXT, None, None, 0, None),
    "texts.currency_eur": ("欧元符号", STR_TEXT, None, None, 0, None),
    "texts.money_title": ("金额页标题", STR_TEXT, None, None, 0, None),
    "texts.money_available": ("可用", STR_TEXT, None, None, 0, None),
    "texts.money_unavailable": ("不可用", STR_TEXT, None, None, 0, None),
    "texts.windows_title": ("额度页标题", STR_TEXT, None, None, 0, None),
    "texts.windows_detail_title": ("明细页标题", STR_TEXT, None, None, 0, None),
    "texts.points_title": ("积分页标题", STR_TEXT, None, None, 0, None),
    "texts.custom_empty": ("自定义空页文案", STR_TEXT, None, None, 0, None),

    "console.window_w": ("控制台宽", NUM, 600.0, 2400.0, 0, None),
    "console.window_h": ("控制台高", NUM, 400.0, 1600.0, 0, None),
    "console.nav_width": ("导航栏宽", NUM, 60.0, 400.0, 0, None),
    "console.nav_item_h": ("导航项高", NUM, 20.0, 120.0, 0, None),
    "console.nav_spacing": ("导航项间距", NUM, 0.0, 40.0, 0, None),
    "console.nav_top_pad": ("导航顶部留白", NUM, 0.0, 80.0, 0, None),
    "console.home_margin_l": ("主页左边距", NUM, 0.0, 120.0, 0, None),
    "console.home_margin_t": ("主页上边距", NUM, 0.0, 120.0, 0, None),
    "console.home_margin_r": ("主页右边距", NUM, 0.0, 120.0, 0, None),
    "console.home_margin_b": ("主页下边距", NUM, 0.0, 120.0, 0, None),
    "console.home_spacing": ("主页区块间距", NUM, 0.0, 80.0, 0, None),
    "console.preview_size": ("形象预览边长", NUM, 80.0, 600.0, 0, None),
    "console.title_pt_boost": ("标题字号增量", NUM, 0.0, 40.0, 0, None),
    "console.page_margin": ("设置/管理页边距", NUM, 0.0, 120.0, 0, None),
    "console.skin_preview_h": ("皮肤预览高", NUM, 60.0, 600.0, 0, None),
    "console.skin_grid_w": ("皮肤格宽", NUM, 60.0, 400.0, 0, None),
    "console.skin_grid_h": ("皮肤格高", NUM, 40.0, 400.0, 0, None),
    "console.skin_icon": ("皮肤图标边长", NUM, 16.0, 256.0, 0, None),
    "console.split_left": ("左右分栏 左", NUM, 1.0, 20.0, 0, None),
    "console.split_right": ("左右分栏 右", NUM, 1.0, 20.0, 0, None),

    "console_texts.nav": ("导航项", WORDS, None, None, 0, None),
    "console_texts.home_title": ("主页标题", STR_TEXT, None, None, 0, None),
    "console_texts.home_group": ("状态分组名", STR_TEXT, None, None, 0, None),
    "console_texts.home_buttons": ("主页按钮", WORDS, None, None, 0, None),
    "console_texts.home_hint": ("主页提示句", STR_TEXT, None, None, 0, None),
    "console_texts.manage_skin_group": ("皮肤库分组名", STR_TEXT, None, None, 0, None),
    "console_texts.manage_apply": ("应用形象按钮", STR_TEXT, None, None, 0, None),
    "console_texts.manage_folder": ("素材目录按钮", STR_TEXT, None, None, 0, None),
    "console_texts.manage_tabs": ("管理页标签", WORDS, None, None, 0, None),
}

_CACHE = {"data": None}


def path():
    """布局文件位置：跟其他用户数据一起落在 app_dir，随配置走。"""
    return getattr(P, "ui_layout", os.path.join(P.app_dir, "ui_layout.json"))


def pending_path():
    return getattr(P, "ui_layout_pending",
                   os.path.join(P.app_dir, "ui_layout_pending.json"))


def _stringify_list(value, base):
    if not isinstance(value, (list, tuple)):
        return list(base)
    return [str(v) for v in value]


def _coerce(base, value):
    """按默认值的类型收敛外来值；类型实在对不上就退回默认。

    int 与 float 分开处理：控制台的像素尺寸默认是 int，收敛成 float 后
    Qt 的 setFixedWidth/setIconSize 会直接报错。
    """
    if isinstance(base, bool):
        return value if isinstance(value, bool) else base
    if isinstance(base, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return base
        return int(value) if isinstance(base, int) else float(value)
    if isinstance(base, str):
        return value if isinstance(value, str) else base
    if isinstance(base, list):
        return _stringify_list(value, base)
    if isinstance(base, dict):
        return value if isinstance(value, dict) else base
    return base


def merge(base, over):
    """深合并：over 只提供覆盖项，缺的键、错的类型全部回落 base。"""
    if isinstance(base, dict):
        out = {}
        src = over if isinstance(over, dict) else {}
        for key, bval in base.items():
            out[key] = merge(bval, src[key]) if key in src else copy.deepcopy(bval)
        return out
    return _coerce(base, over)


def _walk(data, prefix=""):
    if isinstance(data, dict):
        for key, val in data.items():
            yield from _walk(val, f"{prefix}.{key}" if prefix else key)
    else:
        yield prefix, data


def leaves(data=None):
    """遍历全部叶子路径（默认值的一份副本），属性面板据此生成字段行。"""
    return _walk(defaults() if data is None else data)


def clamp(data):
    """按 FIELDS 的范围钳住数值，防止手改 JSON 把界面刷到看不见。"""
    for key, val in list(_walk(data)):
        meta = FIELDS.get(key)
        if not meta:
            continue
        _label, kind, lo, hi, _dec, options = meta
        if kind == NUM and isinstance(val, (int, float)) and not isinstance(val, bool):
            bounded = min(hi, max(lo, float(val)))
            set_num(data, key, int(round(bounded)) if isinstance(val, int) else bounded)
        elif kind == CHOICE and options and val not in options:
            set_num(data, key, options[-1])
    return data


def load(force=False):
    """读布局（带缓存）。文件不存在或损坏都返回完整默认值，不落盘。"""
    if _CACHE["data"] is not None and not force:
        return _CACHE["data"]
    raw = {}
    p = path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("顶层应为对象")
        except Exception as e:
            backup_corrupt(p, e)
            raw = {}
    data = clamp(merge(DEFAULT, raw))
    _CACHE["data"] = data
    return data


def invalidate():
    _CACHE["data"] = None


def get(path_str, fallback=None):
    """按点路径取值（'bubble.main.cx'）。给一次性读取用，逐帧绘制请直接 load()。"""
    node = load()
    for part in path_str.split("."):
        if not isinstance(node, dict) or part not in node:
            return fallback
        node = node[part]
    return node


def set_num(data, path_str, value):
    """按点路径写值，路径不存在则忽略（避免把拼错的键写进文件）。"""
    parts = path_str.split(".")
    node = data
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    if not isinstance(node, dict) or parts[-1] not in node:
        return False
    node[parts[-1]] = value
    return True


def has(path_str):
    return get(path_str, None) is not None


def restore(data, prefixes=None, source=None):
    """把指定前缀的字段恢复成 source（缺省为出厂 DEFAULT）的值，就地修改 data。

    prefixes 为 None 时恢复全部；返回恢复的字段数。只改内存，落盘仍走 save()——
    编辑器退出不保存时还能整体回滚，不会出现“重置完发现文件已被删”。
    """
    src = DEFAULT if source is None else source
    count = 0
    for path, value in _walk(src):
        if prefixes is not None and not any(
                path == p or path.startswith(p + ".") for p in prefixes):
            continue
        if set_num(data, path, copy.deepcopy(value)):
            count += 1
    return count


def save(data):
    """原子落盘：先按 schema 收敛（丢未知键、坏值回落、越界钳制），
    再走临时文件 + os.replace，写坏也只坏临时文件。"""
    p = path()
    try:
        payload = clamp(merge(DEFAULT, data))
        payload["version"] = VERSION
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p) or ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception as e:
        return {"ok": False, "message": f"布局保存失败：{e}"}
    _CACHE["data"] = payload
    return {"ok": True, "message": f"已保存到 {os.path.basename(p)}"}


def reset():
    """删掉用户布局，回到出厂默认。"""
    p = path()
    try:
        if os.path.exists(p):
            os.remove(p)
    except Exception as e:
        return {"ok": False, "message": f"重置失败：{e}"}
    invalidate()
    return {"ok": True, "message": "已恢复默认布局"}


def defaults():
    return copy.deepcopy(DEFAULT)


def save_pending(payload):
    """待落地清单：编辑器改不动的部分（菜单结构、动作帧）写这里，由开发者按单落地。"""
    p = pending_path()
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"ok": False, "message": f"清单保存失败：{e}"}
    return {"ok": True, "message": f"已写入 {os.path.basename(p)}"}


def load_pending():
    p = pending_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
