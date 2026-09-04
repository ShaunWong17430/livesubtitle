# -*- coding: utf-8 -*-
"""验证：能否用『立体声混音』输入设备(设备22)采到系统播放的声音。
如果『立体声混音』已启用，它是一个现成的 loopback 采集源，无需 WASAPI loopback 支持。

做法：向默认扬声器播放 440Hz 音调，同时用 sounddevice 采集输入设备 22。
若 RMSS>0，则『立体声混音』可用 → 提供一条零依赖的 loopback 采集路径。
"""
import sys, io, time
import numpy as np
import sounddevice as sd
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

MIC_IDX = 22   # 立体声混音 (Realtek HD Audio Stereo input)
FREQ = 500.0

try:
    info = sd.query_devices(MIC_IDX)
    print("输入设备 [%d]: %s (in=%d)" % (MIC_IDX, info['name'], info['max_input_channels']))
except Exception as e:
    print("设备 %d 不存在: %s" % (MIC_IDX, e)); sys.exit(1)

# 播放音调到默认输出
out_dev = sd.default.device[1]
oinfo = sd.query_devices(out_dev)
sr = 48000
ch = oinfo['max_output_channels'] or 2
dur = 6.0
t = np.arange(int(sr * dur)) / sr
tone = (0.5 * np.sin(2 * np.pi * FREQ * t) * np.clip(np.linspace(0.1, 0.9, int(sr*dur)), 0, 1)).astype(np.float32)
tone = np.tile(tone[:, None], (1, ch))
print("将向 [%d] %s 播放 %dHz 音调 %ds…" % (out_dev, oinfo['name'], int(FREQ), int(dur)))

# 采集输入设备 22
try:
    mic = sd.InputStream(device=MIC_IDX, samplerate=48000, channels=2, dtype='float32', blocksize=1600)
except Exception as e:
    print("✘ 立体声混音 打不开（可能被禁用）: %s" % repr(e)[:120])
    print("  → 到 设置→系统→声音→声音控制面板→录制→右键→显示禁用的设备→启用『立体声混音』")
    sys.exit(1)

out = sd.OutputStream(device=out_dev, samplerate=sr, channels=ch, dtype='float32')
mic.start(); out.start()
out.write(tone, len(tone))
rms = []
t0 = time.time()
while time.time() - t0 < dur + 0.5:
    d = mic.read(1600)[0]
    r = float(np.sqrt(np.mean(np.square(d)))) if d.size else 0.0
    rms.append(r)
    print("  t=%.1fs RMS=%.4f %s" % (time.time()-t0, r, "#"*int(min(r*200,40))))
    time.sleep(0.1)
mic.stop(); out.stop(); mic.close(); out.close()

mx = max(rms) if rms else 0
print("\n立体声混音 采集峰值 RMS = %.4f" % mx)
if mx > 0.05:
    print("✅ 立体声混音可用：能作为 loopback 采集源采到系统播放的声音。")
elif mx > 0.005:
    print("⚠️ 有微弱信号，可调高音量/确认发声设备。")
else:
    print("❌ 采集不到。可能设备22未被启用，或其指向的不是当前扬声器。")