"""Exercise native image decoding, asynchronous navigation, and task integration."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.core.config import Config
from app.core.manga import MangaLibrary, scan_pages
from app.core.models import DownloadTaskInfo
from app.main_window import MainWindow
from app.view.manga_reader import MangaReader, LocalJobs, read_image


class MangaReaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / "漫画"
        self.folder.mkdir()
        for name, color in (("1.jpg", "red"), ("2.JPG", "green"), ("10.jpeg", "blue")):
            image = QImage(600, 900, QImage.Format.Format_RGB32)
            image.fill(QColor(color))
            self.assertTrue(image.save(str(self.folder / name)))
        self.pages = scan_pages(self.folder)
        self.library = MangaLibrary(self.root / "manga_library.json")
        self.book = self.library.add(self.folder, self.pages)
        self.windows = []
        self.qt_errors = []
        hook = patch("sys.excepthook", side_effect=lambda *error: self.qt_errors.append(error))
        hook.start()
        self.addCleanup(hook.stop)

    def tearDown(self):
        for window in self.windows:
            window.close()
        self.wait(lambda: not LocalJobs._retired)
        self.app.processEvents()
        self.temp.cleanup()
        self.assertFalse(self.qt_errors, f"Unhandled Qt callback errors: {self.qt_errors}")

    def wait(self, predicate):
        deadline = time.monotonic() + 8
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(predicate(), "Async GUI operation timed out")

    def reader(self, pages=None):
        reader = MangaReader(self.library, self.book, pages or self.pages)
        self.windows.append(reader)
        reader.show()
        self.wait(lambda: not reader.jobs.jobs)
        return reader

    def test_decodes_page_and_persists_only_after_success(self):
        reader = self.reader()
        self.assertEqual(reader.counter.text(), "1 / 3")
        self.assertEqual(len(reader.view.scene().items()), 1)
        self.assertTrue(self.book.last_read)
        self.assertEqual(MangaLibrary(self.library.path).books[0].last_page, "1.jpg")

    def test_rapid_page_switch_retains_latest_page(self):
        reader = self.reader()
        reader.show_page(1)
        reader.show_page(2)
        reader.show_page(0)
        self.wait(lambda: not reader.jobs.jobs)
        self.assertEqual(self.book.last_page, "1.jpg")
        self.assertEqual(reader.index, 0)
        self.assertFalse(reader.prev.isEnabled())

    def test_failed_image_keeps_previous_reading_progress(self):
        reader = self.reader()
        Path(self.pages[1]).write_bytes(b"broken JPEG")
        reader.show_page(1)
        self.wait(lambda: not reader.jobs.jobs)
        self.assertIn("无法显示", reader.message.text())
        self.assertEqual(self.book.last_page, "1.jpg")
        reader.show_page(2)
        self.wait(lambda: not reader.jobs.jobs)
        self.assertEqual(self.book.last_page, "10.jpeg")

    def test_width_mode_long_page_scrolls(self):
        image = QImage(600, 3600, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        image.save(self.pages[0])
        reader = self.reader()
        reader.fit.setCurrentIndex(1)
        QTest.qWait(80)
        self.assertGreater(reader.view.verticalScrollBar().maximum(), 100)
        reader.scroll_or_turn()
        self.assertGreater(reader.view.verticalScrollBar().value(), 0)
        self.assertEqual(reader.index, 0)

    def test_rtl_changes_arrow_direction_and_zoom_is_bounded(self):
        reader = self.reader()
        reader.show_page(1)
        self.wait(lambda: not reader.jobs.jobs)
        reader.toggle_direction()
        reader._direction_turn(1)
        self.wait(lambda: not reader.jobs.jobs)
        self.assertEqual(reader.index, 0)
        reader.view.zoom_by(1000)
        self.assertEqual(reader.view.zoom_factor, 6)
        reader.view.zoom_by(0.0001)
        self.assertEqual(reader.view.zoom_factor, 0.2)

    def test_favorite_and_fullscreen_controls(self):
        reader = self.reader()
        reader.toggle_favorite()
        self.assertTrue(MangaLibrary(self.library.path).books[0].favorite)
        reader.toggle_fullscreen()
        self.assertTrue(reader.isFullScreen())
        reader.escape()
        self.assertFalse(reader.isFullScreen())
        reader.toggle_controls()
        self.assertTrue(reader.top.isHidden())
        reader.escape()
        self.assertFalse(reader.top.isHidden())

    def test_jump_input_does_not_swallow_zero_shortcut(self):
        reader = self.reader(self.pages * 10)
        reader.activateWindow()
        reader.jump.setFocus()
        reader.jump.lineEdit().selectAll()
        self.app.processEvents()
        QTest.keyClicks(reader.jump.lineEdit(), "10")
        self.assertEqual(reader.jump.value(), 10)
        QTest.keyClick(reader.jump.lineEdit(), Qt.Key.Key_Return)
        self.wait(lambda: not reader.jobs.jobs)
        self.assertEqual(reader.index, 9)
        self.assertFalse(reader._closed)

    def test_close_while_image_loading_releases_workers(self):
        reader = self.reader()
        reader.show_page(2)
        reader.close()
        self.assertTrue(reader.jobs.closed)
        self.assertFalse(reader.isVisible())
        self.wait(lambda: not LocalJobs._retired)
        self.windows.remove(reader)

    def test_close_is_final_even_in_fullscreen_or_with_hidden_controls(self):
        for mode in ("normal", "fullscreen", "hidden"):
            reader = self.reader()
            if mode == "fullscreen":
                reader.toggle_fullscreen()
            elif mode == "hidden":
                reader.toggle_controls()
            destroyed = []
            reader.destroyed.connect(lambda: destroyed.append(True))
            self.assertTrue(reader.close())
            self.assertFalse(reader.isVisible())
            self.windows.remove(reader)
            self.wait(lambda: bool(destroyed))

    def test_close_during_blocked_decode_is_nonblocking_and_retires_owner(self):
        reader = self.reader()
        started, release = threading.Event(), threading.Event()
        destroyed, ticks = [], []
        reader.destroyed.connect(lambda: destroyed.append(True))

        def slow(path, cover=False):
            started.set()
            release.wait(5)
            return read_image(path, cover)

        with patch("app.view.manga_reader.read_image", side_effect=slow):
            try:
                reader.show_page(1)
                self.wait(started.is_set)
                before = time.monotonic()
                reader.close()
                self.assertLess(time.monotonic() - before, 0.5)
                self.assertFalse(reader.isVisible())
                self.windows.remove(reader)
                self.assertTrue(LocalJobs._retired)
                QTimer.singleShot(0, lambda: ticks.append(True))
                self.wait(lambda: bool(ticks) and bool(destroyed))
                self.assertFalse(release.is_set())
            finally:
                release.set()
            self.wait(lambda: not LocalJobs._retired)
        self.assertEqual(self.book.last_page, "1.jpg")

    def test_shutdown_skips_queued_functions_but_drains_all_jobs(self):
        # Occupy both real Qt workers, guaranteeing all later jobs stay queued.
        owner = LocalJobs(self.app)
        started = [threading.Event(), threading.Event()]
        release = threading.Event()
        running_finished, queued_calls, callbacks, destroyed = [], [], [], []
        owner.destroyed.connect(lambda: destroyed.append(True))

        def running(index):
            started[index].set()
            release.wait(5)
            running_finished.append(index)

        try:
            for i in range(2):
                owner.start(lambda i=i: running(i), lambda *args: callbacks.append(args))
            self.wait(lambda: all(event.is_set() for event in started))
            for i in range(25):
                owner.start(lambda i=i: queued_calls.append(i), lambda *args: callbacks.append(args))
            self.assertEqual(len(owner.jobs), 27)
            before = time.monotonic()
            owner.shutdown()
            self.assertLess(time.monotonic() - before, 0.5)
            QTest.qWait(30)
            self.assertFalse(running_finished)
            self.assertFalse(destroyed)
            self.assertIn(owner, LocalJobs._retired)
        finally:
            owner.shutdown()
            release.set()
        self.wait(lambda: not owner.jobs)
        self.wait(lambda: bool(destroyed))
        self.assertCountEqual(running_finished, [0, 1])
        self.assertEqual(queued_calls, [])
        self.assertEqual(callbacks, [])
        self.assertNotIn(owner, LocalJobs._retired)

    def test_page_requests_coalesce_while_decode_is_blocked(self):
        reader = self.reader()
        started, release = threading.Event(), threading.Event()
        calls = []

        def slow(path, cover=False):
            calls.append(path)
            if len(calls) == 1:
                started.set()
                release.wait(5)
            return read_image(path, cover)

        with patch("app.view.manga_reader.read_image", side_effect=slow):
            try:
                reader.show_page(1)
                self.wait(started.is_set)
                for i in range(100):
                    reader.show_page(i % 3)
                reader.show_page(2)
                self.assertEqual(len(reader.jobs.jobs), 1)
                self.assertIsNotNone(reader.jobs._pending)
            finally:
                release.set()
            self.wait(lambda: not reader.jobs.jobs)
        self.assertEqual(calls, [self.pages[1], self.pages[2]])
        self.assertEqual(self.book.last_page, "10.jpeg")

    def test_decode_pixel_budget_uses_real_image_header(self):
        # Use a smaller budget to exercise the exact boundary without a huge allocation.
        with patch("app.view.manga_reader.MAX_IMAGE_PIXELS", 600 * 900):
            self.assertFalse(read_image(self.pages[0]).isNull())
        with patch("app.view.manga_reader.MAX_IMAGE_PIXELS", 600 * 900 - 1):
            with self.assertRaisesRegex(ValueError, "像素上限"):
                read_image(self.pages[0])
            with self.assertRaises(ValueError):
                read_image(self.pages[0], cover=True)

    def test_display_failure_does_not_mark_page_read(self):
        reader = self.reader()
        with patch.object(reader.view, "display", side_effect=ValueError("display failed")):
            reader.show_page(1)
            self.wait(lambda: not reader.jobs.jobs)
        self.assertEqual(self.book.last_page, "1.jpg")
        self.assertIn("无法显示", reader.message.text())

    def test_side_double_click_does_not_turn_page(self):
        reader = self.reader()
        viewport = reader.view.viewport()
        pos = QPoint(viewport.width() - 20, viewport.height() // 2)
        # Include the first click/release preceding the Qt double-click event.
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=pos)
        self.assertTrue(reader.view._click_timer.isActive())
        QTest.mouseDClick(viewport, Qt.MouseButton.LeftButton, pos=pos)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=pos)
        QTest.qWait(QApplication.doubleClickInterval() + 40)
        self.assertTrue(reader.isFullScreen())
        self.assertEqual(reader.index, 0)
        reader.toggle_fullscreen()
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=pos)
        self.wait(lambda: reader.index == 1 and not reader.jobs.jobs)

    def test_main_window_shelf_and_task_entry_open_downloaded_book(self):
        window = MainWindow(Config(str(self.root / "config.json")))
        self.windows.append(window)
        window.show()
        self.app.processEvents()
        self.assertEqual(window.tabBar.count(), 6)
        task = DownloadTaskInfo(task_id="manga-test", url="jmcomic://album/123", save_path=str(self.folder))
        task.status = "已完成"
        window.ctx.manager._tasks[task.task_id] = task
        window.taskInterface._read_manga_task(task.task_id)
        shelf = window.mangaInterface
        self.wait(lambda: shelf._reader is not None and not shelf._reader.jobs.jobs)
        self.assertEqual(shelf._reader.book.page_count, 3)
        self.assertEqual(shelf.shelf.count(), 1)
        self.assertIs(window.stackedWidget.currentWidget(), shelf)
        # Paint the cover delegate too, so runtime enum/painting errors are caught.
        self.wait(lambda: not shelf.jobs.jobs)
        self.assertIsInstance(shelf.shelf.item(0).data(Qt.ItemDataRole.DecorationRole), QPixmap)
        self.assertFalse(shelf.shelf.grab().isNull())
        self.assertTrue(os.path.exists(self.root / "manga_library.json"))

    def test_running_task_cannot_open_reader(self):
        window = MainWindow(Config(str(self.root / "config.json")))
        self.windows.append(window)
        task = DownloadTaskInfo(task_id="running", url="jmcomic://album/123", save_path=str(self.folder))
        task.status = "下载中"
        window.ctx.manager._tasks[task.task_id] = task
        with patch.object(window.taskInterface, "_toast") as toast:
            window.taskInterface._read_manga_task(task.task_id)
        toast.assert_called_once()
        self.assertIsNone(window.mangaInterface._reader)

    def test_deleted_album_does_not_open_parent_download_directory(self):
        window = MainWindow(Config(str(self.root / "config.json")))
        self.windows.append(window)
        missing = str(self.root / "deleted-album")
        task = DownloadTaskInfo(task_id="missing", url="jmcomic://album/123", save_path=missing)
        task.status = "已完成"
        window.ctx.manager._tasks[task.task_id] = task
        with patch.object(window.mangaInterface, "open_folder") as open_folder:
            window.taskInterface._read_manga_task(task.task_id)
        open_folder.assert_called_once_with(missing)

    def test_filter_and_remove_only_library_record(self):
        window = MainWindow(Config(str(self.root / "config.json")))
        self.windows.append(window)
        shelf = window.mangaInterface
        book = shelf.library.add(self.folder, self.pages)
        shelf.refresh_shelf()
        shelf.filter.setCurrentIndex(2)
        self.assertEqual(shelf.shelf.count(), 0)
        shelf._favorite(book)
        self.assertEqual(shelf.shelf.count(), 1)
        shelf._remove(book)
        self.assertEqual(shelf.shelf.count(), 0)
        self.assertEqual(len(scan_pages(self.folder)), 3)

    def test_refresh_during_cover_load_still_displays_cover(self):
        window = MainWindow(Config(str(self.root / "config.json")))
        self.windows.append(window)
        shelf = window.mangaInterface
        shelf.library.add(self.folder, self.pages)
        started, release = threading.Event(), threading.Event()

        def slow_first_cover(path, cover=False):
            if not started.is_set():
                started.set()
                release.wait(5)
            return read_image(path, cover)

        with patch("app.view.manga_interface.read_image", side_effect=slow_first_cover):
            try:
                shelf.refresh_shelf()
                self.wait(started.is_set)
                shelf.refresh_files()
                self.wait(lambda: not shelf._busy)
            finally:
                release.set()
            self.wait(lambda: not shelf.jobs.jobs)
        self.assertIsInstance(shelf.shelf.item(0).data(Qt.ItemDataRole.DecorationRole), QPixmap)


if __name__ == "__main__":
    unittest.main()
