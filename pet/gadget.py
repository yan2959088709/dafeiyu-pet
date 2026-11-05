# -*- coding: utf-8 -*-
"""DSH 小鲸鱼吸附挂件：官方同款气泡几何、随机台词抽样、文字/GIF 绘制与贴壁偏移。

以 Mixin 形式挂到 PetWindow——方法体仍通过 self 访问主窗状态（cfg、余额、
win_w、bubble_font、_rua_movie 等）；气泡状态机 _bubble_tick 留在 pet/motion.py。

边界：吸附动画与贴壁动作（settle/ensure_visible/on_tick/on_click）在
skins/dsh_whale.py；本模块只管气泡内容与绘制。
"""
import os
import random
import threading
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QPainterPath, QPen,
                           QPixmap, QPolygonF)

from pet import font_ui, ui_layout
from pet.pricing import PEAK_LABEL, is_peak_hour

# 布局文件里字重存字符串（编辑器下拉选），绘制时映射成 Qt 枚举
_WEIGHTS = {
    "Thin": QFont.Weight.Thin,
    "Light": QFont.Weight.Light,
    "Normal": QFont.Weight.Normal,
    "Medium": QFont.Weight.Medium,
    "DemiBold": QFont.Weight.DemiBold,
    "Bold": QFont.Weight.Bold,
}


def _weight(name):
    return _WEIGHTS.get(str(name), QFont.Weight.DemiBold)


def _alpha_color(hex_str, alpha):
    """十六进制色 + 0~255 不透明度 → QColor；色值坏掉时用白色兜底。"""
    c = QColor(str(hex_str))
    if not c.isValid():
        c = QColor("#ffffff")
    c.setAlpha(int(max(0, min(255, alpha))))
    return c


def _fmt(template, **kw):
    """文案模板格式化；用户手改 JSON 写坏花括号时原样返回，不让气泡崩掉。"""
    try:
        return str(template).format(**kw)
    except Exception:
        return str(template)


