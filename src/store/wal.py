# -*- coding: utf-8 -*-
"""会议记录持久化（WAL，Stage 3 · src/store/wal.py）。

满足 F-R1 / F-R2 / A7：
  - 每轮定稿（GUI 收到 final）**立即 append 一行 JSON** 到 `sessions/*.jsonl` 并 flush，
    崩溃/断电不丢已出字幕。
  - 启动时可从最近的 `.jsonl` 恢复已出字幕（A7 崩溃恢复）。
  - 记录含 speaker、绝对墙钟(abs_start/abs_end)、原文/译文、语种、写入时间。

文件命名（F-R6 精神）：`{会议开始北京时间}_会议.jsonl`，如 `2026-09-02_1430_会议.jsonl`。
崩溃后重启 → load_latest() 拿回它，_turn_log 由空态填充为 N 条，下载/回看即可复用。

清空语义（F-D14）：只清内存/字幕/回看，**不删 .jsonl**（防误删）。若用户要继续新会议，
可手动删该文件或本类提供 close_meeting()（由控制窗「清空」可选勾选"同时删除本会议文件"）。
"""
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")

FORMAT_VER = 1


def _root():
    # wal.py 位于 src/store/ 下 → 向上 3 层到项目根（生成在根下的 sessions/）
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def sessions_dir():
    d = os.path.join(_root(), "sessions")
    os.makedirs(d, exist_ok=True)
    return d


def start_filename(meeting_start=None):
    """生成唯一会议文件名（含北京时间）。meeting_start 为 epoch 秒。"""
    ts = meeting_start if meeting_start else time.time()
    base = datetime.fromtimestamp(ts, BEIJING).strftime("%Y-%m-%d_%H%M%S")
    return f"{base}_会议.jsonl"


class Wal:
    """append-only 会议记录（每轮定稿立即写盘 + flush）。"""

    def __init__(self, path):
        self.path = path
        self._count = 0
        self._seen = set()          # 已写 user_item_id（本会话内）
        self._last = {}             # uid -> (asr_final, trans_final)（内容相同去噪）
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8")

    def append_turn(self, turn):
        """把一轮定稿写进 .jsonl。返回 True 已写，False 重复/空/异常。

        语义：last-wins。ASR 定稿即出时可能 trans 还没到（只写 asr）；
        译文后到（同一 user_item_id 重入）→ **允许追加一条完整更新行**，
        load() 时同 uid 取最后一条——恢复/导出拿到带译文的完整版。
        """
        uid = getattr(turn, "user_item_id", None)
        # 只写有内容的轮（原文或译文至少一项），空壳不落
        if not (turn.asr_final or turn.trans_final):
            return False
        # 内容与上次完全相同 → 去噪跳过（如服务端重复 final）
        prior = self._last.get(uid)
        if prior == (turn.asr_final, turn.trans_final):
            return False
        rec = {
            "v": FORMAT_VER,
            "user_item_id": uid,
            "speaker": getattr(turn, "speaker", "?"),
            "abs_start": getattr(turn, "abs_start", None),
            "abs_end": getattr(turn, "abs_end", None),
            "asr": turn.asr_final,
            "trans": turn.trans_final,
            "src_lang": getattr(turn, "src_lang", ""),
            "trans_lang": getattr(turn, "trans_lang", ""),
            "ts": time.time(),
        }
        try:
            self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._fh.flush()
            self._seen.add(uid)
            self._last[uid] = (turn.asr_final, turn.trans_final)
            self._count += 1
            return True
        except Exception:
            return False

    @property
    def count(self):
        return self._count

    def append_marker(self, what, ts=None):
        """写一条中断/事件标记（断线、重建等，F-R4）。返回 True。"""
        rec = {
            "v": FORMAT_VER,
            "kind": "marker",
            "what": what,
            "ts": ts if ts is not None else time.time(),
        }
        try:
            self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._fh.flush()
            self._count += 1
            return True
        except Exception:
            return False

    def close(self):
        try:
            self._fh.flush()
            self._fh.close()
        except Exception:
            pass


def load(path):
    """读 .jsonl → 轮次 dict 列表（保序，**last-wins**：同 uid 的更新行替换旧行——
    译文后到会追加一条完整更新行，取最后一条即带译文版本；marker 无 uid 全部保留）。"""
    out = []
    idx = {}   # uid -> out 内的位置
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            uid = rec.get("user_item_id")
            if uid:
                if uid in idx:
                    out[idx[uid]] = rec   # 替换旧版
                else:
                    idx[uid] = len(out)
                    out.append(rec)
            else:
                out.append(rec)            # marker 等无 uid 记录
    return out


def load_latest(sessions=sessions_dir()):
    """读最新一份 .jsonl（按文件名倒序）。返回 (path, records) 或 (None, [])。"""
    if not os.path.isdir(sessions):
        return None, []
    files = sorted(
        (f for f in os.listdir(sessions) if f.endswith(".jsonl")), reverse=True)
    if not files:
        return None, []
    path = os.path.join(sessions, files[0])
    return path, load(path)


def delete(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass