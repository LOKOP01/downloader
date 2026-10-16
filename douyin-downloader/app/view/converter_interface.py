"""原生、本地 WebP 转换页面；工作线程不持有任何 QWidget。"""
import os
import subprocess
from pathlib import Path
import threading

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot, QEvent
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QColorDialog, QFileDialog,
                               QFormLayout, QHBoxLayout, QListWidget,
                               QListWidgetItem, QProgressBar, QWidget)
from qfluentwidgets import (CheckBox, ComboBox, LineEdit, PrimaryPushButton,
                            PushButton, ScrollArea, ListWidget)

from ..core.ffmpeg import ffmpeg_path
from ..core.system_open import open_local_path
from ..core.webp_converter import ConversionCancelled, ConversionError, convert, merge_webps
from . import design as D


class _Signals(QObject):
    result = Signal(int, str, str, str)
    progress = Signal(int, int, str)
    finished = Signal()


class _ConversionJob:
    """独立线程只保留不可变任务快照、取消标记和信号桥，无 UI 生命周期依赖。"""
    def __init__(self, tasks, merge, options):
        self.signals = _Signals()
        self.cancel = threading.Event()
        self.tasks = tasks
        self.merge = merge
        self.options = options
        self.thread = threading.Thread(target=self.run, name="webp-converter", daemon=True)

    def run(self):
        try:
            for index, (sources, destination) in enumerate(self.tasks):
                if self.cancel.is_set():
                    for pending in range(index, len(self.tasks)):
                        self.signals.result.emit(pending, "已取消", "", "未开始")
                    break
                self.signals.result.emit(index, "处理中", "", "")
                try:
                    kwargs = dict(self.options, cancel=self.cancel,
                                  progress_cb=self.signals.progress.emit)
                    if self.merge:
                        merge_webps(sources, destination, **kwargs)
                    else:
                        convert(sources[0], destination, **kwargs)
                    self.signals.result.emit(index, "完成", str(destination), "")
                except ConversionCancelled as error:
                    self.cancel.set()
                    self.signals.result.emit(index, "已取消", "", str(error))
                except Exception as error:
                    # 单文件失败（包括同名输出）不阻断批量后续任务。
                    self.signals.result.emit(index, "失败", "", str(error))
        finally:
            self.signals.finished.emit()


