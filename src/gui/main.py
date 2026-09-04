# -*- coding: utf-8 -*-
"""Stage 2 · PyQt6 桌面 GUI 入口（src/gui/main.py）。

启动方式（任意位置，文件夹可整体搬移）：
  pyforsub\\python.exe -m src.gui.main   （需在项目根目录下运行）
  或 pyforsub\\python.exe D:\\新位置\\src\\gui\\main.py

把 Stage 1 验证过的核心接进 GUI：
  - subtitle_window 悬浮双语字幕（半透明/置顶/穿透/拖动）
  - control_window 控制窗（设备下拉/开始停止/语言/清空/下载/电平/用量/回看）
  - controller 在后台线程运行音频泵 + 会话管理，Qt signal 桥回 GUI 线程

本层只负责装配与事件分发，不改翻译/采集逻辑。
"""
import os
import sys

# 路径全部相对本文件推导，文件夹整体搬移不影响（不再写死 D:\data\dsh_subtitle）
_THIS = os.path.dirname(os.path.abspath(__file__))          # .../src/gui
_PROJ = os.path.dirname(os.path.dirname(_THIS))             # 项目根
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from src.gui import config as GCONF
from src.gui.controller import load_cred, build_url, Controller
from src.gui.subtitle_window import SubtitleWindow
from src.gui.control_window import ControlWindow
from src.gui import exporter
from src.store import wal as walmod

# 旧默认凭据文件名仅作发现兜底（实际用 discover_cred_file 按内容自动找，名字随意）
DEFAULT_CSV = os.path.join(_PROJ, "默认业务空间-apiKey-7021723.csv")


