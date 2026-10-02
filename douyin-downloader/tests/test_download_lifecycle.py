"""Regression checks for authenticated retries and task lifecycle."""
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.core.downloader import DownloadManager
from app.core.models import DownloadTaskInfo


class DownloadTaskLifecycleTest(unittest.TestCase):
    def test_cookie_is_sent_in_cookie_header(self):
        manager = DownloadManager(segment_enabled=False)
        seen = []
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "result.jpg")
            url = "https://upos.example.bilivideo.com/image.jpg"
            info = DownloadTaskInfo(task_id="cookie-test", url=url, save_path=path)

            def stream(url, temp_path, headers, *args, **kwargs):
                seen.append(headers)
                with open(temp_path, "wb") as file:
                    file.write(b"image")
                return 5

            with patch.object(manager, "_probe_total", return_value=(5, False)), \
                    patch.object(manager, "_stream_to_file", side_effect=stream):
                manager._download_once(info, url, threading.Event(),
                                       "SESSDATA=abc; bili_jct=xyz")

        self.assertEqual(seen[0]["Cookie"], "SESSDATA=abc; bili_jct=xyz")
        self.assertNotIn("SESSDATA", seen[0])
        self.assertNotIn("bili_jct", seen[0])

    def test_retry_keeps_audio_candidates_and_login_cookie(self):
        manager = DownloadManager()
        info = DownloadTaskInfo(task_id="retry-test", url="https://example.test/v")
        info.status = "等待中"
        manager._tasks[info.task_id] = info
        manager._cookies[info.task_id] = "SESSDATA=abc"
        manager._plans[info.task_id] = [
            (info.url, "https://example.test/a", ["https://example.test/a-backup"]),
            ("https://example.test/v-backup", "https://example.test/a", []),
        ]
        seen = []

        def download(info, url, cancel, cookie, audio, backups):
            seen.append((url, cookie, audio, backups))
            if len(seen) <= 2:
                raise ValueError("simulated failure")
            info.status = "已完成"

        manager._cancel_flags[info.task_id] = threading.Event()
        manager._active = 1
        with patch.object(manager, "_download_once", side_effect=download), \
                patch.object(manager, "_pump"):
            manager._run(info.task_id)
            self.assertEqual(info.status, "失败")
            manager.retry(info.task_id)
            manager._cancel_flags[info.task_id] = threading.Event()
            manager._active = 1
            manager._run(info.task_id)
        self.assertEqual(info.status, "已完成")
        self.assertEqual(seen[2], (info.url, "SESSDATA=abc",
                                   "https://example.test/a",
                                   ["https://example.test/a-backup"]))
        self.assertEqual(manager.remove_tasks([info.task_id]), 1)
        self.assertNotIn(info.task_id, manager._plans)
        self.assertNotIn(info.task_id, manager._cookies)

    def test_retry_does_not_start_another_worker_while_merging(self):
        manager = DownloadManager(max_concurrent=3)
        info = DownloadTaskInfo(task_id="merging-test", url="https://example.test/v")
        info.status = "合并中"
        manager._tasks[info.task_id] = info
        original_cancel = threading.Event()
        manager._cancel_flags[info.task_id] = original_cancel
        manager._active = 1
        with patch("app.core.downloader.threading.Thread") as worker:
            manager.retry(info.task_id)
        worker.assert_not_called()
        self.assertIs(manager._cancel_flags[info.task_id], original_cancel)
        self.assertEqual(manager._active, 1)
        self.assertEqual(info.status, "合并中")

    def test_bulk_retry_skips_task_until_previous_worker_exits(self):
        manager = DownloadManager(max_concurrent=3)
        info = DownloadTaskInfo(task_id="finishing-test", url="https://example.test/v")
        info.status = "失败"
        manager._tasks[info.task_id] = info
        manager._cancel_flags[info.task_id] = threading.Event()
        manager._active = 1
        with patch("app.core.downloader.threading.Thread") as worker:
            self.assertEqual(manager.retry_tasks(), 0)
        worker.assert_not_called()
        self.assertEqual(manager._active, 1)


if __name__ == "__main__":
    unittest.main()
