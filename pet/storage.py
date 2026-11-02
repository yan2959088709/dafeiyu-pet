# -*- coding: utf-8 -*-
"""运行期路径表与 JSON 落盘的唯一来源。

为什么集中到这里：这些路径原先是主程序的模块级常量。模块拆分后，子模块若
`from 主程序 import CONFIG_PATH`，拿到的是 import 瞬间的快照，沙箱/测试无法
重定向（会写坏真实配置）。统一走 P 对象后，调用方在使用时取值即可。

APP_DIR 与 BUNDLE_DIR 的区别：打包后前者是可执行文件所在目录（用户数据落这里），
后者是 PyInstaller 解包目录（只读素材落这里）。
"""
import json
import os
import shutil
import sys


class RuntimePaths:
    """当前进程的路径表；测试通过改属性把用户数据重定向到沙箱。"""

    def __init__(self, app_dir=None, bundle_dir=None):
        frozen = getattr(sys, "frozen", False)
        if app_dir is None:
            app_dir = os.path.dirname(sys.executable) if frozen else \
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if bundle_dir is None:
            bundle_dir = getattr(sys, "_MEIPASS", app_dir)
        self.app_dir = app_dir
        self.bundle_dir = bundle_dir
        self.pythonw = sys.executable if frozen else \
            os.path.join(app_dir, ".venv", "Scripts", "pythonw.exe")
        self.sprites = os.path.join(bundle_dir, "sprites")
        self.logo = os.path.join(self.sprites, "dsh-whale.png")
        self.stickers = os.path.join(app_dir, "stickers")
        self.sticker_zip = os.path.join(app_dir, "stickers_20260523_193056.zip")
        self.sticker_meta = os.path.join(self.stickers, "custom_stickers.json")
        # 用户数据：可被测试重定向
        self.config = os.path.join(app_dir, "config.json")
        self.config_jsonc = os.path.join(app_dir, "config.jsonc")
        self.pets_dir = os.path.join(app_dir, "pets")
        self.log = os.path.join(app_dir, "logs.txt")
        self.voice_cache = os.path.join(app_dir, "voice-cache")
        self.attachments = os.path.join(app_dir, "attachments")
        self.api_state = os.path.join(app_dir, "api-runtime.json")
        self.memory = os.path.join(app_dir, "pet_memory.json")
        self.ledger = os.path.join(app_dir, "usage_ledger.json")
        self.session = os.path.join(app_dir, "pet_sessions.json")
        self.ui_layout = os.path.join(app_dir, "ui_layout.json")
        self.ui_layout_pending = os.path.join(app_dir, "ui_layout_pending.json")
        self.action_binding = os.path.join(app_dir, "action_binding.json")


P = RuntimePaths()


def _backup_corrupt(path, err):
    """解析失败时把原文另存一份。

    必要性：调用方拿到 default 后，下一次落盘（切模式、拖窗口都会触发）
    就会把文件覆写。没有备份，一次临时解析失败就是用户的配置永久丢失。
    """
    bak = path + ".corrupt"
    try:
        shutil.copy2(path, bak)
        print(f"{os.path.basename(path)} 读取失败，原文已备份为 {os.path.basename(bak)}：{err}")
    except Exception:
        pass


def backup_corrupt(path, err):
    """公开别名：pet/config.py 需要同一套「先备份再回落」语义。"""
    _backup_corrupt(path, err)


def backup_once(path, suffix):
    """留一份副本，已存在则不覆盖（用于不可逆操作前的自保）。"""
    bak = path + suffix
    if os.path.exists(path) and not os.path.exists(bak):
        try:
            shutil.copy2(path, bak)
        except Exception:
            pass


def load_json(path, default):
    """读 JSON；文件不存在时先按 default 落盘再返回。

    解析失败或顶层类型与 default 不符时，先备份原文件再回落默认值。
    """
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=4)
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if default is not None and not isinstance(data, type(default)):
            raise ValueError(f"顶层类型应为 {type(default).__name__}，实际为 {type(data).__name__}")
        return data
    except Exception as e:
        _backup_corrupt(path, e)
        return default
