# -*- coding: utf-8 -*-
"""下载任务页：进度、状态、操作"""
import logging
import os
import shutil

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QTextCharFormat,
                           QTextCursor)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout,
                               QHeaderView, QLabel, QSizePolicy,
                               QTableWidgetItem, QWidget)
from qfluentwidgets import (FluentIcon as FIF, InfoBar, InfoBarPosition,
                            MessageBox, PlainTextEdit, ProgressBar, PushButton,
                            ScrollArea, TableWidget, TransparentToolButton)

from ..core import applog
from ..core.models import DownloadTaskInfo, fmt_size, fmt_speed, quality_html
from . import design as D
from .perf import ProgressThrottle
from ..core.applog import get_logger

COL_NAME, COL_SIZE, COL_PROGRESS, COL_SPEED, COL_QUALITY, COL_STATUS, COL_ACTION = range(7)

# 日志面板：轮询间隔 / 面板保留行数 / 打开页面时先回看多少行
LOG_POLL_MS = 400
LOG_VIEW_LINES = applog.MEMORY_LINES      # 与内存缓冲对齐：再大也取不到更多
LOG_START_LINES = 200
_LOG_COLORS = {
    logging.WARNING: "#D97706",
    logging.ERROR: "#E5484D",
    logging.CRITICAL: "#E5484D",
}

log = get_logger("task")


