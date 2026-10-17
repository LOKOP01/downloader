# -*- coding: utf-8 -*-
"""本地视频库：扫描文件夹、记录播放进度。

无 GUI、无网络、不改动源文件；结构对齐 manga.py 的书架（QLockFile 协作、
原子写、损坏备份、磁盘修订号防覆盖），只是条目从「书」换成了「视频」。
"""
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QLockFile

# 常见本地视频格式；wmv/rmvb 等老格式能不能播取决于系统解码器，
# 列进来允许用户尝试，播不了播放器会给出错误提示而不是消失。
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv",
              ".ts", ".mpg", ".mpeg", ".wmv", ".3gp")


def natural_key(value):
    # Tagged components remain comparable even if a name starts with a number.
    return tuple((1, int(p), len(p)) if p.isascii() and p.isdigit()
                 else (0, p.casefold()) for p in re.split(r"(\d+)", str(value)))


def folder_key(folder):
    return os.path.normcase(os.path.abspath(os.path.normpath(folder)))


def is_video(path):
    return Path(path).suffix.lower() in VIDEO_EXTS


def is_video_file_entry(path, name=""):
    """下载任务自动入库用：真正的视频文件，而不是同名封面图。

    下载器会给视频额外存一张 `<同名>_cover.jpg`，这里的入参是任务保存路径
    （已是视频时直接判封面后缀即可）；`name` 为作品名，"封面" 任务一律排除。
    """
    if not is_video(path):
        return False
    if (name or "") == "封面":
        return False
    stem = Path(path).stem.casefold()
    return not stem.endswith("_cover")


def _junction(path):
    return getattr(os.path, "isjunction", lambda _: False)(path)


def scan_videos(folder):
    """递归收集视频文件；不跟随目录链接，按相对路径自然排序。"""
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"文件夹不存在：{folder}")
    videos = []

    def fail(error):
        raise error  # Never silently replace a scan with a partial result.

    for root, dirs, files in os.walk(folder, onerror=fail, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))
                   and not _junction(os.path.join(root, d))]
        videos.extend(os.path.join(root, f) for f in files if is_video(f))
    return sorted(videos, key=lambda p: natural_key(os.path.relpath(p, folder)))


