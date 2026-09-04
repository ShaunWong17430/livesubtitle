# -*- coding: utf-8 -*-
"""Stage 0 · PoC-0c：百炼 qwen3.5-livetranslate-flash-realtime 实时翻译验证。"""
import sys, io, json, time, base64, argparse, threading
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DEFAULT_CSV = r"D:\data\dsh_subtitle\默认业务空间-apiKey-7021723.csv"
MODEL = "qwen3.5-livetranslate-flash-realtime"
ASR_MODEL = "qwen3-asr-flash-realtime"
RATE = 16000


def load_cred(path):
    cred = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or "," not in line:
                continue
            k, _, v = line.partition(",")
            cred[k.strip()] = v.strip()
    return cred


def build_url(cred):
    ws_id = cred.get("workspaceId") or cred.get("apiHost", "").split(".")[0]
    return f"wss://{ws_id}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model={MODEL}"


def session_cfg(src, tgt):
    return {
        "type": "session.update",
        "session": {
            "modalities": ["text"],
            "sample_rate": RATE,
            "input_audio_format": "pcm",
            "translation": {"language": tgt},
            "input_audio_transcription": {"language": src, "model": ASR_MODEL},
        },
    }


def connect_mode(api_key, url, src, tgt):
    import websocket
    print(f"连接: {url}")
    ws = websocket.create_connection(url, header=[f"Authorization: Bearer {api_key}"], timeout=20)
    print("✔ WebSocket 已连接")
    ws.send(json.dumps(session_cfg(src, tgt), ensure_ascii=False))
    print("✔ 已发送 session.update")
    got = {}
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            msg = json.loads(ws.recv())
        except Exception as e:
            print("  [recv] 异常/超时:", e)
            break
        t = msg.get("type")
        got[t] = msg
        print("  <<", t, flush=True)
        if t in ("session.updated", "error"):
            break
    errs = got.get("error")
    updated = "session.updated" in got
    print("\n--- 事件流小结 ---")
    print("  session.created :", "✔" if "session.created" in got else "—")
    print("  session.updated :", "✔" if updated else "—")
    if errs:
        print("  错误事件:", json.dumps(errs.get("error"), ensure_ascii=False))
    print("  ✅ 接受" if updated else ("  ❌ 拒绝" if errs else "  ⚠️ 超时"))
    try:
        ws.send(json.dumps({"type": "session.finish"}))
        print("✔ 已发送 session.finish")
        dl = time.time() + 10
        fin = False
        while time.time() < dl:
            try:
                m = json.loads(ws.recv())
            except Exception:
                break
            print("  <<", m.get("type"))
            if m.get("type") == "session.finished":
                fin = True
                break
        print("✅ 收到 session.finished" if fin else "⚠️ 未收到 session.finished")
    except Exception as e:
        print("finish 阶段异常:", e)
    finally:
        try:
            ws.close()
        except Exception:
            pass
        print("✔ 连接已关闭")


# ===SPLIT===


def _load_wav_pcm16(path, target_rate=16000):
    import wave
    import numpy as np
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
        n_out = int(len(a) * target_rate / rate)
        a = np.interp(np.linspace(0, len(a) - 1, n_out), np.arange(len(a)), a.astype(np.float32)).astype(np.int16)
    return target_rate, a.tobytes()


def _iter_pcm16(data_bytes, chunk_frames=960):
    step = chunk_frames * 2
    for i in range(0, len(data_bytes), step):
        yield data_bytes[i:i + step]


def _run_wsapp(url, api_key, src, tgt, chunk_iter):
    """用 create_connection（同步握手，连接立即可用于 send）+ 独立接收线程。
    返回汇总 dict。"""
    import websocket
    ws = websocket.create_connection(url, header=[f"Authorization: Bearer {api_key}"],
                                     timeout=60, ping_interval=30, ping_timeout=10)
    print("✔ WebSocket 已连接（同步握手完成）", flush=True)
    ws.send(json.dumps(session_cfg(src, tgt), ensure_ascii=False))
    print("✔ 已发送 session.update", flush=True)

    stop = threading.Event()
    out = {"asr": "", "trans": "", "error": "", "finished": False}
    asr_finals, trans_finals = [], []

    def receiver():
        asr_live, trans_live = {}, {}
        while not stop.is_set():
            try:
                message = ws.recv()
            except Exception as e:
                if not stop.is_set():
                    print("  [recv] 结束:", e, flush=True)
                break
            try:
                msg = json.loads(message)
            except Exception:
                continue
            t = msg.get("type")
            if t == "conversation.item.input_audio_transcription.text":
                item = msg.get("item_id")
                combined = (msg.get("text") or "") + (msg.get("stash") or "")
                if asr_live.get(item) != combined:
                    asr_live[item] = combined
                    print(f"  [原文流] {combined or '(空)'}", flush=True)
            elif t == "conversation.item.input_audio_transcription.completed":
                txt = msg.get("transcript") or ""
                asr_finals.append(txt)
                print(f"  [原文终] {txt}", flush=True)
            elif t == "response.text.text":
                item = msg.get("item_id")
                combined = (msg.get("text") or "") + (msg.get("stash") or "")
                if trans_live.get(item) != combined:
                    trans_live[item] = combined
                    print(f"  [译文流] {combined or '(空)'}", flush=True)
            elif t == "response.text.done":
                txt = msg.get("text") or ""
                trans_finals.append(txt)
                print(f"  [译文终] {txt}", flush=True)
            elif t == "response.audio_transcript.text":
                item = msg.get("item_id")
                combined = (msg.get("text") or "") + (msg.get("stash") or "")
                if trans_live.get(item) != combined:
                    trans_live[item] = combined
                    print(f"  [译文流] {combined or '(空)'}", flush=True)
            elif t == "response.audio_transcript.done":
                txt = msg.get("transcript") or ""
                trans_finals.append(txt)
                print(f"  [译文终] {txt}", flush=True)
            elif t == "input_audio_buffer.speech_started":
                print("  [VAD] 语音开始", flush=True)
            elif t == "input_audio_buffer.speech_stopped":
                print("  [VAD] 语音结束", flush=True)
            elif t == "session.finished":
                out["finished"] = True
                print("  [会话] finished", flush=True)
                break
            elif t == "error":
                out["error"] = json.dumps(msg.get("error"), ensure_ascii=False)
                print("  [错误]", out["error"], flush=True)
        out["asr"] = " ".join(x for x in asr_finals if x)
        out["trans"] = " ".join(x for x in trans_finals if x)

    rth = threading.Thread(target=receiver, daemon=True)
    rth.start()
    try:
        for chunk in chunk_iter:
            if chunk:
                try:
                    ws.send(json.dumps({"type": "input_audio_buffer.append",
                                        "audio": base64.b64encode(chunk).decode("ascii")}))
                except Exception as e:
                    print("  发送音频块失败:", e, flush=True)
                    break
            else:
                time.sleep(0.05)
        try:
            ws.send(json.dumps({"type": "session.finish"}))
            print("✔ 已发送 session.finish，等待 finished…", flush=True)
        except Exception as e:
            print("发送 finish 失败:", e, flush=True)
        rth.join(timeout=15)
    finally:
        stop.set()
        try:
            ws.close()
        except Exception:
            pass
    return out