class GadgetMixin:
    """PetWindow 的 DSH 挂件气泡能力。"""

    def _dsh_layout_w(self):
        """气泡/边距的基准宽：与「画布加宽」解耦。

        窗口为了装下宽幅特效动作会加宽，但气泡尺寸、字体、头顶留白都应该
        跟本体（判定主体）走，否则加宽会把气泡一起放大。
        """
        return float(getattr(self, "layout_w", 0) or self.width())

    def _dsh_canvas_off(self):
        """画布比基准宽多出来的那一半：把气泡重新居中用。"""
        return (float(self.width()) - self._dsh_layout_w()) / 2.0

    def _dsh_bubble_geom(self):
        """DSH 气泡几何：宽度=布局基准宽，高度按布局里的 viewBox 等比映射。"""
        b = ui_layout.load()["bubble"]
        bw = self._dsh_layout_w()
        return bw, bw * b["view_h"] / b["view_w"]

    def _dsh_top_h(self):
        """素材顶部偏移（所有皮肤统一）：让素材顶落在主椭圆底部之下，官方气泡自然
        罩住素材头顶、小气泡压在素材身上。比例与边距都取自 ui_layout，可实时调。

        基准宽用布局宽而非窗宽：加宽画布不该抬高宠物。"""
        w = ui_layout.load()["window"]
        return w["top_h_ratio"] * self._dsh_layout_w() - w["margin"]

    def _dsh_show_text(self, text, dur=2.8, prio=0):
        """官方气泡台词统一入口（say 调用）：显示任意文本，支持优先级保护。"""
        if prio < self._dsh_prio and self._dsh_bubble_until > time.time():
            return
        if (self._dsh_bubble_kind == "custom" and text == self._dsh_custom_text
                and self._dsh_bubble_until > time.time()):
            return  # 相同台词未过期不重复弹
        self._dsh_prio = prio
        self._dsh_image_path = None
        self._dsh_custom_text = text
        self._dsh_bubble_kind = "custom"
        self._dsh_bubble_switching = False
        self._dsh_pending_kind = None
        self._dsh_line_pick = None
        self._dsh_bubble_until = time.time() + dur
        self._dsh_bubble_alpha = min(1.0, max(self._dsh_bubble_alpha, 0.05))
        self.update()

    def _dsh_show_info_bubble(self):
        """弹余额+今日已用挂件气泡（可配置自动关闭秒数，0=不自动关闭）。可被后台线程调用。"""
        self._dsh_bubble_kind = "info"
        self._balance_page = 0
        self._dsh_image_path = None
        sec = float(self.cfg.get("bubble_close_sec", 5))
        self._dsh_bubble_until = time.time() + (sec if sec > 0 else 1e9)
        self._dsh_bubble_alpha = 0.0
        self._dsh_bubble_switching = False
        self._dsh_pending_kind = None
        self._dsh_line_pick = None
        self._dsh_custom_text = None
        self._dsh_prio = 0
        self.update()

    def _dsh_manual_refresh(self):
        """点击鲸鱼顺带手动刷新余额（原项目点击=打开气泡+刷新）。"""
        key = self.cfg.get("ds_api_key", "")
        if key and not self._balance_busy:
            self._balance_busy = True
            threading.Thread(target=self._refresh_balance, args=(key,),
                             daemon=True).start()

    def _dsh_pick_line(self):
        """随机台词按官方 buildRandomGroups 权重抽样（20/7/7/3/1/1 + gif 9，总数 48）。"""
        usage = self._get_today_usage()
        r = random.random() * 48.0
        if r < 20:
            peak = is_peak_hour()
            p_txt, p_col, i_txt, i_col = PEAK_LABEL.get(
                self.cfg.get("peak_mode", "default"), PEAK_LABEL["default"])
            return [("A", "当前时间段为:"),
                    ("P", p_txt if peak else i_txt, p_col if peak else i_col),
                    ("C", f"今日已用 ¥{usage:.2f}")]
        if r < 27:
            return [("B", random.choice(["好模型... ↓", "好女孩...↓"]))]
        if r < 34:
            return [("A", random.choice(["不知道用户有什么用…", "我也要挣钱吗", "我去吃饭啦",
                                         "压力一只蓝色大肥鱼", "DeepSleep", "用户彻底怒了"]))]
        if r < 37:
            return [("A", random.choice(["大烧货", "token 自由", "便宜货"]))]
        if r < 38:
            return [("A", "这个"), ("B", "凶"), ("A", "是什么意思呀...")]
        if r < 39:
            return [("B", "哦鲸鲸... ")]
        return [("GIF", None)]   # rua.gif 动图

    def _balance_pages(self):
        """余额气泡页：结构化结果优先（money 1 页 / windows 2 页 / custom N 页）。"""
        from pet import balance_view
        view = balance_view.normalize(getattr(self, "balance_result", None))
        pages = balance_view.render_pages(view) if view else []
        if pages:
            return pages
        t = ui_layout.load()["texts"]
        usage = self._get_today_usage()
        used = _fmt(t["usage_line"], usage=usage)
        bal = self._roll_value()
        if bal is None:
            return [[("A", t["balance_title"]), ("B", t["balance_loading"]), ("C", used)]]
        sym = {"CNY": t["currency_cny"], "USD": t["currency_usd"],
               "EUR": t["currency_eur"]}.get(
            self.balance_currency, f"{self.balance_currency} ")
        return [[("A", t["balance_title"]), ("B", f"{sym}{bal:.2f}"), ("C", used)]]

    def _balance_next_page(self):
        """余额多页翻页；只有一页时返回 False（不消费点击）。"""
        pages = self._balance_pages()
        if len(pages) <= 1:
            return False
        self._balance_page = (int(getattr(self, "_balance_page", 0)) + 1) % len(pages)
        return True

    def _maybe_cycle_balance_page(self, e):
        """点击气泡区域翻页（非挂件皮肤也生效）。"""
        if self._dsh_bubble_kind != "info" or self._dsh_bubble_until <= time.time():
            return False
        if e.position().y() > self._dsh_top_h():
            return False
        if not self._balance_next_page():
            return False
        self.update()
        return True

    def _dsh_show_meme(self, path, caption="", dur=10.0):
        """配图气泡：在气泡区域内绘制一张表情图 + 一行文案（上游 [图:名称] 的落地形态）。"""
        self._dsh_image_path = path
        self._dsh_custom_text = str(caption or "")
        self._dsh_bubble_kind = "meme"
        self._dsh_bubble_until = time.time() + dur
        self._dsh_bubble_alpha = min(1.0, max(self._dsh_bubble_alpha, 0.05))
        self._dsh_bubble_switching = False
        self._dsh_pending_kind = None
        self._dsh_line_pick = None
        self.update()

    def _dsh_bubble_content(self):
        """DSH 气泡当前内容行列表：(style, text[, color])。GIF 台词用 ("GIF", None)。"""
        if self._dsh_bubble_kind == "custom" and self._dsh_custom_text:
            return [("A", self._dsh_custom_text)]
        if self._dsh_bubble_kind == "meme":
            return []
        if self._dsh_bubble_kind == "line" and self._dsh_line_pick:
            return self._dsh_line_pick
        if self._dsh_bubble_kind == "info":
            pages = self._balance_pages()
            return pages[int(getattr(self, "_balance_page", 0)) % len(pages)]
        t = ui_layout.load()["texts"]
        usage = self._get_today_usage()
        used = _fmt(t["usage_line"], usage=usage)
        bal = self._roll_value()
        if bal is None:
            return [("A", t["balance_title"]), ("B", t["balance_loading"]), ("C", used)]
        sym = {"CNY": t["currency_cny"], "USD": t["currency_usd"],
               "EUR": t["currency_eur"]}.get(
            self.balance_currency, f"{self.balance_currency} ")
        return [("A", t["balance_title"]), ("B", f"{sym}{bal:.2f}"), ("C", used)]

    def _dsh_draw_bubble(self, p):
        """绘制官方 DSH 风格气泡：白色大椭圆 + 深蓝描边 + 右下小气泡装饰。

        几何、颜色、排版全部取自 ui_layout（viewBox 单位等比映射到窗口宽度），
        编辑器里拖一条边就能立刻看到效果；GIF 台词显示动图，其余画文字行。
        """
        b = ui_layout.load()["bubble"]
        lines = self._dsh_bubble_content()
        main = b["main"]
        tail = b["tail"]
        by = b["top_offset"]
        s = self._dsh_layout_w() / b["view_w"]        # viewBox 单位 → 像素（按布局宽，不跟画布）
        cx = main["cx"] * s                   # 主椭圆/文字 中心 x
        box = QRectF((main["cx"] - main["rx"]) * s,
                     (main["cy"] - main["ry"]) * s + by,
                     main["rx"] * 2.0 * s, main["ry"] * 2.0 * s)
        ty0 = (main["cy"] + main["ry"]) * s + by          # 主椭圆底
        decor = [d for d in b["decor"].values() if d["visible"]]
        pen = QPen(QColor(b["stroke"]["color"]),
                   max(b["stroke"]["min_width"], b["stroke"]["width"] * s))
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.save()
        p.translate(self._dsh_canvas_off(), 0)   # 画布加宽后把气泡重新居中
        p.setOpacity(self._dsh_bubble_alpha)
        # 填充层（先填充全部图形，无描边，避免描边交叉线）
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha_color(b["fill"]["color"], b["fill"]["alpha"]))
        p.drawEllipse(box)
        if tail["visible"]:
            p.drawPolygon(QPolygonF([
                QPointF((tail["cx"] - tail["half_w"]) * s, ty0 + tail["top_inset"] * s),
                QPointF((tail["cx"] + tail["half_w"]) * s, ty0 + tail["top_inset"] * s),
                QPointF(tail["cx"] * s, ty0 + tail["tip"] * s),
            ]))
        for d in decor:
            p.drawEllipse(QRectF((d["cx"] - d["rx"]) * s, (d["cy"] - d["ry"]) * s + by,
                                 d["rx"] * 2.0 * s, d["ry"] * 2.0 * s))
        # 描边层（主椭圆 + 两个小气泡装饰，纯轮廓）
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(pen)
        p.drawEllipse(box)
        for d in decor:
            p.drawEllipse(QRectF((d["cx"] - d["rx"]) * s, (d["cy"] - d["ry"]) * s + by,
                                 d["rx"] * 2.0 * s, d["ry"] * 2.0 * s))
        # 尾巴外侧描边（两条斜边 + 底部尖角，不画与椭圆相交的顶边）
        if tail["visible"]:
            tail_path = QPainterPath()
            tail_path.moveTo((tail["cx"] - tail["half_w"]) * s,
                             ty0 + tail["top_inset"] * s)
            tail_path.lineTo(tail["cx"] * s, ty0 + tail["tip"] * s)
            tail_path.lineTo((tail["cx"] + tail["half_w"]) * s,
                             ty0 + tail["top_inset"] * s)
            p.drawPath(tail_path)
        # 内容：配图 / GIF 动图 / 三行文字
        if self._dsh_bubble_kind == "meme" and getattr(self, "_dsh_image_path", None):
            self._dsh_draw_image(p, cx, by, s)
        elif len(lines) == 1 and lines[0][0] == "GIF":
            self._dsh_draw_gif(p, cx, b["gif"]["center_y"] * s + by, s)
        else:
            self._dsh_draw_text(p, lines, cx, by, s)
        p.restore()

    def _dsh_draw_image(self, p, cx, by, s):
        """把配图画进气泡主椭圆内（等比缩放，不破图）。"""
        path = getattr(self, "_dsh_image_path", None)
        pix = QPixmap(path) if path and os.path.exists(path) else QPixmap()
        if pix.isNull():
            return
        im = ui_layout.load()["bubble"]["image"]
        scaled = pix.scaled(int(im["max_w"] * s), int(im["max_h"] * s),
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        x = cx - scaled.width() / 2.0
        y = im["top"] * s + by
        p.drawPixmap(int(x), int(y), scaled)
        caption = str(getattr(self, "_dsh_custom_text", "") or "").strip()
        if caption:
            self._dsh_draw_text(p, [("C", caption)], cx, by + im["caption_top"] * s, s)

    def _dsh_draw_text(self, p, lines, cx, by, s):
        """官方四档字号文字：A / B / P / C（字号、颜色、字重都取自 ui_layout）。

        排版修复：长文本自动换行（逐字符断行，避免单行无限缩字成蚂蚁字）、
        全部行块在气泡主椭圆中部垂直居中（避免挤在顶部、下方大片空白）。
        """
        cfg_text = ui_layout.load()["bubble"]["text"]
        styles = cfg_text["styles"]
        fallback = styles["A"]
        fam = self.bubble_font.family()
        font_scale = font_ui.scale_for(self.cfg, getattr(self, "pet_size", 0) or 462)
        maxw = cfg_text["max_w"] * s            # 文字最大宽（主椭圆内）
        floor = cfg_text["shrink_floor"]
        min_px_cfg = int(cfg_text["min_px"])
        # 1) 展开行块：每项先按目标字号测宽。
        #    单行超宽时【优先缩字号保单行】（下限由 shrink_floor / min_px 决定），
        #    缩到下限仍放不下（如长台词）才逐字符自动断行。
        #    这样 B 档大数字（¥12345.67）只缩字不拆行，避免数字溢出/断裂。
        blocks = []
        for item in lines:
            st, txt = item[0], item[1]
            sty = styles.get(st, fallback)
            col = item[2] if len(item) > 2 else sty["color"]
            base_px = int(round(sty["size"] * s * font_scale))
            f = QFont(fam)
            f.setPixelSize(base_px)
            f.setWeight(_weight(sty["weight"]))
            fm = QFontMetrics(f)
            if fm.horizontalAdvance(txt) <= maxw:
                blocks.append((txt, f, fm, col))
                continue
            # 超宽：先缩字号保单行（下限由配置决定，且不小于配置的最小像素）
            f2 = QFont(f)
            min_px = max(min_px_cfg, int(round(base_px * floor)))
            while fm.horizontalAdvance(txt) > maxw and f2.pixelSize() > min_px:
                f2.setPixelSize(f2.pixelSize() - 1)
                fm = QFontMetrics(f2)
            if fm.horizontalAdvance(txt) <= maxw:
                blocks.append((txt, f2, fm, col))
                continue
            # 缩到下限仍超（长文本）：按当前字号逐字符断行
            cur = ""
            for ch in txt:
                if cur and fm.horizontalAdvance(cur + ch) > maxw:
                    blocks.append((cur, f2, fm, col))
                    cur = ch
                else:
                    cur += ch
            if cur:
                blocks.append((cur, f2, fm, col))
        if not blocks:
            return
        # 2) 垂直居中：文字区中心 yc（主椭圆中部，官方三行中心约 255u）
        yc = cfg_text["center_y"] * s + by
        line_h = [fm.height() for _, _, fm, _ in blocks]
        total_h = sum(line_h)
        # 3) 总高超出可用区时整体按比例缩小字号，下限由配置决定
        avail = cfg_text["avail_h"] * s
        if total_h > avail:
            k = avail / total_h
            nb = []
            for (txt, f, fm, col) in blocks:
                f2 = QFont(f)
                f2.setPixelSize(max(int(cfg_text["global_min_px"]),
                                    int(round(f.pixelSize() * k))))
                nb.append((txt, f2, QFontMetrics(f2), col))
            blocks = nb
            line_h = [fm.height() for _, _, fm, _ in blocks]
            total_h = sum(line_h)
        y = yc - total_h / 2.0
        for (txt, f, fm, col), h in zip(blocks, line_h):
            p.setPen(QColor(col))
            p.setFont(f)
            p.drawText(QRectF(cx - maxw / 2.0, y, maxw, h),
                       Qt.AlignmentFlag.AlignCenter, txt)
            y += h

    def _dsh_draw_gif(self, p, cx, cy, s):
        """绘制 rua.gif 动图（contain 缩放于 max-w 560u x 400u 区域），失败降级文字。"""
        g = ui_layout.load()["bubble"]["gif"]
        m = self._rua_movie
        if m is not None and self._rua_movie_ok:
            fr = m.currentPixmap()
            if not fr.isNull():
                mw, mh = g["max_w"] * s, g["max_h"] * s
                if fr.width() and fr.height():
                    sc = min(mw / fr.width(), mh / fr.height())
                else:
                    sc = 1.0
                w, h = fr.width() * sc, fr.height() * sc
                p.drawPixmap(QRectF(cx - w / 2.0, cy - h / 2.0, w, h),
                             fr, QRectF(fr.rect()))
                return
        # 降级文字
        p.setPen(QColor(ui_layout.load()["bubble"]["text"]["styles"]["A"]["color"]))
        f = QFont(self.bubble_font.family())
        f.setPixelSize(int(round(g["fallback_px"] * s)))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        half_w = g["max_w"] * s / 2.0
        p.drawText(QRectF(cx - half_w, cy - 50.0 * s, half_w * 2.0, 100.0 * s),
                   Qt.AlignmentFlag.AlignCenter, g["fallback_text"])
