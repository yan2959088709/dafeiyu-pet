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

MAP = {}            # 标签 -> 文件名（ensure() 装载）

# 情绪 -> 推荐标签 分组映射（注入系统提示词，让 AI 有据可依）
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

# 表情使用规范提示词模板（注入 _build_system_prompt 末尾）
PROMPT_TEMPLATE = (
    "\n\n【表情使用规范】\n"
    "你可以在回复的【最后一行】追加一个表情标记来表达当下的情绪，客户端会自动把对应表情图贴到这条消息下方，"
    "不需要你在正文里解释或提及这个标记。\n"
    "写法示例（标点收尾后另起一行）：\n"
    "`.我这儿有办法啦 <sticker:开心>`\n"
    "`.别难过嘛，抱抱你 <sticker:抱抱>`\n"
    "参考「情绪→推荐标签」：\n{groups}\n"
    "使用要点：\n"
    "1. 这是你自带的能力，多数回复都值得在结尾配一个表情来贴合当下情绪（但别每句都刷），每轮最多 1 个；\n"
    "2. 标记必须放在整条回复的【最末尾】、另起一行，格式为 ` <sticker:标签>`，前面不要有其他正文文字；\n"
    "3. 只在确无合适情绪或对方明确不要时才省略；标签只能在上面的推荐列表里选取，不要发明不存在的标签。"
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
                desc = str(it.get("description", "")).strip()
                fn = str(it.get("fileName", "")).strip()
                if desc and fn and os.path.exists(os.path.join(P.stickers, fn)):
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
