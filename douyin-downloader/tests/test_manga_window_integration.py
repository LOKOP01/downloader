"""Exercise the actual five-tab window and import/read/close integration."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import time
import unittest
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication
from app.core.config import Config
from app.main_window import MainWindow

class MangaWindowIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_import_read_close_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(str(Path(tmp) / "config.json"))
            config.set("download_path", str(Path(tmp) / "downloads"))
            folder = Path(tmp) / "book"
            folder.mkdir()
            for name in ["1.jpg", "2.jpg"]:
                image = QImage(200, 300, QImage.Format.Format_RGB32)
                image.fill(QColor("#336699"))
                self.assertTrue(image.save(str(folder / name)))
            errors = []
            import sys
            original = sys.excepthook
            sys.excepthook = lambda *args: errors.append(args)
            window = MainWindow(config)
            try:
                window.show()
                self.assertEqual(len(window._pages), 6)
                window._read_manga(str(folder))
                shelf = window.mangaInterface
                deadline = time.monotonic() + 6
                while (shelf._reader is None or shelf._reader.jobs.jobs) and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
                self.assertEqual(len(shelf.library.books), 1)
                reader = shelf._reader
                self.assertIsNotNone(reader)
                self.assertTrue(reader.isVisible())
                self.assertEqual(len(reader.pages), 2)
                reader.close()
                # 重开不能复用等待DeferredDelete的旧窗口。
                shelf.open_book(shelf.library.books[0])
                deadline = time.monotonic() + 6
                while (shelf._reader is reader or shelf._reader is None or shelf._reader.jobs.jobs) and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
                self.assertIsNotNone(shelf._reader)
                self.assertIsNot(shelf._reader, reader)
                image.save(str(folder / "3.jpg"))
                shelf.refresh_files()
                deadline = time.monotonic() + 6
                while (shelf._busy or len(shelf._reader.pages) != 3 or shelf._reader.jobs.jobs) and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
                self.assertEqual(len(shelf._reader.pages), 3)
                self.assertEqual(shelf.library.books[0].page_count, 3)
                shelf.reload_library()
                self.assertEqual(shelf.library.books[0].page_count, 3)
            finally:
                window.close()
                for _ in range(20):
                    self.app.processEvents()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    time.sleep(0.01)
                sys.excepthook = original
            self.assertFalse(errors, errors)
