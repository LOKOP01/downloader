"""真实 QApplication + mock 转换引擎：队列、线程取消及销毁安全。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.view import converter_interface as module
from app.view.converter_interface import ConverterInterface


class ConverterInterfaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = []
        for name in ("c.webp", "a.webp", "b.WEBP"):
            path = self.root / name
            path.write_bytes(b"mock input")
            self.paths.append(str(path.resolve()))
        config = SimpleNamespace(base_dir=str(self.root), get=lambda key: str(self.root / "downloads"))
        self.widget = ConverterInterface(SimpleNamespace(config=config))
        self.errors = []
        hook = patch("sys.excepthook", side_effect=lambda *args: self.errors.append(args))
        hook.start()
        self.addCleanup(hook.stop)
        ffmpeg = patch.object(module, "ffmpeg_path", return_value="local-ffmpeg")
        self.ffmpeg = ffmpeg.start()
        self.addCleanup(ffmpeg.stop)
        self.jobs = []

    def tearDown(self):
        self.widget.shutdown()
        for job in self.jobs:
            self.wait(lambda: not job.thread.is_alive())
        self.widget.close()
        self.app.processEvents()
        self.temp.cleanup()
        self.assertFalse(self.errors, self.errors)

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), "异步转换未在时限内完成")

    def start(self):
        self.widget.start_conversion()
        if self.widget._job:
            self.jobs.append(self.widget._job)

    def test_defaults_and_order_multiselect_move_delete_clear(self):
        w = self.widget
        self.assertEqual(w.objectName(), "converterInterface")
        self.assertEqual(Path(w.outputEdit.text()), self.root / "downloads" / "converted")
        self.assertFalse(w.overwriteCheck.isChecked())
        w.add_files(self.paths + self.paths + [str(self.root / "not.txt")])
        self.assertEqual(w.input_paths(), self.paths)
        w.inputList.item(1).setSelected(True)
        w.inputList.item(2).setSelected(True)
        QTest.mouseClick(w.upBtn, Qt.MouseButton.LeftButton)
        self.assertEqual(w.input_paths(), [self.paths[1], self.paths[2], self.paths[0]])
        QTest.mouseClick(w.downBtn, Qt.MouseButton.LeftButton)
        self.assertEqual(w.input_paths(), self.paths)
        QTest.mouseClick(w.removeBtn, Qt.MouseButton.LeftButton)
        self.assertEqual(w.input_paths(), [self.paths[0]])
        QTest.mouseClick(w.clearBtn, Qt.MouseButton.LeftButton)
        self.assertEqual(w.input_paths(), [])

    def test_native_drop_and_file_multiselect(self):
        w = self.widget
        with patch.object(module.QFileDialog, "getOpenFileNames", return_value=(self.paths[:2], "")):
            QTest.mouseClick(w.addBtn, Qt.MouseButton.LeftButton)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self.paths[2])])
        enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(w, enter)
        self.assertTrue(enter.isAccepted())
        drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(w, drop)
        self.assertEqual(w.input_paths(), self.paths)

    def test_scroll_viewport_accepts_native_drop(self):
        w = self.widget
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self.paths[0])])
        enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(w.viewport(), enter)
        self.assertTrue(enter.isAccepted())
        drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(w.viewport(), drop)
        self.assertEqual(w.input_paths(), self.paths[:1])

    def test_batch_failure_continues_and_runs_off_gui_thread(self):
        calls = []
        gui_thread = threading.get_ident()
        def engine(source, destination, **kwargs):
            calls.append((source, destination, kwargs, threading.get_ident()))
            kwargs["progress_cb"](1, 2, "mock progress")
            if source == self.paths[0]:
                raise module.ConversionError("broken input")
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            Path(destination).write_bytes(b"video")
            return {}
        self.widget.add_files(self.paths)
        with patch.object(module, "convert", side_effect=engine):
            self.start()
            self.wait(lambda: not self.widget._busy)
        self.assertEqual([c[0] for c in calls], self.paths)
        self.assertTrue(all(c[3] != gui_thread for c in calls))
        self.assertTrue(all(c[2]["overwrite"] is False for c in calls))
        self.assertEqual(calls[0][2]["crf"], 18)
        self.assertEqual(calls[0][2]["background"], "#ffffff")
        self.assertEqual(calls[0][2]["ffmpeg"], "local-ffmpeg")
        self.ffmpeg.assert_called_once_with(str(self.root))
        self.assertIn("失败", self.widget.resultList.item(0).text())
        self.assertIn("完成", self.widget.resultList.item(1).text())
        self.assertTrue(self.widget.startBtn.isEnabled())

    def test_merge_uses_display_order_and_explicit_options(self):
        w = self.widget
        w.add_files(self.paths)
        w.inputList.setCurrentRow(2)
        w.move_selected(-1)
        w.modeCombo.setCurrentIndex(1)
        w.mergeNameEdit.setText("ordered.mp4")
        w.crfCombo.setCurrentIndex(0)
        w.overwriteCheck.setChecked(True)
        with patch.object(module.QColorDialog, "getColor", return_value=QColor(3, 4, 5, 10)):
            w._choose_background()
        with patch.object(module, "merge_webps", return_value={}) as engine:
            self.start()
            self.wait(lambda: not w._busy)
        self.assertEqual(engine.call_args.args[0], [self.paths[0], self.paths[2], self.paths[1]])
        self.assertEqual(engine.call_args.args[1].name, "ordered.mp4")
        self.assertTrue(engine.call_args.kwargs["overwrite"])
        self.assertEqual(engine.call_args.kwargs["crf"], 12)
        self.assertEqual(engine.call_args.kwargs["background"], "#030405")

    def test_cancel_stops_later_inputs_and_blocks_edits_resubmission(self):
        entered = threading.Event()
        def engine(source, destination, **kwargs):
            entered.set()
            if not kwargs["cancel"].wait(3):
                raise AssertionError("cancel not signaled")
            raise module.ConversionCancelled("cancelled")
        w = self.widget
        w.add_files(self.paths)
        with patch.object(module, "convert", side_effect=engine) as mocked:
            self.start()
            self.wait(entered.is_set)
            self.assertFalse(w.editPanel.isEnabled())
            self.assertFalse(w.startBtn.isEnabled())
            w.add_files([self.paths[0]])
            w.clear_inputs()
            w.move_selected(-1)
            w.start_conversion()
            self.assertEqual(w.input_paths(), self.paths)
            QTest.mouseClick(w.cancelBtn, Qt.MouseButton.LeftButton)
            self.wait(lambda: not w._busy)
            self.assertEqual(mocked.call_count, 1)
        self.assertTrue(all("已取消" in w.resultList.item(i).text() for i in range(3)))

    def test_close_and_actual_qobject_destruction_never_wait_for_engine(self):
        entered = threading.Event()
        released = threading.Event()
        def engine(source, destination, **kwargs):
            entered.set()
            kwargs["cancel"].wait(3)
            released.wait(3)  # 模拟引擎取消后的异步清理，关闭不可同步等待。
            raise module.ConversionCancelled("closed")
        w = self.widget
        w.add_files(self.paths)
        with patch.object(module, "convert", side_effect=engine):
            self.start()
            self.wait(entered.is_set)
            job = w._job
            before = time.monotonic()
            w.close()
            self.assertLess(time.monotonic() - before, 0.25)
            self.assertTrue(job.cancel.is_set())
            self.assertTrue(job.thread.is_alive())
            w.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            released.set()
            self.wait(lambda: not job.thread.is_alive())
        # tearDown 不再访问已销毁的 QWidget。
        self.widget = ConverterInterface(w.ctx)

    def test_direct_destruction_also_requests_cancel(self):
        entered = threading.Event()
        def engine(source, destination, **kwargs):
            entered.set()
            kwargs["cancel"].wait(3)
            raise module.ConversionCancelled("destroyed")
        w = self.widget
        ctx = w.ctx
        w.add_files(self.paths)
        with patch.object(module, "convert", side_effect=engine):
            self.start()
            self.wait(entered.is_set)
            job = w._job
            w.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertTrue(job.cancel.is_set())
            self.wait(lambda: not job.thread.is_alive())
        self.widget = ConverterInterface(ctx)

    def test_missing_ffmpeg_and_invalid_merge_name_do_not_start(self):
        w = self.widget
        w.add_files(self.paths)
        with patch.object(module, "ffmpeg_path", return_value=None), patch.object(module, "convert") as engine:
            w.start_conversion()
            self.assertIn("设置下载", w.statusLabel.text())
            engine.assert_not_called()
            self.assertFalse(w._busy)
        w.modeCombo.setCurrentIndex(1)
        for name in ("../escape.mp4", "a/b.mp4", "bad.avi", "", "C:\bad.mp4"):
            w.mergeNameEdit.setText(name)
            w.start_conversion()
            self.assertFalse(w._busy)
            self.assertIn("文件名", w.statusLabel.text())

    def test_same_named_sources_not_silently_overwritten_and_preview(self):
        second = self.root / "nested" / "c.webp"
        second.parent.mkdir()
        second.write_bytes(b"input")
        w = self.widget
        w.add_files([self.paths[0], str(second)])
        def engine(source, destination, **kwargs):
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and not kwargs["overwrite"]:
                raise module.ConversionError("already exists")
            destination.write_bytes(b"video")
            return {}
        with patch.object(module, "convert", side_effect=engine):
            self.start()
            self.wait(lambda: not w._busy)
        self.assertIn("完成", w.resultList.item(0).text())
        self.assertIn("失败", w.resultList.item(1).text())
        with patch.object(module.QDesktopServices, "openUrl", return_value=True) as opened:
            w.resultList.setCurrentRow(0)
            QTest.mouseClick(w.previewBtn, Qt.MouseButton.LeftButton)
            self.assertTrue(opened.call_args.args[0].isLocalFile())
            self.assertTrue(opened.call_args.args[0].toLocalFile().endswith("c.mp4"))
            QTest.mouseClick(w.openBtn, Qt.MouseButton.LeftButton)
            self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), Path(w.outputEdit.text()))
            w.resultList.setCurrentRow(1)
            self.assertFalse(w.previewBtn.isEnabled())


if __name__ == "__main__":
    unittest.main()
