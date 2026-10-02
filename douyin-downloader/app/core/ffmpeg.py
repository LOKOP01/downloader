# -*- coding: utf-8 -*-
"""ffmpeg 组件管理：定位 / 自动下载

B 站 1080P 及以上只有 DASH 流（视频、音频分离），需要 ffmpeg 合并为一个 mp4。
本模块负责查找本机 ffmpeg，并在缺失时从镜像下载到程序目录下的 tools/。
"""
import os
import shutil
import sys
import zipfile
from typing import Callable, Optional

import requests

# 优先小体积包（essentials 约 30MB），失败再试 GitHub 上的完整包
MIRRORS = [
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip",
]


def base_dir() -> str:
    """程序所在目录：打包后为 exe 同级目录，源码运行时为项目根目录"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def ffmpeg_path(where: str = "") -> Optional[str]:
    """返回可用的 ffmpeg 可执行文件路径，找不到返回 None"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    root = where or base_dir()
    for cand in (os.path.join(root, "tools", "ffmpeg.exe"),
                 os.path.join(root, "ffmpeg.exe"),
                 os.path.join(root, "tools", "ffmpeg")):
        if os.path.exists(cand):
            return cand
    return None


def download_ffmpeg(where: str = "",
                    progress_cb: Optional[Callable[[int, int, str], None]] = None
                    ) -> str:
    """下载并解压 ffmpeg 到 <程序目录>/tools/，返回 ffmpeg 路径"""
    root = where or base_dir()
    tools = os.path.join(root, "tools")
    os.makedirs(tools, exist_ok=True)
    last_err = ""
    for url in MIRRORS:
        name = url.rsplit("/", 1)[-1]
        try:
            if progress_cb:
                progress_cb(0, 0, f"正在下载 {name} …")
            tmp_zip = os.path.join(tools, "ffmpeg_download.zip")
            got, total = 0, 0
            with requests.get(url, stream=True, timeout=30,
                              headers={"User-Agent": "Mozilla/5.0"}) as resp:
                resp.raise_for_status()
                try:
                    total = int(resp.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    total = 0
                with open(tmp_zip, "wb") as f:
                    for chunk in resp.iter_content(256 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        got += len(chunk)
                        if progress_cb:
                            progress_cb(got, total, f"正在下载 {name} …")
            if progress_cb:
                progress_cb(got, total, "正在解压 ffmpeg …")
            with zipfile.ZipFile(tmp_zip) as z:
                for member in z.namelist():
                    base = member.rsplit("/", 1)[-1]
                    if base in ("ffmpeg.exe", "ffprobe.exe"):
                        with z.open(member) as src, \
                                open(os.path.join(tools, base), "wb") as dst:
                            shutil.copyfileobj(src, dst)
            try:
                os.remove(tmp_zip)
            except OSError:
                pass
            path = os.path.join(tools, "ffmpeg.exe")
            if os.path.exists(path):
                return path
            last_err = "压缩包中未找到 ffmpeg.exe"
        except Exception as e:  # noqa: BLE001 - 换下一个镜像
            last_err = str(e)
            continue
    raise RuntimeError(f"下载 ffmpeg 失败：{last_err}")
