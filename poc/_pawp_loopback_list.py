# -*- coding: utf-8 -*-
"""Stage 0 · PoC-0b 辅助：列出 PyAudioWPatch 可见的全部 WASAPI loopback 端点。
运行后你就知道哪些 loopback 可采（其中 Teams 输出到的那块=远端源）。
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import pyaudiowpatch as pyaudio


def main():
    p = pyaudio.PyAudio()
    try:
        print("===== WASAPI Loopback 端点 =====")
        gen = p.get_loopback_device_info_generator()
        for d in gen:
            print("  [%d] %s | in=%d | %d Hz" % (
                d["index"], d["name"], d["maxInputChannels"], int(d["defaultSampleRate"])))
        try:
            didx = p.get_default_wasapi_loopback()
            d = p.get_device_info_by_index(didx)
            print("\n默认 loopback = [%d] %s" % (didx, d["name"]))
        except Exception as e:
            print("\n取默认 loopback 失败: %s" % e)
    finally:
        p.terminate()


if __name__ == "__main__":
    main()