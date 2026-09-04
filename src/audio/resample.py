# -*- coding: utf-8 -*-
"""重采样（Stage 1 · src/audio/resample.py）。

目标统一 16k 单声道 PCM16（百炼 realtime 合法采样率）。
numpy 线性插值即可（需求书定稿：够用）。
"""
import numpy as np

TARGET_RATE = 16000


def to_16k_mono_pcm16(raw: bytes, rate: int, channels: int) -> bytes:
    """任意 int16 PCM → 16k 单声道 int16 PCM bytes。

    - channels>1：先取平均混单声道。
    - rate!=16000：numpy 线性插值重采样。
    """
    if not raw:
        return b""
    a = np.frombuffer(raw, dtype=np.int16)
    if channels > 1:
        a = a.reshape(-1, channels).mean(axis=1).astype(np.int16)
    if rate != TARGET_RATE:
        n = max(1, int(len(a) * TARGET_RATE / rate))
        a = np.interp(
            np.linspace(0, len(a) - 1, n),
            np.arange(len(a)),
            a.astype(np.float32),
        ).astype(np.int16)
    return a.tobytes()


def samples_of(data: bytes) -> int:
    return len(data) // 2
