# -*- coding: utf-8 -*-
"""两路采集（Stage 1 · src/audio/capture.py）。

方案 A（G1'）：远端 = 端点 Loopback 虚拟输入；自己 = 物理麦克风普通 input 流。
  - 必须回调模式（WASAPI loopback 端点不活跃时不产帧，阻塞 read 会卡死——PoC-0b）。
  - 每个回调把本块重采样成 16k 单声道 PCM16，连同 speaker 标记推入共享队列。
  - 队列满 → 丢最旧（背压 A12：最多积压 ≈ maxsize×块时长），计数供 UI/CLI 提示。

对外：
    cap = TwoChannelCapture(remote_idx, self_idx)
    cap.start()
    while ...:
        chunk = cap.next_chunk(timeout)   # (speaker, 16k_mono_pcm16_bytes)
    cap.stop()
"""
import queue
import time

import numpy as np
import pyaudiowpatch as pyaudio

from .resample import to_16k_mono_pcm16

SPEAKER_REMOTE = "remote"   # 远端（Teams 里其他人 / 扬声器出声）
SPEAKER_SELF = "self"       # 自己（本机麦克风）


class TwoChannelCapture:
    #: PyAudioWPatch WASAPI loopback 实测（2026-09，已修正，见下方注释）：
    #:   - 早期某自定义播放器出现「内容按 device_rate/2 生成却被按 device_rate 时钟送出」
    #:     的音高 2× 现象，曾用分割 2 重采样规避。
    #:   - 但 2026-09-02 真人 Teams 端点实测（divisor=2 → ASR 全空）证实：真实 Teams
    #:     端点是正常音高，必须 divisor=1 才能被 ASR 识别（divisor=2 语音整体翻倍 → 无法识别）。
    #: 故默认 = 1（真实 Teams 正常）。保留可配置：极少数自定义播放器若复现音高 2×，可设回 2。
    LOOPBACK_RATE_DIVISOR = 1

    def __init__(self, remote_idx, self_idx, chunk_frames=1600, max_backlog=100,
                 on_level=None, silence_threshold=0.03):
        """max_backlog：队列块数上限，每块 ≈ 0.1s → 100 ≈ 10s 积压上限（§A12）。

        on_level：可选回调 fn(speaker, rms_0_1)——GUI 电平条数据源（运行支撑，不改
        翻译逻辑）。rms 为 0..1 的归一化幅度（0.0=静音）。

        silence_threshold：静音门控（0..1）。未超阈值的块（首声道峰值 < 阈值）**不推入
        队列**，减少后端收到的噪声/静音/背景流——2026-09-02 诊断证实：干净远端单路 +
        静音过滤 → ASR 识别正常；全量推送（含环境音/两路重叠）→ 识别严重退化。
        默认 0.03（较低，避免误吞轻声）；传 None 关闭过滤（行为回到无条件推送）。"""
        self.remote_idx = remote_idx
        self.self_idx = self_idx
        self.chunk_frames = chunk_frames
        self.silence_threshold = silence_threshold
        self.q = queue.Queue(maxsize=max_backlog)
        self.dropped = 0
        self.silenced = 0
        self.on_level = on_level
        self._p = None
        self._remote = None
        self._self = None
        self._rinfo = self._sinfo = None
        self._rrate = self._srate = 0
        # 静音自己麦克风（GUI 可切换）：
        # 默认静音=False。为 True 时，自己（麦克风）通道采集块被丢弃不推入后端，
        # 即「GUI 里静音了就不会把本机声音写进记录/翻译」。真实的 Teams/麦克风不受影响。
        self._self_muted = False

    # ---- 内部：逐档降级打开一路 input ----
    def _open(self, idx, label, speaker):
        info = self._p.get_device_info_by_index(idx)
        is_loopback = bool(info.get("isLoopbackDevice"))
        rate = int(info["defaultSampleRate"])
        ch = info["maxInputChannels"] or (2 if is_loopback else 1)
        last_err = None
        for sr in (rate, 48000, 44100, 16000):
            # loopback 内容实际按 sr/divisor 生成（见类注释）
            src_rate = sr // self.LOOPBACK_RATE_DIVISOR if is_loopback else sr
            def _cb(in_data, _frames, _time_info, _status,
                    _speaker=speaker, _ch=ch, _src_rate=src_rate):
                try:
                    # 首声道峰值估算 0..1 幅度
                    stride = 2 * _ch
                    arr = in_data[::stride]
                    n = len(arr)
                    level = 0.0
                    if n:
                        a = np.frombuffer(arr, dtype=np.int16).astype(np.float32)
                        peak = float(np.max(np.abs(a)))
                        level = min(1.0, peak / 32768.0)
                    if self.on_level is not None:
                        try:
                            self.on_level(_speaker, level)
                        except Exception:
                            pass
                    # GUI 静音自己麦克风：self 通道整块丢弃不推送
                    if _speaker == SPEAKER_SELF and self._self_muted:
                        return (None, pyaudio.paContinue)
                    # 静音门控：低于阈值不推（降噪/去背景，防后端 ASR 退化）
                    thr = self.silence_threshold
                    if thr is not None and level < thr:
                        self.silenced += 1
                        return (None, pyaudio.paContinue)
                except Exception:
                    pass
                try:
                    data16 = to_16k_mono_pcm16(in_data, _src_rate, _ch)
                    if data16:
                        try:
                            self.q.put_nowait((_speaker, data16))
                        except queue.Full:
                            try:
                                self.q.get_nowait()  # 丢最旧
                                self.q.put_nowait((_speaker, data16))
                                self.dropped += 1
                            except Exception:
                                pass
                except Exception:
                    pass
                return (None, pyaudio.paContinue)
            try:
                s = self._p.open(format=pyaudio.paInt16, channels=ch, rate=sr,
                                 input=True, input_device_index=idx,
                                 frames_per_buffer=self.chunk_frames,
                                 stream_callback=_cb)
                return sr, s, info
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"打不开 {label} [{idx}]: {last_err}")

    # ---- 生命周期 ----
    def open(self):
        self._p = pyaudio.PyAudio()
        try:
            self._rrate, self._remote, self._rinfo = self._open(
                self.remote_idx, "远端Loopback", SPEAKER_REMOTE)
            self._srate, self._self, self._sinfo = self._open(
                self.self_idx, "自己麦克风", SPEAKER_SELF)
        except Exception:
            self.close()
            raise
        return self

    def start(self):
        self._remote.start_stream()
        self._self.start_stream()

    def next_chunk(self, timeout=0.3):
        """阻塞取一块；超时返回 None。返回 (speaker, bytes)。"""
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def set_self_muted(self, muted: bool):
        """静音/取消静音自己麦克风通道（线程安全，泵运行时可随时切换）。"""
        self._self_muted = bool(muted)

    @property
    def self_muted(self):
        return self._self_muted

    def set_silence_threshold(self, threshold):
        """实时调整静音门控阈值（0..1，None=关闭过滤）。采集回调每块都会读该值，线程安全。"""
        self.silence_threshold = None if threshold is None else float(threshold)

    def stats(self):
        return {"remote_rate": self._rrate, "self_rate": self._srate,
                "dropped": self.dropped, "silenced": self.silenced,
                "remote_name": self._rinfo["name"] if self._rinfo else "",
                "self_name": self._sinfo["name"] if self._sinfo else ""}

    def close(self):
        for s in (self._remote, self._self):
            if s is not None:
                try:
                    s.stop_stream()
                except Exception:
                    pass
                try:
                    s.close()
                except Exception:
                    pass
        self._remote = self._self = None
        if self._p is not None:
            try:
                self._p.terminate()
            except Exception:
                pass
            self._p = None
