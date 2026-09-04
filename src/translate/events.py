# -*- coding: utf-8 -*-
"""事件字段解析与轮次数据结构（Stage 1 · src/translate/events.py）。

事件形态（PoC-0c / 需求书 §6.4 实测确认）：
  conversation.item.created → item A(user) / item B(assistant, previous_item_id=A)
  conversation.item.input_audio_transcription.text      {item_id, text, stash, language}
  conversation.item.input_audio_transcription.completed {item_id, transcript, language}
  response.text.text                                    {item_id, text, stash}
  response.text.done                                    {item_id, text}
  response.done                                         {response:{usage, ...}}
  input_audio_buffer.speech_started                     {audio_start_ms}
  input_audio_buffer.speech_stopped                     {audio_end_ms}
  session.created / session.updated / session.finished / error
"""
import time

RATE = 16000


def text_plus_stash(msg):
    """增量事件把 text（已确认）+ stash（临时）合并，作为当前整行显示。"""
    return (msg.get("text") or "") + (msg.get("stash") or "")


class Turn:
    """一段 VAD 语音 = 一个轮次。原文(ASR) 与译文(trans) 通过 previous_item_id 配对。"""
    __slots__ = ("user_item_id", "asst_item_id", "speaker",
                 "asr_partial", "asr_final", "asr_done",
                 "trans_partial", "trans_final", "trans_done",
                 "audio_start_ms", "audio_end_ms",
                 "abs_start", "abs_end", "finalized", "created",
                 "src_lang", "trans_lang", "last_partial_ts")

    def __init__(self, user_item_id, speaker="?"):
        self.user_item_id = user_item_id
        self.asst_item_id = None
        self.speaker = speaker
        self.asr_partial = ""
        self.asr_final = ""
        self.asr_done = False
        self.trans_partial = ""
        self.trans_final = ""
        self.trans_done = False
        self.audio_start_ms = None
        self.audio_end_ms = None
        self.abs_start = None      # 墙钟（time.time() 绝对秒，UTC epoch）
        self.abs_end = None
        self.finalized = False
        self.created = time.time()
        self.src_lang = ""         # ASR 检测到的源语言（UI 同语言跳过显示用）
        self.trans_lang = ""       # 目标语言（服务端译文语种，UI 展示用）
        self.last_partial_ts = 0.0 # 最近一次 partial 更新的墙钟（客户端静默兜底定稿用）


def usage_total(usage_dict):
    """从 usage 结构取 token 总数。

    实测结构（response.done.response.usage）：
      {total_tokens, input_tokens, output_tokens,
       input_tokens_details: {text_tokens, audio_tokens},
       output_tokens_details: {text_tokens}}
    total_tokens = input_tokens + output_tokens，details 是其展开 → 直接取 total_tokens。
    """
    v = usage_dict.get("total_tokens")
    if isinstance(v, (int, float)):
        return int(v)
    i = usage_dict.get("input_tokens")
    o = usage_dict.get("output_tokens")
    if isinstance(i, (int, float)) and isinstance(o, (int, float)):
        return int(i + o)
    return 0
