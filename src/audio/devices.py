# -*- coding: utf-8 -*-
"""设备枚举与选择（Stage 1 · src/audio/devices.py）。

Stage 0 结论（PoC-0b/0b'）落地：
  - 远端 = WASAPI 端点 Loopback 虚拟输入设备（isLoopbackDevice=True，如 [16]）。
  - 自己 = 物理麦克风（WASAPI 共享模式普通 input 流，非 loopback）。
  - 同一耳机麦在 MME/DirectSound/WASAPI 下有多条重复条目 → 启发式优先选
    「与远端同品牌族 且 同 WASAPI hostApi」的那条（PoC-0b' 自动选中 [15] 正确）。
"""
import pyaudiowpatch as pyaudio

#: 与远端 loopback 匹配麦克风时优先的同 hostApi（WASAPI）。取远端 hostApi 为准。
DEFAULT_CHUNK = 1600  # 帧/块 ≈ 0.1s @16k


def list_devices(p):
    """打印全部设备；返回 [(idx, info), ...]。"""
    rows = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        rows.append((i, info))
        mark = "  <loopback>" if info.get("isLoopbackDevice") else ""
        print("  [%3d] in=%d out=%d | %s%s" % (
            i, info["maxInputChannels"], info["maxOutputChannels"],
            info["name"], mark))
    return rows


def default_remote_loopback(p):
    """默认 WASAPI loopback 端点索引；拿不到抛 RuntimeError。"""
    try:
        d = p.get_default_wasapi_loopback()
        return int(d["index"]) if isinstance(d, dict) else int(d)
    except Exception as e:
        raise RuntimeError(f"取默认 loopback 失败（可能无渲染端点）: {e}")


def pick_self_mic(p, remote_info):
    """启发式选麦克风：
      1) 与远端同品牌族 且 同 WASAPI hostApi → 最准（耳机麦）；
      2) 其次同品牌族任意 hostApi；
      3) 再退默认输入设备。
    返回 (idx, 命中方式字符串)。"""
    family = remote_info["name"].replace("[Loopback]", "").strip()
    fam_key = family.split("(")[-1].rstrip(")").strip() if family else ""
    want_host = remote_info["hostApi"]
    cand = cand_any = None
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if (info["maxInputChannels"] > 0
                and not info.get("isLoopbackDevice")
                and fam_key and fam_key in info["name"]):
            cand_any = cand_any or i
            if info["hostApi"] == want_host:
                cand = i
                break
    if cand is not None:
        return cand, "same-family+WASAPI"
    if cand_any is not None:
        return cand_any, "same-family(非WASAPI条目)"
    di = p.get_default_input_device_info()
    return int(di["index"]), "默认输入设备"


def device_label(p, idx):
    info = p.get_device_info_by_index(idx)
    return info["name"]


def host_rate(p, idx):
    """设备默认采样率（打开时逐档降级，这里只读默认值）。"""
    return int(p.get_device_info_by_index(idx)["defaultSampleRate"])
