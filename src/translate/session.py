# -*- coding: utf-8 -*-
"""会话管理与实时翻译业务（Stage 1 · src/translate/session.py）。

职责：
  - 连接 / session.update / 音频推送 / 优雅结束（session.finish → session.finished）。
  - 事件解析 + previous_item_id 配对原文↔译文（§6.4）。
  - speaker 标注：合并流按段记录每块来源，speech_stopped 时按 audio 窗口内
    占多数采样数的来源判定（R14：重叠极少数，允许偶尔错乱；初版不混音）。
  - 绝对时间戳：stream_t0（本会话首块推入墙钟）+ audio_*_ms（§6.5）。
  - 用量累计（response.done → response.usage.total_tokens）。
  - 优雅重建 rebuild()（§6.6 F-T9：每 30 分钟）与意外断线重连。

实测事件流（PoC-0c 文件模式，2026-09 抓取）：
  speech_started {item_id=ASR项, audio_start_ms}
  response.created → response.output_item.added(翻译项)
  conversation.item.created {previous_item_id=ASR项, item.id=翻译项, role=assistant}
  response.text.text / response.text.done {item_id=翻译项}
  conversation.item.created {item.id=ASR项, role=assistant, content:[{type:input_audio}]}
  conversation.item.input_audio_transcription.text/.completed {item_id=ASR项}
  response.done {response.usage}
  speech_stopped {item_id=ASR项, audio_end_ms}
  → 轮次以 ASR 项 id 为 key；翻译项 previous_item_id → ASR 项 id。

⚠️ 实测关键：`same_language_skip_options.skip_text:true` 会让 en→zh 翻译整体不返回
   （A/B 复验确认）——Stage 1 配置中**不设置**该字段（§6.7 方案 A 需据此订正）。
"""
import json
import logging
import threading
import time

from .client import RealtimeConnection
from .events import Turn, RATE, text_plus_stash, usage_total

_LOG = logging.getLogger("session")

ASR_MODEL = "qwen3-asr-flash-realtime"
# 静默判定时长（ms）：真人讲话常有 <1s 停顿（“I, today we are going to…”）。
# 1000ms 会把这类停顿当句边界切碎（2026-09 实战 40 轮大量 Uh/Um/So 碎片论）。
# 1200ms 在“抗碎”与“断句延迟”间折中：>1.2s 停顿才判定该句结束。
SILENCE_MS = 1200
PREFIX_PADDING_MS = 300
THRESHOLD = 0.2


def _friendly_close_reason(exc):
    """把底层异常转成可读的中文断开原因（导出/日志用）。"""
    text = str(exc)
    cls = exc.__class__.__name__ if exc is not None else ""
    low = (text + " " + cls).lower()
    if "closed" in low or "connection to remote host was lost" in low:
        return "连接被服务端/网络关闭（长连接重置）"
    if "timeout" in low or "timed out" in low:
        return "连接超时"
    if "reset" in low or "connectionreset" in low:
        return "连接被重置"
    if exc is None:
        return "服务端正常关闭"
    return f"连接断开: {cls}({text})"


