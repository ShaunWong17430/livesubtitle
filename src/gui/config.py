# -*- coding: utf-8 -*-
"""配置持久化（Stage 2 · src/gui/config.py）。

满足 F-C1 子集 + Stage 2 目标⑥：记住用户上次选择的远端/麦克风设备、目标语言、
字幕窗位置、控制窗位置、透明度、置顶/穿透等，下次启动恢复。
只读/写本地 JSON（config/gui.json），API Key 不落这里（仍走 CSV——见 src/stage1/main.py
load_cred，第二条用户仍要在此工具里输入/覆写）。

设计：Config 保存/读取一个 dict；读失败返回默认值，写失败静默（GUI 不因配置损坏崩溃）。
"""
import json
import os

import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(ROOT, "config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "gui.json")

_DEFAULTS = {
    # 凭据 CSV 路径；空或不存在 = 自动在项目根发现（名字随便改都能用）
    "cred_path": "",
    # 音频设备（int 索引；None=启动时自动识别默认）
    "remote_idx": None,
    "self_idx": None,
    # 目标语言 zh/en
    "tgt": "zh",
    # 实时模型（build_url 读取；改模型不用动代码）
    "model": "qwen3.5-livetranslate-flash-realtime",
    # VAD 阈值
    "vad_threshold": 0.2,
    # 静音门控电平阈值（0..1）：采集回调里"多大峰值电平算静默"，低于则丢弃不推给后端。
    # None/0 = 关闭过滤（全推）。GUI"录制行为"滑块调节，实时生效。
    "silence_threshold": 0.03,
    # 静默判定时长(ms)：>该时长停顿才判一段话结束（服务端 server_vad）
    "silence_ms": 1200,
    # 落盘兜底定稿时长(秒)：partial 超该时长无更新即强制定稿落盘
    "force_finalize_after": 2.5,
    # 静音自己麦克风（GUI 开关；默认 False=采集本机声音）
    "self_muted": False,
    # 会话重建分钟
    "session_minutes": 30,
    # 方案A·A5 哑推流（0 token 长跑稳定性）：
    "dry_run": False,            # True=不联网不发音频，只跑采集/泵/会话生命周期测内存与稳定
    "dry_fail_every": 0,         # 分钟；>0 = 模拟定时断线驱动重连分支（dry_run 下有效）
    "dry_mem_report_min": 10,    # 分钟；RSS 采样/日志间隔（dry_run 下有效）
    # 字幕窗
    "sub_pos": None,       # [x, y]（None=桌面右下首次定位）
    "sub_opacity": 0.85,   # 0.3–1.0 整体不透明度
    "sub_bg_alpha": 28,    # 衬底 α 0–255（近全透默认）
    "sub_ontop": True,
    "sub_clickthrough": False,
    "sub_width": 720,
    "sub_rows": 3,         # 最近 N 轮显示（字幕窗）
    "sub_contrast": 0,
    "sub_font_pt": 20,     # 字幕基准字号（英文=0.7×，译文=英文+trans_pt_gap）
    "trans_pt_gap": 2,     # 译文(中文)比英文大的 pt 数（用户要求英文不变、中文只大1-2）
    # 控制窗
    "ctl_pos": None,
    "ctl_ontop": True,
    # 回看
    "recent_n": 30,
    # 全局热键（键名加 vk 字符串；支持单字母/数字/F1-F12；ctrl/alt 布尔修饰键）
    "hotkey_scheme": {
        "toggle_run":  {"vk": "S", "ctrl": True, "alt": True},
        "export":      {"vk": "E", "ctrl": True, "alt": True},
        "toggle_lang": {"vk": "L", "ctrl": True, "alt": True},
        "toggle_sub":  {"vk": "W", "ctrl": True, "alt": True},
    },
}


def _load():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


def get_all():
    """返回合并默认值的完整配置 dict。"""
    out = dict(_DEFAULTS)
    out.update(_load())
    return out


def save(partial: dict):
    """把 partial 合并进现有配置并写盘。写失败不抛（仅静默）。"""
    cfg = dict(_DEFAULTS)
    cfg.update(_load())
    cfg.update(partial)
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass