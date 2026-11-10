# -*- coding: utf-8 -*-
"""系统感知：电脑快照（CPU/内存/磁盘/电池/网络/GPU/开机时长）+ 前台窗口 + 闲置。

psutil 走跨平台（requirements 已有）；前台窗口标题与键鼠闲置走 Win32
ctypes（仅 Windows，其它平台返回 None）。所有函数容错：单项失败只缺该项，
绝不抛——感知层挂掉不该把聊天带崩。

线程：快照函数可在 worker 线程直调（psutil/ctypes 都不是 Qt 类）。
"""
import os
import time

try:
    import psutil
except ImportError:  # pragma: no cover - requirements 里是硬依赖
    psutil = None

_GPU_TRIED = False
_GPU_OK = False


def _gpu():
    """懒加载 pynvml：没显卡/没驱动就一次标记后不再尝试。"""
    global _GPU_TRIED, _GPU_OK
    if _GPU_TRIED:
        return pynvml if _GPU_OK else None
    _GPU_TRIED = True
    try:
        import pynvml
        pynvml.nvmlInit()
        _GPU_OK = True
        return pynvml
    except Exception:
        _GPU_OK = False
        return None


def gpu_temp():
    """第一块显卡温度（°C）；无 GPU/失败返回 None。"""
    mod = _gpu()
    if mod is None:
        return None
    try:
        handle = mod.nvmlDeviceGetHandleByIndex(0)
        return int(mod.nvmlDeviceGetTemperature(
            handle, mod.NVML_TEMPERATURE_GPU))
    except Exception:
        return None


def snapshot():
    """系统快照 dict；psutil 不可用返回空 dict。"""
    out = {}
    if psutil is None:
        return out
    try:
        out["cpu_percent"] = round(float(psutil.cpu_percent(interval=None)), 1)
    except Exception:
        pass
    try:
        vm = psutil.virtual_memory()
        out["mem_percent"] = round(float(vm.percent), 1)
        out["mem_used_gb"] = round(vm.used / 1024 ** 3, 1)
        out["mem_total_gb"] = round(vm.total / 1024 ** 3, 1)
    except Exception:
        pass
    try:
        du = psutil.disk_usage(os.path.abspath(os.sep))
        out["disk_percent"] = round(float(du.percent), 1)
        out["disk_free_gb"] = round(du.free / 1024 ** 3, 1)
    except Exception:
        pass
    try:
        bat = psutil.sensors_battery()
        if bat is not None:
            out["battery_percent"] = int(bat.percent)
            out["battery_plugged"] = bool(bat.power_plugged)
    except Exception:
        pass
    try:
        stats = list(psutil.net_if_stats().values())
        if stats:
            out["net_up"] = any(bool(s.isup) for s in stats)
    except Exception:
        pass
    try:
        out["uptime_min"] = int((time.time() - psutil.boot_time()) / 60)
    except Exception:
        pass
    temp = gpu_temp()
    if temp is not None:
        out["gpu_temp"] = temp
    return out


def describe(snap):
    """快照 → 一句人话（回喂模型用）。"""
    if not snap:
        return "读不到系统状态"
    parts = []
    if "cpu_percent" in snap:
        parts.append("CPU %.0f%%" % snap["cpu_percent"])
    if "mem_percent" in snap:
        parts.append("内存 %.0f%%（%.1f/%.1fGB）" % (
            snap["mem_percent"], snap.get("mem_used_gb", 0),
            snap.get("mem_total_gb", 0)))
    if "disk_percent" in snap:
        parts.append("系统盘 %.0f%%（余 %.1fGB）" % (
            snap["disk_percent"], snap.get("disk_free_gb", 0)))
    if "battery_percent" in snap:
        parts.append("电量 %d%%（%s）" % (
            snap["battery_percent"],
            "已插电" if snap.get("battery_plugged") else "未插电"))
    if "net_up" in snap:
        parts.append("网络%s" % ("通" if snap["net_up"] else "断"))
    if "gpu_temp" in snap:
        parts.append("GPU %d°C" % snap["gpu_temp"])
    if "uptime_min" in snap:
        hours = snap["uptime_min"] // 60
        parts.append("开机 %d 小时" % hours)
    return "；".join(parts)


def battery_low(threshold=20):
    """未插电且电量跌破阈值 → True（主动关心用）。无电池返回 False。"""
    if psutil is None:
        return False
    try:
        bat = psutil.sensors_battery()
        return bool(bat is not None and not bat.power_plugged
                    and bat.percent <= threshold)
    except Exception:
        return False


def foreground_window():
    """当前前台窗口 {title, process}；非 Windows / 失败返回 None。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = int(user32.GetWindowTextLengthW(hwnd))
        buf = ctypes.create_unicode_buffer(max(length, 0) + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = ""
        if psutil is not None and pid.value:
            try:
                proc = psutil.Process(int(pid.value)).name()
            except Exception:
                proc = ""
        return {"title": (buf.value or "")[:120], "process": proc[:60]}
    except Exception:
        return None


def idle_seconds():
    """主人多久没动键鼠（GetLastInputInfo）；非 Windows / 失败返回 None。"""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        tick = int(ctypes.windll.kernel32.GetTickCount())
        return max(0, (tick - int(info.dwTime)) // 1000)
    except Exception:
        return None
