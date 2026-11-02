# -*- coding: utf-8 -*-
"""Q弹点击音效：boing 合成（模块级纯函数）与点击播放（异步、可被下一次打断）。

以 Mixin 形式挂到 PetWindow——_play_boing 通过 self 访问 cfg 与 _sfx_cache，
官方 mp3 走 _play_mci（winmm mci 播放，_mci_close 收尾）；make_boing_wav 供
__init__ 预生成三档音高，主程序按需 `from pet.sound import make_boing_wav`。
winsound 只服务本域，导入与 WINSOUND_OK 判定一并放在这里。

边界：SPRITE_DIR 与主窗同名同值（取自 pet.storage 的 P，保持 _play_boing 原文
不动）；子模块禁止反向依赖主程序。
"""
import ctypes
import io
import math
import os
import random
import struct
import wave

try:
    import winsound          # Windows 音效播放（Q弹点击音）
    WINSOUND_OK = True
except Exception:
    WINSOUND_OK = False

from pet.storage import P

SPRITE_DIR = P.sprites


def make_boing_wav(f0=760.0, dur=0.22, rate=22050, vol=0.5):
    """合成"Q弹 boing"音效（WAV bytes）：频率下滑 + 轻微颤音 + 指数衰减"""
    n = int(dur * rate)
    frames = bytearray(n * 2)
    phase = 0.0
    for i in range(n):
        t = i / rate
        f = f0 * math.exp(-7.0 * t) + 170.0        # 音高下滑
        phase += 2.0 * math.pi * f / rate          # 相位积分（频率随时间变化）
        env = math.exp(-9.0 * t) * min(1.0, t / 0.004)  # 快起音 + 衰减
        wobble = 1.0 + 0.18 * math.sin(2.0 * math.pi * 26.0 * t)  # 果冻颤音
        s = env * wobble * math.sin(phase)
        v = int(max(-1.0, min(1.0, s)) * 32767 * vol)
        struct.pack_into("<h", frames, i * 2, v)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


class SoundMixin:
    """PetWindow 的点击音效播放能力。"""

    def _play_boing(self):
        """播放 Q弹音效（异步、可被下一次点击打断；失败静默）。

        sound_set=duck/fx1 时播放官方 mp3（mci 控制音量），dsh-duck 播上游
        click-duck.wav（固定 0.5 音量）；否则合成 boing 兜底。
        """
        if not self.cfg.get("sfx_enabled", True):
            return
        vol = float(self.cfg.get("sfx_vol", 0.9))
        ss = self.cfg.get("sound_set", "synth")
        if ss == "dsh-duck":
            # 上游原始点击音（click-duck.wav，固定 0.5 音量）；缺失则回落到合成音
            path = os.path.join(SPRITE_DIR, "dsh-pet", "sound", "click-duck.wav")
            if os.path.exists(path):
                self._play_mci(path, 0.5)
                return
        if ss in ("duck", "fx1"):
            fname = random.choice(["Ya1.mp3", "Ya2.mp3"]) if ss == "duck" \
                else random.choice(["D1.mp3", "D2.mp3"])
            path = os.path.join(SPRITE_DIR, "dsh", fname)
            if os.path.exists(path):
                self._play_mci(path, vol)
                return
        # 合成 boing 兜底（winsound 无音量控制，仍走内存播放）
        if not self._sfx_cache:
            return
        try:
            winsound.PlaySound(random.choice(self._sfx_cache),
                               winsound.SND_ASYNC | winsound.SND_MEMORY)
        except Exception:
            pass

    def _play_mci(self, path, vol):
        """用 winmm mci 异步播放音频，可打断上一条，并设置 0~1000 音量。"""
        try:
            self._mci_close()
            cmd = f'open "{path}" alias dsfx'
            if ctypes.windll.winmm.mciSendStringW(cmd, None, 0, 0) == 0:
                ctypes.windll.winmm.mciSendStringW(
                    f"setaudio dsfx volume to {int(max(0, min(1, vol)) * 1000)}",
                    None, 0, 0)
                ctypes.windll.winmm.mciSendStringW("play dsfx", None, 0, 0)
        except Exception:
            pass

    def _mci_close(self):
        try:
            ctypes.windll.winmm.mciSendStringW("close dsfx", None, 0, 0)
        except Exception:
            pass
