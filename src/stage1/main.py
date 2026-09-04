# -*- coding: utf-8 -*-
"""Stage 1 · 最小闭环（无 UI）命令行入口（src/stage1/main.py）。

链路：两路采集 → 重采样16k → 实时推送（speaker 标记）→ WS 实时翻译 →
      按 previous_item_id 配对 → 终端打印「原文 / 译文 + speaker + 北京时间」。

用法：
  python -m src.stage1.main --mode list                     # 列设备 + 推荐
  python -m src.stage1.main --mode live [--remote N --self M] [--seconds N]
                          [--session-minutes 30]            # 30 分钟后自动重建会话
  python -m src.stage1.main --mode file --wav 16k_english.wav [--speaker remote]

默认 --csv 从用户目录 CSV 读 API Key（不回显）。目标语言默认 zh（§6.7 方案 A）。
"""
import argparse
import io
import os
import sys
import time
import wave
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

# 路径相对本文件推导（文件夹整体搬移不影响）
_THIS = os.path.dirname(os.path.abspath(__file__))          # .../src/stage1
_PROJ = os.path.dirname(os.path.dirname(_THIS))             # 项目根
DEFAULT_CSV = os.path.join(_PROJ, "默认业务空间-apiKey-7021723.csv")
MODEL = "qwen3.5-livetranslate-flash-realtime"
BEIJING = ZoneInfo("Asia/Shanghai")

# 让 src 作为顶层包可导入（相对路径，不再写死）
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


# ---------------- 凭据 / 端点 ----------------
def load_cred(path):
    from src.cred import load_cred as _lc
    return _lc(path)


def discover_cred_file():
    from src.cred import discover_cred_file as _dc
    return _dc()


def build_url(cred):
    from src.cred import build_url as _bu
    return _bu(cred, model=None)


# ---------------- 显示 ----------------
def fmt_beijing(ts):
    if ts is None:
        return "--:--:--"
    return datetime.fromtimestamp(ts, BEIJING).strftime("%H:%M:%S")


class Printer:
    """终端渲染：partial 用 \r 整行覆盖；final 打印完整轮次。"""

    def __init__(self, show_partial=True):
        self.show_partial = show_partial
        self.final_count = 0
        self._last_partial_len = 0

    def partial(self, turn):
        if not self.show_partial:
            return
        line = "[#? %s] 原文:%s ｜ 译文:%s" % (
            turn.speaker, turn.asr_partial or "(…)",
            turn.trans_partial or "(…)")
        pad = " " * max(0, self._last_partial_len - len(line))
        sys.stdout.write("\r" + line + pad)
        sys.stdout.flush()
        self._last_partial_len = len(line)

    def final(self, turn, idx):
        self.final_count = idx
        sys.stdout.write("\n")
        sp = {"remote": "远端", "self": "自己"}.get(turn.speaker, turn.speaker)
        line = "[#%d · %s · %s]" % (idx, sp, fmt_beijing(turn.abs_start))
        if turn.asr_final:
            line += " 原文: %s" % turn.asr_final
        if turn.trans_final:
            line += "\n  译文: %s" % turn.trans_final
        print(line)
        self._last_partial_len = 0

    def newline(self):
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._last_partial_len = 0


# ---------------- 文件模式辅助 ----------------
def load_wav_pcm16(path, target_rate=16000):
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise RuntimeError(f"仅支持 16bit PCM WAV，当前 {width*8}bit")
    a = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1).astype(np.int16)
    if rate != target_rate:
        n_out = max(1, int(len(a) * target_rate / rate))
        a = np.interp(np.linspace(0, len(a) - 1, n_out),
                      np.arange(len(a)), a.astype(np.float32)).astype(np.int16)
    return target_rate, a.tobytes()


def iter_pcm16(data_bytes, chunk_frames=1600):
    step = chunk_frames * 2
    for i in range(0, len(data_bytes), step):
        yield data_bytes[i:i + step]


# ---------------- 模式：list ----------------
def mode_list():
    import pyaudiowpatch as pyaudio
    from src.audio import devices as dev
    p = pyaudio.PyAudio()
    try:
        print("===== 全部设备 =====")
        dev.list_devices(p)
        print()
        try:
            ridx = dev.default_remote_loopback(p)
            rinfo = p.get_device_info_by_index(ridx)
            print(f"默认远端 loopback: [{ridx}] {rinfo['name']} @{dev.host_rate(p, ridx)}Hz")
            try:
                sidx, how = dev.pick_self_mic(p, rinfo)
                print(f"推荐麦克风: [{sidx}] {dev.device_label(p, sidx)}（启发式: {how}）")
            except Exception as e:
                print(f"推荐麦克风失败: {e}")
        except Exception as e:
            print(f"取默认 loopback 失败: {e}")
    finally:
        p.terminate()


