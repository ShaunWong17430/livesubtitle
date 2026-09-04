# -*- coding: utf-8 -*-
"""全局热键（Stage 3 · src/gui/hotkeys.py）。

不用第三方热键库（环境未装，且安装由用户负责），直接用 Win32 `RegisterHotKey` +
Qt 原生事件过滤器捕获 `WM_HOTKEY`（F-D9：避免开会时找窗口）。

键序列由 `config/gui.json` 的 `hotkey_scheme` 配置（缺失回退下面默认）：
  Ctrl+Alt+S  开始 / 暂停（切换运行）
  Ctrl+Alt+E  导出会议记录（弹保存对话框）
  Ctrl+Alt+L  切换目标语言 zh <-> en
  Ctrl+Alt+W  显示 / 隐藏字幕浮窗
每个动作可单独改 vk（字母/数字/F1-F12）与 ctrl/alt 修饰符，改完重启生效。

用法：
    mgr = HotkeyManager(hwnd)                 # hwnd = 主窗.winId()
    mgr.register(id, ctrl=True, alt=True, vk=ord('S'), callback=fn)   # 返回 bool
    flt = HotkeyFilter(mgr)
    QApplication.instance().installNativeEventFilter(flt)
    # 退出时 mgr.unregister_all()
"""
import ctypes

from PyQt6.QtCore import QAbstractNativeEventFilter, QObject

from src.gui.logging_setup import get_logger

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

USER32 = ctypes.windll.user32

_IS_64BIT = ctypes.sizeof(ctypes.c_void_p) == 8

# 功能键虚拟键码（VK_F1=0x70 ... VK_F12=0x7B）
_FKEY_VK = {"F%d" % i: 0x70 + (i - 1) for i in range(1, 13)}


def vk_from_str(s):
    """把配置里的键名字符串转成虚拟键码。支持单字母 A-Z、数字 0-9、F1-F12。
    s 忽略大小写；返回 int 或 None（非法时）。"""
    if not s:
        return None
    t = str(s).strip().upper()
    if t in _FKEY_VK:
        return _FKEY_VK[t]
    if len(t) == 1 and t.isalnum():
        return ord(t)
    return None


def key_label(vk, ctrl, alt):
    """把 vk+修饰合成可读标签，如 Ctrl+Alt+F5。"""
    if 0x70 <= vk <= 0x7B:
        main = "F%d" % (vk - 0x70 + 1)
    elif 0x30 <= vk <= 0x39:
        main = chr(vk - 0x30 + ord('0'))
    elif ord('A') <= vk <= ord('Z'):
        main = chr(vk)
    else:
        main = "VK_%d" % vk
    return ("Ctrl+" if ctrl else "") + ("Alt+" if alt else "") + main


def _msg_decode(message):
    """把 PyQt6 nativeEventFilter 的 message 参数（PyQt6 里是 sip.voidptr，
    指向一个 MSG 结构）解析出 (msg号, wParam)。

    PyQt6 6.5+ 的 nativeEventFilter 把 message 作为裸 voidptr 传入（不再是带
    .message/.wParam 属性的对象），旧写法 message.message 会抛 AttributeError
    而被吞掉，导致热键永远触达不到 —— 这就是"注册返回 OK 但热键无效"的根因。
    MSG 布局（Windows SDK）:
      x64: HWND(8) + UINT message(4) + pad(4) + WPARAM(8) + LPARAM(8) + ...
      x86: HWND(4) + UINT message(4) + WPARAM(4) + LPARAM(4) + ...
    只读我们关心的两个字段，用 ctypes 按指针对齐读取。
    """
    try:
        addr = int(message)
    except (TypeError, ValueError):
        return None, None
    if _IS_64BIT:
        msg_off, wp_off = 8, 16
    else:
        msg_off, wp_off = 4, 8
    try:
        msg_num = ctypes.c_uint.from_address(addr + msg_off).value
        wparam = ctypes.c_void_p.from_address(addr + wp_off).value
    except Exception:
        return None, None
    return msg_num, wparam


class HotkeyManager(QObject):
    """注册全局热键，收到 WM_HOTKEY 后调用对应回调。"""

    def __init__(self, hwnd):
        super().__init__()
        self._hwnd = int(hwnd)
        self._reg = {}          # id -> callback
        self._log = get_logger("hotkeys")

    def register(self, hk_id, vk, ctrl=True, alt=True, callback=None):
        """hk_id：正整数唯一；vk：虚拟键码（ord('S')、0x70+F5 等）；返回 bool。"""
        self.unregister(hk_id)
        mod = (MOD_CONTROL if ctrl else 0) | (MOD_ALT if alt else 0) | MOD_NOREPEAT
        ok = bool(USER32.RegisterHotKey(self._hwnd, hk_id, mod, vk))
        lbl = key_label(vk, ctrl, alt)
        self._log.info("RegisterHotKey id=%s key=%s -> %s", hk_id, lbl, "OK" if ok else "FAIL")
        if ok:
            self._reg[hk_id] = (callback, lbl)
        return ok

    def unregister(self, hk_id):
        if hk_id in self._reg:
            try:
                USER32.UnregisterHotKey(self._hwnd, hk_id)
            except Exception:
                pass
            del self._reg[hk_id]

    def unregister_all(self):
        for i in list(self._reg.keys()):
            self.unregister(i)

    def dispatch(self, wParam):
        ent = self._reg.get(int(wParam))
        if ent:
            cb, lbl = ent
            try:
                self._log.info("收到 WM_HOTKEY %s，执行回调", lbl)
                cb()
            except Exception as e:
                self._log.exception("热键回调执行出错: %r", e)
            return True
        return False


class HotkeyFilter(QAbstractNativeEventFilter):
    """Qt native 事件过滤器，把 WM_HOTKEY 转发给 manager。"""

    def __init__(self, manager):
        super().__init__()
        self._mgr = manager

    def nativeEventFilter(self, eventType, message):
        try:
            if eventType == b"windows_generic_MSG":
                msg_num, wparam = _msg_decode(message)
                if msg_num == WM_HOTKEY and wparam is not None:
                    return self._mgr.dispatch(wparam), 0
        except Exception:
            pass
        return False, 0