"""Retry an empty Douyin response once and keep image results usable."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from app.core.config import Config
from app.main_window import MainWindow
from app.core.models import VideoInfo

class HomeParseRetryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = MainWindow(Config(os.path.join(self.temp.name, 'config.json')))
        self.window.show()
        self.app.processEvents()
        self.home = self.window.homeInterface
        self.url = 'https://v.douyin.com/W-SXO2z1zaM/'
        self.home.linkEdit.setText(self.url)

    def tearDown(self):
        self.window.close()
        self.temp.cleanup()

    def test_empty_page_retries_once_then_reports_failure(self):
        calls = []
        with patch.object(self.home, '_start_parse', side_effect=calls.append), patch('app.view.home_interface.QTimer.singleShot', side_effect=lambda ms, fn: fn()), patch.object(self.home, '_toast') as toast:
            self.home._on_parse()
            self.home._parse_input = self.url
            self.home._on_parse_fail('作品信息获取失败。接口无数据')
            self.assertEqual(calls, [self.url, self.url])
            self.home._on_parse_fail('作品信息获取失败。接口无数据')
            self.assertTrue(self.home.parseBtn.isEnabled())
            toast.assert_called_once()
            self.assertIn('抖音暂未返回', toast.call_args.args[0])

    def test_stale_retry_does_not_unlock_new_parse(self):
        self.home._parse_generation = 2
        self.home._parse_running = True
        self.home.parseBtn.setEnabled(False)
        with patch.object(self.home, '_start_parse') as start:
            self.home._retry_empty_result(1, self.url)
        start.assert_not_called()
        self.assertFalse(self.home.parseBtn.isEnabled())
        self.assertTrue(self.home._parse_running)

    def test_image_result_is_visible(self):
        note = VideoInfo(source='douyin', is_image=True, aweme_id='7689694274094914490', image_urls=['https://example.com/a.jpg', 'https://example.com/b.jpg'])
        with patch('app.view.home_interface.CoverWorker'):
            self.home._on_parse_ok(note)
        self.app.processEvents()
        self.assertTrue(self.home.resultCard.isVisible())
        self.assertIn('2 张图片', self.home.metaLabel.text())
        self.assertEqual(self.home.downloadBtn.text(), '下载全部图片')

if __name__ == '__main__':
    unittest.main()
