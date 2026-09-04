# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import pyaudiowpatch as pyaudio

p = pyaudio.PyAudio()
try:
    for d in p.get_loopback_device_info_generator():
        print("loopback [%d] %s" % (d["index"], d["name"]))
        # 打印其关联的输出
        for k in d.keys():
            print("    %s = %r" % (k, d[k]))
        print("-----")
    n = p.get_device_count()
    print("全部设备:")
    for i in range(n):
        info = p.get_device_info_by_index(i)
        print("  [%d] %s in=%d out=%d" % (i, info["name"], info["maxInputChannels"], info["maxOutputChannels"]))
finally:
    p.terminate()