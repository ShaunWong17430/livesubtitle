# -*- coding: utf-8 -*-
"""诊断⑥：捕获音频 vs 原文件（2x）频谱对照 + 归一化互相关。"""
import io
import sys
import wave

import numpy as np

sys.path.insert(0, r"D:\data\dsh_subtitle")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def load(path):
    with wave.open(path, "rb") as w:
        return w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def spectral(a, rate=16000):
    seg = a[: min(len(a), rate * 2)]
    zcr = np.mean(np.abs(np.diff(np.sign(seg.astype(np.float32)))) > 0)
    w = np.hanning(len(seg))
    spec = np.abs(np.fft.rfft((seg * w).astype(np.float64)))
    freqs = np.fft.rfftfreq(len(seg), 1.0 / rate)
    dom = freqs[np.argmax(spec)]
    # 谐波率：主频及其 2/3 倍能量占比
    top = np.argsort(spec)[-5:]
    return dict(zcr=zcr, dom=dom, top=top, spec=spec, freqs=freqs)


def xcorr_max(a, b):
    n = min(len(a), len(b))
    a = a[:n].astype(np.float32)
    b = b[:n].astype(np.float32)
    a = (a - a.mean()) / (a.std() + 1e-9)
    b = (b - b.mean()) / (b.std() + 1e-9)
    # 分块计算互相关峰值（避免超大 FFT），只算 ±50ms 范围
    best = 0.0
    lag = 0
    for off in range(-800, 801, 100):
        if off >= 0:
            x = a[: n - off]; y = b[off:]
        else:
            x = a[-off:]; y = b[: n + off]
        if len(x) < 1000:
            continue
        c = float(np.corrcoef(x, y)[0, 1])
        if c > best:
            best, lag = c, off
    return best, lag


def main():
    orig = load(r"D:\data\dsh_subtitle\16k_english.wav")[1].astype(np.float32)
    orig2x = np.clip(orig * 2, -32768, 32767).astype(np.int16)
    cap = load(r"D:\data\dsh_subtitle\poc\_diag_avg.wav")[1]

    print("=== 频谱特征 ===")
    for name, sig in (("原文件", orig.astype(np.int16)), ("原文件2x", orig2x), ("捕获", cap)):
        s = spectral(sig)
        freqs = s["freqs"]
        topf = ", ".join(f"{freqs[i]:.0f}Hz" for i in s["top"][::-1])
        print(f"  {name}: ZCR={s['zcr']:.3f} 主频={s['dom']:.0f}Hz 前5频={topf}")

    print("\n=== 归一化互相关（与捕获比）===")
    for name, sig in (("原文件", orig.astype(np.int16)), ("原文件2x", orig2x)):
        c, lag = xcorr_max(cap, sig)
        print(f"  捕获 vs {name}: r={c:.3f} @lag={lag}ms")


if __name__ == "__main__":
    main()
