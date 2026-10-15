import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile,time,unittest,threading
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtGui import QImage,QColor
from PySide6.QtWidgets import QApplication
from app.core.config import Config
from app.core.downloader import DownloadManager
from app.core.models import DownloadTaskInfo
from app.core.manga import MangaLibrary
from app.view.manga_interface import MangaInterface

class MangaAutoAddTest(unittest.TestCase):
 @classmethod
 def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
  self.manager=DownloadManager(); self.config=Config(str(self.root/'config.json'))
  self.ui=MangaInterface(SimpleNamespace(manager=self.manager,config=self.config))
 def tearDown(self):
  self.ui.shutdown(); self.ui.close()
  end=time.monotonic()+3
  while self.ui.jobs.jobs and time.monotonic()<end:
   self.app.processEvents(); time.sleep(.01)
  self.app.processEvents(); QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
  self.temp.cleanup()
 def task(self,folder='book',status='已完成',url='jmcomic://album/350234'):
  p=self.root/folder;p.mkdir(exist_ok=True)
  image=QImage(60,80,QImage.Format_RGB32);image.fill(QColor('blue'));image.save(str(p/'01.jpg'))
  return DownloadTaskInfo(task_id=folder,url=url,save_path=str(p),name='测试漫画',status=status)
 def wait(self,count):
  end=time.monotonic()+5
  while time.monotonic()<end:
   self.app.processEvents();time.sleep(.01)
   if len(self.ui.library.books)==count and not self.ui._busy and not self.ui._auto_add_queue: return
  self.fail('自动入库未完成')
 def test_worker_completed_signal_persists_book_without_opening_reader(self):
  info=self.task()
  t=threading.Thread(target=lambda:self.manager.task_finished.emit(info));t.start();t.join()
  self.wait(1)
  self.assertIsNone(self.ui._reader)
  disk=MangaLibrary(self.root/'manga_library.json')
  self.assertEqual(len(disk.books),1);self.assertEqual(disk.books[0].title,'测试漫画')
 def test_failed_cancelled_and_non_jm_are_ignored(self):
  for state in ['失败','已取消','下载中']:
   self.manager.task_finished.emit(self.task(status=state))
  self.manager.task_finished.emit(self.task(url='https://example.test/01.jpg'))
  self.app.processEvents();self.assertEqual(self.ui.library.books,[])
 def test_busy_queue_does_not_drop_multiple_completions(self):
  self.ui._set_busy(True)
  self.manager.task_finished.emit(self.task('one'))
  self.manager.task_finished.emit(self.task('two'))
  self.manager.task_finished.emit(self.task('two'))
  self.assertEqual(len(self.ui._auto_add_queue),2)
  self.ui._set_busy(False);self.wait(2)
 def test_repeat_completion_preserves_favorite_progress_and_title(self):
  info=self.task();self.manager.task_finished.emit(info);self.wait(1)
  book=self.ui.library.books[0];book.favorite=True;book.title='用户修改名';book.last_read='2026-10-15';self.ui.library.save()
  self.manager.task_finished.emit(info);self.wait(1)
  self.assertIs(self.ui.library.books[0],book);self.assertTrue(book.favorite)
  self.assertEqual(book.title,'用户修改名');self.assertEqual(book.last_read,'2026-10-15')
 def test_closed_shelf_does_not_import(self):
  info=self.task();self.ui.shutdown();self.manager.task_finished.emit(info)
  self.assertEqual(self.ui.library.books,[])
