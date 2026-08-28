# -*- coding: utf-8 -*-
"""皮肤注册表：主程序通过 load_skin / SKIN_ORDER 路由皮肤，不感知内部实现。

新增皮肤（阶段2 的 deepseek-girl）：在 skins/ 下新建独立模块并在此注册即可。
"""

from .whale_girl import WhaleGirlSkin
from .fish import FishSkin
from .dsh_whale import DshWhaleSkin
from .deepseek_girl import DeepSeekGirlSkin

# 皮肤注册表：注册键（= config character 值）→ 皮肤类
SKIN_REGISTRY = {
    "whale-girl": WhaleGirlSkin,
    "fish": FishSkin,
    "dsh-whale": DshWhaleSkin,
    "deepseek-girl": DeepSeekGirlSkin,
}

# 切换循环顺序（_toggle_character 四态循环）
SKIN_ORDER = ["whale-girl", "fish", "dsh-whale", "deepseek-girl"]

# 皮肤显示名映射（右键菜单"切换形象"文案动态生成）
SKIN_DISPLAY = {name: cls.display_name for name, cls in SKIN_REGISTRY.items()}


def load_skin(name):
    """按注册键实例化皮肤；未知键回退默认 whale-girl。"""
    cls = SKIN_REGISTRY.get(name, WhaleGirlSkin)
    return cls()
