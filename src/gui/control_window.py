# -*- coding: utf-8 -*-
"""控制窗（Stage 2 · src/gui/control_window.py）。

满足 F-D8 / F-D10 / F-D12 / F-D13 / F-D14 / F-D15：
  - 开始/停止、目标语言切换（zh/en）、设备选择（远端/麦克风下拉，切换即重建）、
    清空记录（确认框，不自删已落盘）、下载会议记录（QFileDialog + 三格式）、
    电平条（远端/自我）、用量显示、连接状态灯、最近 N 条回看（可复制）。
"""
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QComboBox, QHBoxLayout, QVBoxLayout,
    QGridLayout, QGroupBox, QFileDialog, QMessageBox, QListWidget,
    QListWidgetItem,
    QApplication, QSlider, QCheckBox, QSpinBox, QDoubleSpinBox, QProgressBar,
)

from src.gui import config as GCONF
from src.gui import exporter
from src.audio import devices as dev

SPEAKER_ZH = {"remote": "远端", "self": "自己"}


def _speaker_zh(sp):
    return SPEAKER_ZH.get(sp, sp or "?")


class ControlWindow(QWidget):
    def __init__(self, get_turns, get_meeting_meta, on_start, on_stop,
                 on_clear, on_export, set_style, persisted_state_changed=None,
                 on_toggle_sub=None, on_self_mute=None, on_noise_gate=None):
        """回调都来自 main 层（访问 controller/字幕窗）。"""
        super().__init__()
        self._get_turns = get_turns
        self._get_meeting_meta = get_meeting_meta   # () -> (start_ts, dur)
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_clear = on_clear
        self._on_export = on_export                 # (path, fmt) -> bool
        self._set_style = set_style                 # (kwargs dict) -> subtitle style
        self._persist_state_changed = persisted_state_changed
        self._on_toggle_sub = on_toggle_sub or (lambda: None)
        self._on_self_mute = on_self_mute or (lambda v: None)
        self._on_noise_gate = on_noise_gate or (lambda v: None)
        self._cfg = GCONF.get_all()
        self._recent_log = []                        # [(uid, speaker, ts, asr, trans)]
        self._recent_uid_item = {}                   # uid -> QListWidgetItem（译文补全时原地更新）
        self._tgt = self._cfg.get("tgt", "zh")

        self.setWindowTitle("会议字幕控制")
        # 2026-09-03：左右分栏——左侧控制、右侧宽屏会议记录回看（可一下看很多行）。
        self.resize(900, 600)
        self._build_ui()
        self._apply_initial_state()
        self._refresh_devices()

    # ---------------- UI 搭建 ----------------
    def _build_ui(self):
        # 左右分栏（2026-09-03 用户需求）：左=控制，右=回看（占宽、高，可看很多行）。
        root = QHBoxLayout(self)
        left = QVBoxLayout()          # 左侧控制堆
        left.setSpacing(6)

        # ---- 状态行 ----
        top = QHBoxLayout()
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color:#b0b0b0; font-size:16px;")
        self.status_label = QLabel("未启动")
        top.addWidget(self.status_dot)
        top.addWidget(self.status_label, 1)
        self.usage_label = QLabel("用量 0 tok")
        top.addWidget(self.usage_label)
        left.addLayout(top)

        # ---- 设备选择（F-D13）----
        devbox = QGroupBox("音频设备")
        dg = QGridLayout(devbox)
        dg.addWidget(QLabel("远端(Loopback):"), 0, 0)
        self.remote_cb = QComboBox()
        self.remote_cb.currentIndexChanged.connect(self._dev_changed)
        dg.addWidget(self.remote_cb, 0, 1)
        dg.addWidget(QLabel("麦克风(自己):"), 1, 0)
        self.self_cb = QComboBox()
        self.self_cb.currentIndexChanged.connect(self._dev_changed)
        dg.addWidget(self.self_cb, 1, 1)
        self.ck_selfmute = QCheckBox("静音自己(录音不采本机声音)")
        self.ck_selfmute.setChecked(bool(self._cfg.get("self_muted", False)))
        self.ck_selfmute.toggled.connect(self._self_mute_changed)
        dg.addWidget(self.ck_selfmute, 2, 0, 1, 2)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self._refresh_devices)
        dg.addWidget(btn_refresh, 3, 1, alignment=Qt.AlignmentFlag.AlignLeft)
        left.addWidget(devbox)

        # ---- 运行控制 ----
        run = QHBoxLayout()
        self.btn_start = QPushButton("开始")
        self.btn_start.clicked.connect(self._start_clicked)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_clicked)
        self.btn_running = QPushButton("运行中")
        self.btn_running.setEnabled(False)
        self.btn_running.setVisible(False)
        run.addWidget(self.btn_start)
        run.addWidget(self.btn_stop)
        run.addWidget(self.btn_running)
        left.addLayout(run)

        # ---- 目标语言（F-D8）----
        lang = QHBoxLayout()
        lang.addWidget(QLabel("目标语言:"))
        self.lang_cb = QComboBox()
        self.lang_cb.addItem("英文→中文 (zh)", "zh")
        self.lang_cb.addItem("中文→英文 (en)", "en")
        self.lang_cb.currentIndexChanged.connect(self._lang_changed)
        lang.addWidget(self.lang_cb, 1)
        self.lang_info = QLabel("切换需停止后生效")
        self.lang_info.setStyleSheet("color:#888; font-size:10px;")
        lang.addWidget(self.lang_info)
        left.addLayout(lang)

        # ---- 录制行为（静默判定 / 落盘兜底，GUI 可调，下次开始生效）----
        rec = QGroupBox("录制行为")
        rg = QGridLayout(rec)
        rg.addWidget(QLabel("静默判定(ms):"), 0, 0)
        self.sp_silence = QSpinBox(); self.sp_silence.setRange(200, 5000)
        self.sp_silence.setSingleStep(100)
        self.sp_silence.setValue(int(self._cfg.get("silence_ms", 1200)))
        self.sp_silence.valueChanged.connect(self._rec_changed)
        self.sp_silence.setToolTip("停顿超过该时长才判一段话结束。越大抗碎、断句越慢。")
        rg.addWidget(self.sp_silence, 0, 1)
        rg.addWidget(QLabel("落盘兜底(秒):"), 1, 0)
        self.sp_force = QDoubleSpinBox(); self.sp_force.setRange(0.5, 30.0)
        self.sp_force.setSingleStep(0.5); self.sp_force.setDecimals(1)
        self.sp_force.setValue(float(self._cfg.get("force_finalize_after", 2.5)))
        self.sp_force.valueChanged.connect(self._rec_changed)
        self.sp_force.setToolTip("停顿超过该时长的长段朗读仍不出字幕时，客户端强制定稿落盘。")
        rg.addWidget(self.sp_force, 1, 1)
        rg.addWidget(QLabel("静默门控(电平):"), 2, 0)
        self.sl_noise = QSlider(Qt.Orientation.Horizontal)
        self.sl_noise.setRange(0, 100)  # 0=关闭过滤；1..100 → 0.01..1.00
        thr = float(self._cfg.get("silence_threshold", 0.03) or 0)
        self.sl_noise.setValue(int(round(min(1.0, max(0.0, thr)) * 100)))
        self.sl_noise.valueChanged.connect(self._noise_changed)
        self.sl_noise.setToolTip("峰值电平低于该值(0-100%)的音频视为静默被丢弃，不进后端。"
                                 "调高→更激进滤噪（可能吞轻声）；调低/0→全保留（可能带环境噪声）。实时生效。")
        rg.addWidget(self.sl_noise, 2, 1)
        self._lbl_noise = QLabel("")
        self._lbl_noise.setStyleSheet("color:#888; font-size:10px;")
        rg.addWidget(self._lbl_noise, 3, 0, 1, 2)
        self._refresh_noise_label()
        rec_info = QLabel("下次开始生效")
        rec_info.setStyleSheet("color:#888; font-size:10px;")
        rg.addWidget(rec_info, 4, 0, 1, 2)
        left.addWidget(rec)

        # ---- 电平条（F-D8）----
        lv = QGroupBox("电平")
        lg = QGridLayout(lv)
        lg.addWidget(QLabel("远端:"), 0, 0)
        self.pb_remote = QProgressBar(); self.pb_remote.setRange(0, 100); self.pb_remote.setTextVisible(False)
        lg.addWidget(self.pb_remote, 0, 1)
        lg.addWidget(QLabel("自己:"), 1, 0)
        self.pb_self = QProgressBar(); self.pb_self.setRange(0, 100); self.pb_self.setTextVisible(False)
        lg.addWidget(self.pb_self, 1, 1)
        left.addWidget(lv)

        # ---- 字幕样式（F-D11）----
        sty = QGroupBox("字幕样式")
        sg = QGridLayout(sty)
        sg.addWidget(QLabel("整体不透明:"), 0, 0)
        self.sl_winop = QSlider(Qt.Orientation.Horizontal); self.sl_winop.setRange(30, 100); self.sl_winop.setValue(int(float(self._cfg.get("sub_opacity", 0.85)) * 100))
        self.sl_winop.valueChanged.connect(lambda v: self._style_changed("sub_opacity", v / 100.0))
        sg.addWidget(self.sl_winop, 0, 1)
        sg.addWidget(QLabel("衬底α(0-255):"), 1, 0)
        self.sl_alpha = QSlider(Qt.Orientation.Horizontal); self.sl_alpha.setRange(0, 255); self.sl_alpha.setValue(self._cfg.get("sub_bg_alpha", 28))
        self.sl_alpha.valueChanged.connect(lambda v: self._style_changed("sub_bg_alpha", v))
        sg.addWidget(self.sl_alpha, 1, 1)
        sg.addWidget(QLabel("高对比:"), 2, 0)
        self.cb_contrast = QComboBox(); self.cb_contrast.addItems(["关", "中", "强"])
        self.cb_contrast.setCurrentIndex(self._cfg.get("sub_contrast", 0))
        self.cb_contrast.currentIndexChanged.connect(lambda i: self._style_changed("sub_contrast", i))
        sg.addWidget(self.cb_contrast, 2, 1)
        sg.addWidget(QLabel("字体pt:"), 3, 0)
        frow = QHBoxLayout()
        self.btn_font_minus = QPushButton("−"); self.btn_font_minus.setFixedWidth(28)
        self.btn_font_minus.clicked.connect(lambda: self._font_step(-1))
        self.sp_font = QSpinBox(); self.sp_font.setRange(12, 72); self.sp_font.setValue(self._cfg.get("sub_font_pt", 26))
        self.sp_font.valueChanged.connect(lambda v: self._style_changed("sub_font_pt", v))
        self.btn_font_plus = QPushButton("+"); self.btn_font_plus.setFixedWidth(28)
        self.btn_font_plus.clicked.connect(lambda: self._font_step(1))
        frow.addWidget(self.btn_font_minus)
        frow.addWidget(self.sp_font, 1)
        frow.addWidget(self.btn_font_plus)
        sg.addLayout(frow, 3, 1)
        # 开关
        chk = QHBoxLayout()
        self.ck_ontop = QCheckBox("字幕置顶"); self.ck_ontop.setChecked(bool(self._cfg.get("sub_ontop", True)))
        self.ck_ontop.toggled.connect(lambda v: self._switch_changed("ontop", v))
        self.ck_pass = QCheckBox("字幕穿透"); self.ck_pass.setChecked(bool(self._cfg.get("sub_clickthrough", False)))
        self.ck_pass.toggled.connect(lambda v: self._switch_changed("clickthrough", v))
        chk.addWidget(self.ck_ontop); chk.addWidget(self.ck_pass)
        sg.addLayout(chk, 4, 0, 1, 2)
        left.addWidget(sty)

        # ---- 操作：清空 / 下载（F-D14/F-D15）----
        ops = QHBoxLayout()
        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self._clear_clicked)
        self.btn_dl = QPushButton("下载会议记录")
        self.btn_dl.clicked.connect(self._download_clicked)
        self.btn_subs = QPushButton("显示/隐藏字幕")
        self.btn_subs.clicked.connect(lambda: self._on_toggle_sub())
        ops.addWidget(self.btn_clear)
        ops.addWidget(self.btn_dl)
        ops.addWidget(self.btn_subs)
        left.addLayout(ops)
        left.addStretch(1)

        # 左列（固定宽度，放右侧回看前）
        leftcol = QWidget()
        leftcol.setLayout(left)
        leftcol.setFixedWidth(430)
        root.addWidget(leftcol)

        # ---- 右侧：最近 N 条回看（F-D12，宽屏，可看很多行）----
        rbox = QGroupBox("会议记录回看（右侧宽屏）")
        rv = QVBoxLayout(rbox)
        self.recent_list = QListWidget()
        self.recent_list.setWordWrap(True)
        rv.addWidget(self.recent_list, 1)
        copy_btn = QPushButton("复制选中的一段")
        copy_btn.clicked.connect(self._copy_selected)
        rv.addWidget(copy_btn)
        root.addWidget(rbox, 1)   # 占满剩余宽度

    # ---------------- 初始状态 ----------------
    def _apply_initial_state(self):
        self.lang_cb.setCurrentIndex(0 if self._tgt == "zh" else 1)

    # ---------------- 设备枚举 / 下拉 ----------------
    def _refresh_devices(self):
        """重列远端 loopback 与物理麦克风候选并应用选择。
        选择优先级：
          远端：配置保存项 → 系统默认 loopback → 首个；
          自我：配置保存项 → 启发式(pick_self_mic 同品牌WASAPI) → 首个物理输入。"""
        import pyaudiowpatch as pyaudio
        from src.audio import devices as dev
        p = pyaudio.PyAudio()
        try:
            loop = []
            mics = []
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info.get("isLoopbackDevice") and info["maxInputChannels"] > 0:
                    loop.append(i)
                elif info["maxInputChannels"] > 0:
                    mics.append(i)
            # 远端下拉
            self._populate_remote(p, loop)
            # 自我下拉（项）
            self._populate_self(p, mics)
            # 选择自我：启发式
            self._apply_self_selection(p)
        finally:
            p.terminate()

    def _apply_self_selection(self, p):
        if self.self_cb.count() == 0:
            return
        saved = self._cfg.get("self_idx")
        if saved is not None and self.self_cb.findData(saved) >= 0:
            self.self_cb.setCurrentIndex(self.self_cb.findData(saved))
            return
        try:
            ridx = self.remote_cb.currentData()
            pick = None
            if ridx is not None:
                rinfo = p.get_device_info_by_index(ridx)
                pick, _h = dev.pick_self_mic(p, rinfo)
            if pick is None or self.self_cb.findData(pick) < 0:
                pick = self.self_cb.itemData(0)
            if pick is not None and self.self_cb.findData(pick) >= 0:
                self.self_cb.setCurrentIndex(self.self_cb.findData(pick))
        except Exception:
            pass

    def _populate_remote(self, p, loop_idx):
        blk = self.remote_cb.blockSignals(True)
        self.remote_cb.clear()
        # 默认 loopback
        default_i = None
        try:
            d = p.get_default_wasapi_loopback()
            default_i = int(d["index"]) if isinstance(d, dict) else int(d)
        except Exception:
            default_i = None
        saved = self._cfg.get("remote_idx")
        chosen = saved if saved in loop_idx else (default_i if default_i in loop_idx
                                                  else (loop_idx[0] if loop_idx else None))
        self._remote_data = {}
        for i in loop_idx:
            name = p.get_device_info_by_index(i)["name"]
            mark = f"[{i}] {name}" + (" ⟨默认⟩" if i == default_i else "")
            self.remote_cb.addItem(mark, i)
            self._remote_data[i] = name
        if chosen is not None:
            ci = self.remote_cb.findData(chosen)
            if ci >= 0:
                self.remote_cb.setCurrentIndex(ci)
        self.remote_cb.blockSignals(blk)

    def _populate_self(self, p, mic_idx):
        blk = self.self_cb.blockSignals(True)
        self.self_cb.clear()
        self._self_data = {}
        for i in mic_idx:
            name = p.get_device_info_by_index(i)["name"]
            self.self_cb.addItem(f"[{i}] {name}", i)
            self._self_data[i] = name
        self.self_cb.blockSignals(blk)

    def current_devices(self):
        """返回 (remote_idx, self_idx) 或 (None, None)。"""
        ri = self.remote_cb.currentData()
        si = self.self_cb.currentData()
        return ri, si

    def _dev_changed(self):
        ri, si = self.current_devices()
        partial = {}
        if ri is not None:
            partial["remote_idx"] = ri
        if si is not None:
            partial["self_idx"] = si
        if partial:
            GCONF.save(partial)
        if self._persist_state_changed:
            self._persist_state_changed()
        self.status_label.setText("设备已变更（若在运行需停止重建）")

    # ---------------- 静音自己 / 录制行为 / 字体步进 ----------------
    def _self_mute_changed(self, v):
        GCONF.save({"self_muted": bool(v)})
        self._on_self_mute(bool(v))
        self.status_label.setText("已" + ("静音自己麦克风" if v else "取消静音"))

    def _rec_changed(self, _v):
        GCONF.save({"silence_ms": int(self.sp_silence.value()),
                    "force_finalize_after": float(self.sp_force.value())})
        self.status_label.setText("录制行为已更新（下次开始生效）")

    def _refresh_noise_label(self):
        v = self.sl_noise.value()
        txt = f"当前门控：{v}%"
        if v == 0:
            txt += "（关闭过滤，全部保留）"
        elif v <= 3:
            txt += "（较低，仅滤底噪）"
        elif v <= 10:
            txt += "（中等）"
        else:
            txt += "（偏高，会滤掉轻声）"
        self._lbl_noise.setText(txt)

    def _noise_changed(self, v):
        # 实时生效（不走"下次开始"）：转发到 controller → capture 回调即时改阈值
        GCONF.save({"silence_threshold": v / 100.0})
        self._on_noise_gate(v / 100.0)
        self._refresh_noise_label()
        self.status_label.setText("静默门控已实时更新")

    def _font_step(self, delta):
        step = 1 if delta > 0 else -1
        newv = self.sp_font.value() + step
        self.sp_font.setValue(max(self.sp_font.minimum(), min(self.sp_font.maximum(), newv)))

    # ---------------- 运行控制 ----------------
    def _start_clicked(self):
        ri, si = self.current_devices()
        if ri is None or si is None:
            QMessageBox.warning(self, "缺设备", "请先在设备下拉中选择远端与麦克风。")
            return
        GCONF.save({"tgt": self._tgt})
        self._on_start(self._tgt)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_running.setVisible(True)
        self.set_state("running")

    def _stop_clicked(self):
        self._on_stop()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_running.setVisible(False)
        self.set_state("stopped")

    def _lang_changed(self, _i):
        self._tgt = self.lang_cb.currentData()
        GCONF.save({"tgt": self._tgt})
        self.status_label.setText("目标语言已设为 " + self._tgt + "（下次开始生效）")

    # ---------------- 清空 / 下载 ----------------
    def _clear_clicked(self):
        ret = QMessageBox.question(
            self, "清空会议记录",
            "将清空字幕窗、内存记录与回看列表。\n已落盘的文件不会被删除（不自动删，防误删）。\n确定清空？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            self._on_clear()
            self.reset_recent_log()
            self.status_label.setText("已清空")

    def _download_clicked(self):
        start, dur = self._get_meeting_meta()
        base = exporter.default_basename(start, dur)
        dlg = QFileDialog(self)
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dlg.setDirectory(os.path.join(GCONF.CONFIG_DIR, "..", "sessions"))
        dlg.setNameFilters(["Markdown (*.md)", "SRT 字幕 (*.srt)", "纯文本 (*.txt)"])
        dlg.selectFile(base + ".md")
        if dlg.exec():
            path = dlg.selectedFiles()[0]
            if not os.path.splitext(path)[1]:
                path += ".md"
            ext = os.path.splitext(path)[1].lstrip(".").lower()
            ok = self._on_export(path, ext)
            if ok:
                self.status_label.setText(f"已导出 {os.path.basename(path)}")
            else:
                self.status_label.setText("导出失败（无记录？）")

    # ---------------- 字幕样式 ----
    def _style_changed(self, key, val):
        GCONF.save({key: val})
        self._set_style({key: val})

    def _switch_changed(self, key, val):
        map_key = {"ontop": "sub_ontop", "clickthrough": "sub_clickthrough"}[key]
        GCONF.save({map_key: val})
        self._set_style({map_key: val})

    # ---------------- 回看 ----
    def append_final(self, speaker, ts, asr, trans, uid=None):
        """追加/更新一条回看。同 uid 的译文补全轮（trans 后到）→ 原地更新该条目。

        ⚠️ 修复（"回看只有英文"）：ASR 定稿先到（trans 尚空）会先追加一行，
        译文后到重入时若无 uid 更新，该行永远只有英文。现在按 uid 原地刷新译文，
        与 turn_log/WAL 的 last-wins 语义保持一致。
        """
        if uid:
            item = self._recent_uid_item.get(uid)
            if item is not None:
                # 译文补全（trans 非空）→ 原地更新文本，不新增行
                if trans:
                    for i, r in enumerate(self._recent_log):
                        if r[0] == uid:
                            self._recent_log[i] = (uid, speaker, ts, asr, trans)
                            break
                    item.setText(self._fmt_row(speaker, ts, asr, trans))
                elif asr and self._recent_log and self._recent_log[-1][0] == uid:
                    # 纯 ASR 更新且该 uid 已是末行 → 也原地刷新（原文最终版）
                    self._recent_log[-1] = (uid, speaker, ts, asr, trans)
                    item.setText(self._fmt_row(speaker, ts, asr, trans))
                return
        if len(self._recent_log) >= self._cfg.get("recent_n", 30):
            old_uid, _sp, _t, _a, _tr = self._recent_log.pop(0)
            if old_uid is not None:
                old_item = self._recent_uid_item.pop(old_uid, None)
                if old_item is not None:
                    row = self.recent_list.row(old_item)
                    self.recent_list.takeItem(row)
        self._recent_log.append((uid, speaker, ts, asr, trans))
        item = QListWidgetItem(self._fmt_row(speaker, ts, asr, trans))
        self.recent_list.addItem(item)
        if uid:
            self._recent_uid_item[uid] = item
        self.recent_list.scrollToBottom()

    @staticmethod
    def _fmt_row(speaker, ts, asr, trans):
        sp = _speaker_zh(speaker)
        t = exporter.dstr(ts) if ts else "--:--:--"
        asr_s = asr or "(…)"
        trans_s = trans or ""
        return f"[{sp} · {t}] {asr_s}" + (f"\n    {trans_s}" if trans_s else "")

    def update_final_view(self, turns):
        """用完整 turn_log 重建回看（清空/初始化用）。"""
        self.recent_list.clear()
        self._recent_uid_item.clear()
        shown = turns[-self._cfg.get("recent_n", 30):]
        for t in shown:
            self.append_final(t.speaker, t.abs_start, t.asr_final, t.trans_final,
                              uid=getattr(t, "user_item_id", None))

    def reset_recent_log(self):
        """清空回看（清空按钮/会话重开：同步清 uid 映射，防孤儿条目）。"""
        self._recent_log = []
        self._recent_uid_item = {}
        self.recent_list.clear()

    def _copy_selected(self):
        item = self.recent_list.currentItem()
        if item:
            QApplication.clipboard().setText(item.text())
            self.status_label.setText("已复制选中段")

    # ---------------- 状态显示 ----
    def set_status(self, text):
        self.status_label.setText(text)

    def set_usage(self, tokens):
        self.usage_label.setText(f"用量 {tokens} tok")

    def set_level(self, remote, self_):
        self.pb_remote.setValue(min(100, int(remote * 100)))
        self.pb_self.setValue(min(100, int(self_ * 100)))

    def set_state(self, state):
        """running / stopped / reconnecting / connected / disconnected"""
        color = {"running": "#2bd62b", "stopped": "#b0b0b0",
                 "connected": "#2bd62b", "disconnected": "#e05b5b",
                 "reconnecting": "#e0a33f"}.get(state, "#b0b0b0")
        self.status_dot.setStyleSheet(f"color:{color}; font-size:16px;")
