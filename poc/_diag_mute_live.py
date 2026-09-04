# -*- coding: utf-8 -*-
"""实测：GUI 运行中切换静音是否实时生效。
真开两路采集，主线程每 2s 切换 self_muted，采集线程记录 (t, speaker, pushed)。
若切换后 1 个回调内 pushed_self 停止增长 → 实时生效 ✓"""
import io, os, sys, threading, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\data\dsh_subtitle")
import pyaudiowpatch as pyaudio
from src.audio import devices as dev
from src.audio.capture import TwoChannelCapture

p = pyaudio.PyAudio()
ri = dev.default_remote_loopback(p)
info = p.get_device_info_by_index(ri)
si, _how = dev.pick_self_mic(p, info)
p.terminate()
print(f"remote={ri} self={si}")

# 记录各通道实际"推入队列"的次数（回调里 self_muted 决定是否丢）
stat = {"self_push": 0, "remote_push": 0, "t": []}
def on_level(sp, lv): pass

cap = TwoChannelCapture(ri, si, on_level=on_level).open()
cap.start()

# 用一个消费者线程模拟泵（取队列 → 计数）
stop = {"v": False}
def consumer():
    while not stop["v"]:
        ch = cap.next_chunk(0.2)
        if ch:
            sp = ch[0]
            stat[sp + "_push"] += 1
threading.Thread(target=consumer, daemon=True).start()

t0 = time.time()
def snap(tag):
    print(f"[{time.time()-t0:5.1f}s] {tag}: self_push={stat['self_push']} remote_push={stat['remote_push']}")

time.sleep(2); snap("初始 (self_muted=False 默认)")
cap.set_self_muted(True);  print("  → 切到静音 True")
time.sleep(2); snap("静音后")
cap.set_self_muted(False); print("  → 切回 False")
time.sleep(2); snap("取消静音后")
stop["v"] = True
cap.close()
print("\n结论：若'静音后'self_push 不再增长、'取消后'恢复增长 → 实时生效")