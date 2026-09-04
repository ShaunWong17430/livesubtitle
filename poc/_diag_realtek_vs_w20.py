# -*- coding: utf-8 -*-
"""诊断⑨：对照 Realtek loopback [17] 是否也有 2x 变调；尝试以 24000Hz 打开 W20 loopback。"""
import io
import sys
import time

import numpy as np
import pyaudiowpatch as pyaudio

sys.path.insert(0, r"D:\data\dsh_subtitle")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def capture(idx, rate, dur=4.0):
    info = p.get_device_info_by_index(idx)
    ch = info["maxInputChannels"] or 2
    raw = bytearray()
    def _cb(d, _f, _t, _s):
        raw.extend(d)
        return (None, pyaudio.paContinue)
    try:
        s = p.open(format=pyaudio.paInt16, channels=ch, rate=rate,
                   input=True, input_device_index=idx,
                   frames_per_buffer=1600, stream_callback=_cb)
    except Exception as e:
        print(f"  [{idx}] rate={rate}: 打开失败 {e}")
        return None, None
    s.start_stream()
    time.sleep(dur)
    s.stop_stream(); s.close()
    return bytes(raw), ch


def dom_freq(raw, ch, rate):
    a = np.frombuffer(raw, dtype=np.int16)
    if not len(a):
        return None, 0, 0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    seg = a[: min(len(a), rate)]
    w = np.hanning(len(seg))
    spec = np.abs(np.fft.rfft((seg * w).astype(np.float64)))
    fr = np.fft.rfftfreq(len(seg), 1.0 / rate)
    return fr[np.argmax(spec)], len(a) / (ch or 1), float(np.sqrt(np.mean(a.astype(np.float32) ** 2)))


p = pyaudio.PyAudio()

print("=== Realtek loopback [17]（播放到 [13] Realtek 扬声器）===")
raw, ch = capture(17, 48000)
if raw:
    f, n, r = dom_freq(raw, ch, 48000)
    print(f"  48k 解释: 主频={f:.0f}Hz 帧={n:.0f} RMS={r:.0f}")

print("=== W20 loopback [16] 以 24000Hz 打开 ===")
raw, ch = capture(16, 24000)
if raw:
    f, n, r = dom_freq(raw, ch, 24000)
    print(f"  24k 打开: 主频={f:.0f}Hz 帧={n:.0f} RMS={r:.0f}")

p.terminate()
