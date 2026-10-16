import os
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from app.core import webp_converter as converter
from app.core.webp_converter import ConversionError, ConversionCancelled, convert, merge_webps

FFMPEG = Path(__file__).resolve().parents[1] / "dist" / "tools" / "ffmpeg.exe"


def duration_ms(path):
    data = path.read_bytes()
    pos = data.index(b"mvhd") + 4
    offset = pos + (20 if data[pos] == 1 else 12)
    scale = struct.unpack_from(">I", data, offset)[0]
    duration = struct.unpack_from(">Q" if data[pos] == 1 else ">I", data, offset + 4)[0]
    return duration * 1000 / scale


def sample_durations(path):
    data = path.read_bytes()
    offset = data.index(b"stts") + 8
    count = struct.unpack_from(">I", data, offset)[0]
    samples = []
    for index in range(count):
        repeat, ticks = struct.unpack_from(">II", data, offset + 4 + 8 * index)
        samples.extend([ticks] * repeat)
    return samples


@unittest.skipUnless(FFMPEG.is_file(), "需要项目 dist/tools/ffmpeg.exe 进行真实编码")
class WebpConverterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="转换 测试-")
        self.root = Path(self.tmp.name)
        self.source = self.root / "动画 中文.webp"
        frames = []
        for color in ["red", "green", "blue"]:
            frame = Image.new("RGBA", (65, 49), (0, 0, 0, 0))
            ImageDraw.Draw(frame).rectangle((8, 8, 40, 35), fill=color)
            frame.putpixel((0, 0), (0, 0, 0, len(frames) + 1))
            frames.append(frame)
        frames[0].save(self.source, save_all=True, append_images=frames[1:],
                       duration=[73, 141, 286], lossless=True,
                       background=(255, 255, 255, 0), minimize_size=False)
        for frame in frames:
            frame.close()
        self.original = self.source.read_bytes()
        self.out = self.root / "输出 结果.mp4"

    def tearDown(self):
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(list(self.root.glob("webp-mp4-*")), [])
        self.tmp.cleanup()

    def convert(self, **kwargs):
        return convert(self.source, self.out, ffmpeg=FFMPEG, **kwargs)

    def decode(self, output):
        # 测试也不使用 PIPE，解码写临时文件，支持受限 Windows 运行环境。
        raw_path = self.root / "decoded.rgb"
        with tempfile.TemporaryFile() as log:
            run = subprocess.run(
                [str(FFMPEG), "-v", "error", "-y", "-i", str(output),
                 "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", str(raw_path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            log.seek(0)
            self.assertEqual(run.returncode, 0, log.read())
        return raw_path.read_bytes()

    def test_animation_millisecond_timing_alpha_padding_and_progress(self):
        progress = []
        info = self.convert(progress_cb=lambda *args: progress.append(args))
        self.assertEqual(info, dict(frames=3, duration_ms=500, width=66, height=50))
        self.assertAlmostEqual(duration_ms(self.out), 500, delta=1)
        self.assertEqual(sample_durations(self.out), [73, 141, 285, 1])
        raw = self.decode(self.out)
        self.assertTrue(all(value > 240 for value in raw[:3]))
        self.assertEqual([done for done, _, msg in progress if msg.startswith("已处理")], [1, 2, 3])
        self.assertTrue(all(isinstance(done, int) and total == 3 for done, total, _ in progress))
        self.assertTrue(any("编码" in msg for _, _, msg in progress))

    def test_static_defaults_to_100ms_and_custom_background(self):
        source = self.root / "透明静态.webp"
        Image.new("RGBA", (33, 31), (0, 0, 0, 0)).save(source, lossless=True)
        info = convert(source, self.out, background="#000000", ffmpeg=FFMPEG)
        self.assertEqual(info, dict(frames=1, duration_ms=100, width=34, height=32))
        self.assertAlmostEqual(duration_ms(self.out), 100, delta=1)
        self.assertTrue(all(value < 10 for value in self.decode(self.out)[:3]))

    def test_merge_max_canvas_centered_order_and_timing(self):
        second = self.root / "第二段.WEBP"
        Image.new("RGB", (80, 30), "yellow").save(second, lossless=True)
        progress = []
        info = merge_webps([self.source, second], self.out, ffmpeg=FFMPEG,
                           progress_cb=lambda *args: progress.append(args))
        self.assertEqual(info, dict(frames=4, duration_ms=600, width=80, height=50))
        self.assertAlmostEqual(duration_ms(self.out), 600, delta=1)
        raw = self.decode(self.out)
        stride = 80 * 50 * 3
        self.assertEqual(len(raw), stride * 5)
        colors = [raw[i * stride + (25 * 80 + 25) * 3: i * stride + (25 * 80 + 25) * 3 + 3]
                  for i in range(4)]
        self.assertGreater(colors[0][0], 200)
        self.assertGreater(colors[1][1], 90)
        self.assertGreater(colors[2][2], 200)
        self.assertTrue(colors[3][0] > 200 and colors[3][1] > 200)
        # 第四帧上下白边各10像素，黄色内容未缩放且居中。
        self.assertTrue(all(value > 240 for value in raw[3 * stride:3 * stride + 3]))
        self.assertTrue(all(total == 4 for _, total, _ in progress))
        reverse = self.root / "反向.mp4"
        merge_webps([second, self.source], reverse, ffmpeg=FFMPEG)
        pixel = self.decode(reverse)[(25 * 80 + 25) * 3:(25 * 80 + 25) * 3 + 3]
        self.assertGreater(pixel[0], 200)
        self.assertGreater(pixel[1], 200)

    def test_one_millisecond_and_zero_duration_fallback(self):
        source = self.root / "短帧.webp"
        frames = [Image.new("RGB", (32, 32), color) for color in ("red", "green", "blue")]
        try:
            for durations, expected in [([1, 2, 1], 4), ([0, 1, 1], 102)]:
                frames[0].save(source, save_all=True, append_images=frames[1:],
                               duration=durations, lossless=True)
                info = convert(source, self.out, ffmpeg=FFMPEG, overwrite=True)
                self.assertEqual(info["duration_ms"], expected)
                self.assertAlmostEqual(duration_ms(self.out), expected, delta=1)
        finally:
            for frame in frames:
                frame.close()

    def test_existing_output_default_rejected_and_explicit_overwrite(self):
        self.out.write_bytes(b"keep")
        with self.assertRaises(ConversionError):
            self.convert()
        self.assertEqual(self.out.read_bytes(), b"keep")
        self.convert(overwrite=True)
        self.assertIn(b"ftyp", self.out.read_bytes()[:16])

    def test_failure_preserves_existing_output(self):
        self.out.write_bytes(b"keep")
        bad = self.root / "损坏.webp"
        bad.write_bytes(b"not a webp")
        with self.assertRaises(ConversionError):
            merge_webps([self.source, bad], self.out, overwrite=True, ffmpeg=FFMPEG)
        self.assertEqual(self.out.read_bytes(), b"keep")
        real_popen = subprocess.Popen
        def fail_ffmpeg(command, **kwargs):
            return real_popen([command[0], "-not-a-real-option"], **kwargs)
        with patch.object(converter.subprocess, "Popen", side_effect=fail_ffmpeg):
            with self.assertRaisesRegex(ConversionError, "FFmpeg 编码失败"):
                self.convert(overwrite=True)
        self.assertEqual(self.out.read_bytes(), b"keep")
        self.out.unlink()
        with patch.object(converter.subprocess, "Popen", side_effect=fail_ffmpeg):
            with self.assertRaises(ConversionError):
                self.convert()
        self.assertFalse(self.out.exists())

    def test_missing_ffmpeg_and_non_webp_rejected(self):
        with patch.object(converter, "ffmpeg_path", return_value=None):
            with self.assertRaisesRegex(ConversionError, "找不到 FFmpeg"):
                convert(self.source, self.out)
        with self.assertRaises(ConversionError):
            convert(self.source, self.out, ffmpeg=self.root / "missing.exe")
        png = self.root / "伪装.webp"
        Image.new("RGB", (10, 10)).save(png, format="PNG")
        with self.assertRaisesRegex(ConversionError, "不是 WebP"):
            convert(png, self.out, ffmpeg=FFMPEG)
        self.assertFalse(self.out.exists())

    def test_cancel_before_and_during_frames_preserves_output(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(ConversionCancelled):
            self.convert(cancel=event)
        self.assertFalse(self.out.exists())
        event.clear()
        self.out.write_bytes(b"keep")
        def progress(done, total, message):
            if done == 1:
                event.set()
        with self.assertRaises(ConversionCancelled):
            self.convert(cancel=event, progress_cb=progress, overwrite=True)
        self.assertEqual(self.out.read_bytes(), b"keep")

    def test_cancel_real_running_ffmpeg_is_prompt_and_reaped(self):
        event = threading.Event()
        processes = []
        real_popen = subprocess.Popen
        def capture(command, **kwargs):
            self.assertNotEqual(kwargs["stderr"], subprocess.PIPE)
            self.assertEqual(kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
            process = real_popen(command, **kwargs)
            processes.append(process)
            return process
        def progress(done, total, message):
            if message.startswith("正在编码"):
                self.assertTrue(processes)
                event.set()
        self.out.write_bytes(b"keep")
        start = time.monotonic()
        with patch.object(converter.subprocess, "Popen", side_effect=capture):
            with self.assertRaises(ConversionCancelled):
                self.convert(cancel=event, progress_cb=progress, overwrite=True)
        self.assertLess(time.monotonic() - start, 3)
        self.assertTrue(all(process.poll() is not None for process in processes))
        self.assertEqual(self.out.read_bytes(), b"keep")

    def test_output_race_does_not_clobber_other_writer(self):
        def progress(done, total, message):
            if message.startswith("编码完成"):
                self.out.write_bytes(b"other writer")
        with self.assertRaises(ConversionError):
            self.convert(progress_cb=progress)
        self.assertEqual(self.out.read_bytes(), b"other writer")

    def test_source_alias_and_invalid_arguments_are_safe(self):
        with self.assertRaises(ConversionError):
            convert(self.source, self.source, overwrite=True, ffmpeg=FFMPEG)
        alias = self.root / "原图硬链接.mp4"
        os.link(self.source, alias)
        with self.assertRaises(ConversionError):
            convert(self.source, alias, overwrite=True, ffmpeg=FFMPEG)
        for kwargs in [{"background": "bad color"}, {"background": "#ffffff00"},
                       {"crf": -1}, {"crf": 52}, {"crf": True}, {"crf": 18.5}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ConversionError):
                self.convert(**kwargs)
        with self.assertRaises(ConversionError):
            merge_webps([], self.out, ffmpeg=FFMPEG)
        with self.assertRaises(ConversionError):
            convert(self.source, self.root / "bad.webp", ffmpeg=FFMPEG)
        self.assertFalse(self.out.exists())

    def test_compressible_frames_not_rejected_by_raw_estimate(self):
        # 原估算约23万字节，实际PNG仅几百字节；低于实际预算就应允许转换。
        with patch.object(converter, "MAX_FRAME_DISK_BYTES", 5000):
            result = self.convert()
        self.assertEqual(result["frames"], 3)
        self.assertAlmostEqual(duration_ms(self.out), 500, delta=1)

    def test_free_space_drop_cleans_frames_and_preserves_output(self):
        self.out.write_bytes(b"existing-output")
        def progress(done, total, message):
            if message.startswith("已处理"):
                usage.return_value.free = 0
        with patch.object(converter.shutil, "disk_usage") as usage:
            usage.return_value.free = 10 * 1024**3
            with self.assertRaisesRegex(ConversionError, "剩余空间不足"):
                self.convert(overwrite=True, progress_cb=progress)
        self.assertEqual(self.out.read_bytes(), b"existing-output")

    def test_pixel_frame_input_and_disk_budgets(self):
        for name, limit in [("MAX_CANVAS_PIXELS", 100), ("MAX_FRAMES", 2),
                            ("MAX_INPUT_BYTES", 1), ("MAX_FRAME_DISK_BYTES", 1)]:
            with self.subTest(name=name), patch.object(converter, name, limit):
                with self.assertRaises(ConversionError):
                    self.convert()
        with patch.object(converter.shutil, "disk_usage") as usage:
            usage.return_value.free = 1
            with self.assertRaisesRegex(ConversionError, "空间不足"):
                self.convert()
        self.assertFalse(self.out.exists())

    def test_frame_save_and_publish_failure_preserve_existing_output(self):
        self.out.write_bytes(b"keep")
        with patch.object(Image.Image, "save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(ConversionError, "disk full"):
                self.convert(overwrite=True)
        self.assertEqual(self.out.read_bytes(), b"keep")
        with patch.object(converter.os, "replace", side_effect=PermissionError("locked output")):
            with self.assertRaisesRegex(ConversionError, "locked output"):
                self.convert(overwrite=True)
        self.assertEqual(self.out.read_bytes(), b"keep")
        self.out.unlink()
        with patch.object(converter.os, "link", side_effect=OSError("hard links unavailable")):
            with self.assertRaisesRegex(ConversionError, "hard links unavailable"):
                self.convert()
        self.assertFalse(self.out.exists())

    def test_cancel_at_publish_boundary_preserves_existing_output(self):
        self.out.write_bytes(b"keep")
        event = threading.Event()
        def progress(done, total, message):
            if message.startswith("编码完成"):
                event.set()
        with self.assertRaises(ConversionCancelled):
            self.convert(overwrite=True, cancel=event, progress_cb=progress)
        self.assertEqual(self.out.read_bytes(), b"keep")

    def test_callback_error_terminates_process_and_removes_temporary_files(self):
        def progress(done, total, message):
            if message.startswith("正在编码"):
                raise RuntimeError("callback failed")
        with self.assertRaisesRegex(ConversionError, "callback failed"):
            self.convert(progress_cb=progress)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
