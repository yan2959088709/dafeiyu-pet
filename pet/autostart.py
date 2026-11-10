# -*- coding: utf-8 -*-
"""开机自启与崩溃自恢复：Startup 目录快捷方式 + 看门狗 supervisor。

开机自启原本只在右键菜单里（menus.set_autostart：PowerShell 建 .lnk）。
这里把 .lnk 构建提成可复用模块，并补上「崩溃后自己活过来」：

- 自启 = %APPDATA%\\...\\Startup\\大肥鱼桌宠.lnk，每用户、无管理员权限；
- 看门狗 = app_dir\\pet-watchdog.bat：start /wait 桌宠，进程消失后若存在
  quit.flag（主人经 API /quit 主动退出）就清 flag 退出，否则重拉；单轮
  最多重拉 MAX_RELAUNCH 次，防崩溃循环空转（重新登录重置计数）；
- quit.flag 由 petservice.quit_app 退出前写；看门狗每轮启动前也会清掉
  残留 flag（崩溃恰好发生在写 flag 之后的小概率）。

.lnk 指向谁：recover=True → 看门狗 bat（崩溃自恢复生效）；False → 直接
pythonw 桌宠.py。frozen 打包态无脚本参数，与 menus 原口径一致。
"""
import os
import subprocess

LNK_NAME = "大肥鱼桌宠.lnk"
WATCHDOG_NAME = "pet-watchdog.bat"
QUIT_FLAG_NAME = "quit.flag"
MAX_RELAUNCH = 5


def lnk_path():
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return ""
    return os.path.join(appdata, "Microsoft", "Windows",
                        "Start Menu", "Programs", "Startup", LNK_NAME)


def watchdog_path(app_dir):
    return os.path.join(app_dir, WATCHDOG_NAME)


def quit_flag_path(app_dir):
    return os.path.join(app_dir, QUIT_FLAG_NAME)


def write_quit_flag(app_dir):
    """主动退出标记：看门狗看到它就不重拉。失败不影响退出本身。"""
    try:
        with open(quit_flag_path(app_dir), "w", encoding="utf-8") as fh:
            fh.write("quit\n")
        return True
    except Exception:
        return False


def clear_quit_flag(app_dir=None):
    """撤掉退出标记（退出投递失败时用：别让它压制下次崩溃重拉）。"""
    try:
        if app_dir is None:
            from pet.storage import P
            app_dir = P.app_dir
        path = quit_flag_path(app_dir)
        if os.path.exists(path):
            os.remove(path)
        return True
    except Exception:
        return False


def enabled():
    """事实口径：.lnk 在不在（配置可能和事实脱节，以事实为准）。"""
    path = lnk_path()
    return bool(path) and os.path.exists(path)


def _target_args(app_dir):
    """直接启动桌宠的 (target, args)。"""
    import sys

    from pet.storage import P
    frozen = getattr(sys, "frozen", False)
    return P.pythonw, ("" if frozen else os.path.join(app_dir, "桌宠.py"))


def watchdog_script(app_dir):
    """看门狗 bat 内容（纯函数，便于测试）。

    单轮最多重拉 4 次（共启动 5 次）：启动即崩的崩溃循环空转 5 回就停，
    重新登录重置计数。
    """
    target, args = _target_args(app_dir)
    cmd = '"%s" "%s"' % (target, args) if args else '"%s"' % target
    return (
        "@echo off\n"
        "rem 桌宠看门狗：进程消失就重拉；主人主动退出（quit.flag）不重启。\n"
        "rem 单轮最多重拉 4 次（共启动 5 次），防崩溃循环空转；重新登录重置。\n"
        "cd /d \"%s\"\n"
        "set /a runs=0\n"
        ":watch\n"
        "if exist \"%s\" del \"%s\" >nul 2>&1\n"
        "start \"\" /wait %s\n"
        "if exist \"%s\" del \"%s\" >nul 2>&1 & exit /b 0\n"
        "set /a runs+=1\n"
        "if %%runs%% GEQ %d exit /b 0\n"
        "timeout /t 3 /nobreak >nul\n"
        "goto watch\n"
        % (app_dir, QUIT_FLAG_NAME, QUIT_FLAG_NAME, cmd,
           QUIT_FLAG_NAME, QUIT_FLAG_NAME, MAX_RELAUNCH)
    )


def write_watchdog(app_dir):
    """落盘看门狗脚本。失败返回 False（调用方据此不指 .lnk 过去）。

    编码用 mbcs（= 系统 ANSI 码页）：cmd 解析 bat 用的是 ANSI/OEM 码页，
    写 UTF-8 会让中文路径/「桌宠.py」变乱码，start 静默失败——看门狗等于
    瘫痪（zh-CN Windows 上 ANSI 与 OEM 都是 936，短名方案备选但没必要）。
    """
    try:
        with open(watchdog_path(app_dir), "w",
                  encoding="mbcs" if os.name == "nt" else "utf-8") as fh:
            fh.write(watchdog_script(app_dir))
        return True
    except Exception:
        return False


def flag_quit():
    """主动退出标记（便捷入口：自己取 app_dir）。"""
    from pet.storage import P
    return write_quit_flag(P.app_dir)


def set_enabled(on, *, recover=True, app_dir=None):
    """开/关开机自启。返回 (ok, err)。

    on=True 且 recover=True 时先写看门狗脚本，.lnk 指向它；写脚本失败
    则整体失败（.lnk 不建），避免自启指向一个不存在的看门狗。
    """
    path = lnk_path()
    if not path:
        return False, "找不到 APPDATA 环境变量"
    if app_dir is None:
        from pet.storage import P
        app_dir = P.app_dir
    try:
        if on:
            if recover:
                if not write_watchdog(app_dir):
                    return False, "看门狗脚本写入失败"
                target, args = watchdog_path(app_dir), ""
            else:
                target, args = _target_args(app_dir)
            # 值一律经子进程环境变量传递，脚本本体是固定字符串：路径里带
            # 单引号/分号（如 O'Brien 用户名、it's a pet 目录名）既不能注入
            # PowerShell，也不会把自启悄悄弄坏
            ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
                  "$env:DSH_PET_LNK);$s.TargetPath=$env:DSH_PET_TARGET;"
                  "$s.Arguments=$env:DSH_PET_ARGS;"
                  "$s.WorkingDirectory=$env:DSH_PET_WORKDIR;"
                  "$s.WindowStyle=7;$s.Save()")
            env = dict(os.environ)
            env["DSH_PET_LNK"] = path
            env["DSH_PET_TARGET"] = target
            env["DSH_PET_ARGS"] = args
            env["DSH_PET_WORKDIR"] = app_dir
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                           check=True, timeout=30)
        else:
            if os.path.exists(path):
                os.remove(path)
        return True, ""
    except Exception as ex:
        return False, str(ex)
