# -*- coding: utf-8 -*-
"""下载任务管理：多线程队列 + Qt 信号汇报进度"""
import os
import subprocess
import threading
import time
import uuid
from collections import deque

import requests
from PySide6.QtCore import QObject, Signal

from .applog import get_logger
from .domain import needs_cookie_for_url, referer_for
from .models import DownloadTaskInfo, clean_cookie

log = get_logger("download")

CHUNK = 256 * 1024

# --------------------------------------------------------------------------- #
# 多连接分段下载（IDM 式）
#
# 实测（B站 CDN，64MB）：单连接 13.0 MB/s，4 连接 23.8 MB/s（1.83x），
# 8 连接 23.6 MB/s（不再提升，已到带宽上限）。瓶颈是单连接的拥塞窗口，
# 不是缓冲区大小（把 CHUNK 从 256K 调到 1M 反而略慢）。
#
# 因此这里不写死「甜点值」，而是照 IDM 的做法把连接数交给用户：默认 8 条，
# 可调 1-32，带宽不够时多开的连接只是排队，不会更慢。
# --------------------------------------------------------------------------- #
SEGMENT_ENABLED = True           # 总开关（设置页可关）
SEGMENT_MIN_SIZE = 2 * 1024 * 1024   # 小于 2MB 不分片：连接开销大于收益
SEGMENT_MAX = 32                 # 每文件并发连接数上限（与 IDM 一致：1-32）
SEGMENT_DEFAULT_CONNECTIONS = 8  # 默认每文件连接数（IDM 默认也是 8）
CONN_BUDGET = 64                 # 全局连接预算：并发任务多时按此摊薄
CONNECT_TIMEOUT = 10             # 建连超时：坏掉的 CDN 要尽快判死好换下一个候选
READ_TIMEOUT = 30                # 读超时：这么久一个字节都没来就判这条连接废了
HTTP_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
FFMPEG_TIMEOUT = 900             # ffmpeg 合并上限（秒），超时判失败，别永远挂在「合并中」
SEGMENT_CHUNK_MIN = 1024 * 1024        # 动态分块的块大小下限
SEGMENT_CHUNK_MAX = 4 * 1024 * 1024    # 上限：太大慢连接会拖尾，太小请求太碎
CHUNK_SLOW_AFTER = 8.0                 # 一个分块跑满这么久才判定"这条连接太慢"
CHUNK_SLOW_RATIO = 0.30                # 慢于全场最快连接的这个比例就考虑重排
CHUNK_MIN_REMAINING_TIME = 4.0         # 按当前速度很快能下完时，省去重连开销
CHUNK_MAX_ATTEMPTS = 4                 # 同一分块最多重排几次（防死循环）
ABANDON_MIN_BUDGET = 8                 # 放弃重排的总次数预算下限

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://www.douyin.com/",
}


def headers_for(url: str) -> dict:
    """按目标域名设置正确的 Referer（外站 Referer 会被 CDN 403）"""
    headers = dict(HEADERS)
    headers["Referer"] = referer_for(url)
    return headers


def content_range_total(value: str) -> int:
    """从 Content-Range（`bytes 100-999/1000`）里解析资源总长度，失败返回 0"""
    if not value or "/" not in value:
        return 0
    tail = value.rsplit("/", 1)[-1].strip()
    if not tail.isdigit():
        return 0      # 形如 `bytes 0-99/*`：服务端不知道总长
    return int(tail)


def _parse_content_range(value: str) -> tuple:
    """解析 Content-Range，返回 (start, end, total, 是否可信)。"""
    try:
        unit, rest = value.split(" ", 1)
        range_part, total_part = rest.split("/", 1)
        start_text, end_text = range_part.split("-", 1)
        start, end = int(start_text), int(end_text)
        total = int(total_part) if total_part.strip().isdigit() else 0
    except (ValueError, IndexError, AttributeError):
        return 0, 0, 0, False
    return start, end, total, unit.lower() == "bytes" and end >= start


def _resource_id(headers) -> str:
    """取能唯一标识"这份资源"的响应头（用于判断续传时服务端是否换了源）

    字节总长度相同的两份不同视频无法靠长度区分，因此优先用 ETag /
    Last-Modified / Content-Disposition 这类强标识；都拿不到时返回空串
    （此时只做长度校验，即退化到旧行为）。
    """
    for key in ("ETag", "Last-Modified", "Content-Disposition"):
        val = (headers.get(key) or "").strip()
        if val:
            return f"{key}={val}"
    return ""


def _should_reconnect_slow_chunk(got: int, length: int, elapsed: float,
                                 best: float, budget_available: bool) -> bool:
    """只重排明显落后且继续下载还需较久的分块。"""
    if (not budget_available or got <= 0 or got >= length
            or elapsed < CHUNK_SLOW_AFTER or best <= 0):
        return False
    rate = got / elapsed
    return (rate < best * CHUNK_SLOW_RATIO
            and (length - got) / rate > CHUNK_MIN_REMAINING_TIME)


