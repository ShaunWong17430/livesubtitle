# -*- coding: utf-8 -*-
"""Stage 0 · PoC-0b'：验证『两路采集可同时打开』—— 麦克风(自己) + 端点Loopback(远端)。

方案 A（G1'）前提验证：
  1) 两个 InputStream 能同时开（互不冲突）：
        - 远端路：loopback 虚拟输入设备（如 [16] 扬声器 (XIBERIA MC01) [Loopback]）
                  → 抓『正在播放到该扬声器』的所有声音 = Teams 远端
        - 自己路：物理麦克风输入设备（如 [15] 耳机 (XIBERIA MC01)）正常开 input
                  → 抓你说话（WASAPI 共享模式允许多程序同时采同一麦克风）
  2) 各自采到非静默且两路独立：你说话 → 自己路 RMS 抬升；放声 → 远端路抬升。

用「回调模式」(stream_callback) 而非阻塞 read：
  - WASAPI loopback 在渲染端点不活跃时不产帧，阻塞 read 会卡死（实测确认）；
    回调模式不会阻塞主循环，也是最终架构（回调→队列→WebSocket）的形态。
  - 备注：真实 Teams 会议中端点一直活跃，两种模式都可用；这里验证回调路径。

用法:
    python poc/poc0b_two_channel.py [seconds]                     # 自动选默认设备
    python poc/poc0b_two_channel.py --remote <loopback_idx> --self <mic_idx> [seconds]
    python poc/poc0b_two_channel.py --list                        # 列全部设备

注意：这是『戴耳机』场景的两路分离验证。戴耳机放视频 → 看 [远端]；说话 → 看 [自己]。
"""
import sys
import io
import time
import queue
import argparse
import threading

import numpy as np
import pyaudiowpatch as pyaudio

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def list_devices(p):
    print("===== 全部设备 =====")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        print("  [%3d] in=%d out=%d | %s%s" % (
            i, info["maxInputChannels"], info["maxOutputChannels"],
            info["name"], "  <loopback>" if info.get("isLoopbackDevice") else ""))
    print("\n--remote 用 loopback 端点（远端）；--self 用物理麦克风（自己）。")


