# 安装 uv
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 重启命令行，将uv加入path
    set Path=C:\Users\YourUserName\.local\bin;%Path%   (cmd)
    $env:Path = "C:\Users\YourUserName\.local\bin;$env:Path"   (powershell)

# 进入拷过来的项目目录，如：
cd E:/LiveSubtitle

# 重建环境（自动下载 3.10 + 按锁文件装包）
uv venv --python 3.10 --relocatable
uv sync

# 验证
.venv\python.exe -c "import numpy; print('OK', numpy.__version__)"

---

# 中英双语实时会议字幕工具（Teams 会议记录）

个人自用工具：在 Microsoft Teams 会议（或其他需要"听远端+记录自己"的场景）中，实时抓取系统音频，调用阿里云百炼 `qwen3.5-livetranslate-flash-realtime` 生成**中英双语字幕**，半透明置顶悬浮显示；会议结束可导出会议记录（`.md` / `.srt` / `.txt`）。

> ⚠️ **合规提醒**：本工具会记录会议中**所有人的语音内容**（含你自己）。使用前请确保已获得参会人同意（或按你所在地区法律要求告知），本工具不提供任何形式的规避/匿名。
>
> ⚠️ **Teams 限制说明**：经实测，Windows WASAPI Loopback 无法捕获 Teams 播放的声音（Teams 已知拦截系统 loopback）。本工具可正常捕获浏览器/播放器/系统输出的声音；**Teams 场景需按下方"搭配方案"使用**（双机/扬声器输出到可捕获端点）。

---

## 一、快速开始（三步）

### 第 1 步：放好凭据文件

把阿里云百炼控制台下载的 **API Key CSV** 放到项目根目录（即 `start.bat` 所在文件夹）。文件名**随意**，程序会自动按内容识别（能解析出 `apiKey` 就算数）。

CSV 里**必须包含**的字段（只需前两个即可）：

| 字段 | 必须 | 说明 |
|---|---|---|
| `apiKey` | ✅ | 鉴权密钥（形如 `sk-...`） |
| `apiHost` | ✅ | 端点主机（形如 `ws-xxxx.cn-beijing.maas.aliyuncs.com`） |
| `workspaceId` | 可选 | `apiHost` 缺失时的兜底 |

> 安全说明：API Key **只放 CSV**，不写入 `config/gui.json`（gui.json 可能被分享/提交，避免泄露密钥）。模型名等非敏感配置在 gui.json。

### 第 2 步：双击启动

双击 **`start.bat`**（或在项目根运行 `pyforsub\python.exe -m src.gui.main`）。

会弹出两个窗口：
- **控制窗**（设置/开始/停止/回看，关闭它即退出程序）
- **字幕浮窗**（半透明置顶，显示最近几轮双语字幕）

系统托盘会出现图标（会议字幕工具），可右键显示/隐藏字幕、退出。

### 第 3 步：选设备 → 开始 → 开会

1. 在控制窗确认「远端(Loopback)」与「麦克风(自己)」设备选择正确（可用「刷新」重新扫描）。
2. **重要**：若希望把自己说话也记进记录（默认记录），**保持「静音自己」不勾选**；若只记远端，勾选「静音自己」。
3. 点 **「开始」**——状态栏显示「已连接，正在实时翻译…」即开始出字幕。
4. 正常开会。会议结束后点 **「停止」**，再点 **「下载会议记录」** 选格式导出。

> 开会期间想隐藏字幕窗，按 `Ctrl+Alt+W`（或托盘菜单）。

---

## 二、界面说明

### 控制窗（主窗口，左侧控制 / 右侧回看）

| 区域 | 内容 |
|---|---|
| 设备 | 远端(Loopback) 下拉（抓"正在播放到扬声器/耳机"的声音）、麦克风(自己) 下拉、刷新按钮 |
| 运行控制 | 开始 / 停止；目标语言 中⇄英 切换；清空记录 |
| 状态区 | 用量(token) 显示、连接状态、电平条（远端/自己） |
| 录制行为 | 静音自己（录音不采本机声音）、静默判定(ms)、落盘兜底(秒)、**静默门控(电平滑块)** |
| 字幕样式 | 整体不透明度、衬底α(0-255)、高对比、字体pt（英文小字=0.7×，译文大 2pt） |
| 会议记录回看 | 右侧宽屏：本次会议已定稿的双语轮次，可滚动查看 |