class App:
    def __init__(self):
        self._cfg = GCONF.get_all()
        self._cred = self._try_cred()
        self._url = build_url(self._cred) if self._cred.get("apiKey") else ""
        self._controller = None
        self._thread = None
        self._turn_log = []         # 内存会议记录（final 轮）
        self._wal = None            # 本次会话 WAL（实时落盘）
        self._meeting_start = None
        self._running = False
        self._recent_rows = []      # 字幕窗显示用 rows
        self._restored = False      # 是否已从 .jsonl 恢复过旧记录
        self._marker_log = []       # [(ts, what)] 断线/重建标记（导出用，F-R4）
        self._hotkeys = None        # 全局热键管理器

        # 字幕窗
        self.sub = SubtitleWindow(self._cfg)
        self.sub.moved_sig.connect(self._save_sub_pos)

        # 控制窗
        self.ctl = ControlWindow(
            get_turns=lambda: self._turn_log,
            get_meeting_meta=lambda: (self._meeting_start,
                                      (self._meeting_start is not None and
                                       (__import__('time').time() - self._meeting_start) / 60.0) or 0.0),
            on_start=self._start,
            on_stop=self._stop,
            on_clear=self._clear,
            on_export=self._export,
            set_style=self._apply_style,
            persisted_state_changed=None,
            on_toggle_sub=self._hotkey_toggle_sub,
            on_self_mute=self._set_self_muted,
            on_noise_gate=self._set_noise_gate,
        )
        self._apply_style({})
        self._restore_latest()      # 崩溃恢复：从最近 .jsonl 拿回已出字幕（A7）

    # ---------------- WAL 崩溃恢复 ----------------
    def _restore_latest(self):
        """启动时把最近一份 .jsonl 恢复进内存（turn_log/字幕/回看）。"""
        path, records = walmod.load_latest()
        if not records:
            return
        from src.translate.events import Turn
        for rec in records:
            t = Turn(rec.get("user_item_id"))
            t.finalized = True
            t.speaker = rec.get("speaker", "?")
            t.abs_start = rec.get("abs_start")
            t.abs_end = rec.get("abs_end")
            t.asr_final = rec.get("asr", "")
            t.trans_final = rec.get("trans", "")
            t.src_lang = rec.get("src_lang", "")
            t.trans_lang = rec.get("trans_lang", "")
            self._turn_log.append(t)
        if self._meeting_start is None and self._turn_log:
            first_ts = self._turn_log[0].abs_start
            self._meeting_start = first_ts if first_ts else __import__('time').time()
        # 刷新 UI
        self._recent_rows = [self._turn_to_row(t) for t in self._turn_log[-self._cfg.get("sub_rows", 3):]]
        self.sub.set_rows(self._recent_rows)
        self.ctl.update_final_view(self._turn_log)
        self.ctl.set_status(f"已恢复上次记录 {len(self._turn_log)} 条")
        self._restored = True

    # ---------------- 凭据 ----------------
    def _try_cred(self):
        """找凭据 CSV：优先 config 的 cred_path → 否则在项目根自动发现（文件名随意）→
        最后回退旧的硬编码默认名。找不到返回 {}。"""
        try:
            from src.cred import load_cred, discover_cred_file
            path = GCONF.get_all().get("cred_path", "") or None
            found = discover_cred_file(preferred=path or DEFAULT_CSV)
            if found:
                return load_cred(found)
        except Exception:
            pass
        try:
            return load_cred(DEFAULT_CSV)
        except Exception:
            return {}

    # ---------------- 样式持久化 ----------------
    def _save_sub_pos(self, x, y):
        GCONF.save({"sub_pos": [x, y]})

    def _set_self_muted(self, muted):
        """GUI 静音自己麦克风：运行中直接改采集；否则由 Controller 启动时读取配置生效。"""
        if self._controller is not None:
            self._controller.set_self_muted(muted)

    def _set_noise_gate(self, threshold):
        """GUI 静默门控滑块：实时改采集回调阈值；未运行则下次启动由配置生效。"""
        if self._controller is not None:
            self._controller.set_silence_threshold(threshold)

    def _apply_style(self, kwargs):
        kw = {}
        keys = {"sub_bg_alpha": "bg_alpha", "sub_contrast": "contrast",
                "sub_font_pt": "font_pt", "sub_ontop": "ontop",
                "sub_clickthrough": "clickthrough", "sub_opacity": "opacity"}
        for k, v in kwargs.items():
            if k in keys:
                kw[keys[k]] = v
        if kw:
            self.sub.set_style(**kw)

    # ---------------- 生命周期 ----------------
    def _start(self, tgt):
        if self._controller is not None and self._running:
            return
        if not self._url:
            self.ctl.set_state("disconnected")
            self.ctl.set_status("API Key 未找到，无法启动")
            return
        ri, si = self.ctl.current_devices()
        if ri is None or si is None:
            self.ctl.set_status("请选择设备")
            return

        self._meeting_start = __import__('time').time()
        self._running = True

        # 若启动时恢复过旧记录，现在开始新会议 → 清掉内存/UI，开新文件（旧记录仍在其 .jsonl）
        if self._restored:
            self._turn_log = []
            self._recent_rows = []
            self.sub.set_rows([])
            self.ctl.update_final_view([])
            self._restored = False

        # 新会议 → 新建一份 .jsonl（不覆盖/续写恢复的旧文件）
        if self._wal is not None:
            self._wal.close()
        fname = walmod.start_filename(self._meeting_start)
        self._wal = walmod.Wal(os.path.join(walmod.sessions_dir(), fname))
        self.ctl.set_status(f"新建会议记录 {fname}")

        self._controller = Controller(self._url, self._cred.get("apiKey", ""),
                                      "auto", tgt, ri, si,
                                      vad_threshold=self._cfg.get("vad_threshold", 0.2),
                                      session_minutes=self._cfg.get("session_minutes", 30),
                                      silence_ms=self._cfg.get("silence_ms", 1200),
                                      force_finalize_after=self._cfg.get("force_finalize_after", 2.5),
                                      self_muted=self._cfg.get("self_muted", False),
                                      dry_run=self._cfg.get("dry_run", False),
                                      dry_fail_every=self._cfg.get("dry_fail_every", 0),
                                      mem_report_min=self._cfg.get("dry_mem_report_min", 10),
                                      silence_threshold=self._cfg.get("silence_threshold", 0.03))
        self._controller.partial.connect(self._on_partial)
        self._controller.final.connect(self._on_final)
        self._controller.usage.connect(self._on_usage)
        self._controller.vad.connect(self._on_vad)
        self._controller.status.connect(self.ctl.set_status)
        self._controller.level.connect(self.ctl.set_level)
        self._controller.closed.connect(lambda r: self._on_closed(r))
        self._controller.finished_run.connect(self._thread_finished)
        self._controller.disconnect_note.connect(self._on_disconnect_note)

        # 用普通 daemon 线程跑控制器（而非 QThread+moveToThread）。
        # 原因（Stage 2 实测）：QThread.started.connect(ctrl.run) 会把 run() 当直连
        # 信号在主线程执行 → 阻塞 Qt 事件循环（栈里 app.exec 直入 run）。普通线程跑的
        # run() 无限循环只向外 emit Qt 信号（跨线程自动 Queued 到 GUI），无需 worker
        # 再收 slot；stop() 用普通 bool 标志（GIL 保护），由泵循环检查退出，安全且不阻塞。
        import threading as _th
        self._thread = _th.Thread(target=self._controller.run, name="controller", daemon=True)
        self._thread.start()
        self.ctl.set_state("running")

    def _stop(self):
        if self._controller is not None and self._running:
            self._controller.stop()   # 置标志；run() 下一轮退出并优雅结束

    def _thread_finished(self, *a):
        self._running = False
        self._controller = None
        self._thread = None
        self.ctl.set_state("stopped")
        self.ctl.set_status("会话已停止")

    # ---------------- 事件处理（GUI 线程）----------------
    def _on_partial(self, turn):
        # 整行替换式更新（F-D7）：按 user_item_id 更新 subtitle rows
        rows = [r for r in self._recent_rows if r.get("uid") != turn.user_item_id]
        rows.append(self._turn_to_row(turn))
        self._recent_rows = rows[-self._cfg.get("sub_rows", 3):]
        self.sub.set_rows(self._recent_rows)

    def _on_final(self, turn, idx):
        # 译文后到的已定稿轮会重入 _on_final：按 user_item_id 替换而非追加，避免日志重复。
        existing = [i for i, t in enumerate(self._turn_log)
                    if t.user_item_id == turn.user_item_id]
        if existing:
            self._turn_log[existing[0]] = turn
        else:
            self._turn_log.append(turn)
        # WAL：每轮定稿立即落盘（F-R1，崩溃不丢已出字幕）。译文补全时同 uid 去重改写。
        if self._wal is not None:
            self._wal.append_turn(turn)
        # 定稿轮在字幕窗替换成定稿样式
        rows = [r for r in self._recent_rows if r.get("uid") != turn.user_item_id]
        row = self._turn_to_row(turn); row["finalized"] = True
        rows.append(row)
        self._recent_rows = rows[-self._cfg.get("sub_rows", 3):]
        self.sub.set_rows(self._recent_rows)
        # 仅首次定稿追加回看；译文更新轮（同一 uid 已在 log）只刷新字幕不重复回看行
        if not existing and turn.speaker is not None:
            self.ctl.append_final(turn.speaker, turn.abs_start, turn.asr_final, turn.trans_final)

    @staticmethod
    def _turn_to_row(turn):
        import time
        ts = time.strftime("%H:%M:%S", time.localtime(turn.abs_start)) if turn.abs_start else "--:--:--"
        return {"uid": turn.user_item_id,
                "speaker": turn.speaker, "time": ts,
                "asr": turn.asr_partial or turn.asr_final or "",
                "trans": turn.trans_partial or turn.trans_final or "",
                "finalized": turn.finalized}

    def _on_usage(self, tokens):
        self.ctl.set_usage(tokens)

    def _on_vad(self, ev, ms):
        pass  # 预留：可切换状态灯

    def _on_closed(self, reason):
        self.ctl.set_state("disconnected")
        self.ctl.set_status(f"会话断开: {reason}")

    def _on_disconnect_note(self, what):
        """断线/重建事件 → 写进 WAL 标记 + 导出用的 marker_log（F-R4）。"""
        ts = __import__('time').time()
        self._marker_log.append((ts, what))
        if self._wal is not None:
            self._wal.append_marker(what, ts)

    # ---------------- 全局热键（F-D9） ----------------
    def setup_hotkeys(self):
        """用主（控制）窗 hwnd 注册全局热键，配置来自 config/gui.json 的 hotkey_scheme。
        逐个检查 RegisterHotKey 返回值，把注册失败的具体键提示出来（便于诊断冲突）。
        默认 Ctrl+Alt+S/E/L/W（见 config.py _DEFAULTS）。"""
        try:
            from src.gui.hotkeys import (HotkeyManager, HotkeyFilter,
                                         vk_from_str, key_label)
            from src.gui.logging_setup import get_logger
            _log = get_logger("gui")
            mgr = HotkeyManager(self.ctl.winId())
            # (动作名, 说明) — 回调映射固定；键位取 config
            actions = [("toggle_run", "开始/暂停"), ("export", "导出"),
                       ("toggle_lang", "语言"), ("toggle_sub", "字幕")]
            cbs = {"toggle_run": self._hotkey_toggle_run, "export": self._hotkey_export,
                   "toggle_lang": self._hotkey_toggle_lang, "toggle_sub": self._hotkey_toggle_sub}
            scheme = self._cfg.get("hotkey_scheme") or {}
            ok_keys, fail_keys = [], []
            for hk_id, (act, label) in enumerate(actions, start=1):
                ent = scheme.get(act) or {}
                vk = vk_from_str(ent.get("vk", ""))
                ctrl = bool(ent.get("ctrl", True))
                alt = bool(ent.get("alt", True))
                if vk is None:
                    fail_keys.append(f"{label}(键名无效:{ent.get('vk')!r})")
                    _log.warning("热键 %s 配置键名无效: %r", act, ent.get("vk"))
                    continue
                if mgr.register(hk_id, vk, ctrl, alt, cbs[act]):
                    ok_keys.append(f"{key_label(vk, ctrl, alt)}({label})")
                else:
                    fail_keys.append(f"{key_label(vk, ctrl, alt)}({label})")
            self._hotkey_filter = HotkeyFilter(mgr)
            from PyQt6.QtWidgets import QApplication as _QA
            _QA.instance().installNativeEventFilter(self._hotkey_filter)
            self._hotkeys = mgr
            base = "全局热键："
            if ok_keys:
                base += "启用 " + " · ".join(ok_keys)
            if fail_keys:
                base += f"；注册失败 {len(fail_keys)} 个: " + " ".join(fail_keys)
                _log.warning("热键注册失败: %s", " ".join(fail_keys))
            self.ctl.set_status(base)
        except Exception as e:
            self.ctl.set_status(f"全局热键启动失败: {e}")

    def teardown_hotkeys(self):
        if self._hotkeys is not None:
            try:
                self._hotkeys.unregister_all()
            except Exception:
                pass
            self._hotkeys = None

    def _hotkey_toggle_run(self):
        if self._running:
            self._stop()
            self.ctl.set_status("热键：暂停")
        else:
            tgt = self.ctl.lang_cb.currentData() or self.ctl._tgt
            self._start(tgt)

    def _hotkey_export(self):
        self.ctl._download_clicked()

    def _hotkey_toggle_lang(self):
        cur = self.ctl.lang_cb.currentIndex()
        self.ctl.lang_cb.setCurrentIndex(1 - cur)  # 触发 _lang_changed

    def _hotkey_toggle_sub(self):
        self.sub.setVisible(not self.sub.isVisible())

    # ---------------- 清空 / 导出 ----------------
    def _clear(self):
        # F-D14：只清内存/字幕/回看，不删已落盘 .jsonl（防误删；本会话文件仍保留）
        self._turn_log = []
        self._marker_log = []
        self._recent_rows = []
        self.sub.set_rows([])
        self.ctl.update_final_view([])
        self.ctl.set_status("已清空（落盘 .jsonl 仍保留）")

    def _export(self, path, fmt):
        if not self._turn_log and not self._marker_log:
            return False
        start = self._meeting_start
        dur = (__import__('time').time() - start) / 60.0 if start else 0.0
        try:
            exporter.export(self._turn_log, path, meeting_start=start, dur_min=dur,
                            markers=self._marker_log)
            return True
        except Exception:
            return False


