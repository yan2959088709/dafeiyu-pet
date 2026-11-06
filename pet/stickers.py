# -*- coding: utf-8 -*-
"""表情包域：标签→文件映射、首次解压、标记解析与提示词素材。

注意 MAP 是启动时装载的进程级状态，因此 ensure() 刻意做"原地更新"
（clear + update）而不是重新赋值——调用方持有模块引用，重新赋值会让
它们看不到新内容（这也是原来 `global _STICKER_MAP` 的隐患所在）。
"""
import json
import os
import re
import zipfile

from .storage import P

THUMB_W = 110       # 聊天气泡内表情缩略图宽度(px)

MAP = {}            # 标签 -> 文件名（ensure() 装载）。仅启动期装配；后台线程只读，
                    # 将来若要支持运行期重载，必须先解决 clear()+update() 的竞态

# 情绪 -> 推荐标签 分组映射（Harness search_stickers 按组检索的素材）
GROUPS = (
    ("开心", ("开心", "嘻嘻", "嘿嘿嘿", "哈哈哈", "豆包大笑", "傻笑", "眯眼笑", "微笑", "快哉快哉", "很好", "赞")),
    ("委屈", ("委屈巴巴", "呜呜", "呜哇", "快哭了", "哭", "大哭", "垂头丧气", "生无可恋", "低头不语", "自闭", "忧郁", "可怜")),
    ("生气", ("生气", "气", "气鼓鼓", "我生气了", "准备生气", "哼", "我要闹啦", "咬你", "打手手", "掐脖子", "敲你脑袋", "指你")),
    ("惊讶", ("惊", "惊恐", "被吓", "？", "！", "我的天", "这不对吧", "怎么会这样", "我倒要看看", "瞪大眼睛")),
    ("撒娇亲昵", ("mua", "贴", "抱着", "抱抱", "爱你", "双眼冒爱心", "害羞", "乖巧", "摇尾巴", "探头", "歪头看你", "对手指")),
    ("思考疑问", ("思考", "仔细看", "看", "不知道啊", "啊原来这样呀", "hey?", "在干嘛", "怎么个事")),
    ("犯困疲惫", ("打哈欠", "趴桌边", "趴床上", "趴地上", "辛苦了", "长舒一口气")),
    ("坏笑耍宝", ("坏笑", "桀桀桀", "略", "噗", "阿巴阿巴", "不语", "看地板", "你个大笨蛋", "你能拿我怎样")),
)


def path_for(filename):
    """表情文件在磁盘上的绝对路径。"""
    return os.path.join(P.stickers, filename)


def ensure():
    """首次启动把表情 zip 解压到 stickers/，并读取 custom_stickers.json
    建立 标签->文件名 映射。任一步骤失败都静默降级（不阻塞启动）。
    主用 json 映射；读取失败回退按 png 文件名（去后缀）作为标签。"""
    try:
        if not os.path.isdir(P.stickers) or not os.listdir(P.stickers):
            if os.path.exists(P.sticker_zip):
                with zipfile.ZipFile(P.sticker_zip) as zf:
                    zf.extractall(P.stickers)
    except Exception:
        pass
    mapping = {}
    try:
        with open(P.sticker_meta, "r", encoding="utf-8") as f:
            items = json.load(f)
        if isinstance(items, list):
            for it in items:
                desc = str(it.get("description", "")).strip()[:80]
                fn = str(it.get("fileName", "")).strip()
                # fileName 只允许单段文件名（防路径穿越）；描述截断进上下文
                if desc and fn and fn == os.path.basename(fn) \
                        and os.path.exists(os.path.join(P.stickers, fn)):
                    mapping[desc] = fn
    except Exception:
        pass
    if not mapping:
        try:
            for fn in os.listdir(P.stickers):
                if fn.lower().endswith(".png"):
                    mapping[os.path.splitext(fn)[0]] = fn
        except Exception:
            pass
    MAP.clear()
    MAP.update(mapping)


def split(text):
    """把文本中的 <sticker:标签> 标记拆出，返回 (纯文字, 标签或None)。

    取第一个命中本地映射的标签作为表情；未命中映射的标记一律剔除。"""
    raw = str(text)
    tag = None
    for cand in re.findall(r"<sticker:([^>]+)>", raw):
        if cand.strip() in MAP:
            tag = cand.strip()
            break
    clean = re.sub(r"<sticker:[^>]*>", "", raw).strip()
    return clean, tag


def strip_marks(text):
    """移除文本中的所有 <sticker:标签> 标记，返回纯文字（用于用户侧显示与气泡/发送）。"""
    return re.sub(r"<sticker:[^>]*>", "", str(text)).strip()