class TaskInterface(ScrollArea):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("taskInterface")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.view = QWidget(self)
        self.view.setObjectName("taskView")
        self.setWidget(self.view)
        self.vBox = D.page_layout(self.view)

        self._rows = {}   # task_id -> row
        # 进度更新节流：把高频的进度信号合并成低频刷新，避免 UI 线程被压满
        self._throttle = ProgressThrottle(120, self)
        self._throttle.set_handler(self._apply_updates)

        self._build_header()
        self._build_table()
        self._build_log_card()

        # 日志面板：先回看最近若干行，之后按固定间隔轮询内存日志（见 applog.MemoryHandler）
        self._log_seq, lines = applog.snapshot(LOG_START_LINES)
        self._append_logs(lines)
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(LOG_POLL_MS)
        self._log_timer.timeout.connect(self._drain_logs)
        self._log_timer.start()

        m = self.ctx.manager
        m.task_added.connect(self._on_task_added)
        m.task_progress.connect(self._on_task_update)
        m.task_finished.connect(self._on_task_update)
        self._init_style()

    def showEvent(self, event):
        """切回本页时立刻补一次日志，别等到下一个轮询周期"""
        super().showEvent(event)
        self._drain_logs()

    def _init_style(self):
        self.view.setStyleSheet("QWidget#taskView { background: transparent; }")

    def _build_header(self):
        self.vBox.addWidget(D.PageHeader(
            "下载任务",
            "进度与队列管理　·　底部运行日志记录解析与下载的关键节点",
            self.view))

    def _build_table(self):
        card, lay = D.card(self.view)

        toolbar = QHBoxLayout()
        self.summaryLabel = D.section_label("暂无任务", card)
        toolbar.addWidget(self.summaryLabel)
        toolbar.addStretch(1)
        openBtn = PushButton(FIF.FOLDER, "打开下载目录", card)
        openBtn.clicked.connect(self._open_download_dir)
        self.retryFailedBtn = PushButton(FIF.SYNC, "重试失败", card)
        self.retryFailedBtn.clicked.connect(self._retry_failed)
        clearBtn = PushButton(FIF.DELETE, "清空已完成", card)
        clearBtn.clicked.connect(self._clear_finished)
        toolbar.addWidget(openBtn)
        toolbar.addWidget(self.retryFailedBtn)
        toolbar.addWidget(clearBtn)
        lay.addLayout(toolbar)

        self.table = TableWidget(card)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["文件名", "大小", "进度", "速度", "画质", "状态", "操作"])
        self.table.verticalHeader().hide()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        for c in (COL_SIZE, COL_PROGRESS, COL_SPEED, COL_QUALITY,
                  COL_STATUS, COL_ACTION):
            header.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(360)
        self.table.setMinimumWidth(0)
        lay.addWidget(self.table)

        self.emptyLabel = D.hint_label(
            "暂无下载任务。在「视频解析」或「批量下载」页面开始下载后，任务会显示在这里",
            card)
        self.emptyLabel.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.emptyLabel)
        self.vBox.addWidget(card)

    def _build_log_card(self):
        """运行日志面板：解析/下载的关键节点都在这儿，出问题不用再去翻文件"""
        card, lay = D.card(self.view)

        head = QHBoxLayout()
        head.addWidget(D.section_label("运行日志", card))
        # 这里只显示相对路径，完整路径放 tooltip：标签太长会把右边的按钮挤出窗口
        path = applog.log_path()
        self.logHint = D.hint_label(
            f"日志文件：{os.path.join('logs', 'app.log')}" if path
            else "日志未启用（程序目录不可写）", card)
        self.logHint.setToolTip(
            f"完整路径：{path}\n下载与解析的关键节点都记在这里（面板只保留最近 "
            f"{LOG_VIEW_LINES} 行，文件里是全量的）" if path
            else "程序目录不可写，日志没有启用")
        head.addSpacing(8)
        head.addWidget(self.logHint)
        head.addStretch(1)

        copyBtn = PushButton(FIF.COPY, "复制", card)
        copyBtn.setToolTip("把面板里的日志复制到剪贴板")
        copyBtn.clicked.connect(self._copy_log)
        clearBtn = PushButton(FIF.DELETE, "清空显示", card)
        clearBtn.setToolTip("只清空这里的显示，不影响 logs\\app.log 文件")
        clearBtn.clicked.connect(self._clear_log_view)
        openBtn = PushButton(FIF.DOCUMENT, "打开日志文件", card)
        openBtn.clicked.connect(self._open_log_file)
        for btn in (copyBtn, clearBtn, openBtn):
            head.addWidget(btn)
        lay.addLayout(head)

        self.logView = PlainTextEdit(card)
        self.logView.setReadOnly(True)
        # 自动折行：日志里有几百字符的 URL，qfluentwidgets 的平滑滚动条会把原生横向
        # 滚动条关掉（SmoothScrollDelegate），不折行就只能看前半截。
        # 横向尺寸策略设成 Ignored，免得长行把外层页面撑宽、把右边按钮挤出窗口。
        self.logView.setLineWrapMode(PlainTextEdit.WidgetWidth)
        self.logView.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        # 超过上限自动丢最旧的行：挂一整天也不会越滚越卡
        self.logView.setMaximumBlockCount(LOG_VIEW_LINES)
        self.logView.setMinimumHeight(200)
        self.logView.setMaximumHeight(240)
        self.logView.setPlaceholderText("暂无日志")
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(9)
        self.logView.setFont(font)
        lay.addWidget(self.logView)
        self.vBox.addWidget(card)

    # ------------------------------------------------------------------ #
    # 日志面板
    def _drain_logs(self):
        if not self.isVisible():
            return                      # 不在本页就不刷，别白白占 UI 线程
        seq, lines = applog.fetch_since(self._log_seq)
        if not lines:
            return
        self._log_seq = seq
        self._append_logs(lines)

    def _append_logs(self, lines):
        if not lines:
            return
        bar = self.logView.verticalScrollBar()
        # 停在底部就继续跟随最新；往上翻看历史时别把他拽回去
        follow = bar.value() >= bar.maximum() - 4
        cursor = self.logView.textCursor()
        cursor.movePosition(QTextCursor.End)
        for level, text in lines:
            fmt = QTextCharFormat()
            color = _LOG_COLORS.get(level)
            if color:
                fmt.setForeground(QColor(color))
            cursor.insertText(text + "\n", fmt)
        self.logView.setTextCursor(cursor)
        if follow:
            bar.setValue(bar.maximum())

    def _copy_log(self):
        text = self.logView.toPlainText()
        if not text.strip():
            self._toast("没有日志", "日志面板当前是空的", "warning")
            return
        QApplication.clipboard().setText(text)
        self._toast("已复制", f"日志已复制到剪贴板（{len(text)} 字符）", "success")

    def _clear_log_view(self):
        self.logView.clear()
        applog.clear_memory()
        # 缓冲清空后序号还在递增：同步一次，免得把清掉之前的行又补回来
        self._log_seq, _ = applog.snapshot(0)

    def _open_log_file(self):
        path = applog.log_path()
        if not path or not os.path.exists(path):
            self._toast("日志文件不存在", "程序目录不可写，日志没有启用", "warning")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ------------------------------------------------------------------ #
    def _on_task_added(self, info: DownloadTaskInfo):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._rows[info.task_id] = row

        name = QTableWidgetItem(info.name)
        name.setToolTip(info.save_path)
        name.setData(Qt.UserRole, info.task_id)   # 行 → 任务 的锚点，删行/对齐靠它
        self.table.setItem(row, COL_NAME, name)
        self.table.setItem(row, COL_SIZE, QTableWidgetItem("--"))

        bar = ProgressBar(self.table)
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setFixedWidth(160)
        self.table.setCellWidget(row, COL_PROGRESS, bar)

        self.table.setItem(row, COL_SPEED, QTableWidgetItem("--"))
        self.table.setCellWidget(row, COL_QUALITY, self._quality_widget(info.quality))
        self.table.setItem(row, COL_STATUS, QTableWidgetItem(info.status))

        actions = QWidget(self.table)
        h = QHBoxLayout(actions)
        h.setContentsMargins(4, 0, 4, 0)
        h.setSpacing(2)
        cancelBtn = TransparentToolButton(FIF.CLOSE, actions)
        cancelBtn.setToolTip("取消")
        cancelBtn.clicked.connect(lambda _, tid=info.task_id: self.ctx.manager.cancel(tid))
        retryBtn = TransparentToolButton(FIF.SYNC, actions)
        retryBtn.setToolTip("重试")
        retryBtn.clicked.connect(lambda _, tid=info.task_id: self.ctx.manager.retry(tid))
        openBtn = TransparentToolButton(FIF.FOLDER, actions)
        openBtn.setToolTip("打开所在文件夹")
        openBtn.clicked.connect(
            lambda _, p=info.save_path: self._open_file_location(p))
        delBtn = TransparentToolButton(FIF.DELETE, actions)
        delBtn.setToolTip("删除下载的文件")
        delBtn.clicked.connect(
            lambda _, tid=info.task_id: self._on_delete_file(tid))
        h.addWidget(cancelBtn)
        h.addWidget(retryBtn)
        h.addWidget(openBtn)
        h.addWidget(delBtn)
        self.table.setCellWidget(row, COL_ACTION, actions)
        self._refresh_summary()

    def _quality_widget(self, quality: str):
        """「画质」单元格：分辨率/码率 + 高亮帧率（60fps 绿色、30fps 灰色）"""
        label = QLabel(self.table)
        label.setContentsMargins(6, 0, 6, 0)
        if not quality:
            label.setText("--")
            label.setToolTip("该来源不提供多档清晰度")
            return label
        label.setText(quality_html(quality))
        label.setTextFormat(Qt.RichText)
        label.setToolTip(f"下载所用档位：{quality}")
        return label

    def _on_task_update(self, info: DownloadTaskInfo):
        """高频信号入口：只入队，由节流器合并后再真正刷 UI"""
        self._throttle.push(info.task_id, info)

    def _apply_updates(self, batch):
        """节流后的批量刷新（每 120ms 最多一次）"""
        for info in batch:
            row = self._rows.get(info.task_id)
            if row is None:
                continue
            size_item = self.table.item(row, COL_SIZE)
            if size_item:
                if (info.url or "").startswith("jmcomic://"):
                    txt = f"{info.downloaded}/{info.total} 张" if info.total else "--"
                elif info.total:
                    txt = fmt_size(info.total)
                else:
                    txt = size_item.text() or "--"
                if size_item.text() != txt:
                    size_item.setText(txt)
            bar = self.table.cellWidget(row, COL_PROGRESS)
            if bar and bar.value() != info.percent:
                bar.setValue(info.percent)
            speed_item = self.table.item(row, COL_SPEED)
            if speed_item:
                txt = fmt_speed(info.speed) if info.speed > 0 else "--"
                if speed_item.text() != txt:
                    speed_item.setText(txt)
            status_item = self.table.item(row, COL_STATUS)
            if status_item:
                if info.status == "失败" and info.error:
                    text = f"失败：{info.error[:30]}"
                elif info.status == "已取消":
                    text = "已取消"
                else:
                    text = info.status
                if status_item.text() != text:
                    status_item.setText(text)
                    status_item.setToolTip(info.error)
        self._refresh_summary()

    def _refresh_summary(self):
        """增量统计：任务列表变化时才重算，避免每次进度更新都全量遍历"""
        tasks = self.ctx.manager.all_tasks()
        count = len(tasks)
        has_task = bool(tasks)
        if self.table.isVisible() != has_task:
            self.table.setVisible(has_task)
            self.emptyLabel.setVisible(not has_task)
        if not tasks:
            if self.summaryLabel.text() != "暂无任务":
                self.summaryLabel.setText("暂无任务")
            return
        done = 0
        running = 0
        for t in tasks:
            s = t.status
            if s == "已完成":
                done += 1
            elif s in ("下载中", "等待中"):
                running += 1
        text = f"共 {count} 个任务 · 进行中 {running} · 已完成 {done}"
        if self.summaryLabel.text() != text:
            self.summaryLabel.setText(text)

    def _open_download_dir(self):
        path = self.ctx.config.get("download_path")
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_file_location(self, path: str):
        if path and os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            return
        folder = os.path.dirname(path)
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    @staticmethod
    def _related_files(path: str) -> list:
        """与主文件配套的文件（如封面 `<同名>_cover.jpg`）"""
        if not path:
            return []
        root, _ = os.path.splitext(path)
        cover = root + "_cover.jpg"
        return [cover] if os.path.exists(cover) else []

    def _on_delete_file(self, task_id: str):
        """删除该任务已下载的文件，并清除对应的任务记录"""
        mgr = self.ctx.manager
        info = next((t for t in mgr.all_tasks() if t.task_id == task_id), None)
        if info is None:
            # 记录已经不在了（异常情况下遗留的行）：清掉这行并给个反馈，别静默无反应
            QTimer.singleShot(0, self._prune_rows)
            self._toast("任务已不存在", "这条任务记录已经被清除了", "warning")
            return
        if info.status in ("下载中", "等待中", "合并中"):
            self._toast("无法删除", "任务正在进行，请先取消任务再删除文件", "warning")
            return

        path = info.save_path or ""
        exists = bool(path) and os.path.exists(path)
        is_dir = exists and os.path.isdir(path)
        extras = [] if is_dir else self._related_files(path)
        if exists:
            size = "文件夹" if is_dir else fmt_size(os.path.getsize(path))
            extra_txt = (f"\n同时删除 {len(extras)} 个关联文件（封面）"
                         if extras else "")
            box = MessageBox(
                "删除文件",
                (f"确定要删除已下载的文件吗？\n\n{os.path.basename(path)}"
                 f"\n大小 {size}{extra_txt}\n\n此操作不可撤销，任务记录会一并清除。"),
                self.window())
        else:
            box = MessageBox("文件已不存在",
                             "本地文件已不在原位置，是否仅清除这条任务记录？",
                             self.window())
        if not box.exec():
            return

        # 先发取消信号，避免下载线程继续写这个文件
        mgr.cancel(task_id)
        removed, failed = [], []
        for p in ([path] if exists else []) + extras:
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
                removed.append(p)
            except OSError as e:
                failed.append(f"{os.path.basename(p)}：{e}")
        # 从任务表移除（只允许清除已结束的任务）
        if mgr.remove_tasks([task_id]):
            self._throttle._pending.clear()
            # 此时还在这个按钮自己的点击事件里，删行要等事件走完
            QTimer.singleShot(0, self._prune_rows)
        self._refresh_summary()

        if failed:
            log.warning("删除文件（含封面）失败 %s：%s", task_id, "；".join(failed))
            self._toast("部分删除失败", "；".join(failed)[:150], "error")
        elif removed:
            log.info("已删除下载文件 %s：%s", task_id,
                     "；".join(os.path.basename(p) for p in removed))
            self._toast("已删除", f"已删除 {len(removed)} 个文件", "success")
        else:
            log.info("文件已不存在，仅清除记录 %s", task_id)
            self._toast("已清除记录", "文件不存在，仅清除了任务记录", "warning")

    def _toast(self, title: str, content: str, kind: str):
        fn = {"success": InfoBar.success, "error": InfoBar.error,
              "warning": InfoBar.warning}.get(kind, InfoBar.info)
        fn(title, content, orient=Qt.Horizontal, isClosable=True,
           position=InfoBarPosition.TOP, duration=3000, parent=self)

    def _clear_finished(self):
        count = len(self.ctx.manager.finished_tasks())
        if not count:
            return
        w = MessageBox("清空记录", f"确定要清除 {count} 条已结束的任务记录吗？"
                                 "（不会删除已下载的文件）", self.window())
        if not w.exec():
            return
        if not self.ctx.manager.clear_finished():
            return
        self._throttle._pending.clear()
        self._prune_rows()
        self._refresh_summary()
        log.info("清空已结束的任务记录 %d 条", count)

    def _retry_failed(self):
        n = self.ctx.manager.retry_tasks()
        if n:
            self._toast("重新入队", f"{n} 个失败任务已重新加入队列", "success")
        else:
            self._toast("无需重试", "当前没有失败的任务", "warning")

    def _sync_rows(self):
        """按表格的实际行序重建 task_id → row 映射（增删行后必须调用）

        注意**不要**拿 `manager.all_tasks()` 的顺序来推行号：任务表里的行
        只增不减/或删除后行号会串位，两者顺序并不一致（映射一乱，进度就会
        画到别的行上，删除按钮也会点到「已经没有记录的行」而毫无反应）。
        """
        self._rows = {}
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_NAME)
            tid = item.data(Qt.UserRole) if item is not None else None
            if tid:
                self._rows[tid] = row

    def _prune_rows(self):
        """删掉已经没有对应任务记录的行（幽灵行），并对齐行号映射"""
        alive = {t.task_id for t in self.ctx.manager.all_tasks()}
        for row in range(self.table.rowCount() - 1, -1, -1):
            item = self.table.item(row, COL_NAME)
            tid = item.data(Qt.UserRole) if item is not None else None
            if not tid or tid not in alive:
                self.table.removeRow(row)
        self._sync_rows()
