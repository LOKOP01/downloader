# -*- coding: utf-8 -*-
"""纯 WebP 转换逻辑；由调用方工作线程调用，不依赖 Qt、HTTP 或 CLI。

逐帧解码，最多 1600 万画布像素、2 万帧、单输入 256 MiB；临时 PNG
临时 PNG 累计实际占用上限 2 GiB；逐帧检查磁盘空间，不按全部帧的未压缩体积拒绝。
不把整段动画装入 Python 帧列表。取消粒度为单帧，编码时每 50ms 检查。
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time

from PIL import Image, ImageColor

from .ffmpeg import ffmpeg_path

MAX_CANVAS_PIXELS = 16_000_000
MAX_FRAMES = 20_000
MAX_INPUT_BYTES = 256 * 1024 * 1024
MAX_FRAME_DISK_BYTES = 2 * 1024 * 1024 * 1024


class ConversionError(Exception):
    """输入、资源预算或编码错误。"""


class ConversionCancelled(ConversionError):
    """用户主动取消；已有输出保持不变。"""


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise ConversionCancelled("转换已取消")


def _report(callback, done, total, message):
    if callback is not None:
        callback(done, total, message)


def _encode(command, log_path, cancel, progress_cb, total):
    # 不使用 PIPE：大量 stderr 不会填满管道而阻塞；只读取日志尾部。
    with log_path.open("w+b") as log:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            next_report = 0.0
            while process.poll() is None:
                _check_cancel(cancel)
                now = time.monotonic()
                if now >= next_report:
                    _report(progress_cb, total, total, "正在编码 MP4 …")
                    next_report = now + 0.25
                if cancel is not None:
                    cancel.wait(0.05)
                else:
                    time.sleep(0.05)
            _check_cancel(cancel)
            if process.returncode:
                log.seek(0, os.SEEK_END)
                log.seek(max(0, log.tell() - 8192))
                detail = log.read().decode("utf-8", errors="replace").strip()
                raise ConversionError(f"FFmpeg 编码失败（{process.returncode}）：{detail}")
        finally:
            # 回调异常同样不能遗留子进程或锁住临时目录。
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def convert(source, destination, *, background="#ffffff", crf=18,
            overwrite=False, cancel: threading.Event | None = None,
            progress_cb=None, ffmpeg=None) -> dict:
    return merge_webps(
        [source], destination, background=background, crf=crf,
        overwrite=overwrite, cancel=cancel, progress_cb=progress_cb, ffmpeg=ffmpeg,
    )


def merge_webps(sources, destination, *, background="#ffffff", crf=18,
                overwrite=False, cancel: threading.Event | None = None,
                progress_cb=None, ffmpeg=None) -> dict:
    """按输入顺序合并，按最大画布居中、不缩放，返回帧数/毫秒/偶数尺寸。

    progress_cb(done, total, message) 中 total 来自输入头 n_frames；编码期间
    done == total，以文字表示编码阶段。所有回调均发生在调用线程。
    默认通过排他硬链接原子提交（需支持硬链接的本地文件系统），覆盖模式
    通过 os.replace 原子提交。提交前取消/失败不会留下输出残片。
    """
    try:
        return _merge(sources, destination, background, crf, overwrite,
                      cancel, progress_cb, ffmpeg)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"WebP 转换失败：{exc}") from exc


def _merge(sources, destination, background, crf, overwrite, cancel, progress_cb, ffmpeg):
    _check_cancel(cancel)
    sources = [Path(source) for source in sources]
    destination = Path(destination).absolute()
    if not sources:
        raise ConversionError("请至少选择一个 WebP 文件")
    for source in sources:
        if (source.resolve() == destination.resolve() or
                (source.exists() and destination.exists() and
                 os.path.samefile(source, destination))):
            raise ConversionError("输入和输出不能是同一个文件")
    if destination.suffix.lower() != ".mp4":
        raise ConversionError("输出文件扩展名必须为 .mp4")
    if os.path.lexists(destination) and not overwrite:
        raise ConversionError(f"输出已存在：{destination}")
    if isinstance(crf, bool) or not isinstance(crf, int) or not 0 <= crf <= 51:
        raise ConversionError("CRF 必须为 0 到 51 之间的整数")
    try:
        color = ImageColor.getrgb(background)
        if len(color) != 3:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ConversionError("背景色应为 #RRGGBB 或有效的不透明颜色名") from exc
    executable = ffmpeg if ffmpeg is not None else ffmpeg_path()
    if not executable:
        raise ConversionError("找不到 FFmpeg，请先安装或下载 FFmpeg 组件")

    headers, total = [], 0
    for source in sources:
        _check_cancel(cancel)
        if source.stat().st_size > MAX_INPUT_BYTES:
            raise ConversionError("单个输入超过 256 MiB 内存安全限制")
        with Image.open(source) as image:
            if image.format != "WEBP":
                raise ConversionError(f"输入内容不是 WebP 图片：{source.name}")
            width, height = image.size
            count = getattr(image, "n_frames", 1)
            if width <= 0 or height <= 0 or width * height > MAX_CANVAS_PIXELS:
                raise ConversionError("输入超过 1600 万像素限制")
            total += count
            if total > MAX_FRAMES:
                raise ConversionError("总帧数超过 20000 帧限制")
            headers.append((image.size, count))
    width = max(size[0] for size, _ in headers)
    height = max(size[1] for size, _ in headers)
    size = (width + width % 2, height + height % 2)
    pixels = size[0] * size[1]
    if pixels > MAX_CANVAS_PIXELS:
        raise ConversionError("合并画布超过 1600 万像素限制")
    # PNG会压缩，未压缩像素数×总帧数不是实际磁盘用量，不能据此提前拒绝。
    # 只给下一帧预留保守空间；总量按实际落盘字节逐帧限制。
    frame_reserve = pixels * 4 + 65536
    free_reserve = 16 * 1024 * 1024
    _report(progress_cb, 0, total, "正在准备逐帧转换 …")
    _check_cancel(cancel)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < frame_reserve + free_reserve:
        raise ConversionError("输出磁盘空间不足以保存临时帧")
    with tempfile.TemporaryDirectory(prefix="webp-mp4-", dir=destination.parent) as folder:
        work = Path(folder)
        manifest, frames, elapsed, disk_bytes = [], 0, 0, 0
        for source, (source_size, count) in zip(sources, headers):
            _check_cancel(cancel)
            with Image.open(source) as image:
                if image.format != "WEBP" or image.size != source_size or image.n_frames != count:
                    raise ConversionError("转换期间输入文件发生变化")
                position = ((size[0] - image.width) // 2, (size[1] - image.height) // 2)
                for index in range(count):
                    _check_cancel(cancel)
                    if shutil.disk_usage(work).free < frame_reserve + free_reserve:
                        raise ConversionError("输出磁盘剩余空间不足，已停止转换并清理临时帧")
                    image.seek(index)
                    with image.convert("RGBA") as rgba:
                        duration = int(image.info.get("duration", 100))
                        duration = duration if duration > 0 else 100
                        name = f"frame-{frames:08d}.png"
                        with Image.new("RGB", size, color) as canvas:
                            with rgba.getchannel("A") as alpha:
                                canvas.paste(rgba, position, alpha)
                            _check_cancel(cancel)
                            canvas.save(work / name)
                    disk_bytes += (work / name).stat().st_size
                    if disk_bytes > MAX_FRAME_DISK_BYTES:
                        raise ConversionError("临时PNG实际占用超过 2 GiB 上限，请减少合并文件数量或分段转换")
                    manifest.extend([f"file '{name}'", "option framerate 1000",
                                     f"duration {duration / 1000:.3f}"])
                    elapsed += duration
                    frames += 1
                    _report(progress_cb, frames, total, f"已处理 {frames}/{total} 帧")
                    _check_cancel(cancel)
        # 结束前 1ms 重复末帧，末尾样本占 1ms，避免 -t 截短最后一帧。
        manifest[-1] = f"duration {(duration - 1) / 1000:.3f}"
        manifest.extend([f"file '{name}'", "option framerate 1000"])
        (work / "frames.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
        result = work / "encoded.mp4"
        command = [str(executable), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                   "-f", "concat", "-safe", "0", "-i", str(work / "frames.txt"),
                   "-an", "-c:v", "libx264", "-preset", "medium", "-bf", "0",
                   "-crf", str(crf), "-pix_fmt", "yuv420p", "-fps_mode", "vfr",
                   "-video_track_timescale", "1000", "-t", f"{elapsed / 1000:.3f}",
                   "-movflags", "+faststart", str(result)]
        _check_cancel(cancel)
        _encode(command, work / "ffmpeg.log", cancel, progress_cb, total)
        if not result.is_file() or result.stat().st_size == 0:
            raise ConversionError("FFmpeg 未生成有效输出")
        _report(progress_cb, total, total, "编码完成，正在保存 …")
        _check_cancel(cancel)
        if overwrite:
            os.replace(result, destination)
        else:
            # 原子排他创建：即使编码期间另一任务创建目标，也绝不覆盖。
            try:
                os.link(result, destination)
            except FileExistsError as exc:
                raise ConversionError(f"输出已存在：{destination}") from exc
    return dict(frames=frames, duration_ms=elapsed, width=size[0], height=size[1])
