# -*- coding: utf-8 -*-
"""诊断：把 TwoChannelCapture 实际喂给 WS 的 16k 单声道字节存成 WAV + 打印 RMS。
用于验证『采集→重采样→downmix』产物是否可被 ASR 识别。"""
import io
import sys
import time
import wave

import numpy as np

sys.path.insert(0, r"D:\data\dsh_subtitle")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.audio.capture import TwoChannelCapture


def main():
    cap = TwoChannelCapture(16, 15).open()
    cap.start()
    print("采集 8s（请确保正在播放英语音频）…")
    buf = {sp: b"" for sp in ("remote", "self")}
    t0 = time.time()
    while time.time() - t0 < 8:
        chunk = cap.next_chunk(0.3)
        if chunk:
            sp, data = chunk
            buf[sp] += data
    cap.close()
    for sp, data in buf.items():
        a = np.frombuffer(data, dtype=np.int16)
        rms = float(np.sqrt(np.mean(np.square(a.astype(np.float32))))) if a.size else 0.0
        dur = len(data) / 32000.0
        print(f"[{sp}] 字节={len(data)} 时长={dur:.1f}s 峰值={np.abs(a).max() if a.size else 0} RMS={rms:.5f}")
        path = rf"D:\data\dsh_subtitle\poc\_diag_{sp}.wav"
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(data)
        print(f"  已存 {path}")


if __name__ == "__main__":
    main()
