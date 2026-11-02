# -*- coding: utf-8 -*-
"""余额视图：四种形态归一、额度配色、金额格式、第三方自定义多页气泡白名单。

第三方（`shape: custom`）传的是**树**，不是 HTML：只认 7 个标签、样式值禁用
外链/表达式/注释，节点数与深度都有上限。这条白名单是安全边界，不是风格
偏好 —— 漏一条就等于让外部输入注入到 UI 层。
"""
from pet import animation_pool, ui_layout

QUOTA_WARN_PERCENT = 70.0
QUOTA_CRIT_PERCENT = 90.0

SHAPES = ("windows", "money", "points", "custom")

ALLOWED_TAGS = frozenset(("div", "span", "b", "i", "em", "strong", "img"))
ALLOWED_STYLES = frozenset((
    "color", "background", "background-color", "font-size", "font-weight",
    "padding", "margin", "border-radius", "text-align", "display", "gap",
))
MAX_DEPTH = 6
MAX_NODES = 200
MAX_CHILDREN = 24
MAX_TEXT = 120
MAX_STYLE_VALUE = 64
MAX_SRC = 512
MAX_VIEWS = 4
BANNED_STYLE = ("url(", "expression", "javascript:", "vbscript:", "@import", "\\", "/*")


def tone(percent):
    """额度配色：70% 起警告，90% 起危险。"""
    p = float(percent or 0)
    if p >= QUOTA_CRIT_PERCENT:
        return "crit"
    if p >= QUOTA_WARN_PERCENT:
        return "warn"
    return "normal"


def fmt_money(value):
    return "%.2f" % float(value or 0)


def sum_money(values):
    """按分相加，避免 0.1+0.2 那类浮点尾巴。"""
    cents = sum(int(round(float(v or 0) * 100)) for v in values)
    return cents / 100.0


def currency_symbol(currency):
    t = ui_layout.load()["texts"]
    return {"CNY": t["currency_cny"], "USD": t["currency_usd"],
            "EUR": t["currency_eur"]}.get(str(currency or "").upper(), "")


def balance_index(percent):
    return animation_pool.balance_index(percent)


def _clean_style(style):
    if not isinstance(style, dict):
        return {}
    out = {}
    for key, val in style.items():
        k = str(key).strip().lower()
        if k not in ALLOWED_STYLES:
            continue
        v = str(val).strip()
        if not v or len(v) > MAX_STYLE_VALUE:
            continue
        low = v.lower()
        if any(bad in low for bad in BANNED_STYLE):
            continue
        out[k] = v
    return out


def _clean_node(node, depth, budget):
    """返回清洗后的节点；非法 tag / 超深 / 超量 → 整树作废（budget[1] 置 False）。"""
    if depth > MAX_DEPTH or budget[0] <= 0 or not isinstance(node, dict):
        budget[1] = False
        return None
    tag = str(node.get("tag") or "").lower()
    if tag not in ALLOWED_TAGS:
        budget[1] = False
        return None
    budget[0] -= 1
    out = {"tag": tag}
    text = node.get("text")
    if isinstance(text, str) and text:
        out["text"] = text[:MAX_TEXT]
    style = _clean_style(node.get("style"))
    if style:
        out["style"] = style
    if tag == "img":
        src = str(node.get("src") or "").strip()
        if not src or len(src) > MAX_SRC or src.lower().startswith(("javascript:", "vbscript:")):
            budget[1] = False
            return None
        out["src"] = src
        alt = node.get("alt")
        if isinstance(alt, str) and alt:
            out["alt"] = alt[:MAX_TEXT]
        return out
    children = node.get("children")
    if isinstance(children, list):
        kept = []
        for child in children[:MAX_CHILDREN]:
            cleaned = _clean_node(child, depth + 1, budget)
            if cleaned is not None:
                kept.append(cleaned)
            if budget[0] <= 0:
                break
        if kept:
            out["children"] = kept
    return out


def parse_custom(views):
    """custom 视图树白名单清洗；非法/超容量整树作废（返回 []）。"""
    if not isinstance(views, list) or not views:
        return []
    out = []
    for view in views[:MAX_VIEWS]:
        budget = [MAX_NODES, True]
        cleaned = _clean_node(view, 0, budget)
        if cleaned is None or not budget[1]:
            return []
        out.append(cleaned)
    return out