### 字幕浮窗

半透明置顶悬浮窗，每轮显示：
- 头部：说话人（远端/自己）+ 时间
- 原文（小字，如英文）
- 译文（稍大 2pt，如中文）

可拖动位置（自动记忆）；`sub_clickthrough` 开启后鼠标可穿透（开会时不挡操作）。

---

## 三、全局热键

默认（可在 `config/gui.json` 的 `hotkey_scheme` 修改，改完重启生效）：

| 热键 | 功能 |
|---|---|
| `Ctrl+Alt+S` | 开始 / 暂停 |
| `Ctrl+Alt+E` | 导出会议记录（弹保存框） |
| `Ctrl+Alt+L` | 切换目标语言 中 ⇄ 英 |
| `Ctrl+Alt+W` | 显示 / 隐藏字幕浮窗 |

改键示例（把切换字幕改成 F9）：
```json
"hotkey_scheme": { "toggle_sub": { "vk": "F9", "ctrl": true, "alt": true } }
```

> 热键只支持 `ctrl`/`alt` 两个修饰位（暂不支持 Shift/Win）。
> 热键没反应时：先看 `logs/app.log` 是否出现 `RegisterHotKey ... FAIL`（多为另一个 GUI 实例占用了组合键，请勿重复开两个实例）。

---

## 四、config/gui.json 可调项

首次运行会自动生成 `config/gui.json`（基于默认值）。常用项：

| 键 | 默认 | 说明 |
|---|---|---|
| `cred_path` | `""` | 凭据 CSV 路径；空 = 自动发现 |
| `model` | `qwen3.5-livetranslate-flash-realtime` | 实时模型（换模型改这里，不动代码） |
| `tgt` | `zh` | 目标语言 |
| `remote_idx` / `self_idx` | `null` | 设备索引；null = 启动自动识别（在 GUI 里选后会自动存） |
| `silence_ms` | 1200 | 静默判定(ms)：停顿超过才判一句结束。调大抗碎、断句更慢 |
| `force_finalize_after` | 2.5 | 落盘兜底(秒)：长段朗读不出字幕时强制定稿 |
| `silence_threshold` | 0.03 | 静默门控电平（0..1，GUI 滑块实时调）。低于此的块不进后端 |
| `self_muted` | false | 静音自己（开会前确认此项状态！） |
| `session_minutes` | 30 | 每 N 分钟优雅重建会话（长会不中断） |
| `sub_opacity` | 0.85 | 字幕窗整体不透明度（0.3–1.0） |
| `sub_bg_alpha` | 28 | 衬底 α（0–255，近全透） |
| `sub_font_pt` | 20 | 字号基准（英文=0.7×，译文=英文+2） |
| `trans_pt_gap` | 2 | 译文比英文大的 pt 数 |
| `sub_ontop` | true | 字幕窗置顶 |
| `sub_clickthrough` | false | 字幕窗鼠标穿透 |
| `sub_rows` | 3 | 字幕窗显示最近 N 轮 |
| `sub_contrast` | 0 | 高对比衬底 0/1/2/3 |
| `dry_run` | false | A5 哑推流测试模式（0 token，见"开发测试"） |
| `hotkey_scheme` | — | 全局热键配置 |

> `silence_ms`（断句时长）与 `silence_threshold`（静音门控电平）是**两个不同概念**：前者决定"停多久算一句"，后者决定"多小的声音算静音、丢弃不进后端"。两者都在 GUI「录制行为」可调。

---

## 五、导出格式

会议结束后「下载会议记录」，按所选扩展名导出到指定路径：

