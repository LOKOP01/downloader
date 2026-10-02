# -*- coding: utf-8 -*-
"""运行日志：把出错信息落到 `<程序目录>/logs/app.log`

打包后的 exe 没有控制台，异常被 PySide6 / 线程吞掉后什么都不剩，
出问题只能靠猜。这里统一接管三处异常入口：
  · 主线程未捕获异常（sys.excepthook）
  · 子线程未捕获异常（threading.excepthook）
  · Qt 槽函数里的异常（PySide6 走 sys.excepthook，同上）

单文件 2MB，保留 3 个历史文件，不会无限长大。
程序目录不可写时退回 `%LOCALAPPDATA%/视频下载器/logs`。
"""
import logging
import os
import sys
import threading
from collections import deque
from logging.handlers import RotatingFileHandler

LOGGER_NAME = "vdl"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3
# 界面（「下载任务」页的日志面板）能回看的历史行数
MEMORY_LINES = 500
_FMT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_state = {"path": "", "dir": "", "memory": None}


class MemoryHandler(logging.Handler):
    """把日志留在内存环形缓冲里，供界面显示

    界面用轮询取，而不是让这里发 Qt 信号：日志可能来自任意下载线程，
    只加锁入队、由 UI 线程定时批量取走，既没有跨线程信号的坑，
    也天然自带节流（一帧最多刷一次）。
    """

    def __init__(self, capacity: int = MEMORY_LINES):
        super().__init__()
        self._lock = threading.Lock()
        self._buf = deque(maxlen=max(1, capacity))
        self._seq = 0
        self.setFormatter(logging.Formatter(_FMT, _DATEFMT))

    def emit(self, record: logging.LogRecord):
        try:
            text = self.format(record)
        except Exception:  # noqa: BLE001 - 日志本身不能把程序带崩
            return
        with self._lock:
            self._seq += 1
            self._buf.append((self._seq, record.levelno, text))

    def snapshot(self, limit: int = 0) -> tuple:
        """返回 (当前序号, [(级别, 文本), ...])；limit>0 时只取最后 limit 行"""
        with self._lock:
            items = list(self._buf)
            seq = self._seq
        if limit > 0:
            items = items[-limit:]
        return seq, [(lvl, txt) for _, lvl, txt in items]

    def since(self, seq: int) -> tuple:
        """返回 seq 之后的新日志，以及当前序号"""
        with self._lock:
            items = [it for it in self._buf if it[0] > seq]
            cur = self._seq
        return cur, [(lvl, txt) for _, lvl, txt in items]

    def clear(self):
        with self._lock:
            self._buf.clear()


def log_path() -> str:
    """当前日志文件路径（未初始化时返回空串）"""
    return _state["path"]


def log_dir() -> str:
    """日志所在目录（未初始化时返回空串）"""
    return _state["dir"]


def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def _pick_dir(base_dir: str) -> str:
    """优先写在程序目录下，不行就退到用户目录"""
    candidates = []
    if base_dir:
        candidates.append(os.path.join(base_dir, "logs"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "视频下载器", "logs"))
    for d in candidates:
        if _writable(d):
            return d
    return ""


def _install_hooks(logger: logging.Logger):
    """未捕获异常一律写进日志（否则打包后没人看得见）"""
    def _main_hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logger.critical("未捕获异常", exc_info=(exc_type, exc, tb))

    sys.excepthook = _main_hook

    def _thread_hook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        name = args.thread.name if args.thread else "?"
        logger.error("线程 %s 未捕获异常", name,
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = _thread_hook


def setup(base_dir: str = "", level: int = logging.INFO) -> logging.Logger:
    """初始化日志（重复调用只生效一次）"""
    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, "_vdl_ready", False):
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    fmt = logging.Formatter(_FMT, _DATEFMT)

    d = _pick_dir(base_dir)
    if d:
        _state["dir"] = d
        _state["path"] = os.path.join(d, "app.log")
        fh = RotatingFileHandler(_state["path"], maxBytes=MAX_BYTES,
                                 backupCount=BACKUP_COUNT, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    # 内存缓冲始终挂上：「下载任务」页的日志面板靠它显示（程序目录不可写也能看）
    mh = MemoryHandler()
    mh.setLevel(level)
    _state["memory"] = mh
    logger.addHandler(mh)
    if not getattr(sys, "frozen", False):
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(level)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    logger._vdl_ready = True
    _install_hooks(logger)
    return logger


def get_logger(name: str = "") -> logging.Logger:
    """取子 logger（`vdl.downloader` 之类），沿用同一个文件句柄"""
    return logging.getLogger(LOGGER_NAME + ("." + name if name else ""))


# --------------------------------------------------------------------------- #
# 给界面用的读取接口（都在锁内取，UI 线程调用安全）
# --------------------------------------------------------------------------- #
def _memory() -> MemoryHandler:
    h = _state.get("memory")
    return h if isinstance(h, MemoryHandler) else None


def snapshot(limit: int = 200) -> tuple:
    """最近 limit 行日志，返回 (序号, [(级别, 文本), ...])；未初始化时返回空"""
    h = _memory()
    if h is None:
        return 0, []
    return h.snapshot(limit)


def fetch_since(seq: int) -> tuple:
    """seq 之后的新日志，返回 (新序号, [(级别, 文本), ...])"""
    h = _memory()
    if h is None:
        return seq, []
    return h.since(seq)


def clear_memory():
    """清掉界面能回看的内存日志（不动 app.log）"""
    h = _memory()
    if h is not None:
        h.clear()
