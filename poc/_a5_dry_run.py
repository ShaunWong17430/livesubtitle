# -*- coding: utf-8 -*-
"""方案A · A5 哑推流长跑稳定性启动器（0 token）。

用真实采集（远端 loopback + 麦克风）+ Controller dry_run=True 跑长时，
验证「连续运行不中断 + 内存增长 <50MB」，**不联网、不发音频 → 不烧 token**。

用法：
  pyforsub\\python.exe poc/_a5_dry_run.py [--minutes 120] [--rebuild 30] [--fail-every 45]

参数：
  --minutes    跑多久（默认 120，即 2h A5）
  --rebuild    session 重建周期分钟（默认 30，会实际触发 controller 的优雅重建分支）
  --fail-every 模拟断线分钟（>0 时 DrySession 定时置 dead 驱动重连分支；默认 0=不断）
  --mem        RSS 采样/日志间隔分钟（默认 10；内存增长/峰值走 logs/app.log 与 stdout）
退出码 0=内存增长<50MB 且无异常；1=失败/异常。
"""
import argparse
import io
import os
import sys
import threading
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
# 相对本文件推导项目根（文件夹整体搬移不影响）
_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from src.gui.logging_setup import setup_logging
from src.gui.controller import Controller, _process_rss_kb

DEFAULT_CSV = os.path.join(_PROJ, "默认业务空间-apiKey-7021723.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=120.0)
    ap.add_argument("--rebuild", type=float, default=30.0)
    ap.add_argument("--fail-every", type=float, default=0.0)
    ap.add_argument("--mem", type=float, default=10.0)
    a = ap.parse_args()
    setup_logging()

    from src.gui.controller import load_cred, build_url
    cred = load_cred(DEFAULT_CSV)
    url = build_url(cred)
    api_key = cred.get("apiKey", "")
    if not api_key:
        print("ERRO: 未找到 apiKey，无法构造 Controller（虽 dry-run 不联网，仍需要它初始化）")
        return 1

    # 复用 GUI 的远端/麦克风默认设备选择
    from src.audio import devices as dev
    import pyaudiowpatch as pyaudio
    p = pyaudio.PyAudio()
    try:
        remote_idx = dev.default_remote_loopback(p)
    except Exception as e:
        print("ERRO: 无法识别远端 loopback 设备：", e)
        return 1
    self_idx = None
    try:
        rinfo = p.get_device_info_by_index(remote_idx)
        self_idx, _how = dev.pick_self_mic(p, rinfo)
    except Exception:
        pass
    p.terminate()
    print(f"远端[{remote_idx}] 自己[{self_idx}] | dry_run=True minutes={a.minutes}")

    ctl = Controller(url, api_key, "auto", "zh", remote_idx, self_idx,
                     session_minutes=a.rebuild, self_muted=True,
                     dry_run=True, dry_fail_every=a.fail_every,
                     mem_report_min=a.mem)
    stop_at = time.time() + a.minutes * 60
    errors = []
    ctl.closed.connect(lambda r: print("close:", r))
    ctl.error.connect(lambda e: errors.append(e) or print("ERRO:", e))

    t = threading.Thread(target=ctl.run, name="a5-dry", daemon=True)
    t.start()
    # 挂机监控：本启动器只负责等待时长到点后停 pump；内存统计由 controller 打印。
    try:
        while time.time() < stop_at:
            if not t.is_alive():
                print("ERRO: 控制器提前退出（非正常结束路径）")
                return 1
            time.sleep(2)
    finally:
        ctl.stop()
        t.join(timeout=15)

    # 汇总
    rss = _process_rss_kb()
    grow = (rss - ctl._mem_start) if (rss and ctl._mem_start) else None
    print("\n======== A5 dry-run 汇总 ========")
    print(f"起始 RSS: {ctl._mem_start} KB")
    print(f"峰值 RSS: {ctl._mem_peak} KB")
    print(f"结束 RSS: {rss} KB")
    print(f"内存增长: {grow} KB = {grow/1024 if grow else '?'} MB")
    import src.gui.controller as CM
    if isinstance(ctl.session, CM.DrySession):
        print(f"DrySession rebuild 次数: {ctl.session._rebuilds}")
    ok = (grow is not None and grow / 1024 < 50.0) and not errors
    print("\nA5 内存<50MB:", "PASS ✓" if ok else "FAIL ✗", ("原始错误: %s" % errors if errors else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())