# -*- coding: utf-8 -*-
"""诊断⑧：精确测 W20 loopback [16] 实际产帧率 + 用不同假设采样率还原 440Hz 主频。"""
import io
import sys
import time
import wave

import numpy as np
import pyaudiowpatch as pyaudio

sys.path.insert(0, r"D:\data\dsh_subtitle")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def main():
    p = pyaudio.PyAudio()
    info = p.get_device_info_by_index(16)
    raw = bytearray()
    t0 = time.time()
    def _cb(d, _f, _t, _s):
        raw.extend(d)
        return (None, pyaudio.paContinue)
    s = p.open(format=pyaudio.paInt16, channels=2, rate=48000,
               input=True, input_device_index=16,
               frames_per_buffer=1600, stream_callback=_cb)
    s.start_stream()
    time.sleep(5.0)
    s.stop_stream(); s.close(); p.terminate()
    wall = time.time() - t0
    a = np.frombuffer(bytes(raw), dtype=np.int16)
    frames = a.shape[0] // 2
    print(f"墙钟 {wall:.2f}s，捕获 int16 样本 {a.shape[0]}，帧 {frames}")
    print(f"→ 实际产帧率 ≈ {frames/wall:.0f} Hz（若≈24000 则 loopback 原生 24k 被误标 48k）")
    # 保存原始，便于多假设速率分析
    with wave.open(r"D:\data\dsh_subtitle\poc\_raw48.wav", "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(bytes(raw))
    m = a.reshape(-1, 2).mean(axis=1)
    print("\n不同假设采样率下的主频（期望 440Hz 的即正确速率）：")
    for sr in (48000, 24000, 16000):
        seg = m[: min(len(m), sr)]
        w = np.hanning(len(seg))
        spec = np.abs(np.fft.rfft((seg * w).astype(np.float64)))
        fr = np.fft.rfftfreq(len(seg), 1.0 / sr)
        print(f"  假设 {sr}Hz → 主频 {fr[np.argmax(spec)]:.1f} Hz")


if __name__ == "__main__":
    main()
