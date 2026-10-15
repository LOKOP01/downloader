"""Native Qt manga reader: asynchronous image decoding and local progress."""
import itertools
import os
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QRectF, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QImageReader, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QDialog, QGraphicsScene, QGraphicsView,
                               QHBoxLayout, QLineEdit, QMessageBox, QSlider, QSpinBox, QVBoxLayout, QWidget)
from qfluentwidgets import ComboBox, PushButton

from . import design as D


class _Signals(QObject):
    done = Signal(int, object, object)


class _Job(QRunnable):
    def __init__(self, number, function, cancelled):
        super().__init__()
        self.number, self.function = number, function
        self.cancelled = cancelled
        self.signals = _Signals()

    def run(self):
        try:
            # Queued jobs must still complete bookkeeping after cancellation.
            # A function already past this check cannot be forcibly interrupted.
            result = None if self.cancelled.is_set() else self.function()
            self.signals.done.emit(self.number, result, None)
        except Exception as error:  # deliver failures on the UI thread
            self.signals.done.emit(self.number, None, error)


class LocalJobs(QObject):
    """Keep signal objects alive; every completion is queued to this UI object."""
    _retired = set()

    def __init__(self, parent):
        super().__init__(parent)
        # Retire this owner under QApplication while workers finish asynchronously.
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self._pending = None
        self._cancelled = Event()
        self.jobs = {}
        self._numbers = itertools.count()
        self.closed = False

    def start(self, function, callback):
        if self.closed:
            return
        number = next(self._numbers)
        job = _Job(number, function, self._cancelled)
        job.signals.done.connect(self._finish, Qt.ConnectionType.QueuedConnection)
        self.jobs[number] = (job, callback)
        self.pool.start(job)

    def start_latest(self, function, callback):
        if self.closed:
            return
        if self.jobs:
            self._pending = (function, callback)
        else:
            self.start(function, callback)

    @Slot(int, object, object)
    def _finish(self, number, result, error):
        entry = self.jobs.pop(number, None)
        try:
            if entry and not self.closed:
                entry[1](result, error)
        finally:
            if self.closed:
                if not self.jobs:
                    self._reap()
            elif self._pending is not None:
                pending, self._pending = self._pending, None
                self.start(*pending)

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self._cancelled.set()
        self._pending = None
        # Drop UI callbacks immediately, but retain signals until queued delivery.
        self.jobs = {number: (job, None) for number, (job, _) in self.jobs.items()}
        self.setParent(QApplication.instance())
        if self.jobs:
            self._retired.add(self)
        else:
            self._reap()

    def _reap(self):
        # done is emitted just before QRunnable.run returns. Do not destroy its
        # pool in that small interval, since the pool destructor would join it.
        if self.pool.activeThreadCount():
            QTimer.singleShot(10, self._reap)
            return
        self._retired.discard(self)
        self.deleteLater()


# Bound full-resolution decode even for codecs that ignore scaledSize.
MAX_IMAGE_PIXELS = 16_000_000