class RealtimeSession:
    def __init__(self, url, api_key, src, tgt,
                 on_partial=None, on_final=None, on_usage=None,
                 on_vad=None, on_close=None, on_ready=None,
                 vad_threshold=THRESHOLD, silence_ms=None):
        self.url = url
        self.api_key = api_key
        self.src = src            # 仅日志/显示；协议自动检测源语言
        self.tgt = tgt
        self.vad_threshold = vad_threshold
        self.silence_ms = int(silence_ms) if silence_ms else SILENCE_MS
        self.on_partial = on_partial   # fn(turn)
        self.on_final = on_final       # fn(turn)
        self.on_usage = on_usage       # fn(total_tokens, last_usage)
        self.on_vad = on_vad           # fn("started"/"stopped", ms)
        self.on_close = on_close       # fn(exc_or_None)
        self.on_ready = on_ready       # fn()

        self.conn = None
        self.turn_log = []         # 已定稿轮次，跨会话重建保留（会议记录）
        self.turns = {}            # ASR_item_id -> Turn（当前会话）
        self.assistant_map = {}    # 翻译项id -> ASR项id
        self.order = []            # 当前会话 ASR_item_id 顺序
        self.stream_t0 = None      # 本会话首块音频推入墙钟（§6.5）
        self.sent_samples = 0      # 已成功推送采样点（漂移校验）
        self.segments = []         # [(start_sample, end_sample, speaker)]
        self.chunk_times = []      # [(start_sample, capture_wall_time)] 采样位置→墙钟（§6.5）
        self.last_speaker = "?"
        # 用量（修正 2026-09-03）：服务端 response.done 的 usage.total_tokens 是
        # **本会话累计值**（含前文上下文重算，实测第二次已含全部已推音频），不是增量。
        # 故会话内取"最后一次 total"（last-wins），跨会话（rebuild/重连）把该段定格值
        # 累进 usage_base。旧实现每次 += 导致显示虚高数倍（GUI 7251 vs 云端 2769）。
        self.usage_base = 0       # 已结束会话段（rebuild 前）定格用量
        self.usage_session = 0    # 当前会话段最后一次 total（服务端累计口径）
        self.usage_tokens = 0     # 对外总用量 = usage_base + usage_session
        self.usage_last = None
        self.dead = False
        self.finished_ok = False
        self.error = ""
        self.last_close_reason = ""   # 记录最近一次断线原因（诊断用）
        self._finish_evt = threading.Event()
        self._session_no = 0
        self._push_n = 0

    # ---------------- 生命周期 ----------------
    def open(self):
        self._session_no += 1
        self.conn = RealtimeConnection(
            self.url, self.api_key,
            on_message=self._handle, on_close=self._on_close)
        self.conn.connect()
        self.conn.start_receiver()
        self._send_session_update()
        if self.on_ready:
            self.on_ready()

    def _send_session_update(self):
        self.conn.send({
            "type": "session.update",
            "session": {
                "modalities": ["text"],
                "sample_rate": RATE,
                "input_audio_format": "pcm",
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": self.vad_threshold,
                    "silence_duration_ms": self.silence_ms,
                    "prefix_padding_ms": PREFIX_PADDING_MS,
                },
                "input_audio_transcription": {"model": ASR_MODEL},
                "translation": {"language": self.tgt},
                # ⚠️ 实测：不能加 same_language_skip_options.skip_text=true，否则 en→zh 无翻译
            },
        })

    def append_audio(self, data: bytes, speaker: str):
        if self.conn is None or not self.conn.connected:
            raise ConnectionError("会话未连接")
        n = len(data) // 2
        start = self.sent_samples
        self.segments.append((start, start + n, speaker))
        self.chunk_times.append((start, time.time()))  # 采集墙钟（合并流位置→墙钟）
        self.sent_samples += n
        self._push_n += 1
        if self._push_n % 300 == 0:   # 每 ~30s(0.1s/块) 打一次统计
            from collections import Counter
            c = Counter(sp for (_a, _b, sp) in self.segments[-3000:])
            _LOG.info("audio.push sp_dist=%s segs=%d", dict(c), len(self.segments))
        self.conn.send_audio(data)

    def finish_graceful(self, timeout=15):
        """发 session.finish，等 session.finished，再关（F-T10，否则末尾丢失）。
        关前先强制定稿未定稿 partial（2026-09-03：停止时也保留最后一句字幕）。"""
        try:
            self.finalize_all_pending()
        except Exception:
            pass
        if self.conn is not None and self.conn.connected:
            self.conn.send_finish()
            self._finish_evt.wait(timeout)
        self.close()

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        self.dead = True

    def rebuild(self):
        """优雅重建（§6.6 / F-T9）：finish→finished→重开→重设 stream_t0。
        turn_log 保留（会议记录跨会话）；当前会话未定稿轮次**先强制定稿落盘**再丢弃
        （2026-09-03 决策：断线不丢已看到字幕）。用量：把本段累计 total 定格进
        usage_base（服务端新会话从 0 重新累计）。"""
        # 先落盘未定稿 partial（断线重建前保留，避免丢字）
        try:
            self.finalize_all_pending()
        except Exception:
            pass
        if self.conn is not None and self.conn.connected:
            self.conn.send_finish()
            self._finish_evt.wait(10)
        # 定格本段用量后再关（新段从 0 累计，总 = base + 新段）
        self.usage_base += self.usage_session
        self.usage_session = 0
        self.usage_tokens = self.usage_base
        self.close()
        self.turns = {}
        self.assistant_map = {}
        self.order = []
        self.stream_t0 = None
        self.sent_samples = 0
        self.segments = []
        self.chunk_times = []
        self.last_speaker = "?"
        self.error = ""
        self.finished_ok = False
        self.dead = False
        self._finish_evt.clear()
        self.open()

    # ---------------- 事件处理 ----------------
    def _handle(self, msg):
        t = msg.get("type")
        if t in ("session.created", "session.updated"):
            return
        if t == "conversation.item.created":
            item = msg.get("item") or {}
            iid = item.get("id")
            prev = msg.get("previous_item_id")
            if iid and prev:
                # 翻译项 → 关联到 ASR 项
                self.assistant_map[iid] = prev
                if prev in self.turns:
                    self.turns[prev].asst_item_id = iid
            elif iid:
                self._ensure_turn(iid)   # ASR 项（role=assistant, content=input_audio, 无 prev）
        elif t == "conversation.item.input_audio_transcription.text":
            turn = self._ensure_turn(msg.get("item_id"))
            if turn and not turn.finalized:
                turn.asr_partial = text_plus_stash(msg)
                if msg.get("language"):
                    turn.src_lang = msg["language"]
                self._emit_partial(turn)
                _LOG.info("asr.partial item=%s sp=%s: %.60s",
                          turn.user_item_id, turn.speaker, turn.asr_partial)
        elif t == "conversation.item.input_audio_transcription.completed":
            turn = self._ensure_turn(msg.get("item_id"))
            if turn:
                turn.asr_final = msg.get("transcript") or ""
                turn.asr_done = True
                if msg.get("language"):
                    turn.src_lang = msg["language"]
                self._set_speaker(turn)
                self._emit_partial(turn)
                self._maybe_finalize(turn)
                _LOG.info("asr.completed item=%s sp=%s: %.60s",
                          turn.user_item_id, turn.speaker, turn.asr_final)
        elif t in ("response.text.text", "response.audio_transcript.text"):
            turn = self._resolve_turn(msg.get("item_id"))
            if turn and not turn.finalized:
                turn.trans_partial = text_plus_stash(msg)
                if msg.get("language"):
                    turn.trans_lang = msg["language"]
                self._emit_partial(turn)
        elif t in ("response.text.done", "response.audio_transcript.done"):
            turn = self._resolve_turn(msg.get("item_id"))
            if turn:
                turn.trans_final = msg.get("text") or msg.get("transcript") or ""
                turn.trans_done = True
                # 译文可能晚于 ASR 定稿到达：若轮已按 ASR 定稿，原地补译文并刷新浮窗/WAL
                if turn.finalized:
                    self._finalize(turn)
                else:
                    self._maybe_finalize(turn)
        elif t == "response.done":
            usage = (msg.get("response") or {}).get("usage") or {}
            u = usage_total(usage)
            if u:
                # last-wins：服务端 total 是本会话累计值，直接记为本段最新（不再累加）
                self.usage_session = u
                self.usage_tokens = self.usage_base + self.usage_session
                self.usage_last = usage
                if self.on_usage:
                    self.on_usage(self.usage_tokens, usage)
            # 兜底：response 结束仍无 translation 事件 → 定稿最近 asr_done 轮
            for iid in reversed(self.order):
                tr = self.turns[iid]
                if tr.asr_done and not tr.finalized:
                    self._finalize(tr)
                    break
        elif t == "input_audio_buffer.speech_started":
            ms = msg.get("audio_start_ms")
            iid = msg.get("item_id")
            turn = self._ensure_turn(iid) if iid else None
            if turn is not None:
                turn.audio_start_ms = ms
            if self.on_vad:
                self.on_vad("started", ms)
            _LOG.info("VAD started ms=%s item=%s last_sp=%s", ms, iid, self.last_speaker)
        elif t == "input_audio_buffer.speech_stopped":
            ms = msg.get("audio_end_ms")
            iid = msg.get("item_id")
            turn = self._ensure_turn(iid) if iid else None
            if turn is not None:
                turn.audio_end_ms = ms
            sp = self._majority_speaker(turn.audio_start_ms if turn else None, ms)
            if sp:
                self.last_speaker = sp
            if turn is not None:
                self._set_speaker(turn)
            if self.on_vad:
                self.on_vad("stopped", ms)
            _LOG.info("VAD stopped ms=%s item=%s majority_sp=%s segments=%d",
                      ms, iid, sp, len(self.segments))
        elif t == "session.finished":
            self.finished_ok = True
            self._finish_evt.set()
            self.dead = True
            self.last_close_reason = "session.finished（服务端正常结束）"
            if self.on_close:
                self.on_close(None)
        elif t == "error":
            self.error = json.dumps(msg.get("error"), ensure_ascii=False)

    def _on_close(self, exc):
        self.dead = True
        self.last_close_reason = _friendly_close_reason(exc)
        if self.on_close:
            self.on_close(exc)

    # ---------------- 轮次 / 配对 / 定稿 ----------------
    def _ensure_turn(self, asr_item_id):
        if not asr_item_id:
            return None
        turn = self.turns.get(asr_item_id)
        if turn is None:
            turn = Turn(asr_item_id, speaker=self.last_speaker)
            self.turns[asr_item_id] = turn
            self.order.append(asr_item_id)
        return turn

    def _resolve_turn(self, asst_item_id):
        uid = self.assistant_map.get(asst_item_id)
        if uid:
            return self.turns.get(uid)
        # 兜底：无映射时按最近未定稿轮次（rare）
        for iid in reversed(self.order):
            tr = self.turns[iid]
            if tr.asst_item_id is None:
                tr.asst_item_id = asst_item_id
                return tr
        return None

    def _set_speaker(self, turn):
        if turn.speaker in ("?", None):
            turn.speaker = self._majority_speaker(turn.audio_start_ms, turn.audio_end_ms) or "?"
        if turn.speaker in ("?", None) and self.last_speaker != "?":
            turn.speaker = self.last_speaker

    def _majority_speaker(self, start_ms, end_ms):
        if start_ms is None:
            return None
        s0 = max(0, int(start_ms * RATE / 1000.0))
        e0 = int(end_ms * RATE / 1000.0) if end_ms else self.sent_samples
        e0 = min(e0, self.sent_samples)
        if e0 <= s0:
            return None
        counts = {}
        for (st, en, sp) in self.segments:
            ov = max(0, min(en, e0) - max(st, s0))
            if ov > 0:
                counts[sp] = counts.get(sp, 0) + ov
        if not counts:
            return None
        winner = max(counts, key=counts.get)
        _LOG.info("majority win=%s counts=%s window=%d..%d", winner, counts, s0, e0)
        return winner

    def _maybe_finalize(self, turn):
        if turn.finalized:
            return
        # ASR 定稿即出字幕（避免死等译文致字幕不出/不落盘）。
        # 译文通常紧随其后到达（response.text.done → 上面分支补 finalized 轮）。
        if turn.asr_done:
            self._finalize(turn)

    def _sample_wall(self, sample_pos):
        """合并流采样位置 → 采集墙钟（§6.5，兼容两路合并 2× 速率）。
        回退：最近块墙钟 + 剩余位置按 16k 折算。"""
        if not self.chunk_times:
            return None
        lo, hi, best = 0, len(self.chunk_times) - 1, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.chunk_times[mid][0] <= sample_pos:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        st0, t0 = self.chunk_times[best]
        if best + 1 < len(self.chunk_times):
            st1, t1 = self.chunk_times[best + 1]
            if st1 > st0:
                return t0 + (sample_pos - st0) / (st1 - st0) * (t1 - t0)
        return t0 + (sample_pos - st0) / RATE

    def _ms_to_wall(self, ms):
        if ms is None:
            return None
        return self._sample_wall(int(ms * RATE / 1000.0))

    def _finalize(self, turn):
        # 首次定稿：置标志、补时间戳、入 turn_log、触发 final。
        # 译文后到（已 finalized 重入）：仅刷新 on_final/on_partial（补充/更新译文），不重复入 log。
        already = turn.finalized
        if not already:
            turn.finalized = True
            self._set_speaker(turn)
            if turn.abs_start is None:
                turn.abs_start = self._ms_to_wall(turn.audio_start_ms)
            if turn.abs_end is None:
                turn.abs_end = self._ms_to_wall(turn.audio_end_ms)
            self.turn_log.append(turn)
        elif turn.trans_done:
            turn.finalized = True
        _LOG.info("FINALIZE item=%s sp=%s asr_done=%s trans_done=%s asr=%.50s",
                  turn.user_item_id, turn.speaker, turn.asr_done, turn.trans_done,
                  turn.asr_final or turn.asr_partial or "")
        if self.on_final:
            self.on_final(turn)

    def _emit_partial(self, turn):
        if turn is not None:
            turn.last_partial_ts = time.time()   # 兜底定稿计时基准
        if self.on_partial:
            self.on_partial(turn)

    # ---- 客户端静默兜底定稿（F-R1 落盘兜底）----
    # 服务端 server_vad 需静默 SILENCE_MS(1200ms) 才判定一段话结束并发 completed。
    # 若说话人连续长时不停顿（如朗读 >20s 无停顿），该轮永远停在 partial、不落盘
    # （实测 2026-09-02：字幕持续变长但 sessions 空）。此方法由泵循环周期调用：
    # 对有 partial 内容且超过 force_after 秒无更新的未定稿轮，强制定稿（asr 取 partial，
    # asr_done 置位）→ 立即落盘出字幕；译文后到会走已定稿轮补全。
    def force_finalize_overdue(self, force_after=2.5):
        if not self.turns:
            return
        now = time.time()
        for iid in list(self.order):
            tr = self.turns.get(iid)
            if tr is None or tr.finalized:
                continue
            if tr.asr_partial and not tr.asr_done and now - tr.last_partial_ts >= force_after:
                tr.asr_final = tr.asr_partial
                tr.asr_done = True
                self._finalize(tr)
                break   # 每次最多强制一轮，避免突发批量（下一轮泵继续）

    def finalize_all_pending(self):
        """重建/重连/停止前：把当前所有未定稿但有 partial 内容的轮次全部强制定稿，
        避免断线清空 turns 时丢掉用户已看到但未落盘的字幕（2026-09-03 需求：
        '断线重建前保留并强制定稿'）。译文若随后到达，走已定稿轮补全路径。"""
        if not self.turns:
            return
        for iid in list(self.order):
            tr = self.turns.get(iid)
            if tr is None or tr.finalized:
                continue
            if tr.asr_partial and not tr.asr_done:
                tr.asr_final = tr.asr_partial
                tr.asr_done = True
                self._finalize(tr)
