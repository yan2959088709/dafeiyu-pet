# -*- coding: utf-8 -*-
"""聊天面板主题：背景（纯色/图片）、气泡配色与间距。

气泡与背景原来是散在主程序里的硬编码色值：白色气泡压在 #f5f7fc 背景上
几乎看不出来，气泡内边距也只有 9px/12px，文字紧贴气泡边缘。这里把它们收成
一组可由配置驱动的值——改视觉只需动本文件，运行时改只需改设置页两个字段。

背景支持纯色 + 本地图片两种；图片模式下气泡仍用高对比底色，保证任何背景图下
文字都读得清。
"""
import html
import os

from pet.storage import P

# 背景默认色：比旧 #f5f7fc 深一档，白色气泡才浮得起来
DEFAULT_BG_COLOR = "#e7ecf7"

# 宠物（左侧）气泡：白底 + 可见描边。旧版描边 #e6eaf2 在浅背景上等于没有
PET_BUBBLE_BG = "rgba(255,255,255,0.96)"
PET_BUBBLE_BORDER = "#ccd7ee"
PET_BUBBLE_FG = "#26303e"
# 主人（右侧）气泡：实色蓝，任何背景下都读得清
USER_BUBBLE_BG = "#4a7df0"
USER_BUBBLE_FG = "#ffffff"

BUBBLE_RADIUS = 16          # 圆角半径（旧 12）
BUBBLE_TAIL_RADIUS = 5      # 尖角那一侧的小圆角（旧 3）
BUBBLE_PAD_X = 15           # 气泡内文字左右边距（旧 12）
BUBBLE_PAD_Y = 11           # 气泡内文字上下边距（旧 9）
BUBBLE_GAP = 10             # 相邻气泡间距（旧 2）

AVATAR_W = 40               # 头像格宽（36px 图 + 4px 间距）
TAIL_W = 8                  # 尖角格宽
BUBBLE_MAX = 236            # 气泡最大宽度

DIVIDER_COLOR = "#9aa5bb"   # 时间分隔行

_TAIL_DIR = os.path.join(P.sprites, "dsh-pet", "chat")


def _file_url(path):
    return "file:///" + str(path).replace("\\", "/")


def _avatar_img(kind):
    """当前头像的 <img>；返回 None 时调用方回退文字占位。"""
    from pet import avatars
    path = avatars.effective(kind)
    if not path or not os.path.exists(path):
        return None
    return '<img src="%s" width="36" height="36">' % _file_url(path)


def bg_image(cfg):
    """背景图路径；不存在或未配置时返回 None（回退纯色）。"""
    path = str((cfg or {}).get("chatBgImage") or "").strip()
    if not path:
        return None
    path = os.path.expanduser(path)
    return path if os.path.exists(path) else None


def bg_color(cfg):
    """背景纯色；非法值回退默认色。"""
    color = str((cfg or {}).get("chatBgColor") or "").strip()
    if len(color) == 4 and color.startswith("#"):
        color = "#" + color[1] * 2 + color[2] * 2 + color[3] * 2
    return color if len(color) == 7 and color.startswith("#") else DEFAULT_BG_COLOR


def _body_bg(cfg):
    """QTextDocument 的 body 背景声明（Qt 富文本支持 background-color / image）。"""
    img = bg_image(cfg)
    if img:
        return "background-color:%s;background-image:url('%s');" % (
            bg_color(cfg), html.escape(img.replace("\\", "/"), quote=True))
    return "background-color:%s;" % bg_color(cfg)


def chat_css(cfg):
    """消息流的完整 CSS（每条气泡自带内联样式，这里只管 body 与表格）。"""
    return ("body{%sfont-family:'Microsoft YaHei';font-size:14px;"
            "line-height:1.5;margin:0;}\n"
            "table{margin-bottom:%dpx;}\n"
            "td{vertical-align:top;}\n"
            ".av{font-size:15px;font-weight:bold;color:#ffffff;text-align:center;}\n"
            ".div{color:%s;font-size:11px;text-align:center;padding:2px 0;}\n"
            % (_body_bg(cfg), BUBBLE_GAP, DIVIDER_COLOR))


def divider_html(label):
    """居中的时间分隔行（细线 + 灰字），间隔过大时由 append_msg 插入。"""
    return ('<table width="100%%" cellspacing="0" cellpadding="0" '
            'style="margin:6px 0 8px 0;"><tr>'
            '<td><div style="border-top:1px solid %s;"></div></td>'
            '<td width="76" class="div">%s</td>'
            '<td><div style="border-top:1px solid %s;"></div></td>'
            '</tr></table>' % (DIVIDER_COLOR, label, DIVIDER_COLOR))


def bubble_html(text, who, inner_w):
    """一条 QQ 风格气泡：who="pet" 左侧白气泡，"user" 右侧蓝气泡。

    inner_w 是消息流版心的可用宽度（面板宽 − 浏览器内边距），面板可缩放后
    它不再是常量。限宽用「气泡格 auto + 尾部 spacer 格固定」实现：短消息
    气泡随内容收窄，长消息在 spacer 处换行——Qt 富文本不支持 max-width，
    这是等效替代。

    text 必须是已转义、<br> 已就绪的气泡内容（表情/图片 <img> 由调用方拼入）。
    尖角是 PNG 资源：Qt 富文本既没有尖角字形（微软雅黑无 U+25C0），也不支持
    CSS 边框三角技巧，两者都实测渲染不出来。
    """
    spacer = max(8, int(inner_w) - AVATAR_W - TAIL_W - BUBBLE_MAX)
    if who == "user":
        user_av = _avatar_img("user") or (
            '<div class="av" style="background:#8fa3c8;border-radius:8px;'
            'height:36px;line-height:36px;">主</div>')
        return ('<table cellspacing="0" cellpadding="0" align="right"><tr>'
                '<td width="%d"></td>'
                '<td><div style="background:%s;'
                'border-radius:%dpx;border-top-right-radius:%dpx;'
                'padding:%dpx %dpx;color:%s;">%s</div></td>'
                '<td width="%d" style="padding-top:14px;">'
                '<img src="%s" width="8" height="11"></td>'
                '<td width="%d" style="padding-left:8px;">%s</td>'
                '</tr></table>'
                % (spacer, USER_BUBBLE_BG, BUBBLE_RADIUS, BUBBLE_TAIL_RADIUS,
                   BUBBLE_PAD_Y, BUBBLE_PAD_X, USER_BUBBLE_FG, text,
                   TAIL_W, _file_url(os.path.join(_TAIL_DIR, "tail_right.png")),
                   AVATAR_W, user_av))
    pet_av = _avatar_img("pet") or (
        '<img src="%s" width="36" height="36">'
        % _file_url(P.logo))
    return ('<table cellspacing="0" cellpadding="0"><tr>'
            '<td width="%d" style="padding-right:8px;">%s</td>'
            '<td width="%d" style="padding-top:14px;">'
            '<img src="%s" width="8" height="11"></td>'
            '<td><div style="background:%s;border:1px solid %s;'
            'border-radius:%dpx;border-top-left-radius:%dpx;'
            'padding:%dpx %dpx;color:%s;">%s</div></td>'
            '<td width="%d"></td>'
            '</tr></table>'
            % (AVATAR_W, pet_av, TAIL_W,
               _file_url(os.path.join(_TAIL_DIR, "tail_left.png")),
               PET_BUBBLE_BG, PET_BUBBLE_BORDER, BUBBLE_RADIUS,
               BUBBLE_TAIL_RADIUS, BUBBLE_PAD_Y, BUBBLE_PAD_X,
               PET_BUBBLE_FG, text, spacer))
