# -*- coding: utf-8 -*-
"""诊断②：对已知可转录的原 wav 模拟『48k 立体声 → 降混单声道 → 重采样 16k』变换，
验证采集变换本身是否破坏 ASR 可辨识度。"""
import io
import sys
import wave

import numpy as np

sys.path.insert(0, r"D:\data\dsh_subtitle")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.audio.resample import to_16k_mono_pcm16


def load(path):
    with wave.open(path, "rb") as w:
        return w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())


def main():
    rate, ch, raw = load(r"D:\data\dsh_subtitle\16k_english.wav")
    a = np.frombuffer(raw, dtype=np.int16)
    # 模拟：上采样到 48k → 复制成 2 声道 → 再走 capture 变换
    n48 = int(len(a) * 48000 / rate)
    a48 = np.interp(np.linspace(0, len(a) - 1, n48), np.arange(len(a)),
                    a.astype(np.float32)).astype(np.int16)
    stereo = np.repeat(a48[:, None], 2, axis=1).astype(np.int16)
    out = to_16k_mono_pcm16(stereo.tobytes(), 48000, 2)
    out_a = np.frombuffer(out, dtype=np.int16)
    rms = float(np.sqrt(np.mean(np.square(out_a.astype(np.float32)))))
    # 与原文件对比
    orig_a = a.astype(np.float32)
    print(f"原 16k:  {len(a)} samples, RMS={np.sqrt(np.mean(np.square(orig_a))):.0f}, peak={np.abs(a).max()}")
    print(f"变换后: {len(out_a)} samples, RMS={rms:.0f}, peak={np.abs(out_a).max()}")
    with wave.open(r"D:\data\dsh_subtitle\poc\_diag_transform.wav", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(out)
    print("已存 poc/_diag_transform.wav")


if __name__ == "__main__":
    main()
