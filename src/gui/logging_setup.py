# -*- coding: utf-8 -*-
"""日志系统（Stage 3 · src/gui/logging_setup.py）。

给 GUI 与后台线程提供落盘运行日志（排障用），不阻塞实时采集。
- 用标准库 logging + RotatingFileHandler（按大小轮转，不额外装包）。
- 日志文件：<项目根>/logs/app.log（默认保留最近 10 份 × 1MB）。
- 同时镜像到控制台（便于前台跑 `python -m src.gui.main` 时直接看）。

用法：
    from src.gui.logging_setup import setup_logging, get_logger
    setup_logging()
    log = get_logger(__name__)
    log.info(...)
"""
import logging
import logging.handlers
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_DIR = os.path.join(ROOT, "logs")
LOG_PATH = os.path.join(LOG_DIR, "app.log")

_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO,
           "warning": logging.WARNING, "error": logging.ERROR}

_initialized = False


def setup_logging(level="info", console=True):
    """初始化根 logger（进程只应调用一次）。"""
    global _initialized
    if _initialized:
        return
    _initialized = True
    lvl = _LEVELS.get((level or "info").lower(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(lvl)
    root.handlers.clear()          # 避免重复 handler

    fmtr = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%H:%M:%S")
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=1024 * 1024, backupCount=10, encoding="utf-8")
        fh.setFormatter(fmtr)
        root.addHandler(fh)
    except Exception as e:
        # 日志目录写不了不致命（GUI 照常跑），print 提示即可
        print(f"[日志] 无法写入日志文件: {e}")

    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(fmtr)
        root.addHandler(ch)
    return root


def get_logger(name="app"):
    """取命名 logger（若未 setup 会自动建，避免 import 报错）。"""
    return logging.getLogger(name)