def fmt_time(ms):
    """毫秒 → h:mm:ss / mm:ss；未知返回 --:--"""
    ms = max(0, int(ms))
    total, sec = divmod(ms // 1000, 60)
    hour, minute = divmod(total, 60)
    if hour:
        return f"{hour}:{minute:02d}:{sec:02d}"
    return f"{minute:02d}:{sec:02d}"


def download_cover_image(url, cache_dir, video_path, timeout=15):
    """把解析出的封面图下载到缓存目录，返回路径；失败返回空串。

    自动入库时本地没有 _cover 文件（用户未开"同时下载封面"）就用它。
    网络失败静默回退（后续还有截帧兜底）。
    """
    import requests
    if not url or not str(url).startswith(("http://", "https://")):
        return ""
    os.makedirs(cache_dir, exist_ok=True)
    target = os.path.join(cache_dir, cover_cache_name(video_path))
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        return target  # 已下载过
    try:
        resp = requests.get(url, timeout=timeout,
                            headers={"User-Agent": "Mozilla/5.0"}, stream=True)
        resp.raise_for_status()
        data = resp.content
        # 过小或非图片开头的响应当失败处理，避免把错误页存成封面
        if len(data) < 1024 or not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"
                                    or data[:6] in (b"GIF87a", b"GIF89a")
                                    or data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
            return ""
        with open(target, "wb") as file:
            file.write(data)
        return target
    except requests.RequestException:
        return ""


def _nonnegative_int(value):
    try:
        return max(0, int(value))
    except OverflowError as error:
        raise ValueError("进度必须是有限整数") from error


@dataclass
class VideoItem:
    path: str
    title: str = ""
    resume_ms: int = 0        # 续播位置；播完归零
    duration_ms: int = 0      # 播放过才有；用于卡片进度与时长徽章
    last_played: str = ""
    cover: str = ""           # 封面图路径；空 = 无封面（卡片显示占位）

    @property
    def percent(self):
        if self.duration_ms <= 0:
            return 0
        return min(100, int(self.resume_ms * 100 / self.duration_ms))

    @property
    def watched(self):
        return bool(self.duration_ms) and self.resume_ms <= 0 and self.percent >= 100


def find_downloaded_cover(video_path):
    """解析下载附带的封面：与视频同目录的 <同名>_cover.jpg。

    单视频下载时封面命名见 home_interface._on_download（<名>_cover.jpg）。
    找不到返回空串。
    """
    stem = Path(video_path).with_suffix("")
    for name in (stem.name + "_cover.jpg", stem.name + "_cover.jpeg", stem.name + "_cover.png"):
        candidate = os.path.join(os.path.dirname(os.path.abspath(video_path)), name)
        if os.path.isfile(candidate):
            return candidate
    return ""


def cover_cache_name(video_path):
    """封面缓存文件名：路径 sha1 前 16 位 + 视频名（可读、免碰撞）。"""
    import hashlib
    digest = hashlib.sha1(os.path.abspath(video_path).encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return f"{digest}_{Path(video_path).stem[:40]}.jpg"


def extract_first_frame(video_path, ffmpeg, cache_dir):
    """用 ffmpeg 截第一帧为 JPG，写入缓存目录，返回路径；失败返回空串。

    本地导入的视频没有现成封面，用首帧代替。ffmpeg 缺失或截帧失败都不算
    错误（卡片回退占位图），静默返回空串即可。
    """
    if not ffmpeg or not os.path.isfile(video_path):
        return ""
    os.makedirs(cache_dir, exist_ok=True)
    target = os.path.join(cache_dir, cover_cache_name(video_path))
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        return target  # 已截过，直接复用
    import subprocess
    try:
        result = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-ss", "0", "-i", video_path,
             "-frames:v", "1", "-q:v", "3", target],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode == 0 and os.path.isfile(target) and os.path.getsize(target) > 0:
            return target
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


class VideoLibrary:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.videos = []
        self.warning = ""
        # None is deliberately not a valid disk revision: an unread library
        # must never overwrite existing records after a transient load failure.
        self._revision = None
        try:
            self.load()
        except OSError as error:
            self._revision = None
            self.warning = f"视频记录暂时无法读取，已禁用保存；请重新加载：{error}"

    @staticmethod
    def _read(path):
        with open(path, encoding="utf-8-sig") as file:
            data = json.load(file)
        if not isinstance(data, dict) or not isinstance(data.get("videos"), list):
            raise ValueError("视频记录格式无效")
        videos, seen = [], set()
        for entry in data["videos"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ValueError("视频条目格式无效")
            raw = entry["path"]
            if not raw.strip():
                raise ValueError("视频路径为空")
            key = folder_key(raw)
            if key in seen:
                continue
            seen.add(key)
            videos.append(VideoItem(
                os.path.abspath(raw), str(entry.get("title") or Path(raw).stem),
                _nonnegative_int(entry.get("resume_ms", 0)),
                _nonnegative_int(entry.get("duration_ms", 0)),
                str(entry.get("last_played") or ""),
                str(entry.get("cover") or "")))
        return videos

    @contextmanager
    def _locked(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        lock = QLockFile(self.path + ".lock")
        # A slow live writer must never lose its lock just because of its age.
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise OSError("视频记录正在被其他实例使用，请稍后重试。")
        try:
            yield
        finally:
            lock.unlock()

    def _disk_revision(self):
        revisions = []
        for path in (self.path, self.path + ".bak"):
            try:
                revisions.append(hashlib.sha256(Path(path).read_bytes()).digest())
            except FileNotFoundError:
                revisions.append(None)
        return tuple(revisions)

    @staticmethod
    def _atomic_write(path, content):
        # Never open the destination for writing: it may be a hard/symbolic link.
        fd, temp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".video-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.remove(temp)

    def load(self):
        with self._locked():
            self._revision = self._disk_revision()
            self.warning = ""
            if self._revision == (None, None):
                return
            try:
                videos = self._read(self.path)
            except (OSError, ValueError, TypeError):
                self.warning = "视频记录缺失或读取失败，已有文件已保留。"
                try:
                    videos = self._read(self.path + ".bak")
                    self.warning += "已从备份恢复。"
                except (OSError, ValueError, TypeError):
                    return
            self.videos = videos

    def save(self):
        """Serialize cooperating writers and reject stale in-memory snapshots."""
        with self._locked():
            if self._disk_revision() != self._revision:
                raise OSError("视频记录已被其他实例修改，为避免丢失记录，已取消保存；请重新加载。")
            data = {"version": 1, "videos": [asdict(item) for item in self.videos]}
            content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
            if os.path.exists(self.path):
                try:
                    self._read(self.path)
                except (OSError, ValueError, TypeError):
                    # Retain an existing corrupt snapshot, including a link itself.
                    if not os.path.lexists(self.path + ".corrupt"):
                        self._atomic_write(self.path + ".corrupt", Path(self.path).read_bytes())
                else:
                    self._atomic_write(self.path + ".bak", Path(self.path).read_bytes())
            self._revision = self._disk_revision()
            self._atomic_write(self.path, content)
            self._revision = self._disk_revision()

    def add(self, path, title=""):
        key = folder_key(path)
        item = next((v for v in self.videos if folder_key(v.path) == key), None)
        if item is None:
            item = VideoItem(os.path.abspath(path), title or Path(path).stem)
            self.videos.append(item)
        elif title:
            item.title = title
        return item

    def remove(self, item):
        key = folder_key(item.path)
        self.videos = [v for v in self.videos if folder_key(v.path) != key]
