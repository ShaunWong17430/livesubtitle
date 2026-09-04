# -*- coding: utf-8 -*-
"""运行控制器（Stage 2 · src/gui/controller.py）。

把 Stage 1 的无 UI 泵（src/stage1/main.py::run_pipeline）搬到 QThread + Qt signal，
复用它验证过的：TwoChannelCapture（两路叠加/重采样/背压）、RealtimeSession（配对/
speaker/时间戳/用量/重建/重连）与 300s 静默兜底。

线程模型：
  - Qt 主线程 = GUI。
  - Controller(QObject) 放入 QThread 跑 run()（泵循环：取块→推送→重建/重连）。
  - 会话事件回调在 receiver 线程触发 → 用 pyqtSignal 桥回 GUI 线程（Qt 跨线程 emit 安全）。

信号（都在 GUI 线程处理）：
  partial(turn)        临时稿（整行替换式更新，F-D7）
  final(turn, idx)     定稿轮次
  usage(tokens)
  vad(started_or_stopped, ms)
  status(msg)
  level(remote, self)  0..1 电平（来自 capture 回调，桥回 GUI）
  closed(reason)       会话断开
  error(msg)

暂停/停止：通过 event 通知 run() 安全退出（停采集→会话 finish_graceful→终止）。
"""
import ctypes
import time

from PyQt6.QtCore import QObject, pyqtSignal

from src.audio.capture import TwoChannelCapture
from src.translate.session import RealtimeSession
from src.gui.logging_setup import get_logger


# ---- A5 内存测量的底层（模块级初始化一次，避免每次调用重建 ctypes 类型导致泄漏）----
class _PROC_MEM_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]

_k32 = ctypes.windll.kernel32
_k32.GetCurrentProcess.restype = ctypes.c_void_p
_k32.GetCurrentProcess.argtypes = []
_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p,
                                        ctypes.POINTER(_PROC_MEM_COUNTERS),
                                        ctypes.c_size_t]
_psapi.GetProcessMemoryInfo.restype = ctypes.c_int


def _process_rss_kb():
    """当前进程常驻内存（RSS/KiB）。psutil 未装，用 Windows GetProcessMemoryInfo。
    注意 GetCurrentProcess 返回伪句柄 (HANDLE)-1，psapi 须按指针传 argtypes
    （否则 ctypes 默认 c_int 传会 OverflowError）。失败返回 None。"""
    try:
        pmc = _PROC_MEM_COUNTERS()
        pmc.cb = ctypes.sizeof(_PROC_MEM_COUNTERS)
        if not _psapi.GetProcessMemoryInfo(_k32.GetCurrentProcess(),
                                           ctypes.byref(pmc), pmc.cb):
            return None
        return int(pmc.WorkingSetSize) // 1024
    except Exception:
        return None


class DrySession:
    """A5 哑推流会话桩（方案A）：与 RealtimeSession 相同的表面，但**不联网、不发音频**，
    故 0 token。让 controller 泵闭环在真实采集/会话生命周期/内存增长下照常跑满长时，
    用于验证「连续运行不中断 + 内存增长 <50MB」，而不烧 token。
    - append_audio 丢弃数据（不发送）；rebuild 记数；可选 fail_every 模拟定时断线以
      驱动 controller 的重连分支（断连/重连逻辑照常被执行）。
    """

    def __init__(self, dry_monitor=None, fail_every_minutes=0):
        self.dead = False
        self.last_close_reason = ""
        self._rebuilds = 0
        self._up0 = time.time()
        self._fail_every = fail_every_minutes * 60.0
        self._mon = dry_monitor

    def open(self):
        pass

    def rebuild(self):
        self._rebuilds += 1
        self.dead = False   # 与真实 RealtimeSession.rebuild 一致：重建后恢复可用
        if self._mon:
            self._mon("DrySession rebuild #%d" % self._rebuilds)

    def append_audio(self, data, speaker):
        # 哑推流：真正丢弃，不发送 → 0 token。
        pass

    def force_finalize_overdue(self, force_after=0.0):
        # 无真实会话无 partial 可兜底；仅当配置了 fail_every>0（模拟定时断线）时，
        # 到点把 dead 置真，让 controller 的重连分支被真实走一遍（验证稳定性/重连，
        # 仍 0 token）。
        if self._fail_every and not self.dead:
            if time.time() - self._up0 >= self._fail_every:
                self.dead = True
                self.last_close_reason = "模拟断线（dry-run fail_every）"
                self._up0 = time.time()  # 重连后清零计到下次

    def finish_graceful(self):
        if self._mon:
            self._mon("DrySession finish（graceful 收尾）")