def run_gui():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    # 日志初始化（GUI + 后台线程共用）——先于任何业务逻辑。
    from src.gui.logging_setup import setup_logging, get_logger
    setup_logging()
    _log = get_logger("gui")
    _log.info("=== GUI 启动 ===")
    # PyQt6 Windows 透明必备：让顶层窗口 surface 带 alpha channel，
    # 否则 WA_TranslucentBackground 的背景会渲染成实心黑（Qt 官方/社区公认坑）。
    from PyQt6.QtGui import QSurfaceFormat
    _sf = QSurfaceFormat()
    _sf.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(_sf)
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    w = App()
    w.sub.show()
    w.ctl.show()
    w.setup_hotkeys()

    # 系统托盘（pystray）
    _quit_clicked = {"v": False}
    def _on_quit():
        _quit_clicked["v"] = True
        app.quit()
    tray = None
    try:
        from src.gui.tray import TrayIcon
        tray = TrayIcon(on_toggle_sub=w._hotkey_toggle_sub,
                        on_hide_sub=w.sub.hide, on_quit=_on_quit)
        tray.start()
    except Exception as e:
        _log.warning("托盘功能未启用: %r", e)

    rc = app.exec()
    if tray is not None:
        try:
            tray.stop()
        except Exception:
            pass
    w.teardown_hotkeys()
    if w._controller is not None and w._running:
        w._controller.stop()
    # 关 WAL（flush + close；崩溃也能靠逐行 flush 保住已出字幕）
    if w._wal is not None:
        try:
            w._wal.close()
        except Exception:
            pass
    sys.exit(rc)


if __name__ == "__main__":
    run_gui()