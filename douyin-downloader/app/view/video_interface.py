# -*- coding: utf-8 -*-
"""本地视频库页面：卡片墙 + 续播进度，结构对齐 manga_interface.py。"""
import logging
import os
from collections import deque

from PySide6.QtCore import QRectF, QSize, Qt, QUrl, Slot
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QListView,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QStyle, QStyledItemDelegate, QWidget)
from qfluentwidgets import ComboBox, FluentIcon as FIF, PrimaryPushButton, PushButton, SearchLineEdit
from qfluentwidgets.common.style_sheet import isDarkTheme

from ..core.ffmpeg import ffmpeg_path
from ..core.video_library import (VideoLibrary, download_cover_image, extract_first_frame,
                                  find_downloaded_cover, folder_key, is_video,
                                  is_video_file_entry, natural_key, scan_videos)
from . import design as D
from .manga_reader import LocalJobs
from .video_player import VideoPlayer

log = logging.getLogger("vdl.video")


class _PosterDelegate(QStyledItemDelegate):
    """视频卡片：封面海报 + 时长徽章 + 标题 + 续播进度条。"""

    def __init__(self, covers, parent=None):
        # covers: {路径: QPixmap|None} 由页面持有并刷新，delegate 只读。
        super().__init__(parent)
        self._covers = covers

    def paint(self, painter, option, index):
        item = index.data(Qt.ItemDataRole.UserRole)
        if item is None:
            return
        dark = isDarkTheme()
        primary = QColor(D.TEXT_PRIMARY[int(dark)])
        muted = QColor(D.TEXT_SECONDARY[int(dark)])
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(8, 8, -8, -8)
        poster_rect = QRectF(rect.left(), rect.top(), rect.width(), 128)
        clip = QPainterPath()
        clip.addRoundedRect(poster_rect, 10, 10)
        painter.fillPath(clip, QColor(D.BG_TERTIARY[int(dark)]))
        pixmap = self._covers.get(item.cover) if item.cover else None
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            painter.save()
            painter.setClipPath(clip)
            # KeepAspectRatioByExpanding 铺满海报区并居中裁掉溢出（对齐漫画封面画法）
            scaled = pixmap.scaled(poster_rect.size().toSize(),
                                   Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)
            left = (scaled.width() - int(poster_rect.width())) // 2
            top = (scaled.height() - int(poster_rect.height())) // 2
            painter.drawPixmap(poster_rect.toRect(),
                               scaled.copy(left, top, int(poster_rect.width()), int(poster_rect.height())))
            painter.restore()
        else:
            painter.setPen(muted)
            painter.setFont(D.font(16))
            painter.drawText(poster_rect, Qt.AlignmentFlag.AlignCenter, "本地视频")
        badge = QRectF(rect.left() + 9, rect.top() + 99, 76, 22)
        painter.setBrush(QColor(0, 0, 0, 180))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(badge, 5, 5)
        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(D.font(11))
        from ..core.video_library import fmt_time
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter,
                         fmt_time(item.duration_ms) if item.duration_ms else "未播放")
        painter.setPen(primary)
        painter.setFont(D.font(14, D.W_SEMI))
        title = painter.fontMetrics().elidedText(item.title, Qt.TextElideMode.ElideRight, int(rect.width()))
        painter.drawText(QRectF(rect.left(), rect.top() + 140, rect.width(), 24), title)
        painter.setPen(muted)
        painter.setFont(D.font(11))
        progress = (f"上次看到 {fmt_time(item.resume_ms)}"
                    if item.resume_ms > 0 else ("已看完" if item.watched else "还没开始看"))
        painter.drawText(QRectF(rect.left(), rect.top() + 168, rect.width(), 20), progress)
        painter.fillRect(QRectF(rect.left(), rect.top() + 194, rect.width(), 3), QColor(D.BORDER[int(dark)]))
        if item.resume_ms and item.duration_ms:
            painter.fillRect(QRectF(rect.left(), rect.top() + 194, rect.width() * item.percent / 100, 3),
                             QColor(D.TEXT_ACCENT[int(dark)]))
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(D.TEXT_ACCENT[int(dark)]))
            painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 5), 10, 10)
        painter.restore()


