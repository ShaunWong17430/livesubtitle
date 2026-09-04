# -*- coding: utf-8 -*-
"""诊断⑤：保存原始 48k 立体声 loopback 捕获，并用不同降混方式转 16k 单声道，
分别走 file 模式看服务端能否转录。同时做频谱粗分析。"""
import io
import sys
import time
import wave

import numpy as np

sys.path.insert(0, r"D:\data\dsh_subtitle")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pyaudiowpatch as pyaudio


def spectral(a, rate=16000):
    """零交叉率 + 主频。语音：ZCR 高、频谱铺开；音调/噪声：ZCR 低或主频集中。"""
    zcr = np.mean(np.abs(np.diff(np.sign(a.astype(np.float32)))) > 0) if len(a) > 1 else 0
    seg = a[: min(len(a), rate)]  # 1s
    w = np.hanning(len(seg))
    spec = np.abs(np.fft.rfft((seg * w).astype(np.float64)))
    freqs = np.fft.rfftfreq(len(seg), 1.0 / rate)
    dom = freqs[np.argmax(spec)]
    return zcr, dom, float(np.max(spec))


def main():
    p = pyaudio.PyAudio()
    info = p.get_device_info_by_index(16)
    rate = int(info["defaultSampleRate"])
    ch = info["maxInputChannels"] or 2
    raw = bytearray()
    def _cb(in_data, _f, _t, _s):
        raw.extend(in_data)
        return (None, pyaudio.paContinue)
    s = p.open(format=pyaudio.paInt16, channels=ch, rate=rate,
               input=True, input_device_index=16,
               frames_per_buffer=1600, stream_callback=_cb)
    s.start_stream()
    print(f"原始捕获 {rate}Hz {ch}ch loopback [16] 10s（请确认在循环播放）…")
    time.sleep(10)
    s.stop_stream(); s.close(); p.terminate()
    a = np.frombuffer(bytes(raw), dtype=np.int16).reshape(-1, ch)
    print(f"原始: {a.shape[0]} frames {ch}ch = {a.shape[0]/rate:.2f}s")
    print(f"  L RMS={np.sqrt(np.mean(a[:,0].astype(np.float32)**2)):.0f} peak={np.abs(a[:,0]).max()}")
    print(f"  R RMS={np.sqrt(np.mean(a[:,1].astype(np.float32)**2)):.0f} peak={np.abs(a[:,1]).max()}")

    # 三种降混转 16k
    n = int(a.shape[0] * 16000 / rate)
    x = np.linspace(0, a.shape[0] - 1, n)
    variants = {}
    for name, col in (("left", a[:, 0]), ("right", a[:, 1]), ("avg", a.mean(axis=1))):
        m = np.interp(x, np.arange(a.shape[0]), col.astype(np.float32)).astype(np.int16)
        variants[name] = m
    for name, m in variants.items():
        zcr, dom, mx = spectral(m)
        rms = float(np.sqrt(np.mean(m.astype(np.float32) ** 2)))
        path = rf"D:\data\dsh_subtitle\poc\_diag_{name}.wav"
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(m.tobytes())
        print(f"  [{name}] RMS={rms:.0f} peak={np.abs(m).max()} ZCR={zcr:.3f} 主频={dom:.0f}Hz → {path}")


if __name__ == "__main__":
    main()
