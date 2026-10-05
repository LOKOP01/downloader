# -*- coding: utf-8 -*-
"""首页：单作品解析与下载"""
import os

from PySide6.QtCore import QMimeData, Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
                               QWidget)
from qfluentwidgets import (BodyLabel, ComboBox, FluentIcon as FIF,
                            IndeterminateProgressBar, InfoBar, InfoBarPosition,
                            LineEdit, PrimaryPushButton, PushButton, ScrollArea,
                            StrongBodyLabel, TransparentToolButton)

from ..core.applog import get_logger
from ..core.domain import needs_cookie_for_source
from ..core.models import (VideoInfo, dropdown_quality_indices, fmt_count,
                           fmt_size)
from ..core.site_ctx import SiteContext
from ..core.workers import CoverWorker, ParseWorker
from . import design as D
from .preview_dialog import PreviewDialog

log = get_logger("home")


class HomeInterface(ScrollArea):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx          # AppContext: config / download manager
        self.setObjectName("homeInterface")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.view = QWidget(self)
        self.setWidget(self.view)
        self.view.setObjectName("homeView")
        self.vBox = D.page_layout(self.view)

        self._current: VideoInfo = None
        self._workers = []
        self._copy_on_done = {}   # task_id -> 提示文案（下载完成后自动复制文件）
        # 下拉可见项 → quality_options 下标（下拉已按「分辨率前三高」筛过）
        self._quality_indices = []
        self._parse_generation = 0
        self._parse_input = ""
        self._empty_result_retries = 0
        self._parse_running = False

        self._build_header()
        self._build_parse_card()
        self._build_result_card()
        self.vBox.addStretch(1)
        self._init_style()

        self.ctx.manager.task_finished.connect(self._on_task_finished)

    # ------------------------------------------------------------------ #
    def _init_style(self):
        self.view.setStyleSheet("QWidget#homeView { background: transparent; }")

    def _build_header(self):
        """页头：主标题 + 一行平台元信息。

        改前是一句 28px 标题接一句 14px 长副标题（把 8 个平台名和域名塞一起），
        主次不分明。现在标题收到 24px，平台清单降到 12px 三级色 —— 从"副标题"
        降格为"能力提示"，不跟主标题抢注意力。
        """
        header = D.PageHeader(
            "视频解析",
            "抖音 / X / Instagram / B站 / 小红书 / Iwara / Pornhub / hanime1 / "
            "禁漫　·　视频与图集　·　画质可选",
            self.view)
        self.vBox.addWidget(header)

    def _build_parse_card(self):
        card, lay = D.card(self.view)

        lay.addWidget(D.section_label("分享链接", card))

        row = QHBoxLayout()
        row.setSpacing(8)
        self.linkEdit = LineEdit(card)
        # 平台清单已经在页头说过了，这里不重复 —— 只讲"粘什么、然后怎么走"
        self.linkEdit.setPlaceholderText(
            "粘贴分享链接或分享文案，回车即可解析（也支持禁漫车号 JM123）")
        self.linkEdit.setClearButtonEnabled(True)
        self.linkEdit.returnPressed.connect(self._on_parse)
        row.addWidget(self.linkEdit, 1)

        self.pasteBtn = PushButton(FIF.PASTE, "粘贴", card)
        self.pasteBtn.clicked.connect(self._on_paste)
        self.parseBtn = PrimaryPushButton(FIF.SEARCH, "开始解析", card)
        self.parseBtn.clicked.connect(self._on_parse)
        row.addWidget(self.pasteBtn)
        row.addWidget(self.parseBtn)
        lay.addLayout(row)

        self.progress = IndeterminateProgressBar(card)
        self.progress.setFixedHeight(4)
        self.progress.hide()
        lay.addWidget(self.progress)

        self.vBox.addWidget(card)

    def _build_result_card(self):
        """解析结果卡：把原来的「四行同权重文字」拆成明确的三层 + 一条统计。

        改前：标题 / 作者 / 元信息 / 点赞评论分享 四个标签字号 12~14、颜色全是
        同一档灰蓝，扫不出重点；四个操作按钮里三个等权。
        改后：标题 18 DemiBold 做 hero，作者带强调色，元信息降为 12 三级色，
        统计做成「数字加粗 + 标签弱化」；副操作收进右侧图标组。
        """
        self.resultCard, lay = D.card(self.view)
        lay.addWidget(D.section_label("解析结果", self.resultCard))

        body = QHBoxLayout()
        body.setSpacing(D.GAP_LG)
        # 封面槽按 3:4 取，尺寸略收：原来 108×144 比右侧文字块高出一截，
        # 卡片里会留一块没有内容的空当
        self.coverLabel = D.Placeholder("封面", size=(100, 133), parent=self.resultCard)
        body.addWidget(self.coverLabel, 0, Qt.AlignTop)

        infoCol = QVBoxLayout()
        infoCol.setSpacing(D.GAP_XS)
        # L1 作品标题 —— 整页视觉权重最高的一行
        self.titleLabel = StrongBodyLabel("-", self.resultCard)
        self.titleLabel.setFont(D.font(D.FONT_TITLE, D.W_SEMI))
        self.titleLabel.setTextColor(*D.TEXT_PRIMARY)
        self.titleLabel.setWordWrap(True)
        self.titleLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # L2 作者 —— 小字 + 强调色：次要但要看得出"是谁的作品"
        self.authorLabel = BodyLabel("-", self.resultCard)
        self.authorLabel.setFont(D.font(D.FONT_CAPTION))
        self.authorLabel.setTextColor(*D.TEXT_ACCENT)
        # L3 元信息（类型 · 时长 · 画质）
        self.metaLabel = D.hint_label("-", self.resultCard)
        # L4 统计：数字是主体，标签退成背景
        self.statsLabel = D.StatStrip(self.resultCard)

        infoCol.addWidget(self.titleLabel)
        infoCol.addSpacing(D.GAP_XS)
        infoCol.addWidget(self.authorLabel)
        infoCol.addWidget(self.metaLabel)
        infoCol.addSpacing(D.GAP_SM)
        infoCol.addWidget(self.statsLabel)
        infoCol.addStretch(1)
        body.addLayout(infoCol, 1)
        lay.addLayout(body)

        lay.addWidget(D.Divider(self.resultCard))

        # 清晰度选择
        qualityRow = QHBoxLayout()
        qualityRow.setSpacing(D.GAP_SM)
        self.qualityLabel = BodyLabel("清晰度", self.resultCard)
        self.qualityLabel.setFont(D.font(D.FONT_CAPTION))
        self.qualityLabel.setTextColor(*D.TEXT_SECONDARY)
        self.qualityCombo = ComboBox(self.resultCard)
        self.qualityCombo.setMinimumWidth(240)
        self.qualityCombo.setToolTip("选择要下载的清晰度，默认最高")
        qualityRow.addWidget(self.qualityLabel)
        qualityRow.addWidget(self.qualityCombo)
        # 帧率小标签：跟着下拉实时变（60fps 绿色，一眼能看出选的是哪条码流）
        self.fpsBadge = QLabel("", self.resultCard)
        self.fpsBadge.setAlignment(Qt.AlignCenter)
        self.fpsBadge.setVisible(False)
        self.qualityCombo.currentIndexChanged.connect(self._update_fps_badge)
        qualityRow.addWidget(self.fpsBadge)
        qualityRow.addStretch(1)
        lay.addLayout(qualityRow)

        # 主操作：拿到文件（左）／ 复制到剪贴板（右）——按"动作意图"分两组，
        # 同类里只留一个主按钮，其余降为图标 + tooltip，减少一排等权文字按钮
        btns = QHBoxLayout()
        btns.setSpacing(D.GAP_SM)
        self.downloadBtn = PrimaryPushButton(FIF.DOWNLOAD, "下载视频", self.resultCard)
        self.downloadBtn.clicked.connect(self._on_download)
        self.previewBtn = PushButton(FIF.VIEW, "下载前预览", self.resultCard)
        self.previewBtn.clicked.connect(self._on_preview)
        self.coverBtn = PushButton(FIF.PHOTO, "下载封面", self.resultCard)
        self.coverBtn.clicked.connect(self._on_download_cover)
        self.copyLinkBtn = TransparentToolButton(FIF.LINK, self.resultCard)
        self.copyLinkBtn.setToolTip("复制无水印链接")
        self.copyLinkBtn.clicked.connect(self._on_copy_link)
        self.copyTitleBtn = TransparentToolButton(FIF.COPY, self.resultCard)
        self.copyTitleBtn.setToolTip("复制标题")
        self.copyTitleBtn.clicked.connect(
            lambda: self._copy(self.titleLabel.text(), "已复制标题"))
        btns.addWidget(self.downloadBtn)
        btns.addWidget(self.previewBtn)
        btns.addWidget(self.coverBtn)
        btns.addStretch(1)
        btns.addWidget(self.copyLinkBtn)
        btns.addWidget(self.copyTitleBtn)
        lay.addLayout(btns)

        self.resultCard.hide()
        self.vBox.addWidget(self.resultCard)

    # ------------------------------------------------------------------ #
    def _on_paste(self):
        text = QGuiApplication.clipboard().text()
        if text:
            self.linkEdit.setText(text)
        else:
            self._toast("剪贴板为空", "请先复制抖音分享文案", "warning")

    def _on_parse(self):
        if self._parse_running:
            return
        text = self.linkEdit.text().strip()
        if not text:
            self._toast("请输入链接", "粘贴分享文案后再开始解析", "warning")
            return
        self._parse_generation += 1
        self._empty_result_retries = 0
        self._start_parse(text)

    def _start_parse(self, text: str):
        self._parse_input = text
        self._parse_running = True
        self.parseBtn.setEnabled(False)
        self.resultCard.hide()
        self._current = None
        self.progress.show()
        # 站点凭据统一由 SiteContext 从 Config 组装（字段集中在一处维护）
        w = ParseWorker(SiteContext.from_config(self.ctx.config), text, self)
        w.succeeded.connect(self._on_parse_ok)
        w.failed.connect(self._on_parse_fail)
        w.finished.connect(lambda: self._cleanup_worker(w))
        self._workers.append(w)
        w.start()

    def _retry_empty_result(self, generation: int, text: str):
        if generation != self._parse_generation:
            return
        if self.linkEdit.text().strip() != text:
            self._parse_done()
            return
        self._start_parse(text)

    def _on_parse_ok(self, info: VideoInfo):
        self._empty_result_retries = 0
        self._current = info
        self.coverLabel.reset()
        self.titleLabel.setText(info.title or "（无标题）")
        self.authorLabel.setText(f"@{info.author}" if info.author else "未知作者")
        parts = [info.type_text]
        if not info.is_image:
            parts.append(f"时长 {info.duration_text}")
            if info.quality:
                parts.append(f"画质 {info.quality}")
        elif info.source == "jmcomic":
            chapters = (info.extra or {}).get("chapters")
            pages = (info.extra or {}).get("pages")
            if chapters:
                parts.append(f"{chapters} 章")
            if pages:
                parts.append(f"{pages} 页")
        else:
            parts.append(f"{len(info.image_urls)} 张图片")
        if info.music_title:
            parts.append(f"BGM {info.music_title}")
        self.metaLabel.setText(" · ".join(parts))
        # 统计：显示折成「万」，精确值放 tooltip —— 量级要一眼可读，精度不能丢
        self.statsLabel.set_stats([
            ("点赞", fmt_count(info.digg_count)),
            ("评论", fmt_count(info.comment_count)),
            ("分享", fmt_count(info.share_count)),
        ])
        self.statsLabel.setToolTip(
            f"点赞 {info.digg_count}　评论 {info.comment_count}　分享 {info.share_count}")
        if info.source == "jmcomic":
            self.downloadBtn.setText("下载本子")
            self.downloadBtn.setIcon(FIF.PHOTO)
        else:
            self.downloadBtn.setText("下载全部图片" if info.is_image else "下载视频")
            self.downloadBtn.setIcon(FIF.PHOTO if info.is_image else FIF.DOWNLOAD)
        self.copyLinkBtn.setVisible(not info.is_image)

        # 清晰度下拉：有多个可选档位时展示，默认选最高
        self.qualityCombo.clear()
        options = info.quality_options
        # 有 60fps 就不摆 30fps；剩下的分辨率只留前三高（同分辨率多条码流算一档）
        self._quality_indices = dropdown_quality_indices(options)
        if info.is_image or not options:
            self.qualityLabel.setVisible(False)
            self.qualityCombo.setVisible(False)
        else:
            for i in self._quality_indices:
                # 标签带文件大小（拿不到大小则不追加）
                self.qualityCombo.addItem(info.quality_label(i))
            self.qualityCombo.setCurrentIndex(0)
            self.qualityLabel.setVisible(True)
            self.qualityCombo.setVisible(True)
            # 下拉里文字可能变长，按最长项自适应宽度
            self.qualityCombo.setMinimumWidth(
                max(240, self.qualityCombo.fontMetrics().horizontalAdvance(
                    max((self.qualityCombo.itemText(i)
                         for i in range(self.qualityCombo.count())),
                        default="")) + 60))
            if self.qualityCombo.count() == 1:
                self.qualityCombo.setEnabled(False)
                self.qualityCombo.setToolTip("该来源仅提供这一档画质")
            else:
                self.qualityCombo.setEnabled(True)
                self.qualityCombo.setToolTip("选择要下载的清晰度，默认最高")
        self._update_fps_badge()

        # 结果卡淡入：解析是一次"有等待"的操作，硬 show() 会让卡片突然砸出来
        self.resultCard.show()
        D.fade_in(self.resultCard, D.DUR_ENTER)

        if info.cover_url:
            w = CoverWorker(info.cover_url, self)
            w.loaded.connect(self._set_cover)
            w.finished.connect(lambda: self._cleanup_worker(w))
            self._workers.append(w)
            w.start()
        self._parse_done()

    def _on_parse_fail(self, msg: str):
        self._parse_running = False
        transient = ("作品信息获取失败" in msg
                     and "douyin.com/" in self._parse_input
                     and self.linkEdit.text().strip() == self._parse_input)
        if transient and self._empty_result_retries == 0:
            # Douyin sometimes returns a 200 response with an empty note page.
            # A fresh worker/session can succeed; retry once without blocking UI.
            self._empty_result_retries = 1
            generation, text = self._parse_generation, self._parse_input
            self.progress.show()
            QTimer.singleShot(1800, lambda: self._retry_empty_result(generation, text))
            return
        self._parse_done()
        if transient:
            self._toast("抖音暂未返回作品数据",
                        "已自动重试一次；请稍后再试，或在浏览器确认作品仍可访问。",
                        "warning")
        else:
            self._toast("解析失败", msg, "error")

    def _parse_done(self):
        self._parse_running = False
        self.parseBtn.setEnabled(True)
        self.progress.hide()

    def _set_cover(self, data: bytes):
        pix = QPixmap()
        if pix.loadFromData(data):
            self.coverLabel.set_image(pix)

    def _update_fps_badge(self, *_):
        """把当前选中档位的帧率做成彩色小标签（≥50fps 绿、30fps 灰、更低橙）"""
        info = self._current
        fps = 0
        # 用 isHidden() 而不是 isVisible()：后者在窗口还没 show() 时恒为 False，
        # 会把帧率标签误判成"不该显示"
        if (info and not info.is_image and not self.qualityCombo.isHidden()
                and self.qualityCombo.count()):
            fps = info.fps_at(self._selected_quality_index())
        if not fps:
            self.fpsBadge.setVisible(False)
            return
        if fps >= 50:
            bg, fg = D.FPS_HIGH
        elif fps >= 30:
            bg, fg = D.FPS_MID
        else:
            bg, fg = D.FPS_LOW
        self.fpsBadge.setText(f"{fps} FPS")
        self.fpsBadge.setToolTip(
            "当前档位帧率：60fps 更顺滑（码率通常更低），30fps 码率更高")
        self.fpsBadge.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {fg}; border-radius: 9px;"
            " padding: 2px 10px; font-weight: 600; }")
        self.fpsBadge.adjustSize()
        self.fpsBadge.setVisible(True)
        # 换档时淡入一次，让"帧率变了"这件事被看见（原先是硬切）
        D.fade_in(self.fpsBadge, D.DUR_INSTANT)

    def _selected_quality_index(self) -> int:
        """下拉当前项对应的 quality_options 下标

        下拉里只摆了「分辨率前三高」的档位，位置和 quality_options 的下标不是
        一回事，下载 / 复制链接 / 帧率标签都得先换算回来。
        """
        pos = self.qualityCombo.currentIndex()
        if 0 <= pos < len(self._quality_indices):
            return self._quality_indices[pos]
        return 0

    # ------------------------------------------------------------------ #
    def _on_preview(self):
        info = self._current
        if not info:
            return
        options = info.quality_options
        idx = self._selected_quality_index() if options and not self.qualityCombo.isHidden() else 0
        if options and 0 <= idx < len(options):
            label, url = options[idx]
            label = info.quality_label(idx)
        else:
            label, url = info.quality or "视频", info.play_url
        context = SiteContext.from_config(self.ctx.config)
        cookie = (context.cookie_for(info.source)
                  if needs_cookie_for_source(info.source) else "")
        cover = self.coverLabel.pixmap() if info.source == "jmcomic" else None
        dialog = PreviewDialog(info, url=url, label=label, cookies=cookie,
                               cover=cover, parent=self)
        if dialog.exec():
            self._on_download()
    def _on_download(self):
        info = self._current
        if not info:
            return
        log.info("用户点击下载：%s「%s」| 图集=%s",
                 info.source, (info.title or "")[:60], bool(info.is_image))
        cfg = self.ctx.config
        # 单个下载直接存到设置的下载目录，不按作者分文件夹
        base = cfg.get("download_path")
        # 一次下载只生成一次文件名：多次调用 build_filename 会因跨秒得到不同
        # 时间戳，导致视频与其封面失去同名前缀（删除时找不到关联封面）
        fname = cfg.build_filename(info)
        if info.source == "jmcomic":
            folder = os.path.join(base, fname)
            self.ctx.manager.add(
                info.play_url, folder, fname,
                cookies=SiteContext.from_config(cfg).cookie_for("jmcomic"))
            pages = (info.extra or {}).get("pages") or "?"
            self._toast("已加入队列", f"禁漫本子共 {pages} 页，开始下载", "success")
            return
        # 只有 IG / B站 的 CDN 需要登录态 Cookie。图集也要带：IG 多图的图挂在
        # cdninstagram.com 上，漏传会 403（此前只有视频分支带，图集需要登录时必挂）
        task_cookies = (SiteContext.from_config(cfg).cookie_for(info.source)
                        if needs_cookie_for_source(info.source) else "")
        if info.is_image:
            folder = os.path.join(base, fname)
            for i, url in enumerate(info.image_urls, 1):
                ext = ".webp" if "webp" in url else ".jpeg"
                path = os.path.join(folder, f"{i:02d}{ext}")
                self.ctx.manager.add(url, path,
                                     f"{info.safe_title(30)}_{i:02d}{ext}",
                                     cookies=task_cookies)
            self._toast("已加入队列", f"图集共 {len(info.image_urls)} 张图片，开始下载", "success")
        else:
            # 取用户选择的清晰度（默认最高档）
            options = info.quality_options
            idx = (self._selected_quality_index()
                   if self.qualityCombo.isVisible() else 0)
            if options and 0 <= idx < len(options):
                url = options[idx][1]
                others = [u for i, (_, u) in enumerate(options) if i != idx]
            else:
                url = info.play_url
                others = info.play_url_candidates[1:]
            # 记住用户选的这一档标签（含帧率），任务页要展示
            chosen = (options[idx][0] if options and 0 <= idx < len(options)
                      else "最高画质")
            path = os.path.join(base, fname + ".mp4")
            task = self.ctx.manager.add(
                url, path, fname + ".mp4",
                fallbacks=others, cookies=task_cookies,
                audio_url=info.audio_map.get(url, ""),
                quality=chosen,
                # 同档备用 CDN / 音频备用地址一起交给下载器，避免主地址挂了卡住
                audio_map=info.audio_map,
                url_backups=info.url_backups,
                audio_backups=info.audio_backups)
            if cfg.get("auto_copy_file"):
                self._copy_on_done[task.task_id] = path
            size = info.size_of(url)
            picked = f"{chosen} · {fmt_size(size)}" if size else chosen
            self._toast("已加入队列", f"{picked} · 开始下载，可在「下载任务」查看进度", "success")
        # 附带封面：封面另存为 _cover，免得和视频文件同名互相覆盖。
        # 注意不要把它也登记进 _copy_on_done —— 剪贴板里该留的是用户要的视频，
        # 封面晚一点下完就会把视频顶掉。
        if cfg.get("download_cover") and info.cover_url and not info.is_image:
            cpath = os.path.join(base, fname + "_cover.jpg")
            self.ctx.manager.add(info.cover_url, cpath, "封面")

    def _on_download_cover(self):
        info = self._current
        if not info or not info.cover_url:
            return
        cfg = self.ctx.config
        path = os.path.join(cfg.get("download_path"),
                            cfg.build_filename(info) + "_cover.jpg")
        task = self.ctx.manager.add(info.cover_url, path, "封面")
        if cfg.get("auto_copy_file"):
            self._copy_on_done[task.task_id] = path
        self._toast("已加入队列", "封面开始下载", "success")

    def _on_copy_link(self):
        info = self._current
        if not info:
            return
        url = info.play_url
        options = info.quality_options
        idx = (self._selected_quality_index()
               if self.qualityCombo.isVisible() else 0)
        if options and 0 <= idx < len(options):
            url = options[idx][1]
        if url:
            label = options[idx][0] if options and 0 <= idx < len(options) else ""
            self._copy(url, f"已复制链接（{label}）" if label else "已复制无水印链接")

    def _copy(self, text: str, msg: str):
        QGuiApplication.clipboard().setText(text)
        self._toast(msg, "", "success")

    def _on_task_finished(self, info):
        # 无论成功与否都要摘掉登记项：失败 / 取消的任务不会再走「已完成」分支，
        # 不 pop 就会永久留在 dict 里（长期运行缓慢泄漏）。
        path = self._copy_on_done.pop(info.task_id, None)
        if path and info.status == "已完成":
            try:
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(path)])
                mime.setText(path)
                QGuiApplication.clipboard().setMimeData(mime)
                self._toast("已复制到剪贴板",
                            "文件已复制，可直接 Ctrl+V 或右键粘贴", "success")
            except Exception:  # noqa: BLE001
                pass

    def _toast(self, title: str, content: str, kind: str):
        fn = {"success": InfoBar.success, "error": InfoBar.error,
              "warning": InfoBar.warning}.get(kind, InfoBar.info)
        fn(title, content, orient=Qt.Horizontal, isClosable=True,
           position=InfoBarPosition.TOP, duration=3500, parent=self)

    def _cleanup_worker(self, w):
        if w in self._workers:
            self._workers.remove(w)
        w.deleteLater()