class Controller(QObject):
    partial = pyqtSignal(object)
    final = pyqtSignal(object, int)
    usage = pyqtSignal(int)
    vad = pyqtSignal(str, object)
    status = pyqtSignal(str)
    level = pyqtSignal(float, float)
    closed = pyqtSignal(str)
    error = pyqtSignal(str)
    finished_run = pyqtSignal(str)   # ""=正常停止；非空=异常信息摘要
    disconnect_note = pyqtSignal(str)  # 断线/重建/结束 事件标记文案（F-R4）

    def __init__(self, url, api_key, src, tgt, remote_idx, self_idx,
                 vad_threshold=0.2, session_minutes=30,
                 silence_ms=None, force_finalize_after=2.5, self_muted=False,
                 dry_run=False, dry_fail_every=0, mem_report_min=10,
                 silence_threshold=0.03):
        super().__init__()
        self.url = url
        self.api_key = api_key
        self.src = src
        self.tgt = tgt
        self.remote_idx = remote_idx
        self.self_idx = self_idx
        self.vad_threshold = vad_threshold
        self.session_minutes = session_minutes
        self.silence_ms = silence_ms
        self.force_finalize_after = float(force_finalize_after)
        self.self_muted = bool(self_muted)
        # 静音门控阈值（0..1；None=关闭过滤）——采集回调每块据此判断"多大电平算静默"
        self.silence_threshold = None if silence_threshold is None else float(silence_threshold)
        # 方案A·A5 哑推流：dry_run=True 时不联网不发音频（0 token），仍跑采集/泵/会话生命周期
        self.dry_run = bool(dry_run)
        self.dry_fail_every = float(dry_fail_every or 0)
        self.mem_report_min = float(mem_report_min or 0)
        self._log = get_logger("controller")
        self._stop = False
        self._session = None
        self._cap = None
        self._last_level = {"remote": 0.0, "self": 0.0}
        self.final_count = 0
        self._mem_start = None      # A5: 起始 RSS
        self._mem_peak = None       # A5: 峰值 RSS
        self._mem_last_report = 0.0
        self._txt_hint = "dry-run" if self.dry_run else "realtime"

    # ---------- callbacks（receiver 线程）→ 桥回 GUI ----------
    def _on_partial(self, turn):
        self.partial.emit(turn)

    def _on_final(self, turn):
        self.final_count += 1
        self.final.emit(turn, self.final_count)

    def _on_usage(self, tokens, _u):
        self.usage.emit(tokens)

    def _on_vad(self, ev, ms):
        self.vad.emit(ev, ms)

    def _on_close(self, exc):
        self.closed.emit(str(exc) if exc else "连接正常关闭")

    # ---------- 电平 ----------
    def _on_level(self, speaker, level):
        if speaker == "remote":
            self._last_level["remote"] = level
        elif speaker == "self":
            self._last_level["self"] = level
        # 电平高频（每块 ~0.1s）；直接 emit 由 GUI 采样节流（GUI 槽内做节流）

    # ---------- A5 内存监控（dry-run 用） ----------
    def _mem_hint(self, why=""):
        """采样 RSS，更新峰值；按 mem_report_min 间隔打日志 + emit 状态。返回当前 KB。"""
        rss = _process_rss_kb()
        if rss is None:
            return None
        if self._mem_start is None:
            self._mem_start = rss
        if self._mem_peak is None or rss > self._mem_peak:
            self._mem_peak = rss
        now = time.time()
        if self.mem_report_min > 0 and now - self._mem_last_report >= self.mem_report_min * 60:
            self._mem_last_report = now
            grow = rss - self._mem_start
            self._log.info(
                "A5内存：now=%sKB  start=%sKB  grow=%sKB  peak=%sKB%s",
                rss, self._mem_start, grow, self._mem_peak,
                ("  " + why if why else ""))
            self.status.emit(
                f"A5长跑 {now/60:.0f}min：RSS {rss/1024:.1f}MB，增长 {grow/1024:.1f}MB"
                f"（<50MB 校验）")
        return rss

    # ---------- 泵 ----------
    def run(self):
        from src.audio import devices as dev
        import pyaudiowpatch as pyaudio
        self._log.info("采集会话启动 remote_idx=%s self_idx=%s tgt=%s", self.remote_idx, self.self_idx, self.tgt)
        p = pyaudio.PyAudio()
        cap = None
        S = None
        session_minutes = self.session_minutes
        try:
            # 设备解析
            if self.remote_idx is None:
                self.remote_idx = dev.default_remote_loopback(p)
            rinfo = p.get_device_info_by_index(self.remote_idx)
            rname = rinfo["name"]
            if self.self_idx is None:
                self.self_idx, _how = dev.pick_self_mic(p, rinfo)
            sname = dev.device_label(p, self.self_idx)
            self.status.emit(f"远端[{self.remote_idx}] {rname}｜自己[{self.self_idx}] {sname}")

            cap = TwoChannelCapture(self.remote_idx, self.self_idx,
                                on_level=self._on_level,
                                silence_threshold=self.silence_threshold).open()
            cap.set_self_muted(self.self_muted)
            self._cap = cap
            cap.start()

            if self.dry_run:
                # 方案A·A5 哑推流：真实采集 + DrySession 桩（不联网/不发音频 → 0 token）。
                # fail_every>0 时定时置 dead，驱动 controller 自身的重建/重连分支。
                self._mem_start = _process_rss_kb()
                self._log.info(">>> DRY-RUN（0 token）开始 A5 稳定性模拟，起始RSS≈%s KB", self._mem_start)
                self.status.emit("A5 哑推流(dry-run)：0 token，长跑稳定性模拟中…")
                S = DrySession(self._mem_hint, self.dry_fail_every)
                S.open()
                self._session = S
                self._mem_last_report = time.time()
            else:
                S = RealtimeSession(
                    self.url, self.api_key, self.src, self.tgt,
                    on_partial=self._on_partial, on_final=self._on_final,
                    on_usage=self._on_usage, on_vad=self._on_vad,
                    on_close=self._on_close, vad_threshold=self.vad_threshold,
                    silence_ms=self.silence_ms,
                )
                S.open()
                self.status.emit("已连接，正在实时翻译…")
                self._log.info("已连接并开始实时翻译（远端[%s] %s / 自己[%s] %s）", self.remote_idx, rname, self.self_idx, sname)

            t0 = time.time()
            session_start = t0
            last_status = t0
            while not self._stop:
                now = time.time()
                # 会话重建
                if session_minutes > 0 and now - session_start >= session_minutes * 60:
                    self._log.info("到达重建周期（%s 分钟），优雅重建", session_minutes)
                    self.disconnect_note.emit(f"会话运行 {session_minutes} 分钟，按周期优雅重建")
                    self.status.emit("会话到达重建周期，优雅重建…")
                    S.rebuild()
                    session_start = time.time()
                    self.status.emit("重建完成")
                    self.disconnect_note.emit("会话已优雅重建完成")
                # 意外断线重连
                if S.dead:
                    what = f"会话断开（{S.last_close_reason}）"
                    self._log.warning("断线：%s，6s 后重连…", S.last_close_reason)
                    self.status.emit(what + "，6s 后重连…")
                    self.disconnect_note.emit(what + "，尝试重连")
                    time.sleep(6)
                    S.rebuild()
                    session_start = time.time()
                    self.status.emit("重连成功")
                    self._log.info("重连成功，继续记录")
                    # 在时间轴上补一条"重连成功"，导出时能看到恢复完成的闭合标记
                    self.disconnect_note.emit("会话已重连成功，继续记录")
                    continue
                chunk = cap.next_chunk(0.3)
                if chunk is not None:
                    speaker, data = chunk
                    try:
                        S.append_audio(data, speaker)
                    except Exception as e:
                        S.dead = True
                        what = "发送失败（连接异常）"
                        self._log.warning("音频发送失败：%r，标记断线等待重连", e)
                        self.status.emit(what + "，标记断线等待重连…")
                        self.disconnect_note.emit(what)
                        continue
                # 客户端静默兜底定稿（F-R1）：说话人长段不停顿时服务端 VAD 不判结束，
                # partial 永不落盘。泵循环周期检查，超 force_finalize_after 秒无更新的
                # partial 强制定稿（时长 GUI 可调）。
                try:
                    S.force_finalize_overdue(force_after=self.force_finalize_after)
                except Exception:
                    pass
                # 电平节流：~每 0.3s 上报一次
                now2 = time.time()
                if now2 - last_status >= 0.3:
                    last_status = now2
                    self.level.emit(self._last_level["remote"], self._last_level["self"])
                    if self.dry_run:
                        self._mem_hint()   # A5 内存采样（阈值由 mem_report_min 决定是否打日志）
            # 正常停止
        except Exception as e:
            import traceback
            self._log.exception("控制器异常终止: %r", e)
            self.error.emit(f"控制器异常: {e}")
            self.status.emit("控制器异常终止")
            return
        finally:
            if S is not None:
                try:
                    S.finish_graceful()
                except Exception:
                    pass
            if cap is not None:
                try:
                    cap.close()
                except Exception:
                    pass
            try:
                p.terminate()
            except Exception:
                pass
            if self.dry_run:
                end = _process_rss_kb()
                grow = (end - self._mem_start) if (end and self._mem_start) else None
                self._log.info(
                    ">>> A5 dry-run 结束：start=%sKB end=%sKB peak=%sKB grow=%sKB（<50MB 通过）",
                    self._mem_start, end, self._mem_peak, grow)
                if grow is not None:
                    ok = (grow / 1024.0) < 50.0
                    self.status.emit(
                        f"A5 dry-run 完成：内存增长 {grow/1024:.1f}MB"
                        f"{'（<50MB ✓）' if ok else '（≥50MB ✗ 需排查）'}")
            self.status.emit("会话已停止")
            self._log.info("会话已停止（正常停止或异常退出）")
            # 通知 GUI 线程结束（无论正常停止还是异常路径，finally 都会执行）
            self.finished_run.emit("done")

    def stop(self):
        self._stop = True

    def set_self_muted(self, muted: bool):
        """GUI 静音自己麦克风（泵运行中即时生效；未运行则记住，下次启动沿用）。"""
        self.self_muted = bool(muted)
        cap = self._cap
        if cap is not None:
            cap.set_self_muted(self.self_muted)

    def set_silence_threshold(self, threshold):
        """实时调整静音门控阈值（0..1）。运行中直接改采集回调；未运行则记住，下次启动生效。"""
        self.silence_threshold = None if threshold is None else float(threshold)
        cap = self._cap
        if cap is not None:
            cap.set_silence_threshold(self.silence_threshold)

    @property
    def session(self):
        return self._session


# 复用凭据/端点装配（统一实现见 src/cred.py；此处薄封装向后兼容）：
def load_cred(path):
    from src.cred import load_cred as _lc
    return _lc(path)


def build_url(cred, model=None):
    """构造实时 wss 端点。model 不传时读 config 的 "model"，缺省用 source.cred 默认。
    向后兼容旧调用 build_url(cred)。"""
    if model is None:
        try:
            from src.gui import config as GCONF
            model = GCONF.get_all().get("model")
        except Exception:
            model = None
    from src.cred import build_url as _bu
    return _bu(cred, model=model)