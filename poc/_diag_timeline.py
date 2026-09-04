# -*- coding: utf-8 -*-
"""诊断③：记录两路每块到达时间 + RMS 时间线（判断 loopback 是否持续产帧）。"""
import io
import sys
import time

import numpy as np

sys.path.insert(0, r"D:\data\dsh_subtitle")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.audio.capture import TwoChannelCapture


def main():
    cap = TwoChannelCapture(16, 15).open()
    cap.start()
    t0 = time.time()
    last_remote = {}
    print("监测 15s（系统循环播放中）…")
    while time.time() - t0 < 15:
        chunk = cap.next_chunk(0.5)
        if chunk:
            sp, data = chunk
            a = np.frombuffer(data, dtype=np.int16)
            rms = float(np.sqrt(np.mean(np.square(a.astype(np.float32)))))
            t = time.time() - t0
            last_remote.setdefault(sp, 0)
            last_remote[sp] += 1
            if sp == "remote":
                bar = "#" * int(min(rms / 3000 * 40, 40))
                print(f"  t={t:5.1f}s [remote #{last_remote[sp]:3d}] RMS={rms:7.1f} peak={np.abs(a).max():6d} {bar}")
            else:
                bar = "#" * int(min(rms / 3000 * 40, 40))
                print(f"  t={t:5.1f}s [self   #{last_remote[sp]:3d}] RMS={rms:7.1f} peak={np.abs(a).max():6d} {bar}")
    cap.close()
    print(f"\nremote 块数: {last_remote.get('remote',0)}，self 块数: {last_remote.get('self',0)}")


if __name__ == "__main__":
    main()
