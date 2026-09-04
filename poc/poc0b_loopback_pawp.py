# -*- coding: utf-8 -*-
"""Stage 0 · PoC-0b：用 PyAudioWPatch 验证 WASAPI 端点 Loopback 抓『扬声器播放的声音』(Teams 远端)。

PyAudioWPatch 是 PyAudio 的分支，直接暴露 WASAPI loopback 采集。它把每个 render(输出) 端点
对应到一个 loopback 输入流，能抓到『正在播到该扬声器』的所有声音（含 Teams 远端）。

用法：
    python poc/poc0b_loopback_pawp.py                # 列设备并默认抓默认输出
    python poc/poc0b_loopback_pawp.py --device N     # 抓指定 loopback 设备 N
    python poc/poc0b_loopback_pawp.py --list         # 仅列设备

运行后播放含声音的视频/在 Teams 做设备测试通话，观察 RMS 起伏。
退出： Ctrl+C。
"""
import sys, io, time, argparse
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import pyaudiowpatch as pyaudio


def list_loopback(p):
    print("===== WASAPI 输出端点（每个对应一个 loopback 采集流）=====")
    n = p.get_device_count()
    for i in range(n):
        info = p.get_device_info_by_index(i)
        if info.get("isLoopbackDevice"):
            print("  [%d] %s  (loopback, maxIn=%d, 默认上传=%dHz)" % (i, info["name"], info["maxInputChannels"], int(info["defaultSampleRate"])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", type=int, default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--seconds", type=float, default=60.0)
    args = ap.parse_args()

    p = pyaudio.PyAudio()
    try:
        if args.list:
            list_loopback(p); return

        # 默认 loopback 设备
        if args.device is None:
            try:
                idx = p.get_default_wasapi_loopback()
            except Exception as e:
                # 在新版里方法名为 get_default_wasapi_loopback
                print("取默认 loopback 失败:", e)
                list_loopback(p)
                sys.exit(1)
            dev = p.get_device_info_by_index(idx)
            print("默认 loopback 设备 [%d]: %s" % (idx, dev["name"]))
        else:
            idx = args.device
            dev = p.get_device_info_by_index(idx)
            if not dev.get("isLoopbackDevice"):
                print("⚠️ [%d] 不是 loopback 设备（可能只是普通输入）。仍尝试打开。" % idx)
            print("指定 loopback 设备 [%d]: %s" % (idx, dev["name"]))

        rate = int(dev["defaultSampleRate"])
        ch = dev["maxInputChannels"] or 2
        print("采样率=%d Hz, 声道=%d" % (rate, ch))
        print("开始采集 %s 秒。请在 Teams『设备测试通话』或播放有声视频…(Ctrl+C 提前结束)" % args.seconds)

        stream = p.open(format=pyaudio.paInt16, channels=ch, rate=rate,
                        input=True, input_device_index=idx,
                        frames_per_buffer=1600)
        rms = []
        t0 = time.time()
        try:
            while time.time() - t0 < args.seconds:
                data = stream.read(1600, exception_on_overflow=False)
                a = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                r = float(np.sqrt(np.mean(np.square(a)))) if a.size else 0.0
                rms.append(r)
                print("  t=%.1fs  RMS=%.5f %s" % (time.time()-t0, r, "#"*int(min(r*200,40))))
        except KeyboardInterrupt:
            print("\n手动中断")
        finally:
            stream.stop_stream(); stream.close()
    finally:
        p.terminate()

    if rms:
        mx = max(rms); avg = float(np.mean(rms))
        print("\n===== 结论 =====")
        print("  loopback 峰值 RMS=%.5f  平均=%.5f" % (mx, avg))
        if mx > 0.01:
            print("  ✅ 端点 Loopback 有效：能采到扬声器端点正在播放的声音（含 Teams 远端）。")
        elif mx > 0.003:
            print("  ⚠️ 微弱信号。确认正在播音且音量足够。")
        else:
            print("  ❌ 无声。确认有声音在播放；若 Teams 静默则听不到。")


if __name__ == "__main__":
    main()