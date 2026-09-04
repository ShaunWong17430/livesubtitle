# -*- coding: utf-8 -*-
"""端到端验证：修复后的 src.gui.hotkeys 真正收到 WM_HOTKEY 并触发回调。

用真实 HotkeyManager + HotkeyFilter，PostMessage 注入与物理按键等价的 WM_HOTKEY，
验证 dispatch 被调用、回调触发。通过 = 修复有效；失败 = 还有问题。
"""
import ctypes, io, os, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"D:\data\dsh_subtitle")

os.environ["DSH_LOG_FMT"] = ""  # 不影响
from PyQt6.QtWidgets import QApplication, QWidget
from src.gui.hotkeys import HotkeyManager, HotkeyFilter

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, WM_HOTKEY = 0x0001, 0x0002, 0x4000, 0x0312
USER32 = ctypes.windll.user32

app = QApplication([])
w = QWidget(); w.show()
hwnd = int(w.winId())

mgr = HotkeyManager(hwnd)
fired = []
mgr.register(1, ord('S'), True, True, lambda: fired.append("S"))
mgr.register(2, ord('E'), True, True, lambda: fired.append("E"))
flt = HotkeyFilter(mgr)
app.installNativeEventFilter(flt)

lp = (MOD_ALT | MOD_CONTROL | MOD_NOREPEAT) << 16
USER32.PostMessageW(hwnd, WM_HOTKEY, 1, lp | ord('S'))
USER32.PostMessageW(hwnd, WM_HOTKEY, 2, lp | ord('E'))
t0=time.time()
while time.time()-t0 < 1.0 and len(fired) < 2:
    app.processEvents(); time.sleep(0.005)

print("回调触发:", fired)
ok = fired == ["S"]
# 顺序可能不定，改判集合
ok = set(fired) == {"S", "E"}
print("\n============ 结论 ============")
print("端到端热键链路:", "通过 ✓ 修复有效" if ok else "仍有问题 ✗")
mgr.unregister_all(); app.quit()
sys.exit(0 if ok else 1)