# -*- coding: utf-8 -*-
"""Stage 0 · PoC-0a (PyQt6)：背景『近全透』+ 文字清晰不透明 + 大字体。

用户需求：背景几乎全透（不是全透）但文字清晰、字号要大 —— 这是 PyQt6 的强项：
  - WA_TranslucentBackground 提供逐像素 alpha：背景只画一层很淡的圆角衬底（可调），
    其余完全透出桌面/会议窗口；文字用不透明颜色 + 黑色描边/阴影，100% 清晰。
  - 与 tkinter 色键相比：没有“键色残留/个别环境失效”的问题，透明是系统级真透明。

交互：
  - 鼠标左键拖拽移动窗口
  - 滚轮：调节背景不透明度（α 0..255，实时刷新）——验证“近全透”可调
  - C：一键切换【近全透 α≈28】/【深衬底 α≈200】两种预设对比
  - Esc：退出
  - --demo N：自动 N 秒后退出（用于无交互冒烟测试）

依赖：PyQt6（用户已装）。运行： pyforsub\\python.exe poc/poc0a_pyqt6.py
"""
import sys
import io

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PRESET_LIGHT = 28    # 近全透：很淡的衬底
PRESET_DARK = 200    # 深衬底：明显深色背景（对比用）
CHINESE = "这是远端发言人的中文译文行"
ENGLISH = "This is the remote speaker's English line."


class SubtitleOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.bg_alpha = PRESET_LIGHT
        self.drag_offset = None
        self.setWindowTitle("PoC-0a PyQt6")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)          # Tool：不进任务栏
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)  # 逐像素透明
        self.setFixedSize(880, 150)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.show()

    # ---- 绘制 ----
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 1) 背景：近全透的圆角衬底（α 由滚轮/C 控制，默认 28 ≈ 只留极淡底色）
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(28, 32, 52, self.bg_alpha))
        p.drawRoundedRect(0, 0, w - 1, h - 1, 12, 12)

        # 2) 译文主行（中文最大最亮，金黄 + 黑描边）
        self._draw_text(p, 26, 50, CHINESE,
                        QFont("Microsoft YaHei", 26, QFont.Weight.Bold), QColor(255, 213, 79))
        # 3) 原文次行（英文，白色 + 黑描边）
        self._draw_text(p, 26, 116, ENGLISH,
                        QFont("Segoe UI", 18, QFont.Weight.Bold), QColor(255, 255, 255))

        # 4) 角落提示
        p.setFont(QFont("Segoe UI", 9))
        p.setPen(QColor(136, 144, 160, 230))
        mode = "近全透" if self.bg_alpha <= 100 else "深衬底"
        hint = f"模式:{mode} α={self.bg_alpha}  滚轮调背景  C切换  Esc退出"
        p.drawText(w - 8 - p.fontMetrics().horizontalAdvance(hint), h - 9, hint)

    def _draw_text(self, p, x, y, text, font, color):
        p.setFont(font)
        # 黑色描边（多方向偏移模拟描边，保证任何背景上都清晰）
        p.setPen(QColor(0, 0, 0, 210))
        for dx, dy in ((1, 1), (2, 2), (-1, 1), (1, -1)):
            p.drawText(x + dx, y + dy, text)
        p.setPen(color)
        p.drawText(x, y, text)

    # ---- 拖拽移动 ----
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self.drag_offset is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self.drag_offset)

    def mouseReleaseEvent(self, e):
        self.drag_offset = None

    # ---- 键盘 / 滚轮 ----
    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_C:
            self.bg_alpha = PRESET_DARK if self.bg_alpha <= 100 else PRESET_LIGHT
            self.update()
        elif e.key() == Qt.Key.Key_Escape:
            self.close()

    def wheelEvent(self, e):
        step = 8 if self.bg_alpha <= 100 else 16
        self.bg_alpha = max(0, min(255, self.bg_alpha + (step if e.angleDelta().y() > 0 else -step)))
        self.update()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", type=float, default=0,
                    help="自动 N 秒后退出（冒烟测试，不交互）")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    w = SubtitleOverlay()
    if args.demo > 0:
        QTimer.singleShot(int(args.demo * 1000), app.quit)
    print("PoC-0a PyQt6：拖拽移动；滚轮调背景透明度；C 切换 近全透/深衬底；Esc 退出。")
    app.exec()
    print("Exit OK")


if __name__ == "__main__":
    main()
