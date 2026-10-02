# -*- coding: utf-8 -*-
"""批量下载：抓取用户主页全部作品，勾选后批量下载"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout,
                               QHeaderView, QLabel, QTableWidgetItem, QWidget)
from qfluentwidgets import (FluentIcon as FIF, IndeterminateProgressBar,
                            InfoBar, InfoBarPosition, LineEdit,
                            PrimaryPushButton, PushButton, ScrollArea,
                            TableWidget)

from ..core.domain import URL_PATTERN, needs_cookie_for_source
from ..core.models import VideoInfo, quality_html
from ..core.site_ctx import SiteContext
from ..core.workers import LoginWorker, UserResolveWorker, UserVideosWorker
from . import design as D

COL_CHECK, COL_TITLE, COL_TYPE, COL_DURATION, COL_QUALITY, COL_DIGG, COL_COMMENT = range(7)


class BatchInterface(ScrollArea):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("batchInterface")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.view = QWidget(self)
        self.view.setObjectName("batchView")
        self.setWidget(self.view)
        self.vBox = D.page_layout(self.view)

        self.sec_uid = ""
        self.nickname = ""
        self.max_cursor = 0
        self.has_more = False
        self._infos = []        # 与表格行一一对应
        self._workers = []

        self._build_header()
        self._build_input_card()
        self._build_table_card()
        self._init_style()
        self._init_clipboard_watch()

    def _init_style(self):
        self.view.setStyleSheet("QWidget#batchView { background: transparent; }")

    def _build_header(self):
        self.vBox.addWidget(D.PageHeader(
            "批量下载",
            "粘贴作者主页链接　·　抓取全部公开作品后勾选下载　·　未登录只能拿到第一页",
            self.view))

    def _build_input_card(self):
        card, lay = D.card(self.view)
        lay.addWidget(D.section_label("用户主页", card))

        row = QHBoxLayout()
        row.setSpacing(D.GAP_SM)
        self.linkEdit = LineEdit(card)
        self.linkEdit.setPlaceholderText(
            "例如：https://www.douyin.com/user/MS4wLjAB… 或用户分享短链")
        self.linkEdit.setClearButtonEnabled(True)
        self.linkEdit.returnPressed.connect(self._on_fetch)
        row.addWidget(self.linkEdit, 1)
        pasteBtn = PushButton(FIF.PASTE, "粘贴", card)
        pasteBtn.clicked.connect(self._on_paste)
        self.loginBtn = PushButton(FIF.PEOPLE, "登录抖音", card)
        self.loginBtn.setToolTip("扫码登录后可抓取全部作品（未登录只能抓取第一页）")
        self.loginBtn.clicked.connect(self._on_login)
        self.fetchBtn = PrimaryPushButton(FIF.PEOPLE, "获取作品列表", card)
        self.fetchBtn.clicked.connect(self._on_fetch)
        row.addWidget(pasteBtn)
        row.addWidget(self.loginBtn)
        row.addWidget(self.fetchBtn)
        lay.addLayout(row)

        self.userLabel = D.hint_label("", card)
        lay.addWidget(self.userLabel)

        self.progress = IndeterminateProgressBar(card)
        self.progress.setFixedHeight(4)
        self.progress.hide()
        lay.addWidget(self.progress)
        self.vBox.addWidget(card)

    def _build_table_card(self):
        card, lay = D.card(self.view)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(D.GAP_SM)
        self.countLabel = D.section_label("作品列表", card)
        toolbar.addWidget(self.countLabel)
        toolbar.addStretch(1)
        self.selectAllBtn = PushButton(FIF.ACCEPT_MEDIUM, "全选", card)
        self.selectAllBtn.clicked.connect(lambda: self._set_all_checked(True))
        self.selectNoneBtn = PushButton(FIF.CLOSE, "全不选", card)
        self.selectNoneBtn.clicked.connect(lambda: self._set_all_checked(False))
        self.loadMoreBtn = PushButton(FIF.DOWNLOAD, "加载更多", card)
        self.loadMoreBtn.clicked.connect(self._on_load_more)
        self.loadMoreBtn.setEnabled(False)
        self.downloadBtn = PrimaryPushButton(FIF.DOWNLOAD, "下载选中", card)
        self.downloadBtn.clicked.connect(self._on_download_selected)
        toolbar.addWidget(self.selectAllBtn)
        toolbar.addWidget(self.selectNoneBtn)
        toolbar.addWidget(self.loadMoreBtn)
        toolbar.addWidget(self.downloadBtn)
        lay.addLayout(toolbar)

        self.table = TableWidget(card)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["选择", "标题", "类型", "时长", "画质", "点赞", "评论"])
        self.table.verticalHeader().hide()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_CHECK, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_CHECK, 50)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        for c in (COL_TYPE, COL_DURATION, COL_QUALITY, COL_DIGG, COL_COMMENT):
            header.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(320)
        lay.addWidget(self.table)
        self.vBox.addWidget(card)

    # ------------------------------------------------------------------ #
    def _on_paste(self):
        text = (QGuiApplication.clipboard().text() or "").strip()
        if text:
            self.linkEdit.setText(text)
        else:
            self._toast("剪贴板为空", "请先复制用户主页链接或分享文案", "warning")

    def _init_clipboard_watch(self):
        """复制到剪贴板后自动填入链接（只在本页可见且输入框为空时生效）"""
        self._clipboard_last = ""
        self._clipboard = QGuiApplication.clipboard()
        self._clipboard.dataChanged.connect(self._on_clipboard_changed)

    def _on_clipboard_changed(self):
        if not self.isVisible() or self.linkEdit.text().strip():
            return
        text = (QGuiApplication.clipboard().text() or "").strip()
        if not text or text == self._clipboard_last:
            return
        if not URL_PATTERN.search(text):
            return
        self._clipboard_last = text
        self.linkEdit.setText(text)
        self.userLabel.setText("已从剪贴板填入链接，点「获取作品列表」开始")

    # ------------------------------------------------------------------ #
    def _on_fetch(self):
        text = self.linkEdit.text().strip()
        if not text:
            self._toast("请输入用户主页链接", "", "warning")
            return
        self._busy(True)
        self.userLabel.setText("正在解析用户主页…")
        w = UserResolveWorker(self.ctx.config.get("cookie", ""), text, self)
        w.succeeded.connect(self._on_user_resolved)
        w.failed.connect(self._on_fail)
        w.finished.connect(lambda: self._cleanup_worker(w))
        self._workers.append(w)
        w.start()

    def _on_user_resolved(self, sec_uid: str):
        self.sec_uid = sec_uid
        self._infos.clear()
        self.table.setRowCount(0)
        self.max_cursor = 0
        self._load_page()

    def _on_load_more(self):
        if self.sec_uid and self.has_more:
            self._load_page()

    def _load_page(self):
        self._busy(True)
        self.userLabel.setText("正在通过浏览器抓取作品列表（作品较多时需要几十秒）…")
        w = UserVideosWorker(self.ctx.config.get("cookie", ""),
                             self.sec_uid, self.max_cursor,
                             profile_dir=self.ctx.config.profile_dir, parent=self)
        w.succeeded.connect(self._on_page_loaded)
        w.failed.connect(self._on_fail)
        w.progress.connect(self._on_progress)
        w.limited.connect(self._on_limited)
        w.finished.connect(lambda: self._cleanup_worker(w))
        self._workers.append(w)
        w.start()

    def _on_login(self):
        self.loginBtn.setEnabled(False)
        self.userLabel.setText("已打开登录窗口，请在 Edge 中扫码登录抖音（最多等待 4 分钟）…")
        w = LoginWorker(self.ctx.config.profile_dir, self)
        w.done.connect(self._on_login_done)
        w.finished.connect(lambda: self._cleanup_worker(w))
        self._workers.append(w)
        w.start()

    def _on_login_done(self, ok: bool, msg: str):
        self.loginBtn.setEnabled(True)
        if ok:
            self._toast("登录成功", "登录状态已保存，重新获取作品列表即可抓取全部作品", "success")
            if self.sec_uid:
                self._infos.clear()
                self.table.setRowCount(0)
                self._load_page()
        else:
            self._toast("登录未完成", msg, "warning")
        self.userLabel.setText("")

    def _on_progress(self, n: int, total: int):
        if total:
            self.userLabel.setText(f"正在抓取作品列表… 已获取 {n}/{total} 个")
        else:
            self.userLabel.setText(f"正在抓取作品列表… 已获取 {n} 个")

    def _on_limited(self):
        self._toast("仅获取到第一页",
                    "未登录时抖音只返回部分作品。点击「登录抖音」扫码后可抓取全部",
                    "warning")

    def _on_page_loaded(self, infos: list, next_cursor: int, has_more: bool, nickname: str):
        self._busy(False)
        self.max_cursor = next_cursor
        self.has_more = has_more
        self.loadMoreBtn.setEnabled(has_more)
        self.loadMoreBtn.setText("加载更多" if has_more else "已全部加载")
        if nickname:
            self.nickname = nickname
        for info in infos:
            self._append_row(info)
        self.countLabel.setText(f"作品列表（共 {len(self._infos)} 个）")
        self.userLabel.setText(f"作者：{self.nickname or self.sec_uid}，已全部加载")
        if infos:
            self._toast("加载完成", f"共获取 {len(infos)} 个作品", "success")

    def _append_row(self, info: VideoInfo):
        row = self.table.rowCount()
        self.table.insertRow(row)
        cb = QCheckBox(self.table)
        cb.setChecked(True)
        wrapper = QWidget(self.table)
        h = QHBoxLayout(wrapper)
        h.setContentsMargins(0, 0, 0, 0)
        h.setAlignment(Qt.AlignCenter)
        h.addWidget(cb)
        self.table.setCellWidget(row, COL_CHECK, wrapper)

        title = QTableWidgetItem(info.title or info.aweme_id)
        title.setToolTip(info.title)
        self.table.setItem(row, COL_TITLE, title)
        self.table.setItem(row, COL_TYPE, QTableWidgetItem(info.type_text))
        self.table.setItem(row, COL_DURATION, QTableWidgetItem(info.duration_text))
        # 画质列：帧率单独上色，批量场景下最容易忽略的就是 60/30fps
        qlabel = QLabel(self.table)
        qlabel.setContentsMargins(6, 0, 6, 0)
        qlabel.setText(quality_html(info.quality))
        qlabel.setTextFormat(Qt.RichText)
        qlabel.setToolTip(f"默认下载档位：{info.quality or '--'}")
        self.table.setCellWidget(row, COL_QUALITY, qlabel)
        for col, val in ((COL_DIGG, info.digg_count), (COL_COMMENT, info.comment_count)):
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, val)
            self.table.setItem(row, col, item)
        self._infos.append((info, cb))

    def _set_all_checked(self, checked: bool):
        for _, cb in self._infos:
            cb.setChecked(checked)

    def _on_download_selected(self):
        selected = [info for info, cb in self._infos if cb.isChecked()]
        if not selected:
            self._toast("未选择作品", "请先勾选要下载的作品", "warning")
            return
        cfg = self.ctx.config
        base = cfg.get("download_path")
        if cfg.get("create_author_folder") and self.nickname:
            import re
            author = re.sub(r'[\\/:*?"<>|\r\n]+', "_", self.nickname)[:30]
            base = os.path.join(base, author)
        n = 0
        site_ctx = SiteContext.from_config(cfg)
        for info in selected:
            name = cfg.build_filename(info)
            # 只有 IG / B站 的 CDN 需要登录态；DASH 音视频分离时须带 audio_url，
            # 否则 ffmpeg 拿不到音频轨（与首页下载保持一致）
            task_cookies = (site_ctx.cookie_for(info.source)
                            if needs_cookie_for_source(info.source) else "")
            if info.is_image:
                folder = os.path.join(base, name)
                for i, url in enumerate(info.image_urls, 1):
                    ext = ".webp" if "webp" in url else ".jpeg"
                    self.ctx.manager.add(url, os.path.join(folder, f"{i:02d}{ext}"),
                                         f"{info.safe_title(30)}_{i:02d}{ext}")
                    n += 1
            elif info.play_url:
                self.ctx.manager.add(
                    info.play_url,
                    os.path.join(base, name + ".mp4"),
                    name + ".mp4",
                    fallbacks=info.play_url_candidates[1:],
                    cookies=task_cookies,
                    audio_url=info.audio_map.get(info.play_url, ""),
                    quality=info.quality,
                    audio_map=info.audio_map,
                    url_backups=info.url_backups,
                    audio_backups=info.audio_backups)
                n += 1
        self._toast("已加入队列", f"{len(selected)} 个作品、共 {n} 个文件开始下载", "success")

    # ------------------------------------------------------------------ #
    def _busy(self, busy: bool):
        self.fetchBtn.setEnabled(not busy)
        self.progress.setVisible(busy)

    def _on_fail(self, msg: str):
        self._busy(False)
        self.userLabel.setText("")
        self._toast("操作失败", msg, "error")

    def _toast(self, title: str, content: str, kind: str):
        fn = {"success": InfoBar.success, "error": InfoBar.error,
              "warning": InfoBar.warning}.get(kind, InfoBar.info)
        fn(title, content, orient=Qt.Horizontal, isClosable=True,
           position=InfoBarPosition.TOP, duration=3500, parent=self)

    def _cleanup_worker(self, w):
        if w in self._workers:
            self._workers.remove(w)
        w.deleteLater()