def open_callback_stream(p, idx, label, q):
    """打开一路 input，回调把 RMS 放进队列。返回 (rate, stream, info)。"""
    info = p.get_device_info_by_index(idx)
    rate = int(info["defaultSampleRate"])
    ch = info["maxInputChannels"] or (2 if info.get("isLoopbackDevice") else 1)

    def _cb(in_data, _frames, _time_info, _status):
        a = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
        r = float(np.sqrt(np.mean(np.square(a)))) if a.size else 0.0
        try:
            q.put_nowait((time.time(), r))
        except queue.Full:
            pass
        return (None, pyaudio.paContinue)

    last_err = None
    for sr in (rate, 48000, 44100, 16000):
        try:
            s = p.open(format=pyaudio.paInt16, channels=ch, rate=sr,
                       input=True, input_device_index=idx,
                       frames_per_buffer=1600, stream_callback=_cb)
            return sr, s, info
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"打不开 {label} 设备 [{idx}]: {last_err}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", type=int, default=None, help="远端 loopback 设备 idx")
    ap.add_argument("--self", type=int, default=None, help="自己麦克风设备 idx")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("seconds", nargs="?", type=float, default=30.0)
    args = ap.parse_args()

    p = pyaudio.PyAudio()
    try:
        if args.list:
            list_devices(p)
            return

        remote_idx = args.remote
        if remote_idx is None:
            try:
                d = p.get_default_wasapi_loopback()
                remote_idx = int(d["index"]) if isinstance(d, dict) else int(d)
            except Exception:
                print("自动取默认 loopback 失败，请 --remote 指定；--list 查看。")
                return

        # 先开远端路，好让 --self 启发式用其品牌族匹配耳机麦
        q_remote = queue.Queue(maxsize=200)
        remote = None
        try:
            rr, remote, rinfo = open_callback_stream(p, remote_idx, "远端Loopback", q_remote)
            print(f"✔ 远端路 Loopback [{remote_idx}] @{rr}Hz: {rinfo['name']}")
        except Exception as e:
            print(f"✘ 远端路打开失败: {e}"); return

        # 自己路：启发式优先找与远端同品牌族、且同在 WASAPI(同hostApi) 的麦克风（耳机麦），
        # 避免选到默认系统麦或 MME/DirectSound 的旧式重复条目。
        self_idx = args.self
        if self_idx is None:
            family = rinfo["name"].replace("[Loopback]", "").strip()
            fam_key = family.split("(")[-1].rstrip(")").strip() if family else ""
            want_host = rinfo["hostApi"]
            cand = cand_any = None
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if (info["maxInputChannels"] > 0
                        and not info.get("isLoopbackDevice")
                        and fam_key and fam_key in info["name"]):
                    cand_any = cand_any or i
                    if info["hostApi"] == want_host:
                        cand = i
                        break
            if cand is not None:
                self_idx = cand
                print(f"  (自动选中与远端同品牌同WASAPI麦克风 [{cand}]，若不对请 --self 指定)")
            elif cand_any is not None:
                self_idx = cand_any
                print(f"  (自动选中同品牌麦克风 [{cand_any}]（非WASAPI条目），若不对请 --self 指定)")
            else:
                try:
                    self_idx = int(p.get_default_input_device_info()["index"])
                    print(f"  (默认麦克风 [{self_idx}]，若不对请 --self 指定)")
                except Exception:
                    print("自动取默认麦克风失败，请 --self 指定；--list 查看。")
                    remote.close(); return

        q_self = queue.Queue(maxsize=200)
        try:
            sr, self_s, sinfo = open_callback_stream(p, self_idx, "自己麦克风", q_self)
            print(f"✔ 自己路 麦克风  [{self_idx}] @{sr}Hz: {sinfo['name']}")
            print("  --- 两路同时打开成功（互不抢麦/互不冲突）→ 方案A采集前提之一成立 ---")
        except Exception as e:
            print(f"✘ 自己路打开失败: {e} —— 可能被独占，需检查。")
            remote.close(); return

        remote.start_stream(); self_s.start_stream()
        print(f"\n开始 {args.seconds}s 两路监测（戴耳机）：")
        print("   ① 播放一段含声音的视频/音乐 → 看 [远端] RMS 抬升")
        print("   ② 你自己说话 → 看 [自己] RMS 抬升")
        t0 = time.time()
        remotes, selves = [], []

        def drain(q):
            out = None
            while True:
                try:
                    out = q.get_nowait()
                except queue.Empty:
                    break
            return out

        try:
            while time.time() - t0 < args.seconds:
                last_r = drain(q_remote)
                last_m = drain(q_self)
                if last_r:
                    remotes.append(last_r[1]); r = last_r[1]
                else:
                    r = remotes[-1] if remotes else 0.0
                if last_m:
                    selves.append(last_m[1]); m = last_m[1]
                else:
                    m = selves[-1] if selves else 0.0
                print("  t=%5.1fs | 远端%.5f %s | 自己%.5f %s" % (
                    time.time() - t0, r, "#" * int(min(r * 200, 36)), m, "#" * int(min(m * 200, 36))))
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\n手动中断")
        finally:
            remote.stop_stream(); remote.close()
            self_s.stop_stream(); self_s.close()
    finally:
        p.terminate()

    if not remotes and not selves:
        print("无数据"); return
    mxr = max(remotes) if remotes else 0.0
    mxm = max(selves) if selves else 0.0
    print("\n===== 结论 =====")
    print(f"  远端(扬声器Loopback) 峰值 RMS = {mxr:.5f}")
    print(f"  自己(麦克风)         峰值 RMS = {mxm:.5f}")
    if mxr > 0.01 and mxm > 0.01:
        print("  ✅ 两路都能采到信号且可同时打开 —— 方案A（戴耳机）采集可行性通过！")
    elif mxm > 0.01:
        print("  ⚠️ 麦克风路正常；远端路无声（确认在播放声音、且输出到了该扬声器）。")
    elif mxr > 0.01:
        print("  ⚠️ 远端路正常；麦克风路无声（确认对着麦说话）。")
    else:
        print("  ❌ 两路都无声。检查设备选择、是否在放声/说话。")


if __name__ == "__main__":
    main()