# ---------------- 运行器 ----------------
def run_pipeline(cap_or_iter, session, printer, seconds=None, session_minutes=30):
    """cap_or_iter：可迭代 (speaker, bytes) 块（live 由 TwoChannelCapture 产，file 由生成器产）。"""
    t0 = time.time()
    session_start = t0
    running = True
    last_status = t0
    idx = 0
    try:
        while running:
            now = time.time()
            # 会话重建（§6.6）
            if session_minutes > 0 and now - session_start >= session_minutes * 60:
                print(f"\n[重建] 会话已运行 {(now-session_start)/60:.1f} 分钟，优雅重建…")
                session.rebuild()
                session_start = time.time()
                printer.newline()
            # 意外断线重连（R10：退避 ≥6s）
            if session.dead:
                wait = 6.0
                print(f"\n[重连] 会话断线（{session.last_close_reason}），{wait:.0f}s 后重连…")
                time.sleep(wait)
                session.rebuild()
                session_start = time.time()
                printer.newline()
                continue

            # 取块
            if hasattr(cap_or_iter, "next_chunk"):
                chunk = cap_or_iter.next_chunk(0.3)
            else:
                try:
                    chunk = next(cap_or_iter)
                except StopIteration:
                    chunk = None
                    running = False
            if chunk is not None:
                speaker, data = chunk
                try:
                    session.append_audio(data, speaker)
                except Exception:
                    # 发送失败（连接被服务端关闭 / 网络断等）→ 标记断线走重连。
                    # 不能只捕 ConnectionError：服务端主动关连接时 send 会抛
                    # WebSocketConnectionClosedException，未捕获会让整个泵崩溃。
                    session.dead = True
                    continue

            # 定期状态行（含合并流秒数 / 用量 / 丢包）
            if now - last_status >= 20:
                last_status = now
                drops = getattr(cap_or_iter, "dropped", 0)
                print("\r[状态] t=%5.0fs 会话#%d 合并流%.1fs 用量%dtok 丢包%d | "
                      % (now - t0, session._session_no, session.sent_samples / 16000.0,
                         session.usage_tokens, drops))
                printer.newline()

            # seconds 到期（live 限时）
            if seconds is not None and now - t0 >= seconds:
                running = False
    except KeyboardInterrupt:
        print("\n[中断] Ctrl+C")
    finally:
        if session.conn is not None and session.conn.connected:
            print("[结束] 发送 session.finish，等待 finished…")
        session.finish_graceful()
        return idx


def mode_file(api_key, url, src, tgt, wav_path, speaker, show_partial, vad_threshold, silence_ms,
              sim_live=False, force_after=2.5):
    from src.translate.session import RealtimeSession
    rate, data = load_wav_pcm16(wav_path)
    dur = len(data) / (rate * 2)
    print(f"✔ 读取 {wav_path}: {rate}Hz 单声道 PCM16，{dur:.1f}s（标记 speaker={speaker}）")
    print(f"✔ 参数: vad_threshold={vad_threshold}  silence_duration_ms={silence_ms}"
          + (f"  [sim-live] 每块 sleep 实时节奏 + force_finalize(force_after={force_after}s)" if sim_live else "  [快速喂入]"))

    printer = Printer(show_partial)
    session = RealtimeSession(url, api_key, src, tgt,
                              on_partial=printer.partial, on_final=printer.final,
                              vad_threshold=vad_threshold, silence_ms=silence_ms)

    def chunks():
        for c in iter_pcm16(data):
            yield (speaker, c)

    session.open()
    print("✔ WebSocket 已连接，session.update 已发送。喂入音频…\n")
    if sim_live:
        # 复刻 GUI 精确时序：0.1s 音频一块 → sleep 0.1s 真实施播；每 ~0.3s 调一次
        # force_finalize_overdue（GUI 泵是 next_chunk(0.3) + 每轮调用）。
        t0 = time.time()
        tick = 0
        for sp, c in chunks():          # chunk=1600 frames=0.1s
            try:
                session.append_audio(c, sp)
            except Exception:
                session.dead = True
                print("[重连] 发送失败，跳过"); session.rebuild(); continue
            # 实时节奏：0.1s 音频就等 0.1s
            time.sleep(0.1)
            tick += 1
            if tick % 3 == 0:           # 每 0.3s 调一次（GUI：每轮 next_chunk(0.3) 后调）
                try:
                    session.force_finalize_overdue(force_after=force_after)
                except Exception:
                    pass
        # 收尾
        session.finish_graceful()
        print_summary(session)
    else:
        run_pipeline(chunks(), session, printer, session_minutes=0)
        print_summary(session)


