"""下载任务与漫画入口的分组、安全边界及合并回归测试。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from qfluentwidgets import TransparentToolButton

from app.core.models import DownloadTaskInfo
from app.view.task_interface import COL_ACTION, TaskInterface


class TaskManagerStub(QObject):
    # 故意不提供已移除的 E-Hentai task_notice 信号。
    task_added = Signal(object)
    task_progress = Signal(object)
    task_finished = Signal(object)

    def __init__(self):
        super().__init__()
        self.tasks = []

    def all_tasks(self):
        return list(self.tasks)


class MangaTaskIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = TaskManagerStub()
        self.interface = TaskInterface(SimpleNamespace(
            manager=self.manager, config={"download_path": str(self.root)}))
        self.emitted = []
        self.interface.read_manga.connect(self.emitted.append)

    def tearDown(self):
        self.interface._log_timer.stop()
        self.interface.close()
        self.interface.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def task(self, relative, status="已完成", name="图片", url="https://cdn/page"):
        task = DownloadTaskInfo(task_id=str(len(self.manager.tasks)), url=url,
                                save_path=str(self.root / relative), name=name)
        task.status = status
        self.manager.tasks.append(task)
        return task

    def read_buttons(self, task):
        self.interface._on_task_added(task)
        actions = self.interface.table.cellWidget(self.interface._rows[task.task_id], COL_ACTION)
        return [button for button in actions.findChildren(TransparentToolButton)
                if button.toolTip().startswith("阅读漫画")]

    def test_local_action_column_layout_survives_merge(self):
        self.assertEqual(COL_ACTION, 0)
        self.assertEqual(self.interface.table.horizontalHeaderItem(0).text(), "操作")
        self.assertEqual(self.interface.table.columnWidth(0), 180)
        self.assertEqual(self.interface.table.horizontalHeader().visualIndex(COL_ACTION), 0)

    def test_video_cover_has_no_reader_button_or_root_import(self):
        task = self.task("video_cover.jpg", name="封面")
        self.assertEqual(self.read_buttons(task), [])
        with patch.object(self.interface, "_toast") as toast:
            self.interface._read_manga_task(task.task_id)
        toast.assert_called_once()
        self.assertEqual(self.emitted, [])

    def test_cover_in_old_download_directory_is_still_excluded(self):
        task = self.task("old-downloads/video_cover.JPG", name="其他名称")
        self.assertEqual(self.read_buttons(task), [])

    def test_unlabelled_root_jpeg_cannot_import_every_book(self):
        task = self.task("single.jpeg")
        self.assertEqual(self.read_buttons(task), [])
        with patch.object(self.interface, "_toast"):
            self.interface._read_manga_task(task.task_id)
        self.assertEqual(self.emitted, [])

    def test_album_waits_for_all_sibling_tasks_including_webp(self):
        first = self.task("album/01.jpeg")
        second = self.task("album/02.webp", status="下载中")
        self.assertEqual(len(self.read_buttons(first)), 1)
        with patch.object(self.interface, "_toast") as toast:
            for status in ("等待中", "下载中", "失败", "已取消"):
                second.status = status
                self.interface._read_manga_task(first.task_id)
            self.assertEqual(toast.call_count, 4)
        self.assertEqual(self.emitted, [])
        second.status = "已完成"
        self.interface._read_manga_task(first.task_id)
        self.assertEqual(self.emitted, [str(self.root / "album")])

    def test_unrelated_download_does_not_block_completed_album(self):
        first = self.task("album/01.JPG")
        self.task("other-album/01.jpeg", status="下载中")
        self.interface._read_manga_task(first.task_id)
        self.assertEqual(self.emitted, [str(self.root / "album")])

    def test_running_selected_task_cannot_open(self):
        task = self.task("album/01.jpeg", status="下载中")
        with patch.object(self.interface, "_toast") as toast:
            self.interface._read_manga_task(task.task_id)
        toast.assert_called_once()
        self.assertEqual(self.emitted, [])

    def test_native_album_retains_missing_folder_not_parent(self):
        task = self.task("missing-album", url="jmcomic://album/123")
        self.assertEqual(len(self.read_buttons(task)), 1)
        self.interface._read_manga_task(task.task_id)
        self.assertEqual(self.emitted, [str(self.root / "missing-album")])

    def test_video_has_no_reader_button(self):
        self.assertEqual(self.read_buttons(self.task("video.mp4")), [])


if __name__ == "__main__":
    unittest.main()
