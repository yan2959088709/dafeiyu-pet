# -*- coding: utf-8 -*-
"""构建入口：按 桌宠.spec 生成 dist/大肥鱼桌宠.exe，可选打安装包与便携 zip。

等价上游的 prepack 闸门里「build」这一步：先做语法与关键文件自检，再调
PyInstaller；任何一步失败都以非零码退出，避免把半成品当产物发出去。

用法（项目根目录）：
    .venv\\Scripts\\python.exe tools\\build_exe.py
    .venv\\Scripts\\python.exe tools\\build_exe.py --check        # 只自检不打包
    .venv\\Scripts\\python.exe tools\\build_exe.py --zip          # 额外出便携 zip
    .venv\\Scripts\\python.exe tools\\build_exe.py --installer    # 额外调 Inno Setup
"""
import glob
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "桌宠.spec")
ISS = os.path.join(ROOT, "tools", "installer.iss")
DIST = os.path.join(ROOT, "dist")
EXE_NAME = "大肥鱼桌宠.exe"
REQUIRED = ("桌宠.py", "pet", "skins", "sprites", "stickers", "icon.ico",
            "tools/installer.iss")


def _version():
    path = os.path.join(ROOT, "pet", "version.py")
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("APP_VERSION"):
                    return line.split("=", 1)[1].strip().strip("\"'")
    except Exception:
        pass
    return "0.0.0"


def _syntax_ok(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            compile(f.read(), path, "exec")
        return True
    except SyntaxError:
        return False


def self_check():
    problems = []
    for name in REQUIRED:
        if not os.path.exists(os.path.join(ROOT, name)):
            problems.append("缺少 %s" % name)
    for pkg in ("pet", "skins", "tools"):
        base = os.path.join(ROOT, pkg)
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                if name.endswith(".py"):
                    path = os.path.join(dirpath, name)
                    if not _syntax_ok(path):
                        problems.append("语法错误：%s" % os.path.relpath(path, ROOT))
    if not _syntax_ok(os.path.join(ROOT, "桌宠.py")):
        problems.append("语法错误：桌宠.py")
    try:
        with open(ISS, "r", encoding="utf-8") as f:
            text = f.read()
        if "[Setup]" not in text or "AppId=" not in text:
            problems.append("installer.iss 缺少 [Setup]/AppId")
    except Exception as exc:
        problems.append("installer.iss 读取失败：%s" % exc)
    return problems


def make_zip():
    version = _version()
    target = os.path.join(DIST, "大肥鱼桌宠-%s-win64.zip" % version)
    exe = os.path.join(DIST, EXE_NAME)
    if not os.path.exists(exe):
        print("缺少 %s，先完成 PyInstaller 构建" % exe)
        return 1
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(exe, EXE_NAME)
        for name in ("LICENSE", "README.md"):
            path = os.path.join(ROOT, name)
            if os.path.exists(path):
                z.write(path, name)
    print("[便携包] %s（%d KB）" % (target, os.path.getsize(target) // 1024))
    return 0


def find_iscc():
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return found
    patterns = [
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Inno Setup 6", "ISCC.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Inno Setup 6", "ISCC.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
    ]
    for path in patterns:
        if path and os.path.exists(path):
            return path
    for path in glob.glob(os.path.join(os.environ.get("ProgramFiles(x86)", ""),
                                       "Inno Setup *", "ISCC.exe")):
        return path
    return None


def make_installer():
    iscc = find_iscc()
    if not iscc:
        print("未找到 Inno Setup 6（ISCC.exe）：exe 已构建，安装包跳过。")
        print("安装 Inno Setup 6 后重跑 --installer，或直接分发 --zip 便携包。")
        return 2
    cmd = [iscc, "/DMyAppVersion=%s" % _version(), ISS]
    print(" ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


def main(argv):
    problems = self_check()
    if problems:
        for line in problems:
            print("[自检失败] " + line)
        return 1
    print("[自检通过] 关键文件与语法均正常")
    want_zip = "--zip" in argv
    want_installer = "--installer" in argv
    if "--check" in argv:
        return 0
    try:
        import PyInstaller  # noqa: F401
    except Exception:
        print("未安装 PyInstaller：pip install pyinstaller")
        return 1
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", SPEC]
    print(" ".join(cmd))
    code = subprocess.call(cmd, cwd=ROOT)
    if code:
        return code
    if want_zip and make_zip():
        return 1
    if want_installer:
        code = make_installer()
        if code:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
