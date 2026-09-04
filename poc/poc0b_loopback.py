# -*- coding: utf-8 -*-
"""Stage 0 · PoC-0b：验证 WASAPI 端点 Loopback 能否抓到『扬声器播放的声音』（Teams 远端音轨）。

用法：
    python poc/poc0b_loopback.py [device_index] [seconds]
    默认 device_index = 默认输出对应的 WASAPI 设备；seconds = 60。

交互：
    - 运行后，在 Teams 里做「设备测试通话」，或打开一段含语音/音乐的音视频播放。
    - 观察打印的 RMS 电平：有声音时 RMS 明显抬升（如 >0.005~0.01），静默时趋近 0。
    - 60 秒后打印统计结论。

注意：
    - 端点 Loopback = 在【扬声器输出设备】上用 WasapiSettings(loopback=True) 打开 InputStream。
    - 这里抓的是「设备混音」：系统里所有播到这个扬声器的声音都会进来（含 Teams 远端）。
"""
import sys
import io
import time
import argparse
import numpy as np
import sounddevice as sd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def snapshot():
    """打印 WASAPI 设备列表，供选设备用。"""
    for i, d in enumerate(sd.query_devices()):
        print(f"  {i:3d} | {d['name']} | in {d['max_input_channels']} out {d['max_output_channels']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("device", nargs="?", type=int, default=None,
                    help="扬声器输出设备的 index（WASAPI）。留空自动用默认输出。")
    ap.add_argument("seconds", nargs="?", type=float, default=60.0)
    ap.add_argument("--list", action="store_true", help="仅列出设备")
    args = ap.parse_args()

    if args.list:
        snapshot()
        return

    if args.device is None:
        # 取默认输出设备；若非 WASAPI，则提示
        out_dev = sd.default.device[1]
    else:
        out_dev = args.device

    info = sd.query_devices(out_dev)
    print(f"使用设备 [{out_dev}] : {info['name']}  (max_out={info['max_output_channels']})")

    # 采样率：先试着按设备原生 48k；若失败回退 44100/16000
    def try_open(rate):
        return sd.InputStream(
            device=out_dev,
            samplerate=rate,
            channels=info["max_output_channels"] or 2,
            dtype="float32",
            extra_settings=sd.WasapiSettings(loopback=True),
            blocksize=3200,
        )

    stream = None
    for sr in (48000, 44100, 16000):
        try:
            stream = try_open(sr)
            print(f"Loopback 打开成功 @ {sr} Hz")
            break
        except Exception as e:
            print(f"  {sr} Hz 失败: {e}")
    if stream is None:
        print("!! 无法用 Loopback 打开该输出设备。可能被独占，或该设备不支持 Loopback。")
        sys.exit(1)

    stream.start()
    print(f"开始采集 {args.seconds}s。请在 Teams 做『设备测试通话』或播放有声视频…")
    rms_list = []
    t0 = time.time()
    peak_time = None
    try:
        while time.time() - t0 < args.seconds:
            data = stream.read(3200)[0]
            rms = float(np.sqrt(np.mean(np.square(data)))) if data.size else 0.0
            rms_list.append(rms)
            if rms > 0.005 and peak_time is None:
                peak_time = time.time() - t0
            bar = "#" * int(min(rms * 200, 40))
            print(f"  t={time.time()-t0:5.1f}s  RMS={rms:.4f}  {bar}")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n手动中断。")
    finally:
        stream.stop()
        stream.close()

    if not rms_list:
        print("\n未采到任何数据。")
        return
    mx = max(rms_list)
    mn = min(rms_list)
    avg = float(np.mean(rms_list))
    print("\n===== 结论 =====")
    print(f"  RMS 范围: {mn:.5f} ~ {mx:.5f}   平均 {avg:.5f}")
    if mx > 0.01:
        print("  ✅ 检测到明显声音信号 —— Loopback 有效！")
        if peak_time is not None:
            print(f"     首个高峰出现在 {peak_time:.1f}s 处（那会儿应该正在放声/说话）。")
    elif mx > 0.002:
        print("  ⚠️ 有微弱信号。若正在播放却仍很低，注意：Teams 可能静默，或该扬声器无输出流。")
    else:
        print("  ❌ 基本无声。若不是没放声：可能 Loopback 拿不到流（独占/设备问题），或 Teams 处于静默。")
        print("     请确认：1) 正在直播声音到这块扬声器；2) 该设备未被独占；3) Teams 扬声器=此设备。")


if __name__ == "__main__":
    main()