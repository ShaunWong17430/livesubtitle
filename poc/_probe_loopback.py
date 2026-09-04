# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import sounddevice as sd

for idx in (12, 13):
    info = sd.query_devices(idx)
    print('=' * 60)
    name = info['name']
    outch = info['max_output_channels']
    print('设备 [%d]: %s out=%d' % (idx, name, outch))
    for sr in (48000, 44100, 16000):
        try:
            ch = outch or 2
            s = sd.InputStream(device=idx, samplerate=sr, channels=ch, dtype='float32',
                               blocksize=1600, extra_settings=sd.WasapiSettings(loopback=True))
            s.start(); s.stop(); s.close()
            print('  OK  %d Hz Loopback' % sr)
        except Exception as e:
            print('  FAIL %d Hz: %s' % (sr, repr(e)[:140]))