# -*- coding: utf-8 -*-
"""双语悬浮字幕窗（Stage 2 · src/gui/subtitle_window.py）。

满足 F-D1..D7：
  F-D1  WA_TranslucentBackground 逐像素透明；近全透圆角衬底（默认 α≈28 可调）；
        文字不透明 + 深色描边（QPainter 两遍绘制：先描边后填实），任何背景下可读。
  F-D2  WindowStaysOnTopHint 置顶，可开关。
  F-D3  无边框、鼠标拖动、位置持久化。
  F-D4  鼠标穿透（ctypes WS_EX_LAYERED|WS_EX_TRANSPARENT），可开关。
  F-D5  Qt6 内建高 DPI。
  F-D6  双语对照：上行原文（浅色小字）/ 下行译文（高亮主色）；显示最近 N 轮，
        最新在最上（自动滚顶）。
  F-D7  临时稿/定稿区分：临时稿用稍暗/描边弱样式，定稿转正常——增量整行替换更新。

【透明结构关键（2026-09-03 修正）】：
  PoC-0a（`poc/poc0a_pyqt6.py`，用户确认透明可用）是用**顶层窗自身作为绘制 widget**
  （`WA_TranslucentBackground` 设在这个唯一 widget 上，paintEvent 直接在它上面画衬底）。
  原先的「顶层窗 SubtitleWindow + 子 SubtitleWidget（放进 layout）再画」的结构，在
  Windows/PyQt6 上会导致子 widget 区域合成成**不透明黑底**（父窗在合成前把子区底刷成黑，
  逐像素 alpha 失效），于是衬底全黑、调 α 无可见变化（"先动一下整体不透明度"恰好触发
  手动重合成才恢复）。修复：**把绘制逻辑并入顶层窗自身**，回到与 PoC-0a 一致的单 widget
  结构，透明从启动即正常。同时保留 showEvent 里 QTimer.singleShot(0) 的"合成后重提
  opacity + 穿透"兜底（Win 层 `WS_EX_TRANSPARENT` 穿透在 hide/show 后可能丢失）。
"""
import ctypes

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRectF, QRect, QSize, QTimer
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QPen, QBrush, QFontMetricsF, QTextOption,
)
from PyQt6.QtWidgets import QWidget, QApplication

SPEAKER_ZH = {"remote": "远端", "self": "自己"}

# 高对比三档：衬底 α 由弱到强（F-D11）。用户可一键切换（控制窗）。
CONTRAST_BG = {0: None, 1: (16, 16, 24, 96), 2: (16, 16, 24, 168)}


def _win_long(w):
    """取原生窗口 long 标识（ctypes 穿透用）。"""
    return int(w.winId())


