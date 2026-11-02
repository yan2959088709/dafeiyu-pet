# -*- coding: utf-8 -*-
"""崩溃兜底：日志 + 托盘提示 + 退避重启。

上游把渲染放在 Electron Helper 子进程里，崩了由宿主拉起来；本项目管理方式不同
（单进程），等价做法是「进程没了就重新拉起，但别在坏环境里无限闪退」：10 分钟
窗口内最多重启 3 次，超出就只记日志不再重启。

重启必须由子进程等父进程退出后再启动：父进程还活着时单实例互斥锁仍在，
立刻 spawn 会被守卫挡回。因此子进程带 --relaunch-parent <pid>，主程序启动前
先等这个 pid 消失。
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback

from pet import log as pet_log

WINDOW_MS = 10 * 60 * 1000
MAX_RESTARTS = 3
PARENT_WAIT_MS = 15000
RELUNCH_FLAG = "--relaunch-parent"
STATE_DIR = os.path.join(tempfile.gettempdir(), "dafeiyu-pet")


def state_path():
    return os.path.join(STATE_DIR, "crash-state.json")


class RestartBudget:
    """重启次数记账：只留窗口内的记录，跨进程落盘到临时目录。"""

    def __init__(self, path=None, clock=None):
        self.path = path or state_path()
        self._clock = clock or (lambda: int(time.monotonic() * 1000))
        self._stamps = self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            stamps = data.get("stamps")
            if isinstance(stamps, list):
                return [int(s) for s in stamps if isinstance(s, (int, float))]
        except Exception:
            pass
        return []

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"stamps": self._stamps[-MAX_RESTARTS:]}, f)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def allow(self):
        now = self._clock()
        self._stamps = [s for s in self._stamps if now - s < WINDOW_MS]
        return len(self._stamps) < MAX_RESTARTS

    def consume(self):
        self._stamps.append(self._clock())
        self._save()

    def count(self):
        return len(self._stamps)


def relaunch_argv(main_path, pid):
    if getattr(sys, "frozen", False):
        return [sys.executable, RELUNCH_FLAG, str(int(pid))]
    return [sys.executable, main_path, RELUNCH_FLAG, str(int(pid))]


def spawn_relaunch(main_path, pid):
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(main_path))
    subprocess.Popen(relaunch_argv(main_path, pid), close_fds=True,
                     creationflags=flags, cwd=base or None)
    return True


def wait_parent(pid, timeout_ms=PARENT_WAIT_MS):
    """等父进程退出；返回 True 表示可以继续启动。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if pid <= 0:
        return True
    if os.name != "nt":
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
            time.sleep(0.1)
        return False
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = k32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
        if not handle:
            return True
        try:
            return k32.WaitForSingleObject(handle, int(timeout_ms)) == 0
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return True


def relaunch_pid(argv):
    """从命令行里取 --relaunch-parent 的 pid；没有就返回 None。"""
    argv = list(argv)
    if RELUNCH_FLAG not in argv:
        return None
    index = argv.index(RELUNCH_FLAG)
    if index + 1 >= len(argv):
        return None
    try:
        return int(argv[index + 1])
    except ValueError:
        return None


class CrashGuard:
    def __init__(self, main_path, budget=None, on_fatal=None, spawner=None):
        self.main_path = main_path
        self.budget = budget or RestartBudget()
        self.on_fatal = on_fatal
        self.spawner = spawner or spawn_relaunch
        self._installed = False

    def install(self):
        sys.excepthook = self.handle
        if hasattr(threading, "excepthook"):
            threading.excepthook = self._thread_hook
        self._installed = True

    def _thread_hook(self, args):
        self.handle(args.exc_type, args.exc_value, args.exc_traceback)

    def handle(self, exc_type, exc, tb):
        if exc_type is None or issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            return
        pet_log.error("未捕获异常：\n" + "".join(
            traceback.format_exception(exc_type, exc, tb)))
        if self.on_fatal:
            try:
                self.on_fatal("桌宠遇到问题", (str(exc) or exc_type.__name__)[:80])
            except Exception:
                pass
        if not self.budget.allow():
            pet_log.error("10 分钟内已重启 %d 次，本次不再重启" % MAX_RESTARTS)
            return
        self.budget.consume()
        try:
            self.spawner(self.main_path, os.getpid())
            pet_log.warn("已安排重启（10 分钟窗口内第 %d 次）" % self.budget.count())
        except Exception as spawn_exc:
            pet_log.error("重启失败：%s" % spawn_exc)
