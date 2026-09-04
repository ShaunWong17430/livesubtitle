# -*- coding: utf-8 -*-
"""Stage 0 PoC：枚举音频设备（含中文名），帮助确定 Teams 输出设备 与 麦克风设备。
运行：python poc/_devices.py
"""
import sys
import io
import sounddevice as sd

# 统一 UTF-8 输出，避免中文设备名乱码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def main():
    print("===== HOST API =====")
    for h in sd.query_hostapis():
        print(f"  {h['name']:24s} default_in={h['default_input_device']} default_out={h['default_output_device']}")

    print("\n===== DEVICES =====")
    print("idx | name | in_ch | out_ch | default_in | default_out")
    devs = sd.query_devices()
    for i, d in enumerate(devs):
        di = "Y" if i == sd.default.device[0] else "."
        do = "Y" if i == sd.default.device[1] else "."
        print(f"{i:3d} | {d['name']} | {d['max_input_channels']} | {d['max_output_channels']} | {di} | {do}")

    print("\n===== 判定建议 =====")
    print("→ 扬声器端点（Loopback 用它）通常 = 你正在听 Teams 的那块声卡输出（如 XIBERIA/Realtek Speakers）。")
    print("→ 麦克风旁路 = 你说话用的麦克风输入（如 XIBERIA/Realtek Mic）。")
    print("→ PoC-0b 会在『扬声器输出设备』上用 WasapiSettings(loopback=True) 打开 InputStream 抓它。")


if __name__ == "__main__":
    main()