def read_image(path, cover=False):
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > MAX_IMAGE_PIXELS:
        raise ValueError("图片尺寸无效或超过安全像素上限（1600 万像素），请先缩小图片")
    if cover and size.isValid():
        reader.setScaledSize(size.scaled(360, 460, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ValueError(reader.errorString() or "图片无法解码")
    return image


class PageView(QGraphicsView):
    turn = Signal(int)
    zoomChanged = Signal()
    fullscreen = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.fit_mode = "page"
        self.zoom_factor = 1.0
        self._press = None
        self._moved = False
        self._double = False
        self._last_wheel = 0
        self._click_delta = 0
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(lambda: self.turn.emit(self._click_delta))

    def display(self, image):
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            raise ValueError("图片显示内存分配失败")
        self.scene().clear()
        self.scene().addPixmap(pixmap)
        self.scene().setSceneRect(QRectF(0, 0, image.width(), image.height()))
        self.apply_fit()
        self.verticalScrollBar().setValue(0)
        self.horizontalScrollBar().setValue(0)

    def clear_page(self):
        self.scene().clear()
        self.scene().setSceneRect(QRectF())

    def apply_fit(self):
        rect = self.sceneRect()
        if rect.isEmpty():
            return
        width, height = max(1, self.viewport().width() - 4), max(1, self.viewport().height() - 4)
        scale = 1.0 if self.fit_mode == "actual" else width / rect.width()
        if self.fit_mode == "page":
            scale = min(scale, height / rect.height())
        self.resetTransform()
        self.scale(scale * self.zoom_factor, scale * self.zoom_factor)
        self.zoomChanged.emit()

    def zoom_by(self, factor):
        before = self.zoom_factor
        self.zoom_factor = min(6.0, max(0.2, before * factor))
        self.scale(self.zoom_factor / before, self.zoom_factor / before)
        self.zoomChanged.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # A scrollbar can change viewport dimensions during fit; queue the update.
        QTimer.singleShot(0, self.apply_fit)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
            event.accept()
        elif self.fit_mode == "page" and self.zoom_factor <= 1.001:
            import time
            now = time.monotonic()
            if now - self._last_wheel > 0.16 and event.angleDelta().y():
                self._last_wheel = now
                self.turn.emit(1 if event.angleDelta().y() < 0 else -1)
            event.accept()
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        self._press = event.position().toPoint()
        self._moved = self._double = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press and (event.position().toPoint() - self._press).manhattanLength() > 6:
            self._moved = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._press and not self._moved and not self._double and event.button() == Qt.MouseButton.LeftButton:
            x = event.position().x() / max(1, self.viewport().width())
            if x < 0.33 or x > 0.67:
                self._click_delta = -1 if x < 0.33 else 1
                self._click_timer.start(QApplication.doubleClickInterval())
        self._press = None

    def mouseDoubleClickEvent(self, event):
        self._double = True
        self._click_timer.stop()
        self.fullscreen.emit()
        event.accept()


class MangaReader(QDialog):
    changed = Signal()

    def __init__(self, library, book, pages, parent=None):
        super().__init__(parent)
        self.library, self.book, self.pages = library, book, pages
        self.index = 0
        self._generation = 0
        self._closed = False
        self._controls_hidden = False
        self._was_maximized = False
        self.jobs = LocalJobs(self)
        self.setWindowTitle(f"{book.title} · 漫画阅读器")
        self.setWindowFlag(Qt.WindowType.WindowMinMaxButtonsHint, True)
        self.resize(1000, 800)
        self.setMinimumSize(760, 500)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setObjectName("mangaReader")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.top = QWidget(self)
        row = QHBoxLayout(self.top)
        row.setContentsMargins(16, 12, 16, 12)
        back = PushButton("← 返回书架", self.top)
        back.clicked.connect(self.close)
        row.addWidget(back)
        self.titleLabel = D.section_label(book.title, self.top)
        self.titleLabel.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.titleLabel, 1)
        self.favoriteBtn = PushButton(self.top)
        self.favoriteBtn.clicked.connect(self.toggle_favorite)
        row.addWidget(self.favoriteBtn)
        fullscreen = PushButton("全屏 F11", self.top)
        fullscreen.clicked.connect(self.toggle_fullscreen)
        row.addWidget(fullscreen)
        help_btn = PushButton("快捷键", self.top)
        help_btn.clicked.connect(self.show_help)
        row.addWidget(help_btn)
        layout.addWidget(self.top)
        self.view = PageView(self)
        self.view.fit_mode = library.preferences["fit"]
        self.view.turn.connect(self._direction_turn)
        self.view.zoomChanged.connect(self._zoom_label)
        self.view.fullscreen.connect(self.toggle_fullscreen)
        layout.addWidget(self.view, 1)
        self.message = D.caption("", self)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.message.setContentsMargins(16, 8, 16, 8)
        layout.addWidget(self.message)
        self.bottom = QWidget(self)
        bottom = QVBoxLayout(self.bottom)
        bottom.setContentsMargins(16, 10, 16, 12)
        line = QHBoxLayout()
        self.counter = D.caption("", self.bottom)
        line.addWidget(self.counter)
        self.slider = QSlider(Qt.Orientation.Horizontal, self.bottom)
        self.slider.setRange(1, len(pages))
        self.slider.setAccessibleName("漫画页码")
        self.slider.valueChanged.connect(self._slide)
        self._slide_timer = QTimer(self)
        self._slide_timer.setSingleShot(True)
        self._slide_timer.setInterval(100)
        self._slide_timer.timeout.connect(lambda: self.show_page(self.slider.value() - 1))
        line.addWidget(self.slider, 1)
        self.jump = QSpinBox(self.bottom)
        self.jump.setObjectName("mangaJump")
        self.jump.setFont(D.font(14))
        D.theme_qss(self.jump,
                    "QSpinBox#mangaJump{background:#F4F4F5;color:#18181B;border:1px solid #D4D4D8;border-radius:6px;padding:5px 8px;}",
                    "QSpinBox#mangaJump{background:#27272A;color:#FAFAFA;border:1px solid #3F3F46;border-radius:6px;padding:5px 8px;}")
        self.jump.setRange(1, len(pages))
        self.jump.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.jump.setFixedWidth(72)
        self.jump.setAccessibleName("跳到第几页")
        self.jump.lineEdit().returnPressed.connect(lambda: self.show_page(self.jump.value() - 1))
        line.addWidget(self.jump)
        jump_btn = PushButton("跳页", self.bottom)
        jump_btn.clicked.connect(lambda: self.show_page(self.jump.value() - 1))
        line.addWidget(jump_btn)
        bottom.addLayout(line)
        line = QHBoxLayout()
        self.prev = PushButton("← 上一页", self.bottom)
        self.next = PushButton("下一页 →", self.bottom)
        self.prev.clicked.connect(lambda: self.show_page(self.index - 1))
        self.next.clicked.connect(lambda: self.show_page(self.index + 1))
        line.addWidget(self.prev)
        line.addWidget(self.next)
        line.addStretch()
        zoom_out = PushButton("−", self.bottom)
        zoom_out.clicked.connect(lambda: self.view.zoom_by(1 / 1.2))
        line.addWidget(zoom_out)
        self.zoomLabel = D.caption("100%", self.bottom)
        line.addWidget(self.zoomLabel)
        zoom_in = PushButton("＋", self.bottom)
        zoom_in.clicked.connect(lambda: self.view.zoom_by(1.2))
        line.addWidget(zoom_in)
        self.fit = ComboBox(self.bottom)
        self.fit.addItems(["适合窗口", "适合宽度", "原始大小"])
        self.fit.setCurrentIndex(["page", "width", "actual"].index(self.view.fit_mode))
        self.fit.currentIndexChanged.connect(self._fit_changed)
        line.addWidget(self.fit)
        line.addStretch()
        self.directionBtn = PushButton(self.bottom)
        self.directionBtn.clicked.connect(self.toggle_direction)
        line.addWidget(self.directionBtn)
        self.backgroundBtn = PushButton(self.bottom)
        self.backgroundBtn.clicked.connect(self.toggle_background)
        line.addWidget(self.backgroundBtn)
        bottom.addLayout(line)
        layout.addWidget(self.bottom)
        D.theme_qss(self, "#mangaReader{background:#FAFAFA;}", "#mangaReader{background:#09090B;}")
        self._shortcuts = []
        self._editing_shortcuts = []
        for key, action in [("Right", lambda: self._direction_turn(1)), ("Left", lambda: self._direction_turn(-1)),
                            ("PgDown", lambda: self.show_page(self.index + 1)), ("PgUp", lambda: self.show_page(self.index - 1)),
                            ("Home", lambda: self.show_page(0)), ("End", lambda: self.show_page(len(self.pages) - 1)),
                            ("Space", self.scroll_or_turn), ("F11", self.toggle_fullscreen),
                            ("Escape", self.escape), ("Tab", self.toggle_controls),
                            ("+", lambda: self.view.zoom_by(1.2)), ("=", lambda: self.view.zoom_by(1.2)),
                            ("-", lambda: self.view.zoom_by(1 / 1.2)), ("0", self.reset_zoom)]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(action)
            self._shortcuts.append(shortcut)
            if key not in ("F11", "Escape"):
                self._editing_shortcuts.append(shortcut)
        self._focus_connection = QApplication.instance().focusChanged.connect(self._focus_changed)
        # Enter in the jump field must not activate a dialog's default Back button.
        for button in self.findChildren(PushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        self._apply_preferences()
        self.show_page(book.restore_index(pages))

    def _focus_changed(self, old, current):
        # Disable before ShortcutOverride processing: ignoring an activated "0"
        # shortcut would still swallow zeroes typed into the jump-page field.
        editing = isinstance(current, (QLineEdit, QAbstractSpinBox, ComboBox))
        for shortcut in self._editing_shortcuts:
            shortcut.setEnabled(not editing)

    def _save(self):
        try:
            self.library.save()
        except OSError as error:
            self.message.setText(f"进度保存失败：{error}")
            self.message.show()

    def show_page(self, index):
        if self._closed:
            return
        self._slide_timer.stop()
        self.index = max(0, min(index, len(self.pages) - 1))
        self._generation += 1
        generation = self._generation
        path = self.pages[self.index]
        self.counter.setText(f"{self.index + 1} / {len(self.pages)}")
        self.slider.blockSignals(True)
        self.slider.setValue(self.index + 1)
        self.slider.blockSignals(False)
        self.jump.setValue(self.index + 1)
        self.prev.setEnabled(self.index > 0)
        self.next.setEnabled(self.index < len(self.pages) - 1)
        self.titleLabel.setText(self.fontMetrics().elidedText(self.book.title, Qt.TextElideMode.ElideRight, 400))
        self.titleLabel.setToolTip(self.book.title)
        self.view.setToolTip(os.path.relpath(path, self.book.folder))
        self.view.clear_page()
        self.message.setText("正在加载页面…")
        self.message.show()

        def loaded(image, error):
            if self._closed or generation != self._generation:
                return
            if error:
                self.message.setText(f"这一页无法显示，可以继续翻页。\n{os.path.basename(path)}：{error}")
                return
            try:
                self.view.display(image)
            except (ValueError, MemoryError) as error:
                self.message.setText(f"这一页无法显示，可以继续翻页。\n{error}")
                return
            self.message.hide()
            self.book.mark_read(self.index, self.pages)
            self._save()
            self.changed.emit()
            self.view.setFocus()

        self.jobs.start_latest(lambda: read_image(path), loaded)

    def _slide(self, value):
        self._slide_timer.start()

    def _direction_turn(self, delta):
        self.show_page(self.index + (-delta if self.library.preferences["rtl"] else delta))

    def _zoom_label(self):
        self.zoomLabel.setText(f"{self.view.zoom_factor:.0%}")

    def _fit_changed(self, index):
        self.view.fit_mode = ["page", "width", "actual"][index]
        self.library.preferences["fit"] = self.view.fit_mode
        self.reset_zoom()
        self._save()
        self.view.setFocus()

    def reset_zoom(self):
        self.view.zoom_factor = 1.0
        self.view.apply_fit()

    def scroll_or_turn(self):
        bar = self.view.verticalScrollBar()
        if bar.value() < bar.maximum() - 2:
            bar.setValue(min(bar.maximum(), bar.value() + int(self.view.viewport().height() * 0.85)))
        else:
            self.show_page(self.index + 1)

    def _apply_preferences(self):
        self.favoriteBtn.setText("★ 已收藏" if self.book.favorite else "☆ 收藏")
        self.directionBtn.setText("从右到左" if self.library.preferences["rtl"] else "从左到右")
        paper = self.library.preferences["paper"]
        self.backgroundBtn.setText("深色背景" if paper else "纸色背景")
        self.view.setBackgroundBrush(QColor("#E1DCD1" if paper else "#0B0E13"))

    def toggle_favorite(self):
        self.book.favorite = not self.book.favorite
        self._apply_preferences()
        self._save()
        self.changed.emit()

    def toggle_direction(self):
        self.library.preferences["rtl"] = not self.library.preferences["rtl"]
        self._apply_preferences()
        self._save()
        self.view.setFocus()

    def toggle_background(self):
        self.library.preferences["paper"] = not self.library.preferences["paper"]
        self._apply_preferences()
        self._save()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized() if self._was_maximized else self.showNormal()
        else:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()

    def toggle_controls(self):
        self._controls_hidden = not self._controls_hidden
        self.top.setVisible(not self._controls_hidden)
        self.bottom.setVisible(not self._controls_hidden)

    def escape(self):
        if self.isFullScreen():
            self.toggle_fullscreen()
        elif self._controls_hidden:
            self.toggle_controls()
        else:
            self.close()

    def reject(self):
        self.escape()

    def show_help(self):
        QMessageBox.information(self, "漫画阅读器 · 快捷键",
                                "左右键 / PageUp、PageDown：翻页\nHome / End：首尾页\n"
                                "空格：向下滚一屏，到底后翻页\nCtrl + 滚轮 / ＋、−：缩放；0：恢复适配\n"
                                "F11 / 双击：全屏；Tab：隐藏工具栏；Esc：退出全屏或关闭\n"
                                "图片两侧点击翻页，放大后拖动平移。\n从右到左会交换左右键和点击方向。")

    def closeEvent(self, event):
        if self._closed:
            event.accept()
            return
        self._closed = True
        self._generation += 1
        self._slide_timer.stop()
        self.view._click_timer.stop()
        QObject.disconnect(self._focus_connection)
        self.jobs.shutdown()
        self.changed.emit()
        # QDialog.closeEvent calls reject(), which implements our Escape policy
        # and would recurse back into close(). A requested close is final.
        event.accept()
