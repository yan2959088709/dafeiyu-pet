# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（单文件 exe）。

为什么用 spec 而不是 README 里那条一行命令：pet/ 与 skins/ 是运行期按名字
动态导入的（皮肤注册表、Mixin），命令行模式不会自动收集；spec 里用
collect_submodules 一次性收全，免得上线才发现某个模块没打进去。

素材（sprites / stickers）走 datas；用户数据（config.json 等）不进包，
运行时落在 exe 同目录。
"""
import os
import re

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(".")
APP_NAME = "大肥鱼桌宠"

VERSION = "0.0.0"
_version_py = os.path.join(ROOT, "pet", "version.py")
if os.path.exists(_version_py):
    with open(_version_py, "r", encoding="utf-8") as fh:
        matched = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', fh.read())
    if matched:
        VERSION = matched.group(1)

datas = [
    (os.path.join(ROOT, "sprites"), "sprites"),
    (os.path.join(ROOT, "stickers"), "stickers"),
]
hiddenimports = collect_submodules("pet") + collect_submodules("skins")

a = Analysis(
    [os.path.join(ROOT, "桌宠.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PySide6.QtWebEngineCore"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "icon.ico"),
    version=VERSION,
)
