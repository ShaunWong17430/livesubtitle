# -*- coding: utf-8 -*-
"""诊断④：同时开 loopback [16] 与 [17]，系统循环播放时看哪个产帧。"""
import io
import sys
import time

import numpy as np
import pyaudiowpatch as pyaudio

sys.path.insert(0, r"D:\data\dsh_subtitle")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def open_loopback(p, idx, label):
    info = p.get_device_info_by_index(idx)
    rate = int(info["defaultSampleRate"])
    ch = info["maxInputChannels"] or 2
    counts = {"n": 0, "rms": 0.0, "peak": 0}
    def _cb(in_data, _f, _t, _s):
        a = np.frombuffer(in_data, dtype=np.int16)
        r = float(np.sqrt(np.mean(np.square(a.astype(np.float32)))))
        counts["n"] += 1
        counts["rms"] += r
        counts["peak"] = max(counts["peak"], int(np.abs(a).max()))
        return (None, pyaudio.paContinue)
    s = p.open(format=pyaudio.paInt16, channels=ch, rate=rate,
               input=True, input_device_index=idx,
               frames_per_buffer=1600, stream_callback=_cb)
    return s, counts


def main():
    p = pyaudio.PyAudio()
    s16, c16 = open_loopback(p, 16, "W20")
    s17, c17 = open_loopback(p, 17, "Realtek")
    s16.start_stream(); s17.start_stream()
    print("两路 loopback 已开，监测 14s（确保系统在循环播放）…")
    time.sleep(14)
    s16.stop_stream(); s17.stop_stream(); s16.close(); s17.close(); p.terminate()
    for label, c in (("W20[16]", c16), ("Realtek[17]", c17)):
        avg = c["rms"] / c["n"] if c["n"] else 0
        print(f"  {label}: 块数={c['n']} 平均RMS={avg:.1f} 峰值={c['peak']}")


if __name__ == "__main__":
    main()
