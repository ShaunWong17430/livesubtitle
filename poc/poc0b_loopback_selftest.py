# -*- coding: utf-8 -*-
"""Stage 0 · PoC-0b 辅助：确定性验证端点 Loopback（自放自采，无需外部应用）。

程序自己向『默认/指定扬声器』播放一段 440Hz 正弦音，同时用 WasapiSettings(loopback=True)
从同一声演讲入。若采到的信号 RMS 明显抬高、且频率≈440Hz，则证明该扬声器的端点 Loopback 有效。

用法： python poc/poc0b_loopback_selftest.py [spk_device_idx] [seconds]
      --list 列出设备；不指定则用默认输出设备。

此验证不依赖 Teams，用于先排除『设备/Loopback 本身』的问题。
"""
import sys
import io
import time
import argparse
import numpy as np
import sounddevice as sd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
FREQ = 440.0


def snapshot():
    for i, d in enumerate(sd.query_devices()):
        print(f"  {i:3d} | {d['name']} | in {d['max_input_channels']} out {d['max_output_channels']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("device", nargs="?", type=int, default=None)
    ap.add_argument("seconds", nargs="?", type=float, default=8.0)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        snapshot()
        return

    out_dev = args.device if args.device is not None else sd.default.device[1]
    info = sd.query_devices(out_dev)
    if info["max_output_channels"] == 0:
        print(f"设备 [{out_dev}] 不是输出设备。请用 --list 选一个输出（扬声器）。")
        sys.exit(1)
    print(f"目标输出设备 [{out_dev}]: {info['name']}")

    sr = 48000
    ch = info["max_output_channels"] or 2
    dur = args.seconds
    t_arr = np.arange(int(sr * dur)) / sr
    # 用一段幅度/频率已知的正弦（含包络便于观察）
    tone = 0.5 * np.sin(2 * np.pi * FREQ * t_arr)
    tone = (tone * np.clip(np.linspace(0.15, 1.0, len(tone)), 0, 1)).astype(np.float32)
    mono = np.tile(tone[:, None], (1, ch))  # 复制到各声道

    # Loopback 输入
    def open_loopback():
        for r in (48000, 44100, 16000):
            try:
                return r, sd.InputStream(device=out_dev, samplerate=r, channels=ch,
                                         dtype="float32", blocksize=1600,
                                         extra_settings=sd.WasapiSettings(loopback=True))
            except Exception:
                continue
        raise RuntimeError("Loopback 打不开")

    try:
        sr_in, instream = open_loopback()
    except Exception as e:
        print(f"✘ 该输出设备的端点 Loopback 打不开: {e}")
        print("  可能被『独占模式』占用。请到 设置→系统→声音→扬声器→更多属性→高级→ 取消勾选『允许应用独占控制』。")
        sys.exit(1)

    outstream = sd.OutputStream(device=out_dev, samplerate=sr, channels=ch, dtype="float32")

    print(f"\n将播放 {dur}s 的 {FREQ}Hz 音调并同时 Loopback 采样，检测是否采回。")
    instream.start()
    outstream.start()
    outstream.write(mono, len(mono))
    rms = []
    t0 = time.time()
    while time.time() - t0 < dur + 0.5:
        data = instream.read(1600)[0]
        r = float(np.sqrt(np.mean(np.square(data)))) if data.size else 0.0
        rms.append(r)
        bar = "#" * int(min(r * 200, 40))
        print(f"  t={time.time()-t0:5.1f}s  RMS={r:.4f} {bar}")
        time.sleep(0.08)
    instream.stop(); outstream.stop()
    instream.close(); outstream.close()

    if not rms:
        print("\n未采到数据"); return
    mx = max(rms); avg = float(np.mean(rms))
    print("\n===== 结论 =====")
    print(f"  该扬声器 Loopback 采样峰值 RMS = {mx:.4f}（放 440Hz 音调期间）")
    if mx > 0.05:
        print("  ✅ 端点 Loopback 有效：能采到本扬声器正在播放的声音。")
        print("     → 说明设备侧 OK；接下来接 Teams 实测远端音轨即可。")
    elif mx > 0.01:
        print("  ⚠️ 有信号但偏弱。可能音量低或声道选择问题，可放大或换设备。")
    else:
        print("  ❌ 基本无声。Loopback 拿不到流（多为独占模式）或设备不支持。")


if __name__ == "__main__":
    main()