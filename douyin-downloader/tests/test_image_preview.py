"""Exercise real Qt HTTP replies: successful images must not be treated as errors."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from app.core.models import VideoInfo
from app.view.preview_dialog import PreviewDialog


class ImagePreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        image = QImage(80, 40, QImage.Format_RGB32)
        image.fill(QColor('blue'))
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        assert image.save(buffer, 'PNG')
        payload = bytes(data)
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                if self.path == '/missing':
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def wait_reply(self, dialog):
        end = time.monotonic() + 5
        while dialog._reply is not None and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertIsNone(dialog._reply)
        self.app.processEvents()

    def test_success_and_page_switch_render_image(self):
        info = VideoInfo(is_image=True, image_urls=[self.base + '/1.png', self.base + '/2.png'])
        dialog = PreviewDialog(info)
        try:
            self.wait_reply(dialog)
            self.assertFalse(dialog._original_pix.isNull())
            self.assertEqual(dialog._original_pix.width(), 80)
            self.assertEqual(dialog.status.text(), '')
            dialog._change_image(1)
            self.wait_reply(dialog)
            self.assertEqual(dialog.counter.text(), '2 / 2')
            self.assertFalse(dialog.stage.pixmap().isNull())
        finally:
            dialog.reject()

    def test_http_error_is_reported(self):
        dialog = PreviewDialog(VideoInfo(is_image=True, image_urls=[self.base + '/missing']))
        try:
            self.wait_reply(dialog)
            self.assertEqual(dialog.stage.text(), '图片预览失败')
            self.assertTrue(dialog.status.text())
            self.assertTrue(dialog._original_pix.isNull())
        finally:
            dialog.reject()

    def test_fast_page_switch_and_close(self):
        dialog = PreviewDialog(VideoInfo(is_image=True, image_urls=[self.base + '/1.png', self.base + '/2.png']))
        dialog._change_image(1)
        dialog.reject()
        self.assertIsNone(dialog._reply)


if __name__ == '__main__':
    unittest.main()