class DownloadManager(QObject):
    """下载队列管理器。所有信号都在 UI 线程接收（QueuedConnection）"""
    task_added = Signal(object)                       # DownloadTaskInfo
    task_progress = Signal(object)                    # DownloadTaskInfo
    task_finished = Signal(object)                    # DownloadTaskInfo

    def __init__(self, max_concurrent: int = 3, parent=None,
                 segment_enabled: bool = True,
                 segment_connections: int = SEGMENT_DEFAULT_CONNECTIONS):
        super().__init__(parent)
        self.max_concurrent = max(1, max_concurrent)
        self._segment_enabled = bool(segment_enabled)
        # 每文件连接数（IDM 式）：设置页可改，1-32
        self._segment_connections = self._clamp_connections(segment_connections)
        self._tasks = {}          # task_id -> DownloadTaskInfo
        self._cancel_flags = {}   # task_id -> threading.Event
        self._cookies = {}        # task_id -> Cookie 字符串（用于需要登录态的平台）
        # task_id -> 有序尝试序列 [(视频地址, 音频地址, [音频备用地址]), ...]
        self._plans = {}
        self._queue = []
        self._active = 0
        self._lock = threading.Lock()
        self._watchdog = None     # 卡住检测线程（第一次入队时启动）
        self._stall_seen = {}     # task_id -> (字节数, 该字节数最后一次变化的时间)

    # ------------------------------------------------------------------ #
    def add(self, url: str, save_path: str, name: str,
            fallbacks: list = None, cookies: str = "",
            audio_url: str = "", quality: str = "",
            audio_map: dict = None, url_backups: dict = None,
            audio_backups: dict = None) -> DownloadTaskInfo:
        save_path = self._unique_path(save_path)
        info = DownloadTaskInfo(task_id=uuid.uuid4().hex[:12], url=url,
                                save_path=save_path, name=name,
                                quality=quality or "")
        plan = self._build_plan(url, fallbacks, audio_url, audio_map,
                                url_backups, audio_backups)
        with self._lock:
            self._tasks[info.task_id] = info
            self._cookies[info.task_id] = cookies or ""
            self._plans[info.task_id] = plan
            self._queue.append(info.task_id)
        log.info("入队 %s | %s | 画质=%s | 候选=%d | 音频轨=%s | 保存到 %s",
                 info.task_id, url, quality or "-",
                 len(plan),
                 "有" if audio_url else "无", save_path)
        self.task_added.emit(info)
        self._ensure_watchdog()
        self._pump()
        return info

    @staticmethod
    def _build_plan(url: str, fallbacks, audio_url: str, audio_map: dict,
                    url_backups: dict, audio_backups: dict) -> list:
        """把「主地址 + 同档备用 CDN + 其他清晰度」摊平成有序尝试序列

        每一项是 (视频地址, 音频地址, [音频备用地址])。同一档位先试主地址，再试
        它的备用地址——B站 baseUrl 挂在 PCDN 节点（cn-jsnj-fx-*）上，实测经常
        TLS 重置或直接连不上，而 backupUrl 的 upos-* 镜像稳定可用；只认主地址
        时整单会一直卡在 0%。一档全挂才降到下一档画质，且每档自带它的音频轨
        （旧逻辑只有第一档带音频，降档后下出来的是无声视频）。
        """
        url_backups = url_backups or {}
        audio_backups = audio_backups or {}
        audio_map = audio_map or {}
        plan, seen = [], set()

        def push(video: str, audio: str = ""):
            key = (video, audio)
            if not video or key in seen:
                return
            seen.add(key)
            plan.append((video, audio,
                         [a for a in (audio_backups.get(audio) or [])
                          if a and a != audio]))

        for q_url in [url] + list(fallbacks or []):
            if not q_url:
                continue
            audio = audio_map.get(q_url, "") or (audio_url if q_url == url else "")
            push(q_url, audio)
            for alt in (url_backups.get(q_url) or []):
                push(alt, audio)
        return plan

    def _unique_path(self, save_path: str) -> str:
        """同一秒内文件名相同时自动追加 _1/_2 后缀，避免互相覆盖"""
        if not os.path.exists(save_path) and not self._path_in_use(save_path):
            return save_path
        root, ext = os.path.splitext(save_path)
        i = 1
        while True:
            candidate = f"{root}_{i}{ext}"
            if not os.path.exists(candidate) and not self._path_in_use(candidate):
                return candidate
            i += 1

    def _path_in_use(self, save_path: str) -> bool:
        with self._lock:
            return any(t.save_path == save_path and t.status in ("等待中", "下载中")
                       for t in self._tasks.values())

    def cancel(self, task_id: str):
        with self._lock:
            flag = self._cancel_flags.get(task_id)
            info = self._tasks.get(task_id)
            if flag:
                flag.set()
            elif info and info.status == "等待中":
                info.status = "已取消"
                if task_id in self._queue:
                    self._queue.remove(task_id)
                self.task_progress.emit(info)

    def retry(self, task_id: str):
        with self._lock:
            info = self._tasks.get(task_id)
            if (not info or info.status not in self.FINISHED_STATUS
                    or task_id in self._cancel_flags):
                return
            info.status = "等待中"
            info.error = ""
            info.downloaded = 0
            info.speed = 0
            self._queue.append(task_id)
        self.task_progress.emit(info)
        self._pump()

    def all_tasks(self):
        with self._lock:
            return list(self._tasks.values())

    # 已结束（不会再有进度更新）的状态集合
    FINISHED_STATUS = ("已完成", "已取消", "失败")

    # 「卡住」判定：任务在 STALL_WARN_AFTER 秒内一个字节都没涨，就写一条 WARNING
    # （下载线程还在跑但没有任何进展——事后查日志能直接看出卡在哪一步）
    STALL_WARN_AFTER = 60
    STALL_CHECK_INTERVAL = 15

    def finished_tasks(self, statuses=FINISHED_STATUS):
        """已结束的任务（用于「清空已完成」等操作）"""
        with self._lock:
            return [t for t in self._tasks.values() if t.status in statuses]

    def retry_tasks(self, tasks=None):
        """批量重试；tasks 为空时重试所有失败任务。返回实际重试的任务数"""
        with self._lock:
            targets = [t for t in (tasks if tasks is not None
                                   else self._tasks.values())
                       if t.status == "失败" and t.task_id not in self._cancel_flags]
            for t in targets:
                t.status = "等待中"
                t.error = ""
                t.downloaded = 0
                t.speed = 0
                self._queue.append(t.task_id)
            pending = list(targets)
        for t in pending:
            self.task_progress.emit(t)
        if pending:
            self._pump()
        return len(pending)

    def cancel_tasks(self, tasks=None):
        """批量取消；tasks 为空时取消所有未结束任务。返回实际取消的任务数"""
        with self._lock:
            pool = tasks if tasks is not None else list(self._tasks.values())
            targets = [t for t in pool if t.status in ("等待中", "下载中")]
            ids = [t.task_id for t in targets]
        for tid in ids:
            self.cancel(tid)
        return len(ids)

    def remove_tasks(self, task_ids) -> int:
        """从任务表移除记录（只清列表，不删已下载的文件）。

        只移除 `status in FINISHED_STATUS` 的任务，正在跑的任务会被跳过，
        避免下载线程回调时找不到 task_id。
        """
        removed = 0
        with self._lock:
            for tid in list(task_ids):
                info = self._tasks.get(tid)
                if info is None or info.status not in self.FINISHED_STATUS:
                    continue
                self._tasks.pop(tid, None)
                self._cookies.pop(tid, None)
                self._plans.pop(tid, None)
                self._cancel_flags.pop(tid, None)
                removed += 1
        return removed

    def clear_finished(self) -> int:
        """清除所有已结束的任务记录，返回清除条数"""
        return self.remove_tasks([t.task_id for t in self.finished_tasks()])

    def _pump(self):
        with self._lock:
            while self._active < self.max_concurrent and self._queue:
                task_id = self._queue.pop(0)
                if self._tasks[task_id].status != "等待中":
                    continue
                self._active += 1
                self._cancel_flags[task_id] = threading.Event()
                t = threading.Thread(target=self._run, args=(task_id,), daemon=True)
                t.start()

    def _run(self, task_id: str):
        info = self._tasks[task_id]
        cancel = self._cancel_flags[task_id]
        cookies = self._cookies.get(task_id, "")
        plan = self._plans.get(task_id) or [(info.url, "", [])]
        t0 = time.time()
        log.info("开始下载 %s | %s | 画质=%s | 候选=%d",
                 task_id, info.url, info.quality or "-",
                 len(plan))
        if (info.url or "").startswith("jmcomic://"):
            self._run_jmcomic(info, cancel, cookies)
            with self._lock:
                self._active -= 1
                self._cancel_flags.pop(task_id, None)
                # 重试禁漫任务仍需原来的 Cookie；移除任务记录时再释放。
            log.info("下载结束 %s 状态=%s 用时%.1fs", task_id, info.status,
                     time.time() - t0)
            self.task_finished.emit(info)
            self._pump()
            return
        # 依次尝试「主地址 → 同档备用 CDN → 下一档画质」，全部失败才标记失败
        last_err = ""
        for idx, (url, audio_url, audio_alts) in enumerate(plan):
            if cancel.is_set():
                break
            if idx > 0:  # 换地址时清掉上一个地址的残片，避免错误续传
                self._cleanup_temp_files(info.save_path)
            success = False
            # 同一地址最多 3 次（截断时用 .part 续传重试）
            for _ in range(3):
                if cancel.is_set():
                    break
                try:
                    self._download_once(info, url, cancel, cookies,
                                        audio_url, audio_alts)
                    last_err = ""
                    info.error = ""
                    success = True
                    break
                except _Cancelled:
                    last_err = ""
                    break
                except IncompleteDownload as e:
                    last_err = str(e)[:200]
                    info.error = last_err
                    log.warning("下载不完整，重试 %s（地址 %d/%d）：%s",
                                task_id, idx + 1, len(plan), e)
                    continue
                except (MissingDependency, ValueError) as e:
                    # 参数/依赖类错误（如缺 ffmpeg、地址无效）换其他地址也一样，
                    # 直接判定失败，不要浪费 3 次重试
                    last_err = str(e)[:200]
                    info.error = last_err
                    log.error("下载失败（不可重试）%s：%s", task_id, e,
                              exc_info=True)
                    break
                except Exception as e:  # noqa: BLE001 - 换备用地址再试
                    last_err = str(e)[:200]
                    info.error = last_err
                    log.error("下载出错 %s（地址 %d/%d）：%s",
                              task_id, idx + 1, len(plan), e, exc_info=True)
                    break
            if success or cancel.is_set():
                break
        if cancel.is_set():
            info.status = "已取消"
        elif last_err:
            info.status = "失败"
            info.error = last_err
        # 只要任务没有真正成功，就清掉可能残留的临时文件，
        # 避免用户下载目录里堆 .part / .meta
        if info.status != "已完成":
            self._cleanup_temp_files(info.save_path)
        info.speed = 0
        with self._lock:
            self._active -= 1
            self._cancel_flags.pop(task_id, None)
            # 保留 Cookie/候选地址直到任务记录被移除，失败任务才能完整重试。
        log.info("下载结束 %s 状态=%s 用时%.1fs 字节=%s/%s 错误=%s",
                 task_id, info.status, time.time() - t0,
                 info.downloaded, info.total, info.error or "-")
        self.task_finished.emit(info)
        self._pump()

    # ------------------------------------------------------------------ #
    # 卡住检测
    def _ensure_watchdog(self):
        if self._watchdog is not None:
            return
        self._watchdog = threading.Thread(target=self._watch_stalls,
                                          name="stall-watch", daemon=True)
        self._watchdog.start()

    def _watch_stalls(self):
        """盯着「一个字节都不涨」的任务：卡住时日志里要留下时间/URL/画质/字节数

        用户能看到的只有界面上「不动了」，这里把当时的现场写进 app.log，
        事后定位不必再靠复现。
        """
        while True:
            time.sleep(self.STALL_CHECK_INTERVAL)
            try:
                now = time.time()
                alive = set()
                for t in self.all_tasks():
                    if t.status not in ("下载中", "合并中"):
                        self._stall_seen.pop(t.task_id, None)
                        continue
                    alive.add(t.task_id)
                    got, ts = self._stall_seen.get(t.task_id, (t.downloaded, now))
                    if t.downloaded != got:
                        self._stall_seen[t.task_id] = (t.downloaded, now)
                    elif now - ts >= self.STALL_WARN_AFTER:
                        log.warning("任务疑似卡住 %s：已 %ds 无字节增长 | 状态=%s "
                                    "进度=%s/%s 画质=%s | %s",
                                    t.task_id, int(now - ts), t.status,
                                    t.downloaded, t.total, t.quality or "-",
                                    t.url)
                        self._stall_seen[t.task_id] = (got, now)
                for tid in list(self._stall_seen):
                    if tid not in alive:
                        self._stall_seen.pop(tid, None)
            except Exception:  # noqa: BLE001 —— 看门狗自己不能把程序带崩
                log.exception("卡住检测线程异常")

    # ------------------------------------------------------------------ #
    # 临时文件
    TEMP_SUFFIXES = (".part", ".vpart", ".apart", ".mpart",
                     ".part.meta", ".vpart.meta", ".apart.meta", ".mpart.meta")

    def _cleanup_temp_files(self, save_path: str) -> int:
        """清除某个保存路径对应的所有临时/指纹文件，返回清除数量"""
        n = 0
        for suffix in self.TEMP_SUFFIXES:
            path = save_path + suffix
            try:
                if os.path.exists(path):
                    os.remove(path)
                    n += 1
            except OSError:
                pass
        return n

    def temp_files(self) -> list:
        """当前存在的临时文件列表（供自检/清理用）"""
        return [t.save_path + s for t in self.all_tasks()
                for s in self.TEMP_SUFFIXES
                if os.path.exists(t.save_path + s)]

    def cleanup_all_temp(self) -> int:
        """清理所有任务的临时文件，返回清除数量"""
        return sum(self._cleanup_temp_files(t.save_path)
                   for t in self.all_tasks())

    @staticmethod
    def _discard_partial(tmp_path: str, meta_path: str = ""):
        """丢弃残片与指纹文件（续传不可信时调用）"""
        for path in (tmp_path, meta_path or (tmp_path + ".meta")):
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

    @staticmethod
    def _read_meta(meta_path: str) -> str:
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    @staticmethod
    def _write_meta(meta_path: str, value: str):
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                f.write(value)
        except OSError:
            pass

    @staticmethod
    def _clamp_connections(value) -> int:
        """把连接数收敛到 1-32（IDM 的取值区间），非法值回退到默认 8"""
        try:
            n = int(value)
        except (TypeError, ValueError):
            return SEGMENT_DEFAULT_CONNECTIONS
        return max(1, min(SEGMENT_MAX, n))

    def set_connections(self, value) -> int:
        """设置页实时改每文件连接数；返回落地后的值"""
        self._segment_connections = self._clamp_connections(value)
        return self._segment_connections

    def _segment_count(self, total: int) -> int:
        """按文件大小与当前并发数决定分段数（1 = 不分片）

        IDM 语义：用户设几条连接就给几条。只有全局预算吃紧时（并发任务太多，
        连接总数会爆）才按比例摊薄，避免一次开出几百个 socket。
        """
        if not getattr(self, "_segment_enabled", True):
            return 1
        if total < SEGMENT_MIN_SIZE:
            return 1
        per_file = self._clamp_connections(
            getattr(self, "_segment_connections", SEGMENT_DEFAULT_CONNECTIONS))
        with self._lock:
            active = max(1, self._active)
        budget = max(1, CONN_BUDGET // active)
        return max(1, min(per_file, budget))

    @staticmethod
    def _probe_total(url: str, headers: dict) -> tuple:
        """探测资源总长度与是否支持分段，返回 (total, 是否支持 Range)"""
        h = dict(headers)
        h["Range"] = "bytes=0-0"
        try:
            with requests.get(url, headers=h, stream=True,
                              timeout=HTTP_TIMEOUT) as resp:
                if resp.status_code == 206:
                    total = content_range_total(
                        resp.headers.get("Content-Range", ""))
                    if total:
                        return total, True
                elif resp.status_code == 200:
                    return int(resp.headers.get("Content-Length", 0) or 0), False
        except requests.RequestException:
            pass
        return 0, False

    def _stream_segments(self, url: str, tmp_path: str, headers: dict,
                         cancel, info, total: int, n: int,
                         base_done: int = 0, base_total: int = 0) -> int:
        """多连接「动态分块」下载：小块队列 + 谁快谁多干，慢连接会被绕过

        `base_done` / `base_total` 用于「一条轨道下完接着下另一条」的场景
        （如 DASH 先下视频再下音频）：进度按两条轨道的合计上报，不会下音频时
        进度条从 0 重新开始。

        静态切段的致命问题是**最慢的那条连接决定收尾速度**：实测 twimg 走代理
        时 6 条并发连接里 2 条只有 0.26MB/s（其余 6~7.7MB/s），静态切段下整单
        只有 2.4MB/s，而且到 80% 左右快的那几段都下完了，只剩那条慢连接在跑
        —— 用户看到的就是「前面速度很快，后面只剩几百 KB/s」。

        这里改成小块队列：谁先下完谁再拿下一块；某块跑够 CHUNK_SLOW_AFTER 秒
        仍明显慢于全场最快连接，且剩余部分按当前速度还要下载较久，才换连接
        重排。连接超时/报错同样重排（有次数上限），所以单条连接的慢与坏都只影响一个小块，
        不会再拖住收尾。
        """
        with open(tmp_path, "wb") as f:
            f.truncate(total)

        chunk = max(SEGMENT_CHUNK_MIN,
                    min(SEGMENT_CHUNK_MAX, total // max(1, n * 12)))
        pending = deque()
        pos = 0
        while pos < total:
            end = min(pos + chunk - 1, total - 1)
            pending.append({"s": pos, "e": end, "tries": 0})
            pos = end + 1
        abandon_budget = max(ABANDON_MIN_BUDGET, len(pending) // 2)

        state = {"done": 0, "err": None, "best": 0.0, "abandon": 0}
        lock = threading.Lock()
        began = time.time()
        last = [0.0, 0]                  # 上次 emit 的时间 / 字节

        info.total = base_total + total
        info.downloaded = base_done

        chunk_count = len(pending)

        def take():
            with lock:
                return pending.popleft() if pending else None

        def requeue(item, offset):
            """把没下完的部分重新排队（已写入的字节算数，不重复下）"""
            with lock:
                item["s"] = offset
                item["tries"] += 1
                pending.append(item)

        def worker():
            session = requests.Session()
            first = True
            while not cancel.is_set():
                item = take()
                if item is None:
                    return
                s, e = item["s"], item["e"]
                got = 0
                t_start = time.time()
                try:
                    h = dict(headers)
                    h["Range"] = f"bytes={s}-{e}"
                    with session.get(url, headers=h, stream=True,
                                     timeout=HTTP_TIMEOUT) as resp:
                        resp.raise_for_status()
                        if resp.status_code != 206:
                            raise ValueError(
                                f"服务端未返回分段数据（HTTP {resp.status_code}）")
                        # 每个 Range 都必须返回完全对应的区间。只检查文件大小会
                        # 放过“服务器返回了错误区间但长度恰好相同”的损坏文件。
                        range_start, range_end, range_total, range_ok = _parse_content_range(
                            resp.headers.get("Content-Range", ""))
                        expected = e - s + 1
                        if (not range_ok or range_start != s or range_end != e
                                or range_total != total):
                            raise ValueError(
                                "服务端返回的 Content-Range 与请求不一致 "
                                f"（请求 bytes={s}-{e}，响应 {resp.headers.get('Content-Range', '-')})")
                        content_length = resp.headers.get("Content-Length")
                        if content_length and content_length.isdigit() and int(content_length) != expected:
                            raise ValueError(
                                f"服务端分段长度异常（期望 {expected}，实际 {content_length}）")
                        if first:
                            # 卡住时日志里得能看出用的是哪个 CDN、切了多少块
                            first = False
                            log.info("分段连接就绪 %s：HTTP %s | %d 块 × %s 字节 "
                                     "| 总量 %s 字节 | %s",
                                     info.task_id, resp.status_code, chunk_count,
                                     chunk, total, url[:120])
                        ctype = (resp.headers.get("Content-Type") or "").lower()
                        if any(k in ctype for k in ("mpegurl", "text/",
                                                    "application/json")):
                            raise ValueError(f"该地址返回的不是视频文件（{ctype}）")
                        with open(tmp_path, "r+b") as f:
                            f.seek(s)
                            for buf in resp.iter_content(CHUNK):
                                if cancel.is_set():
                                    raise _Cancelled()
                                if not buf:
                                    continue
                                if got + len(buf) > expected:
                                    raise ValueError(
                                        f"服务端返回分段超出请求范围（期望 {expected} 字节）")
                                f.write(buf)
                                got += len(buf)
                                now = time.time()
                                with lock:
                                    state["done"] += len(buf)
                                    done = state["done"]
                                    if now - last[0] >= 0.4:
                                        info.downloaded = base_done + done
                                        info.speed = ((done - last[1]) /
                                                      (now - last[0]))
                                        last[0], last[1] = now, done
                                        self.task_progress.emit(info)
                                    slow = _should_reconnect_slow_chunk(
                                        got, e - s + 1, now - t_start,
                                        state["best"],
                                        state["abandon"] < abandon_budget)
                                    if slow:
                                        state["abandon"] += 1
                                if slow:
                                    raise _SlowChunk()
                    if got < expected:
                        raise IncompleteDownload(
                            f"分段下载不完整 {got}/{expected} 字节")
                    used = max(0.001, time.time() - t_start)
                    with lock:
                        state["best"] = max(state["best"], got / used)
                except _Cancelled:
                    with lock:
                        if state["err"] is None:
                            state["err"] = _Cancelled()
                    return
                except Exception as ex:  # noqa: BLE001
                    if item["tries"] < CHUNK_MAX_ATTEMPTS and got < e - s + 1:
                        # 连接坏了或太慢：换一条连接，把剩下的部分重排
                        if isinstance(ex, _SlowChunk):
                            log.warning("分块太慢，换连接续下 %s：offset=%s 已 %.1fs "
                                        "拿到 %s/%s 字节", info.task_id, s,
                                        time.time() - t_start, got, e - s + 1)
                        else:
                            log.warning("分块中断，换连接续下 %s：offset=%s 已 %s/%s "
                                        "字节 | %s", info.task_id, s, got,
                                        e - s + 1, str(ex)[:120])
                        try:
                            session.close()
                        except Exception:  # noqa: BLE001
                            pass
                        session = requests.Session()
                        requeue(item, s + got)
                        continue
                    with lock:
                        if state["err"] is None:
                            state["err"] = ex
                    return

        threads = [threading.Thread(target=worker, daemon=True)
                   for _ in range(max(1, n))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if cancel.is_set():
            raise _Cancelled()
        if state["err"] is not None:
            info.downloaded = base_done
            raise state["err"]
        got = state["done"]
        if got < total:
            raise IncompleteDownload(f"分段下载不完整 {got}/{total} 字节")
        info.downloaded = base_done + total
        info.speed = total / max(0.001, time.time() - began)
        return total

    def _fetch_track(self, url: str, tmp_path: str, headers: dict, cancel,
                     info, base_done: int = 0, base_total: int = 0) -> int:
        """下载一条轨道（视频或音频）：能分段就 IDM 式多连接，否则单连接。

        返回本次写入 tmp_path 的字节数。分段走整块重写（预分配全尺寸），
        失败时清掉残片再降级单连接，避免两种写法的残片互相污染。
        """
        total, can_range = self._probe_total(url, headers)
        n = self._segment_count(total) if (can_range and total > 0) else 1
        if n > 1:
            log.info("轨道分段 %s：%s 字节 | 连接数=%d | %s",
                     info.task_id, total, n, url[:120])
            try:
                self._stream_segments(url, tmp_path, headers, cancel, info,
                                      total, n, base_done=base_done,
                                      base_total=base_total)
                return total
            except _Cancelled:
                raise
            except Exception:  # noqa: BLE001 - 降级到单连接重下
                # 分片文件是预分配全尺寸的，必须清掉，不能被续传逻辑接管
                log.warning("分段下载失败，降级单连接重下 %s（%s）",
                            info.task_id, url[:120], exc_info=True)
                self._discard_partial(tmp_path)
        return self._stream_to_file(url, tmp_path, headers, cancel, info,
                                    base_done=base_done, base_total=base_total)

    def _stream_to_file(self, url: str, tmp_path: str, headers: dict, cancel,
                        info, base_done: int = 0, base_total: int = 0) -> int:
        """把 url 流式写入 tmp_path（断点续传），返回已写入字节数"""
        downloaded = 0
        mode = "wb"
        resume_from = 0
        meta_path = tmp_path + ".meta"
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            downloaded = os.path.getsize(tmp_path)
            resume_from = downloaded
            headers = dict(headers)
            headers["Range"] = f"bytes={downloaded}-"
            mode = "ab"
        with requests.get(url, headers=headers, stream=True,
                          timeout=HTTP_TIMEOUT) as resp:
            resp.raise_for_status()
            log.info("请求 %s 返回 %s | 目标 %s | 续传自 %s | Content-Length=%s",
                     url[:150], resp.status_code, os.path.basename(tmp_path),
                     resume_from, resp.headers.get("Content-Length", "?"))
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if any(k in ctype for k in ("mpegurl", "text/", "application/json")):
                raise ValueError(f"该地址返回的不是视频文件（{ctype}）")
            if mode == "ab" and resp.status_code == 206:
                start, end, rng_total, ok = _parse_content_range(
                    resp.headers.get("Content-Range", ""))
                if (not ok or start != resume_from
                        or (rng_total and end >= rng_total)):
                    # 服务端没按我们要求的位置续传（或签名 URL 已换源）：
                    # 继续追加会把两段数据拼成坏文件，必须丢弃残片重下。
                    resp.close()
                    self._discard_partial(tmp_path, meta_path)
                    info.downloaded = base_done
                    log.warning("续传起点不符，丢弃残片重下：%s（本地 %s，服务端从 %s 开始）",
                                os.path.basename(tmp_path), resume_from, start)
                    raise IncompleteDownload(
                        f"服务端未按 {resume_from} 位置续传（起点 {start}），已重新下载")
                # 206 且起点正确：本地残片可直接沿用，继续追加
                got_len = int(resp.headers.get("Content-Length", 0))
                rng_total = content_range_total(
                    resp.headers.get("Content-Range", ""))
                if rng_total and rng_total != downloaded + got_len:
                    # 声明的资源总长 ≠ 本地残片 + 本次余量 → 服务端资源已变
                    resp.close()
                    self._discard_partial(tmp_path, meta_path)
                    info.downloaded = base_done
                    log.warning("服务端文件长度已变，丢弃残片重下：%s",
                                os.path.basename(tmp_path))
                    raise IncompleteDownload(
                        f"服务端文件已变更（续传应得 {rng_total}，"
                        f"实为 {downloaded + got_len}），已重新下载")
                # 长度可能完全一致但内容已换源（CDN 改签名/重编码），
                # 再用 ETag 这类强标识比对一次
                rid = _resource_id(resp.headers)
                saved_rid = self._read_meta(meta_path)
                if rid and saved_rid and rid != saved_rid:
                    resp.close()
                    self._discard_partial(tmp_path, meta_path)
                    info.downloaded = base_done
                    log.warning("服务端资源标识已变，丢弃残片重下：%s",
                                os.path.basename(tmp_path))
                    raise IncompleteDownload("服务端资源标识已变化，已重新下载")
            elif resp.status_code in (200, 206):
                # 200：服务端忽略了 Range；206：我们没请求 Range。
                # 两种情况拿到的都是全量内容，从头写入
                downloaded = 0
                mode = "wb"
            # 记录本次的强标识，供下次续传时比对
            rid = _resource_id(resp.headers)
            if rid and mode == "wb":
                self._write_meta(meta_path, rid)
            total = int(resp.headers.get("Content-Length", 0)) + downloaded
            info.total = (base_total or 0) + total
            info.downloaded = base_done + downloaded
            last_time, last_bytes = time.time(), downloaded
            with open(tmp_path, mode) as f:
                for chunk in resp.iter_content(CHUNK):
                    if cancel.is_set():
                        raise _Cancelled()
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if now - last_time >= 0.4:
                            info.downloaded = base_done + downloaded
                            info.speed = (downloaded - last_bytes) / (now - last_time)
                            last_time, last_bytes = now, downloaded
                            self.task_progress.emit(info)
        if total > 0 and downloaded < total:
            log.warning("下载被截断：%s 只拿到 %s/%s 字节", os.path.basename(tmp_path),
                        downloaded, total)
            raise IncompleteDownload(f"下载不完整 {downloaded}/{total} 字节")
        return downloaded


    def _run_jmcomic(self, info, cancel, cookies: str):
        """禁漫本子走 jmcomic 解码下载（原图切片混淆，不能当普通直链）。"""
        from .jmcomic_bridge import DownloadCancelled, download_jmcomic
        info.status = "下载中"
        self.task_progress.emit(info)

        def on_progress(done: int, total: int):
            info.downloaded = max(0, int(done))
            info.total = max(int(total), info.downloaded)
            info.status = "下载中"
            self.task_progress.emit(info)

        try:
            download_jmcomic(info.url, info.save_path, cookies,
                             cancel=cancel, progress_cb=on_progress)
            if cancel.is_set():
                info.status = "已取消"
                return
            if info.total <= 0:
                info.total = max(info.downloaded, 1)
            info.downloaded = info.total
            info.status = "已完成"
        except DownloadCancelled:
            info.status = "已取消"
        except Exception as e:  # noqa: BLE001
            info.status = "失败"
            info.error = str(e)[:200]
        info.speed = 0
        self.task_progress.emit(info)

    def _download_once(self, info, url: str, cancel, cookies: str = "",
                       audio_url: str = "", audio_fallbacks: list = None):
        """下载单个地址；有 audio_url 时先分别下载视频/音频再 ffmpeg 合并

        `audio_fallbacks` 是这路音频的其他 CDN 地址，按顺序作为兜底。
        """
        info.status = "下载中"
        self.task_progress.emit(info)
        os.makedirs(os.path.dirname(info.save_path), exist_ok=True)

        def _headers(u):
            h = headers_for(u)
            # 只有 IG / B站 的 CDN 需要 Cookie（抖音 CDN 带 Cookie 反而更易被风控）
            if cookies and needs_cookie_for_url(u):
                # Cookie 必须作为单个请求头发送；把每个 cookie 拆成独立
                # header 会被 requests/服务器当成未知头，登录态不会生效。
                h["Cookie"] = clean_cookie(cookies)
            return h

        if audio_url:
            from .ffmpeg import ffmpeg_path
            ff = ffmpeg_path()
            if not ff:
                # 抛出可识别异常：换备用地址也无济于事，直接判定失败
                raise MissingDependency(
                    "缺少 ffmpeg 合并组件，无法合并该清晰度的音视频。"
                    "请到「设置」中下载组件后重试，或改用较低清晰度")
            v_tmp = info.save_path + ".vpart"
            a_tmp = info.save_path + ".apart"
            # 视频轨是 DASH 下的大头，同样走 IDM 式多连接（旧实现只有单连接）
            v_got = self._fetch_track(url, v_tmp, _headers(url), cancel, info)
            v_total = info.total or v_got
            # 音频轨同样可能挂在坏 CDN 上：自带备用地址，逐个换着下
            audio_urls = [audio_url] + [u for u in (audio_fallbacks or [])
                                        if u and u != audio_url]
            a_got, audio_err = 0, None
            for i, a_url in enumerate(audio_urls):
                try:
                    a_got = self._fetch_track(a_url, a_tmp, _headers(a_url),
                                              cancel, info, base_done=v_got,
                                              base_total=v_total)
                    audio_err = None
                    break
                except _Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001 - 换备用音频地址
                    audio_err = e
                    log.warning("音频轨下载失败 %s（地址 %d/%d）：%s",
                                info.task_id, i + 1, len(audio_urls), e)
                    if i + 1 < len(audio_urls):
                        # 换 CDN 不续传：不同签名的地址不能拼在同一份残片上
                        self._discard_partial(a_tmp)
                        info.downloaded = v_got
            if audio_err is not None:
                raise audio_err
            log.info("DASH 两轨下载完成 %s：视频 %s 字节 + 音频 %s 字节，开始合并",
                     info.task_id, v_got, a_got)
            info.status = "合并中"
            self.task_progress.emit(info)
            # -nostdin + DEVNULL：打包成窗口程序后没有控制台，ffmpeg 若去读
            # stdin 会一直等（任务永远停在「合并中」）
            cmd = [ff, "-nostdin", "-y", "-loglevel", "error", "-i", v_tmp,
                   "-i", a_tmp, "-c", "copy", info.save_path]
            log.info("合并 %s：%s", info.task_id, " ".join(cmd))
            t_merge = time.time()
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired:
                # 合并卡死不能把任务永远挂住：超时直接判失败并清掉半成品
                self._cleanup_temp_files(info.save_path)
                try:
                    os.remove(info.save_path)
                except OSError:
                    pass
                raise ValueError(f"ffmpeg 合并超过 {FFMPEG_TIMEOUT}s 未结束，已中止")
            log.info("合并结束 %s：返回码 %s 用时 %.1fs%s", info.task_id,
                     proc.returncode, time.time() - t_merge,
                     (" stderr=" + (proc.stderr or "").strip()[:300])
                     if proc.returncode != 0 else "")
            if proc.returncode != 0 or not os.path.exists(info.save_path):
                self._cleanup_temp_files(info.save_path)
                raise ValueError(f"ffmpeg 合并失败：{(proc.stderr or '')[:150]}")
            self._cleanup_temp_files(info.save_path)
            info.downloaded = info.total or (v_got + a_got)
            info.status = "已完成"
            return

        # 单文件：IDM 式多连接分段（能分段就分段，失败自动降级单连接）
        tmp_path = info.save_path + ".part"
        got = self._fetch_track(url, tmp_path, _headers(url), cancel, info)
        if info.save_path.lower().endswith(".mp4") and got < 30 * 1024:
            self._cleanup_temp_files(info.save_path)
            raise ValueError(f"文件仅 {got} 字节，疑似分片或错误页面，已换用其他清晰度")
        os.replace(tmp_path, info.save_path)
        # 收尾：清掉 .part.meta 之类不再需要的附属文件
        self._cleanup_temp_files(info.save_path)
        info.downloaded = info.total or got
        info.status = "已完成"


class _Cancelled(Exception):
    pass


class _SlowChunk(Exception):
    """当前分块跑得太慢：放弃剩余部分，换一条连接重排（见 _stream_segments）"""
    pass


class IncompleteDownload(Exception):
    """下载字节数少于 Content-Length，判定为截断，需续传重试"""
    pass


class MissingDependency(Exception):
    """缺少运行依赖（如 ffmpeg），重试与换地址都无法解决"""
    pass
