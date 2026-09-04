# -*- coding: utf-8 -*-
"""会议记录导出（Stage 2 · src/gui/exporter.py）。

满足 F-R3 / F-R6 / F-R7 的 Stage-2 范围：把内存 turn_log 导出到当前截点，三种格式：
  .md  — 双语对照段落（含绝对北京时间 + 说话人标注 + 语种）
  .srt — 标准字幕（可拖进播放器/剪辑）
  .txt — 纯原文流水
文件名默认含会议开始北京时间；输出到用户 QFileDialog 所选路径（F-R7 在 control_window 层）。
以 in-memory 记录为准（Stage 3 才有 WAL .jsonl，F-R1）。
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")

SPEAKER_ZH = {"remote": "远端", "self": "自己"}


def speaker_zh(sp):
    return SPEAKER_ZH.get(sp, sp or "?")


def dstr(ts, fmt="%H:%M:%S"):
    if ts is None:
        return "--:--:--"
    return datetime.fromtimestamp(ts, BEIJING).strftime(fmt)


def default_basename(meeting_start, dur_min):
    """F-R6: 2026-09-02_1430_Teams会议_62min"""
    start = dstr(meeting_start, "%Y-%m-%d_%H%M") if meeting_start else "meeting"
    dur = max(0, int(round(dur_min)))
    return f"{start}_会议_{dur}min"


def _merge(turns, markers):
    """把 turns（带 abs_start）和 markers（(ts,what)）按时间混排成事件流。
    每个事件：{'kind':'turn','turn':t,'ts':ts} 或 {'kind':'marker','what':w,'ts':ts}。"""
    events = [{"kind": "turn", "turn": t, "ts": (t.abs_start if t.abs_start else 0)}
              for t in turns]
    events += [{"kind": "marker", "what": w, "ts": (ts if ts else 0)} for (ts, w) in markers]
    events.sort(key=lambda e: e["ts"])
    return events


def render_md(turns, meeting_start, dur_min, markers=None):
    lines = []
    lines.append(f"# 会议记录（{dstr(meeting_start, '%Y-%m-%d %H:%M:%S')} · 约 {dur_min:.0f} 分钟）\n")
    for ev in _merge(turns, markers or []):
        if ev["kind"] == "marker":
            lines.append(f"> ⚠️ **{ev['what']}**（{dstr(ev['ts'])}）\n")
            continue
        t = ev["turn"]
        sp = speaker_zh(t.speaker)
        start = dstr(t.abs_start)
        lines.append(f"### [{sp} · {start}]")
        if t.asr_final:
            lines.append(f"**原文**：{t.asr_final}")
        if t.trans_final:
            lines.append(f"**译文**：{t.trans_final}")
        lines.append("")
    return "\n".join(lines)


def render_srt(turns, meeting_start, dur_min, markers=None):
    """标准 SRT：每轮一条字幕（标记不入 SRT 时间轴，避免破坏字幕同步）。"""
    def tcode(seconds):
        if seconds is None:
            return "00:00:00,000"
        ms = int(seconds * 1000)
        h, rem = divmod(ms, 3600000)
        m, rem = divmod(rem, 60000)
        s, ms = divmod(rem, 1000)
        return "%02d:%02d:%02d,%03d" % (h, m, s, ms)

    blocks = []
    for i, t in enumerate(turns, 1):
        s0 = t.abs_start
        e0 = t.abs_end if t.abs_end is not None else (t.abs_start or 0) + (2.0 if 1 else 0)
        if t.abs_end is None:
            e0 = (t.abs_start or 0) + 2.0
        body = f"[{speaker_zh(t.speaker)}] {t.asr_final}" if t.asr_final else ""
        if t.trans_final:
            body = (body + "\n" if body else "") + t.trans_final
        if not body:
            continue
        blocks.append(f"{i}\n{tcode(s0)} --> {tcode(e0)}\n{body}\n")
    return "\n".join(blocks).rstrip("\n") + "\n"


def render_txt(turns, meeting_start, dur_min, markers=None):
    lines = []  # 纯原文流水 + 时间 + 说话人；标记以 >>> 起
    for ev in _merge(turns, markers or []):
        if ev["kind"] == "marker":
            lines.append(f">>> 中断：{ev['what']}（{dstr(ev['ts'])}）")
            continue
        t = ev["turn"]
        if not t.asr_final:
            continue
        sp = speaker_zh(t.speaker)
        start = dstr(t.abs_start)
        lines.append(f"[{sp} · {start}] {t.asr_final}")
    return "\n".join(lines) + ("\n" if lines else "")


def export(turns, path, meeting_start=None, dur_min=0.0, markers=None):
    """按扩展名写文件。返回写入的字节数或抛异常。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".md":
        text = render_md(turns, meeting_start, dur_min, markers=markers)
    elif ext == ".srt":
        text = render_srt(turns, meeting_start, dur_min, markers=markers)
    elif ext in (".txt", ""):
        text = render_txt(turns, meeting_start, dur_min)
    else:
        raise ValueError(f"不支持的导出格式: {ext}")
    if not text.endswith("\n"):
        text += "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return len(text.encode("utf-8"))