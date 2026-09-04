# -*- coding: utf-8 -*-
"""诊断⑩：同一段 loopback 原始捕获，用不同源速率假设转 16k，测哪个得到正确主频。"""
import io
import sys
import time

import numpy as np
import pyaudiowpatch as pyaudio

sys.path.insert(0, r"D:\data\dsh_subtitle")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def main():
    p = pyaudio.PyAudio()
    info = p.get_device_info_by_index(17)  # Realtek loopback
    rate = int(info["defaultSampleRate"])
    ch = info["maxInputChannels"] or 2
    raw = bytearray()
    def _cb(d, _f, _t, _s):
        raw.extend(d)
        return (None, pyaudio.paContinue)
    s = p.open(format=pyaudio.paInt16, channels=ch, rate=rate,
               input=True, input_device_index=17,
               frames_per_buffer=1600, stream_callback=_cb)
    s.start_stream()
    print("捕获 Realtek loopback 5s（440Hz 播放中）…")
    time.sleep(5.0)
    s.stop_stream(); s.close(); p.terminate()
    a = np.frombuffer(bytes(raw), dtype=np.int16)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    print(f"原始帧数 {a.shape[0]}，假设 48k 解释主频：")
    for src in (16000, 24000, 32000, 48000):
        n = max(1, int(len(a) * 16000 / src))
        b = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)),
                      a.astype(np.float32)).astype(np.int16)
        seg = b[: min(len(b), 16000)]
        w = np.hanning(len(seg))
        spec = np.abs(np.fft.rfft((seg * w).astype(np.float64)))
        fr = np.fft.rfftfreq(len(seg), 1.0 / 16000)
        dom = fr[np.argmax(spec)]
        print(f"  src_rate={src} → 16k 输出主频 {dom:.0f}Hz（目标 440）  输出时长 {len(b)/16000:.2f}s")


if __name__ == "__main__":
    main()
