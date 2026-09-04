# -*- coding: utf-8 -*-
"""凭据解析 + 实时端点构建（统一实现 · src/cred.py）。

单一来源：GUI（src/gui）与 CLI（src/stage1）共用，避免两处实现 drift。
两个职责：
  1. load_cred(path) —— 稳健解析阿里百炼下载的凭据 CSV，**不怕格式变动**：
     - 兼容 "key,value" 每行一对、带 BOM、带表头行、key=value、多余列、全角标点等。
     - 用 Python csv 模块（自动处理引号/换行/分隔符），拒绝仅按逗号 partition 的脆弱写法。
     - 精确键优先，缺失时大小写不敏感回退（Key/APIKEY/...）。
  2. build_url(cred, model) —— 从 apiHost / workspaceId 推导 wss 端点 + 指定模型。
     model 由调用方传入（config 或默认）。region/子域默认中国(北京)，
     apiHost 里已带 workspaceId+region 时按它拼接。

注意：仅解析增量文本；若遇到无法识别的未知新 CSV 布局，返回空 dict（调用方按"未找到
凭据"处理），不炸进程。
"""
import csv
import io
import os

# 实时端点固定版本参数；model 可由 config/apiHost 影响，默认值放这里
DEFAULT_MODEL = "qwen3.5-livetranslate-flash-realtime"

_KEY_ALIASES = {
    "apikey": "apiKey",
    "api_key": "apiKey",
    "key": "apiKey",
    "workspaceid": "workspaceId",
    "workspace_id": "workspaceId",
    "wsid": "workspaceId",
    "apihost": "apiHost",
    "api_host": "apiHost",
    "host": "apiHost",
    "endpoint": "apiHost",
    "model": "model",
    "region": "region",
}


def _normalise(s):
    s = (s or "").replace("\ufeff", "").replace("　", " ")
    # 去掉可能的前后引号
    s = s.strip().strip('"').strip("'")
    return s


def _rows(text):
    """把文本按 CSV 规则拆成行列表（[[...]], 每行已 trim）。"""
    rows = []
    for line in csv.reader(io.StringIO(text)):
        rows.append([c.strip() for c in line])
    return [r for r in rows if any(c != "" for c in r)]


def _pair_from_row(row):
    """把一行变 (key, value)；无法解析返回 None。"""
    cleaned = [c for c in row if c != ""]
    if len(cleaned) < 1:
        return None
    if len(cleaned) == 1:
        # "key=value" 或 "key:value"
        cell = cleaned[0]
        for sep in ("=", ":", "，", ";"):
            if sep in cell:
                k, _, v = cell.partition(sep)
                return _normalise(k), _normalise(v)
        return None
    # "key,value[,extra...]" 取前两个非空
    return _normalise(cleaned[0]), _normalise(cleaned[1])


def load_cred(path):
    """稳健解析凭据 CSV（或 cfg/ini 风格），返回 dict。失败/未知布局返回 {}。"""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            text = f.read()
    except Exception:
        return {}
    rows = _rows(text)
    cred = {}
    if not rows:
        return cred
    # 跳过纯表头行：首行若是 (header,header) 或只含 key/value 之类，不产出值
    header = rows[0]
    # 若首行是表头（两个值都非空且不是明显的 key:value），跳过，同时记下列名用于转置
    skip_header = False
    header_map = None
    if len(header) >= 2:
        l0, l1 = header[0].lower().strip(), header[1].lower().strip()
        # 形如 ("key","value") 或 ("字段","值") 或 ("name","apiKey") 视为表头
        if l0 in ("key", "键", "字段", "name", "item") or l1.lower() in ("value", "值"):
            skip_header = True
        # 两列表头：第一列是名，第二列是值
        if (l0 == "key" or l0 in ("name", "item")) and l1 in ("value", "值"):
            header_map = ("col", 0, 1)
        # 一列表头 "key;value" 拆不开则不特殊处理
    data_rows = rows[1:] if skip_header else rows
    for row in data_rows:
        pr = _pair_from_row(row)
        if pr is None:
            continue
        k, v = pr
        if not k:
            continue
        # 规范化键名（大小写/别名）
        norm = _KEY_ALIASES.get(k.lower().replace(" ", ""))
        if norm:
            cred[norm] = v
        else:
            cred[k] = v  # 保留原始未知键，方便后续兜底读取
    return cred


def build_url(cred, model=None):
    """构造实时 wss 端点。model 不传用 DEFAULT_MODEL。
    - 优先 apiHost（已含 workspaceId.cn-beijing...）→ 取其 IP 前缀当 workspace 子域。
    - 否则 workspaceId → 拼 北京(CN-BEIJING) 标准子域。
    """
    model = model or (cred.get("model") or DEFAULT_MODEL)
    ws_id = cred.get("workspaceId")
    host = cred.get("apiHost", "")
    if host:
        base = host.strip()
        if not base.startswith(("ws://", "wss://")):
            # apiHost 形如 ws-zxx.cn-beijing.maas.aliyuncs.com，直接作主机
            return f"wss://{base}/api-ws/v1/realtime?model={model}"
        # 含协议（罕见）则按 host 原样，仅补 path
        return f"{base}/api-ws/v1/realtime?model={model}"
    if ws_id:
        ws = ws_id.strip()
        if not ws.startswith("ws-"):
            ws = f"ws-{ws}"
        return f"wss://{ws}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model={model}"
    return ""


def discover_cred_file(dirs=None, preferred=None):
    """在给定目录（默认项目根）里自动找阿里百炼凭据 CSV。文件名随便改都不影响使用。

    判定依据是**内容**（能解析出 apiKey）而非文件名；同名 hint 仅用于排序优先。
    返回绝对路径，找不到返回 None。
    """
    import os as _os
    # 收集候选：preferred 优先，再收集各目录下所有 .csv
    dirs = dirs or []
    if not dirs:
        me = _os.path.dirname(_os.path.abspath(__file__))      # .../src
        dirs = [_os.path.dirname(me)]                          # 项目根
    ordered = []
    def _score(n):
        low = n.lower()
        if "apikey" in low or "业务空间" in n or "key" in low:
            return 0
        if ".csv" in low:
            return 1
        return 2
    if preferred:
        ordered.append((preferred, -1))
    for d in dirs:
        if not _os.path.isdir(d):
            continue
        try:
            names = _os.listdir(d)
        except Exception:
            continue
        for n in names:
            if not n.lower().endswith(".csv"):
                continue
            p = _os.path.join(d, n)
            ordered.append((p, _score(n)))
    ordered.sort(key=lambda t: t[1])
    seen = set()
    for p, _sc in ordered:
        p_path = _os.path.abspath(p)
        if p_path in seen or not _os.path.isfile(p_path):
            continue
        seen.add(p_path)
        if load_cred(p_path).get("apiKey"):
            return p_path
    return None