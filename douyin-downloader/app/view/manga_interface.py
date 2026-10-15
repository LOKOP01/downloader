"""Manga shelf integrated with the desktop application's design and navigation."""
import os
import logging
from collections import deque

from PySide6.QtCore import QRectF, QSize, Qt, QUrl, Slot, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QListView,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QStyle, QStyledItemDelegate, QWidget)
from qfluentwidgets import ComboBox, FluentIcon as FIF, PrimaryPushButton, PushButton, SearchLineEdit
from qfluentwidgets.common.style_sheet import isDarkTheme

from ..core.manga import MangaLibrary, discover_books, folder_key, is_page, natural_key, scan_pages
from . import design as D
from .manga_reader import LocalJobs, MangaReader, read_image


class _CoverDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        book = index.data(Qt.ItemDataRole.UserRole)
        if book is None:
            return
        dark = isDarkTheme()
        primary = QColor(D.TEXT_PRIMARY[int(dark)])
        muted = QColor(D.TEXT_SECONDARY[int(dark)])
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(8, 8, -8, -8)
        cover_rect = QRectF(rect.left(), rect.top(), rect.width(), 218)
        clip = QPainterPath()
        clip.addRoundedRect(cover_rect, 10, 10)
        painter.fillPath(clip, QColor(D.BG_TERTIARY[int(dark)]))
        pixmap = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            painter.save()
            painter.setClipPath(clip)
            scaled = pixmap.scaled(cover_rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)
            left = (scaled.width() - int(cover_rect.width())) // 2
            top = (scaled.height() - int(cover_rect.height())) // 2
            cropped = scaled.copy(left, top, int(cover_rect.width()), int(cover_rect.height()))
            painter.drawPixmap(cover_rect.toRect(), cropped)
            painter.restore()
        else:
            painter.setPen(muted)
            painter.setFont(D.font(16))
            painter.drawText(cover_rect, Qt.AlignmentFlag.AlignCenter, "本地漫画")
        badge = QRectF(rect.left() + 9, rect.top() + 187, 68, 24)
        painter.setBrush(QColor(0, 0, 0, 180))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(badge, 5, 5)
        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(D.font(11))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, f"{book.page_count} 页")
        if book.favorite:
            painter.setFont(D.font(20))
            painter.drawText(QRectF(rect.right() - 32, rect.top() + 8, 26, 28), Qt.AlignmentFlag.AlignCenter, "★")
        painter.setPen(primary)
        painter.setFont(D.font(14, D.W_SEMI))
        title = painter.fontMetrics().elidedText(book.title, Qt.TextElideMode.ElideRight, int(rect.width()))
        painter.drawText(QRectF(rect.left(), rect.top() + 230, rect.width(), 24), title)
        painter.setPen(muted)
        painter.setFont(D.font(11))
        progress = f"读到第 {book.page_index + 1} 页" if book.last_read else "还没开始阅读"
        painter.drawText(QRectF(rect.left(), rect.top() + 258, rect.width(), 20), progress)
        painter.fillRect(QRectF(rect.left(), rect.top() + 284, rect.width(), 3), QColor(D.BORDER[int(dark)]))
        if book.last_read and book.page_count:
            percent = min(1.0, (book.page_index + 1) / book.page_count)
            painter.fillRect(QRectF(rect.left(), rect.top() + 284, rect.width() * percent, 3), QColor(D.TEXT_ACCENT[int(dark)]))
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(D.TEXT_ACCENT[int(dark)]))
            painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 5), 10, 10)
        painter.restore()


