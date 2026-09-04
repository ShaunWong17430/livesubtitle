# -*- coding: utf-8 -*-
"""调试：原始事件流 dump（不经过 session 业务逻辑）。"""
import io, sys, json, time, base64, threading, wave
import numpy as np

sys.path.insert(0, r"D:\data\dsh_subtitle")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import websocket

DEFAULT_CSV = r"D:\data\dsh_subtitle\默认业务空间-apiKey-7021723.csv"
MODEL = "qwen3.5-livetranslate-flash-realtime"
ASR_MODEL = "qwen3-asr-flash-realtime"

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

def load_wav_pcm16(path, target_rate=16000):
    with wave.open(path, "rb") as w:
        rate = w.getframerate(); ch = w.getnchannels(); width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1).astype(np.int16)
    if rate != target_rate:
        n_out = int(len(a) * target_rate / rate)
        a = np.interp(np.linspace(0, len(a)-1, n_out), np.arange(len(a)), a.astype(np.float32)).astype(np.int16)
    return a.tobytes()

def main():
    cred = load_cred(DEFAULT_CSV)
    api_key = cred["apiKey"]
    ws_id = cred.get("workspaceId")
    url = f"wss://{ws_id}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model={MODEL}"
    print("连接…")
    ws = websocket.create_connection(url, header=[f"Authorization: Bearer {api_key}"],
                                     timeout=60, ping_interval=30, ping_timeout=10)
    print("已连接，发 session.update")
    ws.send(json.dumps({
        "type": "session.update",
        "session": {
            "modalities": ["text"],
            "sample_rate": 16000,
            "input_audio_format": "pcm",
            "turn_detection": {"type": "server_vad", "threshold": 0.2,
                               "silence_duration_ms": 1000, "prefix_padding_ms": 300},
            "input_audio_transcription": {"model": ASR_MODEL},
            "translation": {"language": "zh"},
        },
    }, ensure_ascii=False))

    stop = threading.Event()
    def recv_loop():
        while not stop.is_set():
            try:
                raw = ws.recv()
            except Exception as e:
                print("[recv] end:", e); break
            try:
                msg = json.loads(raw)
            except Exception:
                print("[recv] unparseable:", raw[:200]); continue
            t = msg.get("type")
            if t in ("response.done", "session.updated"):
                print("<<" , t, json.dumps(msg, ensure_ascii=False)[:1600], flush=True)
            elif t.startswith("response") or "translation" in t:
                print("<<", t, json.dumps(msg, ensure_ascii=False)[:400], flush=True)
            else:
                print("<<", t, flush=True)
    th = threading.Thread(target=recv_loop, daemon=True)
    th.start()

    data = load_wav_pcm16(r"D:\data\dsh_subtitle\16k_english.wav")
    chunks = [data[i:i+1920] for i in range(0, len(data), 1920)]
    print(f"喂 {len(chunks)} 块 …")
    for c in chunks:
        ws.send(json.dumps({"type": "input_audio_buffer.append",
                            "audio": base64.b64encode(c).decode("ascii")}))
    time.sleep(2)
    print("发 session.finish")
    ws.send(json.dumps({"type": "session.finish"}))
    time.sleep(15)
    stop.set()
    ws.close()
    print("done")

if __name__ == "__main__":
    main()
