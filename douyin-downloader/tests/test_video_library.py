"""Video library persistence, scanning, and window integration."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.core.config import Config
from app.core.video_library import (VideoLibrary, extract_first_frame,
                                    find_downloaded_cover, fmt_time, is_video,
                                    is_video_file_entry, scan_videos)
from app.main_window import MainWindow


class VideoLibraryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_persist_resume_and_backup_restore(self):
        lib = VideoLibrary(self.root / "video_library.json")
        item = lib.add(str(self.root / "a.mp4"), "示例")
        item.resume_ms, item.duration_ms, item.last_played = 61000, 120000, "2026-10-17T12:00:00"
        lib.save()
        item.resume_ms = 30000
        lib.save()
        self.assertTrue((self.root / "video_library.json.bak").exists())
        reloaded = VideoLibrary(self.root / "video_library.json")
        self.assertEqual(len(reloaded.videos), 1)
        self.assertEqual(reloaded.videos[0].resume_ms, 30000)
        self.assertEqual(reloaded.videos[0].percent, 25)
        self.assertEqual(reloaded.videos[0].title, "示例")
        (self.root / "video_library.json").write_text("{broken", encoding="utf-8")
        recovered = VideoLibrary(self.root / "video_library.json")
        self.assertEqual(len(recovered.videos), 1)

    def test_stale_snapshot_rejects_save(self):
        lib1 = VideoLibrary(self.root / "video_library.json")
        lib1.add(str(self.root / "a.mp4"))
        lib1.save()
        lib2 = VideoLibrary(self.root / "video_library.json")
        lib1.add(str(self.root / "b.mp4"))
        lib2.add(str(self.root / "c.mp4"))
        lib1.save()
        with self.assertRaises(OSError):
            lib2.save()

    def test_scan_videos_filters_and_handles_missing(self):
        (self.root / "sub").mkdir()
        for name in ("1.mp4", "b.mkv", "note.txt", "cover.jpg"):
            (self.root / name).write_bytes(b"x")
        (self.root / "sub" / "2.webm").write_bytes(b"x")
        got = [Path(p).name for p in scan_videos(self.root)]
        self.assertEqual(sorted(got), ["1.mp4", "2.webm", "b.mkv"])
        with self.assertRaises(FileNotFoundError):
            scan_videos(self.root / "missing")

    def test_cover_from_download_and_first_frame(self):
        """解析封面优先同名 _cover 图；本地导入截第一帧并缓存复用。"""
        from app.core.ffmpeg import ffmpeg_path
        ff = ffmpeg_path(str(Path(__file__).resolve().parents[1] / "dist"))
        if not ff:
            self.skipTest("ffmpeg unavailable")
        import subprocess
        video = self.root / "movie.mp4"
        subprocess.run([ff, "-y", "-loglevel", "error",
                        "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
        # 本地导入：截第一帧，且第二次命中缓存
        cache = self.root / "video_covers"
        frame = extract_first_frame(str(video), ff, str(cache))
        self.assertTrue(frame and Path(frame).is_file())
        self.assertEqual(len(list(cache.glob("*.jpg"))), 1)
        self.assertEqual(Path(extract_first_frame(str(video), ff, str(cache))).name,
                         Path(frame).name)
        # 解析封面：同名 _cover.jpg 存在时优先
        (self.root / "movie_cover.jpg").write_bytes(b"jpeg")
        self.assertEqual(Path(find_downloaded_cover(str(video))).name, "movie_cover.jpg")
        self.assertEqual(find_downloaded_cover(str(self.root / "none.mp4")), "")
        # cover 字段随库持久化
        lib = VideoLibrary(self.root / "video_library.json")
        item = lib.add(str(video))
        item.cover = frame
        lib.save()
        self.assertEqual(VideoLibrary(self.root / "video_library.json").videos[0].cover, frame)

    def test_helpers(self):
        self.assertTrue(is_video("x.MP4"))
        self.assertFalse(is_video("photo.jpg"))
        self.assertTrue(is_video_file_entry("x.mp4"))
        self.assertFalse(is_video_file_entry("x_cover.mp4"))
        self.assertFalse(is_video_file_entry("x.mp4", "封面"))
        self.assertEqual(fmt_time(3723000), "1:02:03")
        self.assertEqual(fmt_time(61000), "01:01")
        self.assertEqual(fmt_time(0), "00:00")

    def test_main_window_video_tab_import_and_player(self):
        config = Config(str(self.root / "config.json"))
        config.set("download_path", str(self.root / "downloads"))
        (self.root / "downloads").mkdir()
        video = self.root / "downloads" / "sample.mp4"
        video.write_bytes(b"not a real video")  # 触发播放器的错误分支即可
        window = MainWindow(config)
        window.show()
        self.app.processEvents()
        self.addCleanup(window.close)
        self.assertEqual(window.tabBar.count(), 7)
        self.assertIs(window._pages[4], window.videoInterface)
        page = window.videoInterface
        window.switchTo(page)
        self.app.processEvents()
        page.import_paths([str(video)])
        deadline = time.monotonic() + 6
        while page._busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(page._busy)
        self.assertEqual(page.shelf.count(), 1)
        self.assertTrue((self.root / "video_library.json").exists())
        page.open_item(page.shelf.item(0))
        deadline = time.monotonic() + 4
        while page._player is None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertIsNotNone(page._player)
        self.assertTrue(page._player.isVisible())
        page._player.close()
        deadline = time.monotonic() + 4
        while page._player is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertIsNone(page._player)


if __name__ == "__main__":
    unittest.main()
