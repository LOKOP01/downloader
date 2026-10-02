"""IDM 式多连接：连接数可配置 + 轨道分段进度按 base 偏移上报。"""
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.core.downloader import (CONN_BUDGET, SEGMENT_MAX, DownloadManager)
from app.core.models import DownloadTaskInfo

BIG = 64 * 1024 * 1024


class ConnectionCountTest(unittest.TestCase):
    def test_default_is_eight(self):
        self.assertEqual(DownloadManager()._segment_count(BIG), 8)

    def test_user_setting_is_honored(self):
        m = DownloadManager(segment_connections=16)
        self.assertEqual(m._segment_count(BIG), 16)

    def test_clamped_to_idm_range(self):
        self.assertEqual(
            DownloadManager(segment_connections=99)._segment_count(BIG),
            SEGMENT_MAX)
        self.assertEqual(
            DownloadManager(segment_connections=0)._segment_count(BIG), 1)

    def test_small_file_stays_single_connection(self):
        self.assertEqual(DownloadManager()._segment_count(1024 * 1024), 1)

    def test_disabled_returns_single_connection(self):
        m = DownloadManager(segment_enabled=False)
        self.assertEqual(m._segment_count(BIG), 1)

    def test_budget_thins_out_when_many_tasks(self):
        m = DownloadManager(max_concurrent=8, segment_connections=32)
        m._active = 8
        self.assertEqual(m._segment_count(BIG), CONN_BUDGET // 8)

    def test_set_connections_takes_effect_live(self):
        m = DownloadManager()
        self.assertEqual(m.set_connections(20), 20)
        self.assertEqual(m._segment_count(BIG), 20)


class BaseOffsetProgressTest(unittest.TestCase):
    """先下视频轨、再下音频轨时，进度要按两轨合计上报，不能回到 0。"""

    def test_progress_reports_combined_bytes(self):
        payload = bytes(range(256)) * 4096          # 1 MiB

        class FakeResponse:
            status_code = 206

            def __init__(self, start, end):
                self.start, self.end = start, end
                self.headers = {
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes {start}-{end}/{len(payload)}",
                }

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def raise_for_status(self):
                pass

            def iter_content(self, size):
                data = payload[self.start:self.end + 1]
                for i in range(0, len(data), size):
                    yield data[i:i + size]

        class FakeSession:
            def get(self, url, headers, stream, timeout):
                start, end = map(int, headers["Range"][6:].split("-"))
                return FakeResponse(start, end)

            def close(self):
                pass

        info = DownloadTaskInfo(task_id="base-offset")
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "track.part")
            with patch("app.core.downloader.requests.Session", FakeSession):
                DownloadManager()._stream_segments(
                    "https://example.test/a", path, {}, threading.Event(),
                    info, len(payload), 1,
                    base_done=5_000_000, base_total=9_000_000)
        self.assertEqual(info.total, 9_000_000 + len(payload))
        self.assertEqual(info.downloaded, 5_000_000 + len(payload))


class FetchTrackTest(unittest.TestCase):
    def test_large_track_uses_segments(self):
        info = DownloadTaskInfo(task_id="fetch-track")
        m = DownloadManager(segment_connections=4)
        seen = {}

        def fake_segments(url, tmp, headers, cancel, info_, total, n,
                          base_done=0, base_total=0):
            seen["n"] = n
            return total

        with patch.object(m, "_probe_total",
                          return_value=(20 * 1024 * 1024, True)), \
                patch.object(m, "_stream_segments", side_effect=fake_segments):
            got = m._fetch_track("https://example.test/v", "x.vpart",
                                 {}, threading.Event(), info)
        self.assertEqual(seen["n"], 4)
        self.assertEqual(got, 20 * 1024 * 1024)

    def test_small_track_falls_back_to_single_connection(self):
        info = DownloadTaskInfo(task_id="fetch-track-small")
        m = DownloadManager(segment_connections=8)
        seen = {"single": False}

        def fake_single(url, tmp, headers, cancel, info_, base_done=0,
                        base_total=0):
            seen["single"] = True
            return 1024 * 1024

        with patch.object(m, "_probe_total", return_value=(1024 * 1024, True)), \
                patch.object(m, "_stream_to_file", side_effect=fake_single):
            got = m._fetch_track("https://example.test/a", "x.apart",
                                 {}, threading.Event(), info)
        self.assertTrue(seen["single"])
        self.assertEqual(got, 1024 * 1024)

    def test_segment_failure_falls_back_to_single_connection(self):
        info = DownloadTaskInfo(task_id="fetch-track-fail")
        m = DownloadManager(segment_connections=8)
        seen = {"single": False}

        def fake_single(url, tmp, headers, cancel, info_, base_done=0,
                        base_total=0):
            seen["single"] = True
            return 20 * 1024 * 1024

        with patch.object(m, "_probe_total",
                          return_value=(20 * 1024 * 1024, True)), \
                patch.object(m, "_stream_segments",
                             side_effect=ValueError("boom")), \
                patch.object(m, "_discard_partial") as discard, \
                patch.object(m, "_stream_to_file", side_effect=fake_single):
            got = m._fetch_track("https://example.test/v", "x.part",
                                 {}, threading.Event(), info)
        self.assertTrue(seen["single"])
        self.assertEqual(got, 20 * 1024 * 1024)
        discard.assert_called_once_with("x.part")


if __name__ == "__main__":
    unittest.main()
