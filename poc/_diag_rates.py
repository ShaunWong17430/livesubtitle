# -*- coding: utf-8 -*-
"""诊断⑦：以不同采样率打开 W20 loopback [16]，播放 440Hz 纯音，找哪个速率得到 440Hz。"""
import io
import sys
import time

import numpy as np
import pyaudiowpatch as pyaudio

sys.path.insert(0, r"D:\data\dsh_subtitle")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def try_rate(p, rate, dur=4.0):
    info = p.get_device_info_by_index(16)
    ch = info["maxInputChannels"] or 2
    raw = bytearray()
    def _cb(d, _f, _t, _s):
        raw.extend(d)
        return (None, pyaudio.paContinue)
    try:
        s = p.open(format=pyaudio.paInt16, channels=ch, rate=rate,
                   input=True, input_device_index=16,
                   frames_per_buffer=1600, stream_callback=_cb)
    except Exception as e:
        print(f"  rate={rate}: 打开失败 {e}")
        return
    s.start_stream()
    time.sleep(dur)
    s.stop_stream(); s.close()
    if not raw:
        print(f"  rate={rate}: 无帧")
        return
    a = np.frombuffer(bytes(raw), dtype=np.int16)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    # 用打开速率做频谱
    seg = a[: min(len(a), rate)]
    w = np.hanning(len(seg))
    spec = np.abs(np.fft.rfft((seg * w).astype(np.float64)))
    fr = np.fft.rfftfreq(len(seg), 1.0 / rate)
    dom = fr[np.argmax(spec)]
    print(f"  rate={rate}: 帧数={len(a)/ (ch or 1):.0f} 主频={dom:.1f}Hz RMS={np.sqrt(np.mean(a.astype(np.float32)**2)):.0f}")


def main():
    p = pyaudio.PyAudio()
    info = p.get_device_info_by_index(16)
    print(f"设备[16] defaultSampleRate={info['defaultSampleRate']}")
    for rate in (48000, 96000, 44100, 32000):
        try_rate(p, rate)
    p.terminate()


if __name__ == "__main__":
    main()