def file_mode(api_key, url, src, tgt, wav_path):
    rate, data = _load_wav_pcm16(wav_path)
    dur = len(data) / (rate * 2)
    print(f"✔ 读取 {wav_path}: {rate}Hz 单声道 PCM16，{dur:.1f}s")
    chunks = list(_iter_pcm16(data))
    print(f"  切分 {len(chunks)} 块。喂入 API…")
    out = _run_wsapp(url, api_key, src, tgt, chunks)
    print("\n--- 文件测试小结 ---")
    print(f"  [原文累积] {out['asr'] or '(空)'}")
    print(f"  [译文累积] {out['trans'] or '(空)'}")
    if out["error"]:
        print(f"  错误: {out['error']}")
    print("  session.finished:", "✔" if out["finished"] else "—（未收到）")


def live_mode(api_key, url, src, tgt, seconds, mic_idx):
    """实时麦克风采集，喂给 _run_wsapp。返回汇总。"""
    import pyaudiowpatch as pyaudio
    import queue
    p = pyaudio.PyAudio()
    stream = None
    q = queue.Queue(maxsize=200)
    try:
        if mic_idx is None:
            info = p.get_default_input_device_info()
            mic_idx = int(info["index"])
            print(f"  (使用默认麦克风 [{mic_idx}] {info['name']})", flush=True)
        info = p.get_device_info_by_index(mic_idx)
        ch = info["maxInputChannels"] or 1
        for sr in (RATE, 48000, 44100):
            try:
                stream = p.open(format=pyaudio.paInt16, channels=ch, rate=sr,
                                input=True, input_device_index=mic_idx,
                                frames_per_buffer=960, stream_callback=lambda d, *_: (
                                    (q.put_nowait(d), (None, pyaudio.paContinue))[1]
                                    if not q.full() else (None, pyaudio.paContinue)))
                break
            except Exception:
                continue
        if stream is None:
            print("打不开麦克风，请检查 --mic"); return
        stream.start_stream()
        print(f"✔ 麦克风 [{mic_idx}] 已开启.{seconds}s 内请用英文说话…", flush=True)

        def gen():
            t0 = time.time()
            while time.time() - t0 < seconds:
                try:
                    yield q.get(timeout=0.4)
                except Exception:
                    continue

        out = _run_wsapp(url, api_key, src, tgt, gen())
        stream.stop_stream(); stream.close()
        print("\n--- live 小结 ---")
        print(f"  [原文累积] {out['asr'] or '(空)'}")
        print(f"  [译文累积] {out['trans'] or '(空)'}")
        if out["error"]:
            print(f"  错误: {out['error']}")
    finally:
        if stream is not None:
            try:
                stream.stop_stream(); stream.close()
            except Exception:
                pass
        p.terminate()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["connect", "live", "file"], default="connect")
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--mic", type=int, default=None)
    ap.add_argument("--src", default="en")
    ap.add_argument("--tgt", default="zh")
    ap.add_argument("wav", nargs="?", default=None, help="file 模式用的 WAV 路径")
    args = ap.parse_args()

    cred = load_cred(args.csv)
    api_key = cred.get("apiKey")
    if not api_key:
        print("CSV 里没找到 apiKey"); return
    url = build_url(cred)
    print("API Key 已从 CSV 读取（不回显）。源=%s 目标=%s" % (args.src, args.tgt))
    print("端点 =", url)

    if args.mode == "connect":
        connect_mode(api_key, url, args.src, args.tgt)
    elif args.mode == "file":
        if not args.wav:
            print("file 模式需指定 WAV 路径"); return
        file_mode(api_key, url, args.src, args.tgt, args.wav)
    else:
        live_mode(api_key, url, args.src, args.tgt, args.seconds, args.mic)


if __name__ == "__main__":
    main()