class SubtitleWindow(QWidget):
    """悬浮字幕窗：顶层窗自身即画布（PoC-0a 已验证可行的透明结构）。

    提供 set_rows(rows dict 列表)/set_style(**kw) 供控制窗与主装配层调用。
    """
    moved_sig = pyqtSignal(int, int)   # 拖动结束位置，供持久化

    def __init__(self, cfg, rows_cap=3):
        super().__init__()
        self._cfg = cfg
        self._drag_pos = None
        self._rows = []            # [ {speaker,time,asr,trans,finalized} ]
        self._rows_cap = rows_cap
        self._bg_alpha = int(cfg.get("sub_bg_alpha", 28))
        self._contrast = cfg.get("sub_contrast", 0)
        self._font_pt = int(cfg.get("sub_font_pt", 26))
        # 译文(中文)比英文大多少 pt：满足"英文不变、中文只大1-2"（可调）
        self._trans_pt_gap = int(cfg.get("trans_pt_gap", 2))
        self._h_pad, self._v_pad = 14, 10
        self._line_gap = 12         # 相邻轮次垂直间距
        self._radius = 14           # 衬底圆角

        self._clickthrough = bool(cfg.get("sub_clickthrough", False))
        self._ontop = bool(cfg.get("sub_ontop", True))

        # 整体窗口透明度（F-D1：可调）。config 里 sub_opacity 之前是死配置从未调用——
        # 现在接通：默认 0.85，越小越透。
        self._opacity = float(cfg.get("sub_opacity", 0.85))
        self.setWindowOpacity(max(0.05, min(1.0, self._opacity)))

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint * self._ontop
            | Qt.WindowType.Tool)
        # 逐像素透明设在顶层窗自身（与 PoC-0a 一致；子 widget 结构会黑底）
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        self.resize(cfg.get("sub_width", 720), 160)
        pos = cfg.get("sub_pos")
        if pos and isinstance(pos, (list, tuple)) and len(pos) == 2:
            self.move(int(pos[0]), int(pos[1]))
        else:
            self._place_bottom_right()

    # ---- 窗口就绪后施加层叠 alpha / 穿透（对 Hide/Show 后丢失的 Win 标志兜底）----
    def showEvent(self, ev):
        super().showEvent(ev)
        # 等到事件循环完成本次显示布局（真正合成）后再施加，避免画前时机太早。
        QTimer.singleShot(0, self._apply_native_style)

    def _apply_native_style(self):
        try:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setWindowOpacity(max(0.05, min(1.0, self._opacity)))
        except Exception:
            pass
        self._apply_clickthrough()   # 穿透标志 hide/show 后可能丢失，每次 show 重设

    def _place_bottom_right(self, margin=40):
        scr = QApplication.primaryScreen().availableGeometry()
        # 只定位，不 adjustSize()——那是宽度塌成 0 的元凶
        self.move(max(scr.left(), scr.right() - self.width() - margin),
                  max(scr.top(), scr.bottom() - self.height() - margin))

    # ---- 数据（rows）----
    def set_rows(self, rows):
        self._rows = rows[-self._rows_cap:] if rows else []
        self._autosize()
        self.update()

    # ---- 样式 ----
    def set_style(self, **kw):
        # 内容级样式
        if "bg_alpha" in kw:
            self._bg_alpha = max(0, min(255, int(kw["bg_alpha"])))
        if "contrast" in kw:
            self._contrast = kw["contrast"]
        if "font_pt" in kw:
            self._font_pt = kw["font_pt"]
            self.set_rows(self._rows)   # 折行行数随字号变 → 重算高度
        if "ontop" in kw:
            self._ontop = bool(kw["ontop"])
            self._reflag()
        if "clickthrough" in kw:
            self._clickthrough = bool(kw["clickthrough"])
            self._apply_clickthrough()
        if "opacity" in kw:
            self._opacity = max(0.05, min(1.0, float(kw["opacity"])))
            self.setWindowOpacity(self._opacity)
        self._autosize()
        self.update()

    def _autosize(self):
        w = self.width() or self._cfg.get("sub_width", 720)
        h = self.estimate_height(width=w)
        h = max(h, 40)
        if h != self.height():
            self.resize(self.width() or self._cfg.get("sub_width", 720), h)

    def _reflag(self):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self._ontop:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    # ---- 布局尺寸 ----
    def minimumSizeHint(self):
        return self.sizeHint()

    def sizeHint(self):
        fm = QFontMetricsF(self.font_for_size(self._asr_pt()))
        return QSize(max(240, int(fm.horizontalAdvance("会议字幕测试" * 4))), 40)

    def estimate_height(self, width=None):
        """估算内容总高（供窗口 resize）。必须与 paintEvent 用同一折行逻辑。"""
        w = width if width and width > 40 else (self.width() or 720)
        fm = QFontMetricsF(self.font_for_size(self._trans_pt()))
        cm = QFontMetricsF(self.font_for_size(self._asr_pt()))
        asr_h = fm.height()
        max_text_w = max(40, w - self._h_pad * 2)
        h = 0
        for r in self._rows:
            tl = 0
            if r["asr"]:
                n = len(self.word_wrap_lines(cm, r["asr"], max_text_w)) or 1
                tl += cm.height() * n
            if r["trans"]:
                n = len(self.word_wrap_lines(fm, r["trans"], max_text_w)) or 1
                tl += asr_h * n
            if not r["asr"] and not r["trans"]:
                tl = fm.height()
            tl += cm.height() + 2
            h += tl + self._line_gap
        return int(h) + self._v_pad * 2

    def font_for_size(self, pt):
        f = QFont("Microsoft YaHei UI", pt)
        f.setBold(True)
        return f

    def _asr_pt(self):
        """英文/原文（asr）字号：基准 _font_pt 的 70%，下限 11。保持不变。"""
        return max(11, int(self._font_pt * 0.7))

    def _trans_pt(self):
        """译文/中文（trans）字号：英文 + trans_pt_gap（只大 1-2 pt）。"""
        return max(12, self._asr_pt() + self._trans_pt_gap)

    def word_wrap_lines(self, fm, text, max_w):
        """按宽折行，返回行文本列表。"""
        if not text:
            return []
        lines = []
        cur = ""
        for ch in text:
            t = cur + ch
            if fm.horizontalAdvance(t) <= max_w:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        return lines or [text]

    # ---- 绘制（直接在顶层窗自身画）----
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()

        # 1) 衬底（近全透圆角）——只有这里带 alpha，其余区域全透出桌面
        if self._contrast and self._contrast in CONTRAST_BG:
            r, g, b, a = CONTRAST_BG[self._contrast]
            p.setBrush(QBrush(QColor(r, g, b, a)))
        else:
            p.setBrush(QBrush(QColor(20, 20, 28, self._bg_alpha)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRect(0, 0, w, h), self._radius, self._radius)

        # 2) 文本
        if not self._rows:
            p.end()
            return
        f_trans = self.font_for_size(self._trans_pt())
        f_asr = self.font_for_size(self._asr_pt())
        fm_trans = QFontMetricsF(f_trans)
        fm_asr = QFontMetricsF(f_asr)
        max_text_w = max(40, w - self._h_pad * 2)
        y = self._v_pad
        for r in self._rows:
            sp = SPEAKER_ZH.get(r["speaker"], r["speaker"] or "?")
            head = f" {sp} · {r['time']} "
            self._draw_head(p, head, x=self._h_pad, y=y, f_head=f_asr)
            y += fm_asr.height() + 2

            if r["asr"]:
                for ln in self.word_wrap_lines(fm_asr, r["asr"], max_text_w):
                    self._draw_text(p, ln, self._h_pad, y, f_asr,
                                    color="#cfd6e4" if r["finalized"] else "#8f96a6",
                                    outline="#101018")
                    y += fm_asr.height()
            if r["trans"]:
                for ln in self.word_wrap_lines(fm_trans, r["trans"], max_text_w):
                    self._draw_text(p, ln, self._h_pad, y, f_trans,
                                    color="#5fd4ff" if r["finalized"] else "#3f9fc4",
                                    outline="#101018")
                    y += fm_trans.height()
            y += self._line_gap
        p.end()

    def _draw_head(self, p, text, x, y, f_head):
        fm = QFontMetricsF(f_head)
        tw = int(fm.horizontalAdvance(text))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(0, 0, 0, 110)))
        p.drawRoundedRect(QRect(int(x), int(y), tw + 8, int(fm.height()) + 2), 4, 4)
        self._draw_text(p, text, x + 4, y + 1, f_head,
                        color="#aee0ff", outline="#000000", pen_w=1)

    def _draw_text(self, p, text, x, y, font, color, outline="#101018", pen_w=2):
        """先描边（深色宽 pen）再填实（亮色），保证任意背景下可读。"""
        fm = QFontMetricsF(font)
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.NoWrap)
        p.setFont(font)
        p.setPen(QPen(QColor(outline), pen_w, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
            p.drawText(QRectF(x + dx, y + dy, 1000000, fm.height()), text, opt)
        p.setPen(QPen(QColor(color), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawText(QRectF(x, y, 1000000, fm.height()), text, opt)

    # ---- 穿透（WS_EX_TRANSPARENT 命中穿透）----
    # 只切换 WS_EX_TRANSPARENT（让鼠标点击穿透），**绝不能移除 WS_EX_LAYERED**：
    # WA_TranslucentBackground 的逐像素 alpha 依赖 WS_EX_LAYERED；若在 clickthrough 关闭时
    # 把 LAYERED 也清掉，字幕窗会退化为普通不透明窗 → 衬底整黑（2026-09-03 复现根因）。
    def _apply_clickthrough(self):
        try:
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            styles = ctypes.windll.user32.GetWindowLongW(_win_long(self), GWL_EXSTYLE)
            if self._clickthrough:
                styles |= WS_EX_LAYERED | WS_EX_TRANSPARENT
            else:
                # 只关命中穿透，保留 LAYERED（透明必需）
                styles &= ~WS_EX_TRANSPARENT
            ctypes.windll.user32.SetWindowLongW(_win_long(self), GWL_EXSTYLE, styles)
        except Exception:
            pass

    # ---- 拖动 ----
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self._drag_pos = None
            self.moved_sig.emit(self.x(), self.y())

    # ---- 鼠标穿透时不响应拖动 ----
    def hitTest(self, pos):
        if self._clickthrough:
            return False
        return self.rect().contains(pos)