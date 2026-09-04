# -*- coding: utf-8 -*-
"""WS 底层连接（Stage 1 · src/translate/client.py）。

PoC-0c 踩坑落地：
  - websocket-client 用 `create_connection`（同步握手，立即可 send）+ 独立接收线程。
  - 必须 ping_interval > ping_timeout（30 > 10），否则 WebSocketException 连接秒断。
"""
import json
import threading
import time

import websocket


class RealtimeConnection:
    """单条到百炼 realtime 网关的 WS 连接（不关心业务逻辑）。"""

    def __init__(self, url, api_key, on_message=None, on_close=None):
        self.url = url
        self.api_key = api_key
        self.on_message = on_message      # fn(dict)
        self.on_close = on_close          # fn(exc_or_None)
        self.ws = None
        self._stop = threading.Event()
        self._rth = None
        self.closed_by_us = False
        self.connected = False

    # ---- 连接 ----
    def connect(self, timeout=60):
        self.ws = websocket.create_connection(
            self.url,
            header=[f"Authorization: Bearer {self.api_key}"],
            timeout=timeout,
            ping_interval=30,
            ping_timeout=10,
        )
        self.connected = True

    def start_receiver(self):
        self._stop.clear()
        self._rth = threading.Thread(target=self._receiver, daemon=True)
        self._rth.start()

    def _receiver(self):
        # 静默期（无语音→无数据帧）时 recv() 会按 socket timeout 抛 WebSocketTimeoutException，
        # 但连接仍然存活——**不能当成断线**（否则静默 60s 就会自断会话，实测确认：纯静音测试
        # 会话每 ~60s 死一次，恰为 create_connection(timeout=60)）。处理：超时→继续等；
        # 连续静默超过 300s 才判定断线（兜底半开连接）。
        silent_since = None
        while not self._stop.is_set():
            try:
                raw = self.ws.recv()
                silent_since = None
            except websocket.WebSocketTimeoutException:
                if silent_since is None:
                    silent_since = time.time()
                elif time.time() - silent_since > 300:
                    if not self._stop.is_set() and not self.closed_by_us:
                        self.connected = False
                        if self.on_close:
                            self.on_close("静默超过300s，判定断线")
                    break
                continue
            except Exception as e:
                if not self._stop.is_set() and not self.closed_by_us:
                    self.connected = False
                    if self.on_close:
                        self.on_close(e)
                break
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if self.on_message:
                try:
                    self.on_message(msg)
                except Exception:
                    pass

    # ---- 发送 ----
    def send(self, obj):
        self.ws.send(json.dumps(obj, ensure_ascii=False))

    def send_audio(self, pcm16_bytes):
        import base64
        self.ws.send(json.dumps({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm16_bytes).decode("ascii"),
        }))

    def send_finish(self):
        try:
            self.ws.send(json.dumps({"type": "session.finish"}))
        except Exception:
            pass

    # ---- 关闭 ----
    def close(self):
        self._stop.set()
        self.closed_by_us = True
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
        self.connected = False
