import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app.core.config import Config
from app.main_window import MainWindow
from app.core.webp_converter import ConversionCancelled

class ConverterCloseIntegrationTest(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
 def test_window_waits_asynchronously_for_cancel_cleanup(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);cfg=Config(str(root/'config.json'));cfg.set('download_path',str(root/'downloads'))
   src=root/'input.webp';Image.new('RGB',(10,10),'red').save(src)
   started=threading.Event();cancel_seen=threading.Event();release=threading.Event()
   def conversion(source,destination,*,cancel,**kwargs):
    started.set()
    if not cancel.wait(3):raise RuntimeError('cancel missing')
    cancel_seen.set()
    release.wait(3)
    raise ConversionCancelled('cancelled')
   window=MainWindow(cfg);window.show();page=window.converterInterface;page.add_files([str(src)])
   try:
    with patch('app.view.converter_interface.ffmpeg_path',return_value='test-ffmpeg'),patch('app.view.converter_interface.convert',side_effect=conversion):
     page.start_conversion();self.assertTrue(started.wait(2))
     before=time.monotonic();window.close()
     self.assertLess(time.monotonic()-before,.5)
     self.assertTrue(window.isVisible())
     self.assertTrue(cancel_seen.wait(2))
     heartbeat=[];QTimer.singleShot(0,lambda:heartbeat.append(True));self.app.processEvents();self.assertTrue(heartbeat)
     release.set();deadline=time.monotonic()+5
     while window.isVisible() and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
     self.assertFalse(window.isVisible())
     page._job.thread.join(2);self.assertFalse(page._job.thread.is_alive())
   finally:
    release.set();window.close();self.app.processEvents()