class MangaInterface(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("mangaInterface")
        self.setAcceptDrops(True)
        self.library = MangaLibrary(os.path.join(ctx.config.base_dir, "manga_library.json"))
        self.jobs = LocalJobs(self)
        self._busy = False
        self._closed = False
        self._auto_add_queue = deque()
        self._auto_add_keys = set()
        self._reader = None
        self._covers = {}
        self._cover_pending = set()
        self._cover_generation = 0
        layout = D.page_layout(self)
        header = QHBoxLayout()
        header.addWidget(D.PageHeader("漫画阅读器", "一夹一本 · JPG / JPEG · 自然排序 · 自动保存阅读进度", self), 1)
        self.addBtn = PrimaryPushButton(FIF.ADD, "添加漫画", self)
        self.addBtn.clicked.connect(self._choose_book)
        header.addWidget(self.addBtn)
        self.libraryBtn = PushButton(FIF.FOLDER, "导入漫画库", self)
        self.libraryBtn.clicked.connect(self._choose_library)
        header.addWidget(self.libraryBtn)
        layout.addLayout(header)
        tools = QHBoxLayout()
        self.search = SearchLineEdit(self)
        self.search.setPlaceholderText("搜索漫画名称")
        self.search.setMaximumWidth(340)
        self.search.textChanged.connect(self.refresh_shelf)
        tools.addWidget(self.search, 1)
        self.filter = ComboBox(self)
        self.filter.addItems(["全部漫画", "最近阅读", "我的收藏"])
        self.filter.currentIndexChanged.connect(self.refresh_shelf)
        tools.addWidget(self.filter)
        self.sort = ComboBox(self)
        self.sort.addItems(["最近阅读", "名称顺序", "阅读进度"])
        self.sort.currentIndexChanged.connect(self.refresh_shelf)
        tools.addWidget(self.sort)
        tools.addStretch(1)
        self.refreshBtn = PushButton(FIF.SYNC, "刷新", self)
        self.refreshBtn.clicked.connect(self.refresh_files)
        tools.addWidget(self.refreshBtn)
        more = PushButton("更多", self)
        more.clicked.connect(self._more_menu)
        tools.addWidget(more)
        self.moreBtn = more
        layout.addLayout(tools)
        self.shelf = QListWidget(self)
        self.shelf.setViewMode(QListView.ViewMode.IconMode)
        self.shelf.setResizeMode(QListView.ResizeMode.Adjust)
        self.shelf.setMovement(QListView.Movement.Static)
        self.shelf.setWrapping(True)
        self.shelf.setGridSize(QSize(200, 316))
        self.shelf.setUniformItemSizes(True)
        self.shelf.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.shelf.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.shelf.setItemDelegate(_CoverDelegate(self.shelf))
        self.shelf.setAccessibleName("漫画书架")
        self.shelf.itemClicked.connect(lambda item: self.open_book(item.data(Qt.ItemDataRole.UserRole)))
        self.shelf.itemActivated.connect(lambda item: self.open_book(item.data(Qt.ItemDataRole.UserRole)))
        self.shelf.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.shelf.customContextMenuRequested.connect(self._context_menu)
        D.theme_qss(self.shelf, "QListWidget{background:transparent;border:0;}",
                    "QListWidget{background:transparent;border:0;}")
        layout.addWidget(self.shelf, 1)
        self.empty = D.caption("", self)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setMinimumHeight(180)
        layout.addWidget(self.empty, 1)
        self.status = D.hint_label("本地阅读 · 原图片不变 · 右键封面可收藏或移除记录", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh_shelf()
        if self.library.warning:
            self.status.setText(self.library.warning)
        ctx.manager.task_finished.connect(self._queue_completed_jm)

    def _save(self):
        try:
            self.library.save()
            return True
        except OSError as error:
            self.status.setText(f"书架保存失败：{error}")
            return False

    @Slot(object)
    def _queue_completed_jm(self, info):
        if self._closed or info.status != "已完成" or not (info.url or "").startswith("jmcomic://"):
            return
        folder = info.save_path
        if not folder or not os.path.isdir(folder):
            logging.getLogger("vdl.manga").warning("禁漫自动入库跳过：保存目录不存在")
            return
        key = folder_key(folder)
        if key not in self._auto_add_keys:
            self._auto_add_keys.add(key)
            self._auto_add_queue.append((folder, info.name or ""))
        self._drain_auto_add()

    def _drain_auto_add(self):
        if self._closed or self._busy or not self._auto_add_queue:
            return
        folder, title = self._auto_add_queue.popleft()
        self._auto_add_keys.discard(folder_key(folder))
        self.import_folder(folder, title=title)

    def _set_busy(self, busy):
        self._busy = busy
        for button in (self.addBtn, self.libraryBtn, self.refreshBtn, self.moreBtn):
            button.setEnabled(not busy)
        if not busy and not self._closed and self._auto_add_queue:
            QTimer.singleShot(0, self._drain_auto_add)

    def _choose_book(self):
        path = QFileDialog.getExistingDirectory(self, "选择一本漫画的文件夹", self.ctx.config.get("download_path"))
        if path:
            self.import_folder(path)

    def _choose_library(self):
        path = QFileDialog.getExistingDirectory(self, "选择漫画总目录（每个子文件夹一本）", self.ctx.config.get("download_path"))
        if path:
            self.import_folder(path, True)

    def import_folder(self, folder, library_root=False, read=False, title=""):
        if self._busy or self._closed:
            return
        self._set_busy(True)
        self.status.setText("正在扫描漫画文件夹…")

        def done(found, error):
            self._set_busy(False)
            if error:
                self.status.setText(f"扫描失败：{error}")
                return
            if not found:
                self.status.setText("没有找到 JPG / JPEG 页面。导入漫画库时，每个子文件夹需要包含漫画页面。")
                return
            added = []
            for path, pages in found:
                existing = next((b for b in self.library.books if folder_key(b.folder) == folder_key(path)), None)
                book = self.library.add(path, pages)
                if existing is None and title:
                    book.title = title
                added.append((book, pages))
                self._covers.pop(folder_key(path), None)
            self._cover_generation += 1
            saved = self._save()
            self.refresh_shelf()
            if saved:
                self.status.setText(f"已导入 / 更新 {len(added)} 本漫画 · 阅读进度自动保存")
            if read and len(added) == 1:
                self._show_reader(*added[0])

        self.jobs.start(lambda: discover_books(folder, library_root), done)

    def open_folder(self, folder):
        """Entry point for the completed-download action."""
        self.import_folder(folder, read=True)

    def refresh_shelf(self, *_):
        if self._closed:
            return
        query = self.search.text().strip().casefold()
        mode = self.filter.currentIndex()
        books = [b for b in self.library.books if query in b.title.casefold()
                 and (mode != 1 or b.last_read) and (mode != 2 or b.favorite)]
        sort = self.sort.currentIndex()
        if sort == 1:
            books.sort(key=lambda b: natural_key(b.title))
        elif sort == 2:
            books.sort(key=lambda b: (b.page_index + 1) / max(1, b.page_count) if b.last_read else 0, reverse=True)
        else:
            books.sort(key=lambda b: b.last_read, reverse=True)
        self.shelf.clear()
        for book in books:
            item = QListWidgetItem(book.title)
            item.setData(Qt.ItemDataRole.UserRole, book)
            item.setToolTip(book.folder)
            item.setSizeHint(QSize(200, 316))
            key = folder_key(book.folder)
            if key in self._covers:
                item.setData(Qt.ItemDataRole.DecorationRole, self._covers[key])
            self.shelf.addItem(item)
            self._load_cover(book)
        self.shelf.setVisible(bool(books))
        self.empty.setVisible(not books)
        self.empty.setText("没有匹配的漫画。试试其他关键词或分类。" if self.library.books
                           else "从一个文件夹开始阅读\n\n点击「添加漫画」，或把漫画文件夹拖到这里。\n一个文件夹一本漫画，每张 JPG 是一页；支持章节子目录。")

    def _load_cover(self, book):
        key = folder_key(book.folder)
        if key in self._covers or key in self._cover_pending:
            return
        generation = self._cover_generation
        self._cover_pending.add(key)

        def load():
            pages = scan_pages(book.folder)
            return read_image(pages[0], True) if pages else None

        def done(image, error):
            self._cover_pending.discard(key)
            if generation != self._cover_generation:
                if any(folder_key(b.folder) == key for b in self.library.books):
                    self._load_cover(book)
                return
            if not any(b is book for b in self.library.books):
                return
            if error or image is None:
                return
            pixmap = QPixmap.fromImage(image)
            if len(self._covers) >= 120:
                self._covers.pop(next(iter(self._covers)))
            self._covers[key] = pixmap
            for i in range(self.shelf.count()):
                item = self.shelf.item(i)
                if folder_key(item.data(Qt.ItemDataRole.UserRole).folder) == key:
                    item.setData(Qt.ItemDataRole.DecorationRole, pixmap)

        self.jobs.start(load, done)

    def open_book(self, book):
        if self._busy or self._closed:
            return
        if self._reader is not None and not self._reader._closed and self._reader.book is book:
            self._reader.raise_()
            self._reader.activateWindow()
            return
        self._set_busy(True)
        self.status.setText("正在打开漫画…")

        def done(pages, error):
            self._set_busy(False)
            if error or not pages:
                self.status.setText(f"无法打开：{error or '文件夹中没有 JPG / JPEG 页面'}。记录已保留，可重新添加移动后的文件夹。")
                return
            self.library.add(book.folder, pages)
            self._show_reader(book, pages)

        self.jobs.start(lambda: scan_pages(book.folder), done)

    def _show_reader(self, book, pages):
        if self._reader is not None:
            self._reader.close()
        reader = MangaReader(self.library, book, pages, self.window())
        self._reader = reader
        reader.changed.connect(self._reader_changed)

        reader.destroyed.connect(lambda *_: self._reader_destroyed(reader))
        reader.show()
        reader.raise_()
        reader.activateWindow()
        self.status.setText(f"正在阅读：{book.title}")

    def _reader_destroyed(self, reader):
        if self._reader is reader:
            self._reader = None

    def _reader_changed(self):
        # Update visible cards without rebuilding a large shelf on every page turn.
        self.shelf.viewport().update()
        if self._reader is not None and self._reader._closed:
            self.refresh_shelf()

    def refresh_files(self):
        if self._busy:
            return
        self._set_busy(True)
        books = list(self.library.books)
        self.status.setText("正在刷新页数与封面…")

        def scan():
            result = []
            for book in books:
                try:
                    result.append((book, scan_pages(book.folder)))
                except OSError:
                    result.append((book, None))
            return result

        def done(results, error):
            self._set_busy(False)
            if error:
                self.status.setText(f"刷新失败：{error}")
                return
            unavailable = 0
            for book, pages in results:
                if not pages:
                    unavailable += 1
                else:
                    self.library.add(book.folder, pages)
                    if self._reader is not None and not self._reader._closed and self._reader.book is book:
                        self._reader.pages = pages
                        self._reader.slider.setMaximum(len(pages))
                        self._reader.jump.setMaximum(len(pages))
                        self._reader.show_page(book.restore_index(pages))
            self._covers.clear()
            self._cover_generation += 1
            saved = self._save()
            self.refresh_shelf()
            if saved:
                self.status.setText(f"书架已刷新 · {unavailable} 本暂时无法读取，记录已保留" if unavailable else "书架已刷新")

        self.jobs.start(scan, done)

    def _context_menu(self, position):
        if self._busy:
            return
        item = self.shelf.itemAt(position)
        if item is None:
            return
        book = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction("阅读 / 继续阅读", lambda: self.open_book(book))
        menu.addAction("取消收藏" if book.favorite else "收藏", lambda: self._favorite(book))
        menu.addAction("打开原文件夹", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(book.folder)))
        menu.addSeparator()
        menu.addAction("从书架移除（保留图片）", lambda: self._remove(book))
        menu.exec(self.shelf.mapToGlobal(position))

    def _favorite(self, book):
        book.favorite = not book.favorite
        self._save()
        self.refresh_shelf()

    def _remove(self, book):
        if self._reader is not None and self._reader.book is book:
            self._reader.close()
        self.library.books.remove(book)
        self._covers.pop(folder_key(book.folder), None)
        self._cover_generation += 1
        self._save()
        self.refresh_shelf()

    def _more_menu(self):
        menu = QMenu(self)
        menu.addAction("导入当前下载目录", lambda: self.import_folder(self.ctx.config.get("download_path"), True))
        menu.addAction("重新加载已保存书架（放弃未保存修改）", self.reload_library)
        menu.addAction("导入独立页间阅读器书架…", self.import_standalone)
        menu.addAction("使用说明", lambda: QMessageBox.information(
            self, "漫画阅读器", "添加漫画：选择一本的文件夹，可包含章节子目录。\n"
            "导入漫画库：选择总目录，每个子文件夹一本。\n"
            "文件名按 1、2、10 自然排序，支持 JPG / JPEG。\n"
            "点击封面继续阅读，右键可收藏或移除记录。\n"
            "可拖入一个或多个漫画文件夹；总目录请使用「导入漫画库」。\n"
            "下载任务的「阅读漫画」按钮可打开已下载图集。\n\n"
            "书架保存在程序目录的 manga_library.json，原图片不被修改。"))
        menu.exec(self.moreBtn.mapToGlobal(self.moreBtn.rect().bottomLeft()))

    def reload_library(self):
        if self._busy or self._closed:
            return
        if self._reader is not None:
            self._reader.close()
            self._reader = None
        try:
            self.library.load()
        except OSError as error:
            self.status.setText(f"重新加载失败：{error}")
            return
        self._covers.clear()
        self._cover_generation += 1
        self.refresh_shelf()
        self.status.setText(self.library.warning or "已重新加载磁盘上的书架记录")

    def import_standalone(self):
        default = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "FolderMangaReader", "library.json")
        path, _ = QFileDialog.getOpenFileName(self, "选择页间阅读器的 library.json", default, "书架文件 (*.json)")
        if not path:
            return
        try:
            count = self.library.import_yiejian(path)
            if self._save():
                self.status.setText(f"已导入页间书架的 {count} 本漫画，收藏和进度已保留")
            self.refresh_shelf()
        except (OSError, ValueError, TypeError) as error:
            self.status.setText(f"导入失败：{error}")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self._busy:
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self._busy:
            return
        paths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            folder = path if os.path.isdir(path) else os.path.dirname(path) if is_page(path) else ""
            if folder and folder_key(folder) not in {folder_key(p) for p in paths}:
                paths.append(folder)
        if not paths:
            return
        event.acceptProposedAction()
        self._set_busy(True)
        self.status.setText("正在导入拖入的漫画…")

        def scan():
            return [(folder, scan_pages(folder)) for folder in paths]

        def done(found, error):
            self._set_busy(False)
            if error:
                self.status.setText(f"导入失败：{error}")
                return
            for folder, pages in found:
                if pages:
                    self.library.add(folder, pages)
            if self._save():
                self.status.setText(f"已导入 {sum(bool(pages) for _, pages in found)} 本漫画")
            self.refresh_shelf()

        self.jobs.start(scan, done)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._auto_add_queue.clear()
        self._auto_add_keys.clear()
        if self._reader is not None:
            self._reader.close()
        self.jobs.shutdown()