def render_pages(view):
    """把归一化后的余额视图拆成气泡页（每页 3 行 A/B/C）。

    页数口径与上游一致：windows 两页、money/points 一页、custom 按 views 数量。
    """
    if not isinstance(view, dict):
        return []
    t = ui_layout.load()["texts"]
    shape = view.get("shape")
    if shape == "money":
        rows = view.get("money") or []
        if not rows:
            return []
        others = " · ".join(r["text"] for r in rows[1:3])
        tail = others or (t["money_available"] if view.get("isAvailable", True)
                          else t["money_unavailable"])
        return [[("A", t["money_title"]), ("B", rows[0]["text"]), ("C", tail)]]
    if shape == "windows":
        rows = view.get("windows") or []
        if not rows:
            return []
        first = rows[0]
        rest = " · ".join("%s %d%%" % (r["label"], int(r["percent"])) for r in rows[1:4])
        detail = " · ".join("%s %s/%s" % (r["label"], fmt_money(r["used"]),
                                          fmt_money(r["limit"])) for r in rows[:3])
        return [
            [("A", t["windows_title"]),
             ("B", "%s %d%%" % (first["label"], int(first["percent"]))),
             ("C", rest or detail)],
            [("A", t["windows_detail_title"]), ("C", detail)],
        ]
    if shape == "custom":
        pages = []
        for tree in view.get("views") or []:
            texts = []
            _collect_text(tree, texts)
            if not texts:
                texts = [t["custom_empty"]]
            lines = [("A", texts[0])]
            if len(texts) > 1:
                lines.append(("B", texts[1]))
            if len(texts) > 2:
                lines.append(("C", " · ".join(texts[2:5])))
            pages.append(lines)
        return pages
    if shape == "points":
        return [[("A", t["points_title"]), ("B", "%s %s" % (fmt_money(view.get("total", 0)),
                                                          view.get("unit") or ""))]]
    return []


def _collect_text(node, out, depth=0):
    if depth > MAX_DEPTH or not isinstance(node, dict):
        return
    text = node.get("text")
    if isinstance(text, str) and text.strip():
        out.append(text.strip())
    alt = node.get("alt")
    if node.get("tag") == "img" and isinstance(alt, str) and alt.strip():
        out.append(alt.strip())
    for child in node.get("children") or []:
        _collect_text(child, out, depth + 1)


def normalize(result):
    """取数结果 → 渲染用形态；未知 shape 返回 None（不猜）。"""
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    shape = result.get("shape")
    if shape not in SHAPES:
        return None
    if shape == "custom":
        views = parse_custom(result.get("views"))
        if not views:
            return None
        return {"shape": "custom", "views": views,
                "percent": max(0.0, min(100.0, float(result.get("percent") or 0)))}
    if shape == "windows":
        rows = []
        for row in result.get("windows") or []:
            if not isinstance(row, dict):
                continue
            used = float(row.get("used") or 0)
            limit = float(row.get("limit") or 0)
            percent = float(row.get("percent") or 0)
            rows.append({"label": str(row.get("label") or ""),
                         "used": used, "limit": limit, "percent": percent,
                         "tone": tone(percent),
                         "resetAt": row.get("resetAt")})
        if not rows:
            return None
        return {"shape": "windows", "windows": rows,
                "rollingLabel": result.get("rollingLabel") or rows[0]["label"]}
    if shape == "money":
        rows = []
        for row in result.get("money") or []:
            cur = str(row.get("currency") or "CNY")
            total = float(row.get("total") or 0)
            rows.append({"currency": cur, "total": total,
                         "text": "%s%s" % (currency_symbol(cur), fmt_money(total))})
        if not rows:
            return None
        rows.sort(key=lambda r: 0 if r["currency"] == "CNY" else 1)
        return {"shape": "money", "money": rows,
                "isAvailable": bool(result.get("isAvailable", True))}
    total = float(result.get("total") or 0)
    return {"shape": "points", "total": total,
            "unit": str(result.get("unit") or ""),
            "text": "余额 %s %s" % (fmt_money(total) if isinstance(total, float) else total,
                                   result.get("unit") or "")}
