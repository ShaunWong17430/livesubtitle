# -*- coding: utf-8 -*-
"""Stage 0 · PoC-0a v3：背景完全透明 + 文字清晰不透明 + 更大字体。

关键改进（用户需求：背景透明、字体清晰、英文更大）：
  - 用 tkinter 的 `-transparentcolor`（色键透明）实现『背景全透、文字不透明』。
    整窗铺一个不会出现在会议中的键色（如紫红 #ff00fe），设为透明键 → 背景消失；
    文字用非键色 → 保持 100% 清晰，不随背景变淡。
  - 英文字体加大（Segoe UI 17 bold），译文用更大更亮。

操作：拖动移动；滚轮/↑↓ 调『文字阴影深度』（可选）；按 C 切换键色与普通底两种模式对比；Esc 退出。
说明：Windows 下 -transparentcolor 对置顶层叠窗有效；某些合成器可能不支持，届时会提示。
"""
import sys
import io
import tkinter as tk

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

KEY = "#ff00fe"   # 色键（紫红）：任何用到它的像素都会透明


class App:
    def __init__(self, root):
        self.root = root
        root.title("PoC-0a v3")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        # 整窗先用接近全不透明，让文字清晰；背景靠色键抠掉
        root.attributes("-alpha", 1.0)
        root.geometry("820x160+30+30")

        self.bg_idx = 0  # 0=键色演示背景全透；1=深衬底普通窗（对比）
        self.canvas = tk.Canvas(root, bg=KEY, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # 应用色键透明（失败会抛错或无效，捕获提示）
        try:
            root.attributes("-transparentcolor", KEY)
            self.key_ok = True
        except Exception as e:
            self.key_ok = False
            print("[提示] 该 tk 合成器不支持 -transparentcolor:", e)

        self.drag = {"x": 0, "y": 0}
        self.canvas.bind("<Button-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        root.bind("<KeyPress>", self.on_key)
        root.bind("<Escape>", lambda e: root.destroy())
        root.after(60, self.canvas.focus_set)
        self.draw()

    def draw(self):
        c = self.canvas
        c.delete("all")
        # 背景：键色 → 透明；或深衬底 → 对比
        if self.bg_idx == 0:
            c.configure(bg=KEY)
        else:
            c.configure(bg="#22263a")

        cy1, cy2 = 52, 120
        # 译文主行（最大最亮）
        for sx, sy in ((1, 1), (-1, -1)):
            c.create_text(26 + sx, cy1 + sy, text="这是远端发言人的中文译文行",
                          anchor="w", font=("Microsoft YaHei", 24, "bold"), fill="#000000")
        c.create_text(26, cy1, text="这是远端发言人的中文译文行",
                      anchor="w", font=("Microsoft YaHei", 24, "bold"), fill="#ffd54f")
        # 原文次行（英文加大）
        for sx, sy in ((1, 1), (-1, -1)):
            c.create_text(26 + sx, cy2 + sy, text="This is the remote speaker's English line.",
                          anchor="w", font=("Segoe UI", 17, "bold"), fill="#000000")
        c.create_text(26, cy2, text="This is the remote speaker's English line.",
                      anchor="w", font=("Segoe UI", 17, "bold"), fill="#ffffff")

        mode = "背景全透(色键)" if self.bg_idx == 0 else "深衬底(对比)"
        c.create_text(800, 6, text="模式:%s   C切换  Esc退出" % mode,
                      anchor="ne", font=("Segoe UI", 9), fill="#8890a0" if self.bg_idx == 0 else "#8890a0")

    def on_press(self, e):
        self.drag["x"] = e.x_root - self.root.winfo_x()
        self.drag["y"] = e.y_root - self.root.winfo_y()

    def on_drag(self, e):
        self.root.geometry("+%d+%d" % (e.x_root - self.drag["x"], e.y_root - self.drag["y"]))

    def on_wheel(self, e):
        # 滚轮在透明模式下没意义，留作未来扩展；简单忽略
        pass

    def on_key(self, e):
        if e.keysym in ("c", "C"):
            self.bg_idx = 1 - self.bg_idx
            if self.bg_idx == 1:
                # 深衬底时关色键，避免键色也被抠掉（改用普通alpha）
                try:
                    self.root.attributes("-transparentcolor", "")
                except Exception:
                    pass
            else:
                try:
                    self.root.attributes("-transparentcolor", KEY)
                except Exception:
                    pass
            self.draw()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    print("PoC-0a v3：拖动画布移动；C 切换『背景全透 / 深衬底』；Esc 退出。")
    root.mainloop()
    print("Exit OK")


if __name__ == "__main__":
    main()