class VideoInterface(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("videoInterface")
        self.setAcceptDrops(True)
        self.library = VideoLibrary(os.path.join(ctx.config.base_dir, "video_library.json"))
        self._busy = False
        self._closed = False
        self._auto_add_queue = deque()
        self._auto_add_keys = set()
        self._player = None
        self.jobs = LocalJobs(self)
        # 封面：路径 → QPixmap 的运行时缓存（含加载失败的负缓存 None），
        # 命中失败超过上限自动丢弃最旧的，行为对齐漫画封面的 _covers。
        self._covers = {}
        self._cover_pending = set()
        self._cover_generation = 0
        self._ffmpeg = ffmpeg_path()
        # 截帧缓存目录：程序目录/video_covers/（与 video_library.json 同级）
        self._covers_dir = os.path.join(ctx.config.base_dir, "video_covers")
        layout = D.page_layout(self)
        header = QHBoxLayout()
        header.addWidget(D.PageHeader("本地视频播放", "一键入库 · MP4 / MKV / WebM · 记住播放位置", self), 1)
        self.addBtn = PrimaryPushButton(FIF.ADD, "添加视频", self)
        self.addBtn.clicked.connect(self._choose_video)
        header.addWidget(self.addBtn)
        self.folderBtn = PushButton(FIF.FOLDER, "导入文件夹", self)
        self.folderBtn.clicked.connect(self._choose_folder)
        header.addWidget(self.folderBtn)
        layout.addLayout(header)
        tools = QHBoxLayout()
        self.search = SearchLineEdit(self)
        self.search.setPlaceholderText("搜索视频名称")
        self.search.setMaximumWidth(340)
        self.search.textChanged.connect(self.refresh_shelf)
        tools.addWidget(self.search, 1)
        self.sort = ComboBox(self)
        self.sort.addItems(["最近播放", "名称顺序", "观看进度"])
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
        self.shelf.setGridSize(QSize(200, 230))
        self.shelf.setUniformItemSizes(True)
        self.shelf.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.shelf.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.shelf.setItemDelegate(_PosterDelegate(self._covers, self.shelf))
        self.shelf.setAccessibleName("视频墙")
        self.shelf.itemClicked.connect(self.open_item)
        self.shelf.itemActivated.connect(self.open_item)
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
        self.status = D.hint_label("本地播放 · 源文件不变 · 右键可定位文件或移除记录", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh_shelf()
        if self.library.warning:
            self.status.setText(self.library.warning)
        ctx.manager.task_finished.connect(self._queue_completed_video)

    # ------------------------------------------------------------------ 落盘
    def _save(self):
        try:
            self.library.save()
            return True
        except OSError as error:
            self.status.setText(f"视频记录保存失败：{error}")
            return False

    # ------------------------------------------------------------------ 自动入库
    @Slot(object)
    def _queue_completed_video(self, info):
        """下载完成的视频任务自动进视频库（jm/eh 图集交给漫画库）。"""
        if self._closed or info.status != "已完成":
            return
        if (info.url or "").startswith(("jmcomic://", "ehentai://")):
            return
        path = info.save_path
        if not path or not is_video_file_entry(path, info.name):
            return
        key = folder_key(path)
        if key not in self._auto_add_keys:
            self._auto_add_keys.add(key)
            # 封面此刻可能还没下完（封面是独立任务），入库时再逐级兜底；
            # cover_url 用于本地没有封面文件时直接下载解析时看到的封面。
            self._auto_add_queue.append((path, info.name or "", info.cover_url or ""))
        self._drain_auto_add()

    def _drain_auto_add(self):
        while not self._closed and not self._busy and self._auto_add_queue:
            path, title, cover_url = self._auto_add_queue.popleft()
            self._auto_add_keys.discard(folder_key(path))
            item = self.library.add(path, title)
            if item.cover:
                continue  # 重入库的老记录已有封面
            item.cover = find_downloaded_cover(path)
            self._save()
            if item.cover:
                self.refresh_shelf()
                continue
            # 网络下载封面 / 截帧都在工作线程做，完成后回 UI 线程补写并刷新
            self._fetch_cover_async(item, cover_url)
            log.info("视频自动入库：%s", item.title)

    def _fetch_cover_async(self, item, cover_url):
        """工作线程里按「下载解析封面 → 截第一帧」补封面，完成后落盘刷新。"""
        if self._closed:
            return
        generation = self._cover_generation
        ffmpeg = self._ffmpeg
        covers_dir = self._covers_dir

        def work():
            return (download_cover_image(cover_url, covers_dir, item.path)
                    or extract_first_frame(item.path, ffmpeg, covers_dir))

        def done(cover, error):
            if self._closed or generation != self._cover_generation:
                return
            if cover and not item.cover:
                item.cover = cover
                if self._save():
                    self.refresh_shelf()
                    self._load_cover(item)

        self.jobs.start(work, done)

    # ------------------------------------------------------------------ 导入
    def _choose_video(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择视频文件", self.ctx.config.get("download_path"),
            "视频文件 (*.mp4 *.mkv *.webm *.mov *.m4v *.avi *.flv *.ts *.mpg *.mpeg *.wmv *.3gp);;所有文件 (*.*)")
        if paths:
            self.import_paths(paths)

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(
            self, "选择视频文件夹（递归扫描全部子目录）", self.ctx.config.get("download_path"))
        if path:
            self.import_paths([path])

    def import_paths(self, paths, read=False):
        if self._busy or self._closed:
            return
        self._set_busy(True)
        self.status.setText("正在扫描视频…")

        def scan():
            found = []
            for path in paths:
                if os.path.isdir(path):
                    for video in scan_videos(path):
                        found.append((video, ""))
                elif is_video(path):
                    found.append((path, ""))
            # 封面：解析下载的用同名 _cover 图；本地导入的截第一帧（ffmpeg
            # 子进程在工作线程跑，不卡 UI）。失败静默回退占位图。
            return [(video, title,
                     find_downloaded_cover(video)
                     or extract_first_frame(video, self._ffmpeg, self._covers_dir))
                    for video, title in found]

        def done(found, error):
            self._set_busy(False)
            if error:
                self.status.setText(f"扫描失败：{error}")
                return
            if not found:
                self.status.setText("没有找到视频文件。支持 MP4 / MKV / WebM / MOV / AVI / FLV / TS / WMV 等常见格式。")
                return
            for path, title, cover in found:
                item = self.library.add(path, title)
                if not item.cover and cover:
                    item.cover = cover
            saved = self._save()
            self.refresh_shelf()
            if saved:
                self.status.setText(f"已导入 / 更新 {len(found)} 个视频 · 播放进度自动保存")
            # 单选时直接播放（拖入单个文件 / 添加一个视频）。

        self.jobs.start(scan, done)

    def open_folder(self, folder):
        """Entry point for the completed-download action."""
        self.import_paths([folder])

    def _set_busy(self, busy):
        self._busy = busy
        for button in (self.addBtn, self.folderBtn, self.refreshBtn, self.moreBtn):
            button.setEnabled(not busy)
        if not busy and not self._closed and self._auto_add_queue:
            self._drain_auto_add()

    # ------------------------------------------------------------------ 展示
    def refresh_shelf(self, *_):
        if self._closed:
            return
        query = self.search.text().strip().casefold()
        items = [v for v in self.library.videos if query in v.title.casefold()]
        mode = self.sort.currentIndex()
        if mode == 1:
            items.sort(key=lambda v: natural_key(v.title))
        elif mode == 2:
            items.sort(key=lambda v: v.percent if v.resume_ms else 0, reverse=True)
        else:
            items.sort(key=lambda v: v.last_played, reverse=True)
        self.shelf.clear()
        for item in items:
            widget_item = QListWidgetItem(item.title)
            widget_item.setData(Qt.ItemDataRole.UserRole, item)
            widget_item.setToolTip(item.path)
            widget_item.setSizeHint(QSize(200, 230))
            self.shelf.addItem(widget_item)
            self._load_cover(item)
        self.shelf.setVisible(bool(items))
        self.empty.setVisible(not items)
        self.empty.setText("没有匹配的视频。试试其他关键词或重新导入。" if self.library.videos
                           else "从一个文件开始播放\n\n点击「添加视频」选择文件，或把视频 / 文件夹拖到这里。\n"
                                "下载完成的视频会自动出现在这里；播放进度自动保存。")

    def _load_cover(self, video):
        """异步解码封面到 self._covers（工作线程读文件，UI 线程只收 QPixmap）。"""
        if not video.cover or video.cover in self._covers or video.cover in self._cover_pending:
            return
        generation = self._cover_generation
        self._cover_pending.add(video.cover)

        def load():
            from PySide6.QtGui import QImage, QImageReader
            if not os.path.isfile(video.cover):
                return None
            reader = QImageReader(video.cover)
            reader.setAutoTransform(True)
            # 海报区最大约 184×128 的两倍，足够清晰又不吃内存
            size = reader.size()
            if size.isValid() and size.width() > 500:
                reader.setScaledSize(size.scaled(500, 400, Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
            return None if image.isNull() else image

        def done(image, error):
            self._cover_pending.discard(video.cover)
            if generation != self._cover_generation:
                return
            if error or image is None:
                # 负缓存：坏封面不再反复读盘
                self._covers[video.cover] = None
                if len(self._covers) >= 240:
                    self._covers.pop(next(iter(self._covers)))
                return
            pixmap = QPixmap.fromImage(image)
            if len(self._covers) >= 240:
                self._covers.pop(next(iter(self._covers)))
            self._covers[video.cover] = pixmap
            self.shelf.viewport().update()

        self.jobs.start(load, done)

    # ------------------------------------------------------------------ 播放
    def open_item(self, item):
        if self._busy or self._closed:
            return
        video = item.data(Qt.ItemDataRole.UserRole)
        if not os.path.isfile(video.path):
            self.status.setText(f"文件不存在：{video.path}（记录已保留，可右键移除）")
            return
        self._show_player(video)

    def _show_player(self, video):
        if self._player is not None:
            self._player.close()
        player = VideoPlayer(self.library, video, self.window())
        self._player = player
        player.changed.connect(self._player_changed)
        player.destroyed.connect(lambda *_: self._player_destroyed(player))
        player.show()
        player.raise_()
        player.activateWindow()
        self.status.setText(f"正在播放：{video.title}")

    def _player_destroyed(self, player):
        if self._player is player:
            self._player = None

    def _player_changed(self):
        if self._player is not None and self._player._closed:
            self.refresh_shelf()

    # ------------------------------------------------------------------ 维护
    def refresh_files(self):
        if self._busy:
            return
        self._set_busy(True)
        items = list(self.library.videos)
        self.status.setText("正在检查文件与刷新记录…")

        def scan():
            results = []
            for item in items:
                ok = os.path.isfile(item.path) and is_video(item.path)
                cover = ""
                if ok and not item.cover:
                    # 老记录补封面：先找解析附带的 _cover 图，没有再截第一帧
                    cover = (find_downloaded_cover(item.path)
                             or extract_first_frame(item.path, self._ffmpeg, self._covers_dir))
                results.append((item, ok, cover))
            return results

        def done(results, error):
            self._set_busy(False)
            if error:
                self.status.setText(f"刷新失败：{error}")
                return
            missing = 0
            filled = 0
            for item, ok, cover in results:
                if not ok:
                    missing += 1
                elif cover and not item.cover:
                    item.cover = cover
                    filled += 1
            saved = self._save()
            self._cover_generation += 1
            self.refresh_shelf()
            if saved:
                text = f"视频库已刷新 · {missing} 个文件暂时无法访问，记录已保留" if missing else "视频库已刷新"
                if filled:
                    text += f" · 补齐 {filled} 个封面"
                self.status.setText(text)
            if saved:
                self.status.setText(f"视频库已刷新 · {missing} 个文件暂时无法访问，记录已保留" if missing
                                    else "视频库已刷新")

        self.jobs.start(scan, done)

    def _context_menu(self, position):
        if self._busy:
            return
        item = self.shelf.itemAt(position)
        if item is None:
            return
        video = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction("播放 / 继续播放", lambda: self.open_item(item))
        menu.addAction("从头播放", lambda: (video.__setattr__("resume_ms", 0), self._save(), self.open_item(item)))
        menu.addAction("打开文件夹", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(os.path.dirname(os.path.abspath(video.path)))))
        menu.addSeparator()
        menu.addAction("删除…", lambda: self._delete_source(video))
        menu.exec(self.shelf.mapToGlobal(position))

    def _remove(self, video):
        if self._player is not None and self._player.item is video:
            self._player.close()
        self.library.remove(video)
        self._save()
        self.refresh_shelf()

    def _delete_source(self, video):
        """右键「删除…」：让用户选择仅移除记录，还是连源文件一起删除。

        删除源文件时连带清理关联封面（解析下载的 _cover 图 + 截帧缓存）。
        文件被播放器或其它程序占用时逐个收集失败原因；主文件删不掉则保留
        记录供用户重试。
        """
        from ..core.video_library import folder_key as _fk
        path = video.path
        exists = bool(path) and os.path.isfile(path)
        if not exists:
            # 文件已经没了：等价于"仅移除记录"，直接走记录清理
            self._remove(video)
            self.status.setText(f"文件已不在原位置，已仅移除记录：{os.path.basename(path)}")
            return
        from ..core.models import fmt_size
        # 关联封面：解析下载附带的 <同名>_cover 图 + 截帧缓存（排除主文件自身，去重）
        seen, extras = set(), []
        for candidate in (find_downloaded_cover(path), video.cover):
            if (candidate and os.path.isfile(candidate)
                    and _fk(candidate) != _fk(path) and _fk(candidate) not in seen):
                seen.add(_fk(candidate))
                extras.append(candidate)
        size = fmt_size(os.path.getsize(path))
        extra_txt = f"\n同时删除 {len(extras)} 个关联封面" if extras else ""
        # 三选一：仅移除记录（源文件保留）/ 连源文件一起删 / 取消
        box = QMessageBox(self.window())
        box.setWindowTitle("删除视频")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"{os.path.basename(path)}"
                    f"\n大小 {size}{extra_txt}\n\n选择要执行的删除方式：")
        remove_only = box.addButton("仅移除记录", QMessageBox.ButtonRole.ActionRole)
        delete_all = box.addButton("删除记录和源文件", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(remove_only)
        box.exec()
        clicked = box.clickedButton()
        if clicked is delete_all:
            self._delete_files_and_record(video, path, extras)
        elif clicked is remove_only:
            self._remove(video)
            self.status.setText(f"已从视频库移除记录（源文件保留）：{os.path.basename(path)}")
        # 取消：什么都不做

    def _delete_files_and_record(self, video, path, extras):
        """执行源文件删除（不可撤销）。主文件删除失败时保留记录供重试。"""
        from ..core.video_library import folder_key as _fk
        # 播放器正占着这个文件时必须先关，否则 Windows 上删除会失败
        if self._player is not None and self._player.item is video:
            self._player.close()
        failed = []
        for p in [path] + extras:
            try:
                os.remove(p)
            except OSError as error:
                failed.append(f"{os.path.basename(p)}：{error}")
        if any(_fk(p) == _fk(path) for p in failed):
            # 主文件没删掉就不动记录，用户可以重试
            self.status.setText("删除失败（文件可能正被占用）：" + "；".join(failed)[:150])
            return
        # 顺带清掉封面缓存里的旧 pixmap（主文件 + 关联封面）
        self._covers.pop(video.cover, None)
        for extra in extras:
            self._covers.pop(extra, None)
        self.library.remove(video)
        self._save()
        self.refresh_shelf()
        if failed:
            self.status.setText("视频已删除，但部分封面清理失败：" + "；".join(failed)[:120])
        else:
            self.status.setText(f"已删除源文件：{os.path.basename(path)}")

    def _more_menu(self):
        menu = QMenu(self)
        menu.addAction("导入当前下载目录", lambda: self.import_paths([self.ctx.config.get("download_path")]))
        menu.addAction("重新加载已保存记录（放弃未保存修改）", self.reload_library)
        menu.addAction("使用说明", lambda: QMessageBox.information(
            self, "本地视频播放", "添加视频：选择一个或多个视频文件。\n"
            "导入文件夹：递归扫描子目录里的全部视频。\n"
            "点击封面继续播放，右键可打开文件夹或删除（可选仅移除记录，\n"
            "或连源文件一起删除，删除不可撤销）。\n"
            "下载任务完成的视频会自动入库（封面图不会）。\n"
            "播放进度保存在程序目录的 video_library.json，原视频不被修改。\n"
            "个别老格式（WMV / RMVB 等）能否播放取决于系统解码器，播不了会有明确提示。"))
        menu.exec(self.moreBtn.mapToGlobal(self.moreBtn.rect().bottomLeft()))

    def reload_library(self):
        if self._busy or self._closed:
            return
        if self._player is not None:
            self._player.close()
            self._player = None
        try:
            self.library.load()
        except OSError as error:
            self.status.setText(f"重新加载失败：{error}")
            return
        self.refresh_shelf()
        self.status.setText(self.library.warning or "已重新加载磁盘上的视频记录")

    # ------------------------------------------------------------------ 拖放
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self._busy:
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self._busy:
            return
        paths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and folder_key(path) not in {folder_key(p) for p in paths}:
                paths.append(path)
        if not paths:
            return
        event.acceptProposedAction()
        self.import_paths(paths)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._auto_add_queue.clear()
        self._auto_add_keys.clear()
        if self._player is not None:
            self._player.close()
        self.jobs.shutdown()
