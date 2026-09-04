# -*- coding: utf-8 -*-
"""Stage 0 · PoC-0b 确定性验证（纯 PyAudioWPatch）：端点 Loopback 自放自采。

用 PyAudioWPatch 向物理输出设备播放 440Hz 音调，同时从同名 loopback 端点采集。
若 loopback 采到明显信号 → 端点 Loopback 有效（能采到你听到的扬声器输出）。

用法: python poc/poc0b_loopback_pawp_selftest.py --lb <loopback_idx> --out <output_idx> [--seconds 6]
      --list 列全部设备。
"""
import sys, io, time, argparse
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import pyaudiowpatch as pyaudio

FREQ = 440.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lb", type=int)
    ap.add_argument("--out", type=int)
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    p = pyaudio.PyAudio()
    try:
        if args.list:
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                print("  [%d] %s in=%d out=%d%s" % (
                    i, info["name"], info["maxInputChannels"], info["maxOutputChannels"],
                    "  <loopback>" if info.get("isLoopbackDevice") else ""))
            return

        lb_idx = args.lb
        out_idx = args.out
        if lb_idx is None or out_idx is None:
            print("需指定 --lb(loopback) 与 --out(物理输出)。--list 查看。")
            return

        lb = p.get_device_info_by_index(lb_idx)
        outdev = p.get_device_info_by_index(out_idx)
        rate = int(lb["defaultSampleRate"])
        lbch = lb["maxInputChannels"] or 2
        outch = outdev["maxOutputChannels"] or 2
        print("采集端 loopback [%d] %s @%dHz" % (lb_idx, lb["name"], rate))
        print("播放端 output  [%d] %s" % (out_idx, outdev["name"]))

        dur = args.seconds
        n = int(rate * dur)
        t = np.arange(n) / rate
        tone = (0.5 * np.sin(2 * np.pi * FREQ * t) * np.clip(np.linspace(0.1, 0.9, n), 0, 1))
        tone16 = (tone * 32767).astype(np.int16)
        tone16 = np.tile(tone16[:, None], (1, outch))

        # 采集 loopback
        stream = p.open(format=pyaudio.paInt16, channels=lbch, rate=rate,
                        input=True, input_device_index=lb_idx, frames_per_buffer=1600)
        # 播放到物理输出
        outstream = p.open(format=pyaudio.paInt16, channels=outch, rate=rate,
                           output=True, output_device_index=out_idx, frames_per_buffer=1600)
        stream.start_stream(); outstream.start_stream()
        print("将播放 %dHz %s 秒…" % (FREQ, dur))
        rms = []
        t0 = time.time()
        tone_ptr = 0
        try:
            while time.time() - t0 < dur + 0.6:
                # 推一小段到输出
                if tone_ptr < len(tone16):
                    chunk = tone16[tone_ptr:tone_ptr+3200]
                    outstream.write(chunk.tobytes())
                    tone_ptr += len(chunk)
                # 读 loopback
                try:
                    data = stream.read(1600, exception_on_overflow=False)
                    a = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                    r = float(np.sqrt(np.mean(np.square(a)))) if a.size else 0.0
                    rms.append(r)
                    print("  t=%.2fs RMS=%.5f %s" % (time.time()-t0, r, "#"*int(min(r*200, 40))))
                except Exception as e:
                    print("  read err:", e)
                time.sleep(0.1)
        finally:
            stream.stop_stream(); stream.close()
            outstream.stop_stream(); outstream.close()

        if not rms:
            print("\n无数据"); return
        mx = max(rms); avg = float(np.mean(rms))
        print("\n===== 结论 =====")
        print("  loopback 峰值 RMS=%.5f 平均=%.5f" % (mx, avg))
        if mx > 0.05:
            print("  ✅ 端点 Loopback 有效：能采到该扬声器正在播放的声音（含 Teams 远端）！")
        elif mx > 0.01:
            print("  ⚠️ 有信号但偏弱，可调大音量或确认 loopback/out 匹配。")
        else:
            print("  ❌ 无声。loopback 与播放设备可能不匹配，或音量太低。")
    finally:
        p.terminate()


if __name__ == "__main__":
    main()