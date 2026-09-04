# -*- coding: utf-8 -*-
"""探测：不依赖 WasapiSettings.loopback 的可采集源。
重点看『立体声混音』类输入设备（设备22等）能否被 sounddevice 直接采集。
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import sounddevice as sd

# 所有 max_input_channels>0 的 WASAPI 输入设备（可能含立体声混音）
cands = []
for i, d in enumerate(sd.query_devices()):
    if d['max_input_channels'] > 0:
        cands.append((i, d['name'], d['max_input_channels']))

print("候选输入设备：")
for i, n, c in cands:
    print("  [%d] %s (in=%d)" % (i, n, c))

print("\n尝试逐一打开采集 300ms（前10个）：")
ok = []
for i, n, c in cands[:10]:
    try:
        for sr in (48000, 44100, 16000):
            try:
                s = sd.InputStream(device=i, samplerate=sr, channels=min(c,2),
                                   dtype='float32', blocksize=1600)
                s.start()
                import time; time.sleep(0.3)
                d = s.read(1600)[0]
                s.stop(); s.close()
                rms = float(__import__('numpy').sqrt(__import__('numpy').mean(__import__('numpy').square(d)))) if d.size else 0.0
                ok.append((i, n, sr, rms))
                print("  OK  [%d] %s @ %d RMS=%.4f" % (i, n, sr, rms))
                break
            except Exception as e:
                continue
        else:
            print("  FAIL [%d] %s (所有采样率都不行)" % (i, n))
    except Exception as e:
        print("  FAIL [%d] %s: %s" % (i, n, repr(e)[:80]))

print("\n总结：能直接打开并采到信号的输入设备：")
for i, n, sr, rms in ok:
    print("  [%d] %s @ %d RMS=%.4f" % (i, n, sr, rms))