| 格式 | 内容 | 典型用途 |
|---|---|---|
| `.md` | 双语对照段落 + 北京时间(精确到秒) + 说话人(远端/自己) + 语种标注 + 断线/重建标记 | 会议纪要、事后整理 |
| `.srt` | 标准字幕（时间轴 + 文本） | 拖进播放器/剪辑软件校对 |
| `.txt` | 纯原文流水 | 快速检索 |

原始逐轮记录实时落盘在 `sessions/{会议开始时间}_会议.jsonl`（崩溃不丢已出字幕，重启自动恢复）。

---

## 六、常见问题

**Q1：为什么自己的声音被记成了"远端"？**
多半是测试时有其他程序在播放声音（浏览器/音乐），与你的说话声被合并成同一服务端声源。真实开会时保持 Teams 独占输出即可。另外确认「静音自己」未勾选——勾选时自己的声音**整路不采集**，不会出现在字幕里。

**Q2：开会没字幕 / 识别差？**
- 确认状态栏是「已连接…」而非报错
- 看控制窗电平条：远端说话时远端电平应起伏；没起伏=没采到设备声音（设备选错 / 音量 / Teams 拦截）
- 断句太碎 → 调大「静默判定(ms)」；长段不出字 → 调小「落盘兜底(秒)」或调小静默判定
- 环境噪声多 → 调高「静默门控(电平)」滑块（实时生效）

**Q3：用量怎么算？**
GUI 显示的是服务端会话累计 token（与百炼控制台账单同口径）。参考价：音频输入约 7 token/秒、译文输出约 12.5 token/秒；免费额度 100 万 tokens ≈ 15-18 小时会议。

**Q4：字幕窗拖不动 / 鼠标点不到？**
`sub_clickthrough` 开着时鼠标穿透。关闭穿透即可拖动。

**Q5：文件夹整体移动后还能用吗？**
能。所有路径都按 `__file__` 相对推导，解释器 `pyforsub\python.exe` 也在文件夹内。移走后到新位置双击 `start.bat` 即可（凭据 CSV 也放回项目根）。

**Q6：改模型怎么改？**
编辑 `config/gui.json` 的 `"model"`，重启 GUI。模型必须为百炼 realtime 兼容模型（默认 `qwen3.5-livetranslate-flash-realtime`）。

---

## 七、开发测试（可选）

脱会即可验证，不需真开会：

- **播音频测试**：用任意播放器播放音频（英文视频/录音），声音从扬声器/耳机出声即会被"远端 Loopback"抓到 → 出字幕。
- **命令行最小闭环**：`pyforsub\python.exe -m src.stage1.main --mode file --wav 16k_english.wav`（列设备用 `--mode list`）
- **A5 2 小时稳定性（0 token 哑推流）**：`pyforsub\python.exe poc/_a5_dry_run.py --minutes 120 --rebuild 30 --fail-every 45`
- **日志**：运行日志在 `logs/app.log`（自动轮转 1MB×10），排障先看它。

---

## 八、目录结构

```
dsh_subtitle/
├─ start.bat              一键启动（双击）
├─ README.md              本文件
├─ config/gui.json        运行配置（自动生成）
├─ sessions/*.jsonl       会议逐轮实时落盘
├─ logs/app.log           运行日志
├─ 默认业务空间-apiKey-*.csv  你的凭据（放项目根即可）
├─ src/
│  ├─ gui/                PyQt6 桌面 GUI（main/controller/control_window/subtitle_window/tray/hotkeys/…）
│  ├─ audio/              两路采集（远端 loopback + 麦克风）
│  ├─ translate/          百炼实时翻译会话（session/client/events）
│  ├─ store/wal.py        会议记录持久化
│  └─ cred.py             凭据解析（不怕 CSV 格式变动）
├─ poc/                   开发验证脚本（PoC/诊断/A5 长跑）
└─ pyforsub/              内置 Python 环境（随文件夹整体搬移）
```

## 九、免责与合规

本工具仅用于**你有权记录**的会议（个人自用）。记录他人语音前请确保符合当地法律与所在组织规定，并取得参会人同意。软件按现状提供，作者不对误识别、断线导致的记录缺失承担责任（已尽量通过 WAL 实时落盘与自动重连降低损失）。
