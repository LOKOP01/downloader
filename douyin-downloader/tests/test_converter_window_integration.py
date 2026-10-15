import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import struct, tempfile, time, unittest, sys
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from app.core.config import Config
from app.core.ffmpeg import ffmpeg_path
from app.main_window import MainWindow

class ConverterWindowIntegrationTest(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
 def test_real_engine_from_six_tab_main_window(self):
  ff=ffmpeg_path(str(Path(__file__).resolve().parents[1]/'dist'))
  if not ff:self.skipTest('ffmpeg unavailable')
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);config=Config(str(root/'config.json'));config.set('download_path',str(root/'downloads'))
   one=root/'一 动图.webp';two=root/'二 静态.webp'
   frames=[Image.new('RGB',(65,49),color) for color in ['red','green','blue']]
   frames[0].save(one,save_all=True,append_images=frames[1:],duration=[73,141,286],lossless=True)
   Image.new('RGB',(80,30),'yellow').save(two,lossless=True)
   errors=[];hook=sys.excepthook;sys.excepthook=lambda *args:errors.append(args)
   window=MainWindow(config)
   try:
    window.show();page=window.converterInterface;window.switchTo(page)
    self.app.processEvents()
    self.assertEqual(window.tabBar.count(),6)
    self.assertLessEqual(window.height(),720)
    self.assertIs(window._pages[4],page)
    self.assertEqual(window.stackedWidget.currentWidget(),page)
    page.add_files([str(one),str(two)])
    page.outputEdit.setText(str(root/'outputs'))
    with patch('app.view.converter_interface.ffmpeg_path',return_value=ff):
     page.start_conversion();deadline=time.monotonic()+15
     while page._busy and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
     self.assertFalse(page._busy)
     self.assertTrue(all(page.resultList.item(i).text().startswith('完成') for i in range(2)))
     self.assertTrue((root/'outputs'/one.with_suffix('.mp4').name).is_file())
     page.modeCombo.setCurrentIndex(1);page.mergeNameEdit.setText('顺序合并.mp4')
     page.start_conversion();deadline=time.monotonic()+15
     while page._busy and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
     self.assertFalse(page._busy)
     output=root/'outputs/顺序合并.mp4'
     self.assertTrue(output.is_file())
     data=output.read_bytes();pos=data.index(b'mvhd')+4;offset=pos+(20 if data[pos]==1 else 12)
     timescale=struct.unpack_from('>I',data,offset)[0]
     ticks=struct.unpack_from('>Q' if data[pos]==1 else '>I',data,offset+4)[0]
     self.assertAlmostEqual(ticks*1000/timescale,600,delta=2)
     self.assertEqual(page.resultList.item(0).data(Qt.UserRole),str(output))
    # 六个标签在默认1100像素窗口不应挤出主题按钮。
    self.app.processEvents()
    self.assertLessEqual(window.tabBar.themeButton.geometry().right(),window.tabBar.width())
   finally:
    window.close();self.app.processEvents();sys.excepthook=hook
   self.assertFalse(errors,errors)
