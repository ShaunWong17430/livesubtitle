# -*- coding: utf-8 -*-
"""Stage 1 辅助：把 WAV 通过指定物理输出设备播放（配合 live 冒烟测试）。

  用法: python poc/_play_wav_out.py <wav> <out_idx> [volume]
输出设备用 WASAPI 物理输出（如 [12] 耳机 (W20 dongle)）；其 loopback 孪生 [16] 会抓到它。
"""
import sys
import time
import wave

import numpy as np
import pyaudiowpatch as pyaudio


def main():
    wav_path, out_idx = sys.argv[1], int(sys.argv[2])
    volume = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    delay = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    loops = int(sys.argv[5]) if len(sys.argv) > 5 else 1
    if delay > 0:
        print(f"延迟 {delay:.0f}s 后播放…", flush=True)
        time.sleep(delay)
    with wave.open(wav_path, "rb") as w:
        rate = w.getframerate()
        ch = w.getnchannels()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) * volume
    a = np.clip(a, -32768, 32767).astype(np.int16)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1).astype(np.int16)

    p = pyaudio.PyAudio()
    info = p.get_device_info_by_index(out_idx)
    out_rate = int(info["defaultSampleRate"])
    print(f"播放 {wav_path} → [{out_idx}] {info['name']} @{out_rate}Hz", flush=True)
    if out_rate != rate:
        n = int(len(a) * out_rate / rate)
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)),
                      a.astype(np.float32)).astype(np.int16)
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=out_rate,
                    output=True, output_device_index=out_idx,
                    frames_per_buffer=1600)
    stream.start_stream()
    data = a.tobytes()
    step = 3200
    for _ in range(loops):
        for i in range(0, len(data), step):
            stream.write(data[i:i + step])
            time.sleep(0.01)
    stream.stop_stream(); stream.close()
    p.terminate()
    print("播放完成", flush=True)


if __name__ == "__main__":
    main()