class ConverterInterface(ScrollArea):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("converterInterface")
        self.setAcceptDrops(True)
        self._busy = False
        self._closed = False
        self._job = None
        self._background = "#ffffff"
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.content = QWidget(self)
        self.content.setObjectName("converterContent")
        self.setWidget(self.content)
        D.theme_qss(self.content, "QWidget#converterContent{background:transparent;}", "QWidget#converterContent{background:transparent;}")
        layout = D.page_layout(self.content)
        layout.addWidget(D.PageHeader("WebP 转 MP4", "本地处理 · 单个 / 批量转换 · 按顺序合并 · 无需浏览器", self))
        card, box = D.card(self)
        layout.addWidget(card, 1)
        self.editPanel = QWidget(self)
        from PySide6.QtWidgets import QVBoxLayout
        edit = QVBoxLayout(self.editPanel)
        edit.setContentsMargins(0, 0, 0, 0)
        edit.setSpacing(D.GAP_MD)
        box.addWidget(self.editPanel)
        toolbar = QHBoxLayout()
        self.addBtn = PrimaryPushButton("添加 WebP", self)
        self.upBtn = PushButton("上移", self)
        self.downBtn = PushButton("下移", self)
        self.removeBtn = PushButton("删除", self)
        self.clearBtn = PushButton("清空", self)
        for button in (self.addBtn, self.upBtn, self.downBtn, self.removeBtn, self.clearBtn):
            toolbar.addWidget(button)
        toolbar.addStretch()
        edit.addLayout(toolbar)
        self.inputList = ListWidget(self)
        self.inputList.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.inputList.setFixedHeight(110)
        self.inputList.setAcceptDrops(False)
        edit.addWidget(self.inputList)
        edit.addWidget(D.hint_label("多选或拖入 WebP；合并播放顺序以上方列表为准。同名输出默认报错，不会静默覆盖。", self))
        form = QFormLayout()
        self.modeCombo = ComboBox(self)
        self.modeCombo.addItems(["逐个转换（单个 / 批量）", "按列表顺序合并为一个 MP4"])
        self.crfCombo = ComboBox(self)
        self.crfCombo.addItems(["CRF 12 · 高保真（有损）", "CRF 18 · 推荐", "CRF 23 · 较小文件"])
        self.crfCombo.setCurrentIndex(1)
        self.backgroundBtn = PushButton("背景色：#ffffff", self)
        self.outputEdit = LineEdit(self)
        self.outputEdit.setText(str(Path(ctx.config.get("download_path")) / "converted"))
        self.outputBtn = PushButton("选择目录", self)
        output = QHBoxLayout()
        output.addWidget(self.outputEdit, 1)
        output.addWidget(self.outputBtn)
        self.mergeNameEdit = LineEdit(self)
        self.mergeNameEdit.setText("merged.mp4")
        self.mergeNameEdit.setEnabled(False)
        self.overwriteCheck = CheckBox("显式允许覆盖已有 MP4", self)
        self.overwriteCheck.setChecked(False)
        form.addRow(D.caption("转换方式", self), self.modeCombo)
        form.addRow(D.caption("画质", self), self.crfCombo)
        form.addRow(D.caption("不透明 RGB 背景", self), self.backgroundBtn)
        form.addRow(D.caption("输出目录", self), output)
        form.addRow(D.caption("合并文件名", self), self.mergeNameEdit)
        form.addRow(D.caption("覆盖策略", self), self.overwriteCheck)
        edit.addLayout(form)
        actions = QHBoxLayout()
        self.startBtn = PrimaryPushButton("开始转换", self)
        self.cancelBtn = PushButton("取消", self)
        self.cancelBtn.setEnabled(False)
        self.openBtn = PushButton("打开输出目录", self)
        self.previewBtn = PushButton("预览选中视频", self)
        self.previewBtn.setEnabled(False)
        for button in (self.startBtn, self.cancelBtn, self.openBtn, self.previewBtn):
            actions.addWidget(button)
        actions.addStretch()
        box.addLayout(actions)
        self.statusLabel = D.hint_label("等待添加 WebP", self)
        self.statusLabel.setWordWrap(True)
        box.addWidget(self.statusLabel)
        self.progressBar = QProgressBar(self)
        self.progressBar.setTextVisible(False)
        self.progressBar.setFixedHeight(6)
        D.theme_qss(self.progressBar,
                    "QProgressBar{border:0;border-radius:3px;background:#e5e7eb;}QProgressBar::chunk{background:#009faa;border-radius:3px;}",
                    "QProgressBar{border:0;border-radius:3px;background:#303036;}QProgressBar::chunk{background:#009faa;border-radius:3px;}")
        self.progressBar.setRange(0, 1)
        self.progressBar.setValue(0)
        box.addWidget(self.progressBar)
        self.resultList = ListWidget(self)
        self.resultList.setFixedHeight(100)
        box.addWidget(self.resultList, 1)
        self.addBtn.clicked.connect(self._choose_files)
        self.upBtn.clicked.connect(lambda: self.move_selected(-1))
        self.downBtn.clicked.connect(lambda: self.move_selected(1))
        self.removeBtn.clicked.connect(self.remove_selected)
        self.clearBtn.clicked.connect(self.clear_inputs)
        self.modeCombo.currentIndexChanged.connect(self._mode_changed)
        self.backgroundBtn.clicked.connect(self._choose_background)
        self.outputBtn.clicked.connect(self._choose_output)
        self.startBtn.clicked.connect(self.start_conversion)
        self.cancelBtn.clicked.connect(self.cancel_conversion)
        self.openBtn.clicked.connect(self.open_output_directory)
        self.previewBtn.clicked.connect(self.preview_selected)
        self.resultList.itemSelectionChanged.connect(self._update_preview)
        self.resultList.itemDoubleClicked.connect(self.preview_selected)
        # 实际鼠标常落在列表/输入框子控件，不能只处理外层滚动视口。
        for child in [self.content] + self.content.findChildren(QWidget):
            child.setAcceptDrops(True)
            child.installEventFilter(self)

    def input_paths(self):
        return [self.inputList.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.inputList.count())]

    def add_files(self, paths):
        if self._busy or self._closed:
            return
        known = {os.path.normcase(p) for p in self.input_paths()}
        skipped = 0
        for path in paths:
            path = Path(path).expanduser().resolve()
            key = os.path.normcase(str(path))
            if not path.is_file() or path.suffix.lower() != ".webp":
                skipped += 1
                continue
            if key not in known:
                item = QListWidgetItem(str(path))
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                self.inputList.addItem(item)
                known.add(key)
        self.statusLabel.setText(f"已添加 {self.inputList.count()} 个 WebP" +
                                 (f"；忽略 {skipped} 个非 WebP 或无效路径" if skipped else ""))

    def _choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择 WebP", "", "WebP (*.webp *.WEBP)")
        self.add_files(paths)

    def move_selected(self, direction):
        if self._busy or self._closed:
            return
        rows = sorted(self.inputList.row(item) for item in self.inputList.selectedItems())
        selected = set(rows)
        for row in (rows if direction < 0 else reversed(rows)):
            target = row + direction
            if 0 <= target < self.inputList.count() and target not in selected:
                item = self.inputList.takeItem(row)
                self.inputList.insertItem(target, item)
                item.setSelected(True)
                selected.remove(row)
                selected.add(target)

    def remove_selected(self):
        if not self._busy and not self._closed:
            for item in self.inputList.selectedItems():
                self.inputList.takeItem(self.inputList.row(item))

    def clear_inputs(self):
        if not self._busy and not self._closed:
            self.inputList.clear()

    def _mode_changed(self, index):
        self.mergeNameEdit.setEnabled(index == 1)

    def _choose_background(self):
        color = QColorDialog.getColor(QColor(self._background), self, "选择不透明背景色")
        if color.isValid():
            self._background = color.name(QColor.NameFormat.HexRgb)
            self.backgroundBtn.setText(f"背景色：{self._background}")

    def _choose_output(self):
        # 原生Windows选择器在受限启动环境可能不显示；Qt自绘对话框不依赖Shell。
        text = self.outputEdit.text().strip()
        initial = Path(text).expanduser() if text else Path(self.ctx.config.base_dir)
        while not initial.is_dir() and initial.parent != initial:
            initial = initial.parent
        path = QFileDialog.getExistingDirectory(
            self, "选择输出目录", str(initial),
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontUseNativeDialog)
        if path:
            self.outputEdit.setText(path)
            self.statusLabel.setText("输出目录已设为：" + path)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return self._handle_drag_event(event)
        return super().eventFilter(watched, event)

    def _handle_drag_event(self, event):
        if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            self.dragEnterEvent(event)
            return True
        if event.type() == QEvent.Type.Drop:
            self.dropEvent(event)
            return True
        return False

    def event(self, event):
        if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return self._handle_drag_event(event)
        return super().event(event)

    def viewportEvent(self, event):
        if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return self._handle_drag_event(event)
        return super().viewportEvent(event)

    def dragEnterEvent(self, event):
        if not self._busy and not self._closed and event.mimeData().hasUrls():
            if any(url.isLocalFile() and url.toLocalFile().lower().endswith(".webp")
                   for url in event.mimeData().urls()):
                event.acceptProposedAction()

    def dropEvent(self, event):
        if not self._busy and not self._closed:
            self.add_files([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
            event.acceptProposedAction()

    def start_conversion(self):
        if self._busy or self._closed:
            return
        sources = self.input_paths()
        if not sources:
            self.statusLabel.setText("请先添加 WebP 文件")
            return
        ffmpeg = ffmpeg_path(self.ctx.config.base_dir)
        if not ffmpeg:
            self.statusLabel.setText("未找到 FFmpeg，请前往设置下载 FFmpeg 组件，然后重试。不会自动联网。")
            return
        output_text = self.outputEdit.text().strip()
        if not output_text:
            self.statusLabel.setText("请选择输出目录")
            return
        output = Path(output_text).expanduser().resolve()
        merge = self.modeCombo.currentIndex() == 1
        name = self.mergeNameEdit.text().strip()
        if merge and (not name or name in (".", "..") or any(c in name for c in '/\\<>:"|?*')
                      or Path(name).suffix.lower() != ".mp4"):
            self.statusLabel.setText("合并文件名必须是 MP4 文件名，不能包含目录或非法字符")
            return
        tasks = ([(sources, output / name)] if merge else
                 [([source], output / (Path(source).stem + ".mp4")) for source in sources])
        self.resultList.clear()
        for inputs, destination in tasks:
            item = QListWidgetItem(f"等待 · {destination.name}")
            item.setToolTip("\n".join(inputs))
            self.resultList.addItem(item)
        options = dict(background=self._background, crf=(12, 18, 23)[self.crfCombo.currentIndex()],
                       overwrite=self.overwriteCheck.isChecked(), ffmpeg=ffmpeg)
        job = _ConversionJob(tasks, merge, options)
        self._job = job
        self._busy = True
        self.editPanel.setEnabled(False)
        self.startBtn.setEnabled(False)
        self.cancelBtn.setEnabled(True)
        self.statusLabel.setText("正在转换…")
        self.progressBar.setRange(0, 0)
        job.signals.result.connect(self._on_result, Qt.ConnectionType.QueuedConnection)
        job.signals.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        job.signals.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        # 即使主窗口未显式 shutdown，QObject 销毁也会通知引擎取消。
        self.destroyed.connect(job.cancel.set)
        job.thread.start()

    @Slot(int, str, str, str)
    def _on_result(self, index, status, path, detail):
        if self._closed:
            return
        item = self.resultList.item(index)
        destination = self._job.tasks[index][1]
        item.setText(f"{status} · {destination.name}" + (f" · {detail}" if detail else ""))
        item.setData(Qt.ItemDataRole.UserRole, path or None)
        item.setToolTip(str(destination) + (f"\n{detail}" if detail else ""))
        self._update_preview()

    @Slot(int, int, str)
    def _on_progress(self, done, total, message):
        if self._closed:
            return
        self.statusLabel.setText(message)
        self.progressBar.setRange(0, max(0, total))
        self.progressBar.setValue(max(0, min(done, total)))

    @Slot()
    def _on_finished(self):
        self._busy = False
        if self._closed:
            return
        self.editPanel.setEnabled(True)
        self.startBtn.setEnabled(True)
        self.cancelBtn.setEnabled(False)
        self.progressBar.setRange(0, 1)
        self.progressBar.setValue(1)
        self.statusLabel.setText("已取消，已完成的结果仍可预览" if self._job.cancel.is_set() else "处理结束，请查看各项结果")
        self.destroyed.disconnect(self._job.cancel.set)

    def cancel_conversion(self):
        if self._busy and self._job:
            self._job.cancel.set()
            self.cancelBtn.setEnabled(False)
            self.statusLabel.setText("正在取消，等待当前任务清理…")

    def _update_preview(self):
        item = self.resultList.currentItem()
        self.previewBtn.setEnabled(bool(item and item.data(Qt.ItemDataRole.UserRole)))

    def open_output_directory(self):
        text = self.outputEdit.text().strip()
        if not text:
            self.statusLabel.setText("请先选择输出目录")
            return
        try:
            path = Path(text).expanduser().resolve()
            if not path.is_dir():
                self.statusLabel.setText("输出目录尚不存在，请先选择已有目录或完成一次转换：" + str(path))
                return
            opened = open_local_path(path, lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
            self.statusLabel.setText(("已请求打开：" if opened else "系统未能打开目录，请复制路径到资源管理器：") + str(path))
        except (OSError, ValueError) as error:
            self.statusLabel.setText("打开目录失败：" + str(error))

    def preview_selected(self, *_):
        item = self.resultList.currentItem()
        path = item.data(Qt.ItemDataRole.UserRole) if item else None
        if path and Path(path).is_file():
            try:
                opened = open_local_path(path, lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
                if not opened:
                    self.statusLabel.setText("系统未能打开视频，请复制路径到播放器：" + path)
            except OSError as error:
                self.statusLabel.setText("打开视频失败：" + str(error))
        elif path:
            self.statusLabel.setText("结果文件不存在或已被移动")

    def shutdown(self):
        """主窗口 closeEvent 可直接调用；绝不在 GUI 线程 join / wait。"""
        self._closed = True
        if self._job:
            self._job.cancel.set()
        self.startBtn.setEnabled(False)
        self.cancelBtn.setEnabled(False)
        self.editPanel.setEnabled(False)

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