def mode_live(api_key, url, src, tgt, remote_idx, self_idx, seconds, session_minutes, show_partial, vad_threshold):
    from src.audio import devices as dev
    from src.audio.capture import TwoChannelCapture
    from src.translate.session import RealtimeSession

    p = None
    import pyaudiowpatch as pyaudio
    p = pyaudio.PyAudio()
    try:
        if remote_idx is None:
            remote_idx = dev.default_remote_loopback(p)
        rinfo = p.get_device_info_by_index(remote_idx)
        print(f"✔ 远端 Loopback [{remote_idx}] @{dev.host_rate(p, remote_idx)}Hz: {rinfo['name']}")
        if self_idx is None:
            self_idx, how = dev.pick_self_mic(p, rinfo)
            print(f"✔ 自己麦克风 [{self_idx}] {dev.device_label(p, self_idx)}（启发式: {how}；若不对请 --self 指定）")
        cap = TwoChannelCapture(remote_idx, self_idx).open()
        cap.start()
        st = cap.stats()
        print(f"✔ 两路同开成功：远端 @{st['remote_rate']}Hz / 自己 @{st['self_rate']}Hz")
        print("  （要求：戴耳机。放声→远端；说话→自己。R16 真实 Teams 场景需开会时复验。）")

        printer = Printer(show_partial)
        session = RealtimeSession(url, api_key, src, tgt,
                                  on_partial=printer.partial, on_final=printer.final,
                                  vad_threshold=vad_threshold)
        session.open()
        print(f"✔ WebSocket 已连接。{'限时 %ds' % seconds if seconds else 'Ctrl+C 结束'}，"
              f"会话每 {session_minutes} 分钟自动重建。\n")
        run_pipeline(cap, session, printer, seconds=seconds, session_minutes=session_minutes)
        cap.close()
        print_summary(session, cap)
    finally:
        if p is not None:
            p.terminate()


def print_summary(session, cap=None):
    sp = {"remote": 0, "self": 0}
    for t in session.turn_log:
        sp[t.speaker] = sp.get(t.speaker, 0) + 1
    audio_s = session.sent_samples / 16000.0
    print("\n===== 本轮小结 =====")
    print(f"  定稿轮次: {len(session.turn_log)}  （远端 {sp.get('remote',0)} / 自己 {sp.get('self',0)}）")
    print(f"  推送音频: {audio_s:.1f}s  |  用量: {session.usage_tokens} tokens")
    if cap is not None:
        print(f"  丢包(背压): {cap.dropped}")
    if session.error:
        print(f"  会话错误: {session.error}")
    print(f"  session.finished: {'✔' if session.finished_ok else '—'}")

    print("\n----- 最近 5 轮 -----")
    for t in session.turn_log[-5:]:
        spn = {"remote": "远端", "self": "自己"}.get(t.speaker, t.speaker)
        print(f"  [{spn} · {fmt_beijing(t.abs_start)}] 原文: {t.asr_final}")
        if t.trans_final:
            print(f"     译文: {t.trans_final}")


# ---------------- 入口 ----------------
def main():
    ap = argparse.ArgumentParser(description="Stage 1 最小闭环（两路采集→实时翻译→终端字幕）")
    ap.add_argument("--mode", choices=["live", "file", "list"], default="live")
    ap.add_argument("--csv", default=None,
                    help="凭据 CSV 路径；省略则自动在项目根发现（文件名随意）")
    ap.add_argument("--remote", type=int, default=None, help="远端 loopback 设备 idx")
    ap.add_argument("--self", type=int, default=None, help="自己麦克风设备 idx")
    ap.add_argument("--seconds", type=float, default=None, help="live 限时（默认不限，Ctrl+C 结束）")
    ap.add_argument("--session-minutes", type=float, default=30.0, help="会话重建间隔（0=不重建）")
    ap.add_argument("--vad-threshold", type=float, default=0.2,
                    help="VAD 阈值 [-1,1]（默认 0.2；loopback 信号偏弱时调低如 0.05）")
    ap.add_argument("--silence-ms", type=int, default=1200,
                    help="静默判定时长 ms（server_vad silence_duration_ms；抗碎调大如 2000）")
    ap.add_argument("--sim-live", action="store_true",
                    help="file 模式模拟 GUI 实时节奏（每块 sleep + 每轮 force_finalize_overdue）——用于复现兜底切割")
    ap.add_argument("--force-after", type=float, default=2.5,
                    help="sim-live 时客户端兜底定稿秒数（默认 2.5，GUI 同款）")
    ap.add_argument("--src", default="en", help="源语言（协议自动检测，仅显示用）")
    ap.add_argument("--tgt", default="zh", help="目标语言（默认 zh，§6.7 方案 A）")
    ap.add_argument("--speaker", choices=["remote", "self"], default="remote", help="file 模式标记")
    ap.add_argument("--no-partial", action="store_true", help="不打印 \r 增量行")
    ap.add_argument("--wav", default=None, help="file 模式 WAV 路径")
    args = ap.parse_args()

    if args.mode == "list":
        mode_list()
        return

    csv_path = args.csv or discover_cred_file()
    cred = load_cred(csv_path)
    api_key = cred.get("apiKey")
    if not api_key:
        print("CSV 里没找到 apiKey（自动发现/--csv 均无效）"); return
    url = build_url(cred)
    print(f"API Key 已读取（不回显）。源={args.src} 目标={args.tgt}")
    print(f"端点 = {url}")

    if args.mode == "file":
        if not args.wav:
            print("file 模式需 --wav 指定 WAV"); return
        mode_file(api_key, url, args.src, args.tgt, args.wav, args.speaker,
                  not args.no_partial, args.vad_threshold, args.silence_ms,
                  sim_live=args.sim_live, force_after=args.force_after)
    else:
        mode_live(api_key, url, args.src, args.tgt, args.remote, args.self,
                  args.seconds, args.session_minutes, not args.no_partial, args.vad_threshold)


if __name__ == "__main__":
    main()
