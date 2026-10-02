"""Regression checks for slow-chunk decisions and partial range retries."""
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import requests

from app.core.downloader import (DownloadManager, _should_reconnect_slow_chunk)
from app.core.models import DownloadTaskInfo


class SlowChunkDecisionTest(unittest.TestCase):
    def test_nearly_complete_chunk_finishes_on_existing_connection(self):
        size = 4 * 1024 * 1024
        self.assertFalse(_should_reconnect_slow_chunk(
            3932160, size, 8.1, 3 * 1024 * 1024, True))
        self.assertFalse(_should_reconnect_slow_chunk(
            3407872, size, 8.2, 3 * 1024 * 1024, True))

    def test_slow_chunk_with_long_remaining_time_reconnects(self):
        self.assertTrue(_should_reconnect_slow_chunk(
            1572864, 4 * 1024 * 1024, 8.2, 3 * 1024 * 1024, True))

    def test_fast_early_complete_and_exhausted_budget_do_not_reconnect(self):
        size = 4 * 1024 * 1024
        self.assertFalse(_should_reconnect_slow_chunk(1572864, size, 7, 3e6, True))
        self.assertFalse(_should_reconnect_slow_chunk(1572864, size, 9, 3e6, False))
        self.assertFalse(_should_reconnect_slow_chunk(size, size, 9, 3e6, True))
        self.assertFalse(_should_reconnect_slow_chunk(0, size, 9, 3e6, True))


class SegmentRetryTest(unittest.TestCase):
    def test_interrupted_segment_retries_only_unwritten_bytes(self):
        payload = bytes(range(256)) * (8192 + 1) + b"end"
        ranges = []
        failed = [False]

        class FakeResponse:
            status_code = 206
            def __init__(self, start, end, interrupt):
                self.start, self.end, self.interrupt = start, end, interrupt
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
                if self.interrupt:
                    yield data[:100000]
                    raise requests.ConnectionError("simulated disconnect")
                for i in range(0, len(data), size):
                    yield data[i:i + size]

        class FakeSession:
            def get(self, url, headers, stream, timeout):
                start, end = map(int, headers["Range"][6:].split("-"))
                ranges.append((start, end))
                interrupt = not failed[0]
                failed[0] = True
                return FakeResponse(start, end, interrupt)

            def close(self):
                pass

        info = DownloadTaskInfo(task_id="retry-test")
        manager = DownloadManager()
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "test.mpart")
            with patch("app.core.downloader.requests.Session", FakeSession):
                result = manager._stream_segments(
                    "https://example.test/video", target, {},
                    threading.Event(), info, len(payload), 1)
            with open(target, "rb") as file:
                self.assertEqual(file.read(), payload)
        self.assertEqual(result, len(payload))
        self.assertEqual(info.downloaded, len(payload))
        self.assertEqual(info.total, len(payload))
        self.assertIn((100000, 1024 * 1024 - 1), ranges)


    def test_wrong_content_range_is_rejected_before_writing(self):
        payload = bytes((i // 4096) % 251 for i in range(2 * 1024 * 1024 + 256))
        attempts = []

        class WrongRangeResponse:
            status_code = 206
            def __init__(self, start, end):
                self.start, self.end = start, end
                # 模拟 CDN 忽略偏移、重复返回文件开头。
                self.headers = {"Content-Type": "video/mp4",
                                "Content-Range": f"bytes 0-{end-start}/{len(payload)}"}

            def __enter__(self): return self
            def __exit__(self, *_): pass
            def raise_for_status(self): pass
            def iter_content(self, size):
                data = payload[:self.end - self.start + 1]
                for i in range(0, len(data), size):
                    yield data[i:i + size]

        class FakeSession:
            def get(self, url, headers, stream, timeout):
                start, end = map(int, headers["Range"][6:].split("-"))
                attempts.append((start, end))
                return WrongRangeResponse(start, end)
            def close(self): pass

        info = DownloadTaskInfo(task_id="bad-range")
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "test.mpart")
            with patch("app.core.downloader.requests.Session", FakeSession):
                with self.assertRaisesRegex(ValueError, "Content-Range"):
                    DownloadManager()._stream_segments(
                        "https://example.test/video", path, {},
                        threading.Event(), info, len(payload), 1)

        self.assertTrue(any(start > 0 for start, _ in attempts))

    def test_short_response_requeues_remaining_range(self):
        payload = b"a" * (1024 * 1024)
        ranges = []

        class ShortResponse:
            status_code = 206
            def __init__(self, start, end):
                self.start, self.end = start, end
                self.headers = {"Content-Type": "video/mp4",
                                "Content-Range": f"bytes {start}-{end}/{len(payload)}"}
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def raise_for_status(self): pass
            def iter_content(self, size):
                data = payload[self.start:self.end + 1]
                if self.start == 0:
                    yield data[:100000]
                else:
                    yield data

        class FakeSession:
            def get(self, url, headers, stream, timeout):
                start, end = map(int, headers["Range"][6:].split("-"))
                ranges.append((start, end))
                return ShortResponse(start, end)
            def close(self): pass

        info = DownloadTaskInfo(task_id="short-range")
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "test.mpart")
            with patch("app.core.downloader.requests.Session", FakeSession):
                DownloadManager()._stream_segments(
                    "https://example.test/video", path, {},
                    threading.Event(), info, len(payload), 1)
            with open(path, "rb") as file:
                self.assertEqual(file.read(), payload)
        self.assertIn((100000, len(payload) - 1), ranges)


if __name__ == "__main__":
    unittest.main()
