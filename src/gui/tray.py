# -*- coding: utf-8 -*-
"""系统托盘（Stage 3 · src/gui/tray.py）。

用 pystray（用户已安装）把程序收进托盘图标，避免开会时任务栏占位 / 误关窗口。
- 菜单：显示/隐藏字幕窗、退出。
- pystray 的 run() 会阻塞自己的线程 → 放入 daemon 线程里跑，不挡 Qt 主循环。
- 图标：用 PIL 程序化生成（不额外带资源文件）。
- 退出：菜单项调用主窗口 close → Qt 主循环收尾；关闭 Qt 时也 stop 托盘线程。

用法（在 run_gui 装配后）：
    tray = TrayIcon(on_toggle_sub=..., on_quit=app.quit)
    tray.start()
    rc = app.exec()
    tray.stop()
"""
import threading

from src.gui.logging_setup import get_logger


def _make_image(size=64):
    """程序化生成一个圆角小图标（避免依赖图标资源文件）。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 2, size - 2], radius=14,
                        fill=(31, 120, 200, 255))          # 蓝底
    d.rounded_rectangle([10, 10, size - 10, 22], radius=6,
                        fill=(255, 213, 79, 255))          # 顶部"字幕条"
    d.rounded_rectangle([10, 28, size - 10, 40], radius=6,
                        fill=(255, 255, 255, 230))         # 中间"字幕条"
    d.rounded_rectangle([16, 46, size - 16, 54], radius=4,
                        fill=(255, 255, 255, 160))         # 底部细条
    return img


class TrayIcon:
    """托盘：启动/停止 + 菜单回调。不阻塞 Qt 主线程。"""

    def __init__(self, on_toggle_sub=None, on_hide_sub=None, on_quit=None):
        self._on_toggle_sub = on_toggle_sub or (lambda: None)
        self._on_hide_sub = on_hide_sub or (lambda: None)
        self._on_quit = on_quit or (lambda: None)
        self._log = get_logger("tray")
        self._icon = None
        self._thread = None

    def _menu(self):
        import pystray
        return pystray.Menu(
            pystray.MenuItem("显示/隐藏字幕", lambda: self._on_toggle_sub(), default=True),
            pystray.MenuItem("隐藏字幕", lambda: self._on_hide_sub()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit),
        )

    def _build(self):
        import pystray
        self._icon = pystray.Icon("会议字幕", _make_image(),
                                  "会议字幕工具", self._menu())

    def _run(self):
        try:
            if self._icon is not None:
                self._icon.run()
        except Exception as e:
            self._log.warning("托盘线程异常: %r", e)

    def start(self):
        try:
            self._build()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            self._log.info("系统托盘已启动")
        except Exception as e:
            self._log.warning("托盘启动失败: %r", e)

    def _quit(self):
        try:
            if self._icon is not None:
                self._icon.stop()
        except Exception:
            pass
        self._on_quit()

    def stop(self):
        try:
            if self._icon is not None:
                self._icon.stop()
        except Exception:
            pass