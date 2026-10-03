# -*- coding: utf-8 -*-
"""设置页：下载、账号与登录、个性化、关于"""
import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QWidget
from qfluentwidgets import (ComboBox, FluentIcon as FIF, HyperlinkCard,
                            InfoBar, InfoBarPosition, PushSettingCard,
                            ScrollArea, SettingCard, SettingCardGroup,
                            SpinBox, SwitchSettingCard, Theme, setTheme)

from . import design as D
from .account_card import (AccountCard, STATE_COOKIE, STATE_LOGIN, STATE_NONE,
                           profile_logged_in)
from .cookie_dialog import (BILI_FIELDS, CookieDialog, DOUYIN_FIELDS,
                            INS_FIELDS, JM_FIELDS, X_FIELDS, XHS_FIELDS)

# 平台账号规格：一张卡 = 一个平台。
# (key, 图标, 显示名, 说明, cookie 配置键, cookie 特征字段, profile 目录属性)
#
# 登录态有两个真实来源（浏览器持久化 profile / 手填 Cookie），两个都要认，
# 否则会出现「左边登录成功、右边显示未配置」的自相矛盾。
_PLATFORM_SPECS = [
    ("douyin", FIF.VIDEO, "抖音",
     "解析受限作品时需要登录态；与「批量下载」页共用同一份登录态",
     "cookie", "sessionid", "profile_dir"),
    ("x", FIF.GLOBE, "X(Twitter)",
     "解析敏感 / 受限内容推文时需要",
     "x_cookie", "auth_token", "x_profile_dir"),
    ("instagram", FIF.CAMERA, "Instagram",
     "解析需登录的内容时需要",
     "ins_cookie", "sessionid", "ins_profile_dir"),
    ("bilibili", FIF.PLAY, "Bilibili",
     "登录后可解锁 1080P 及以上画质（需 ffmpeg 合并）",
     "bili_cookie", "SESSDATA", "bili_profile_dir"),
    ("xiaohongshu", FIF.EDIT, "小红书",
     "免登录即可解析（含最高画质），登录后更稳定",
     "xhs_cookie", "web_session", "xhs_profile_dir"),
    ("jmcomic", FIF.PHOTO, "禁漫天堂",
     "大部分内容免登录，登录后可解析受限本子",
     "jm_cookie", "AVS", "jm_profile_dir"),
]

# 填 Cookie 对话框的文案与字段
_COOKIE_SPECS = {
    "douyin": ("抖音", "在浏览器登录 www.douyin.com 后，按 F12 → 应用/Application "
                       "→ Cookies 中找到以下字段：", DOUYIN_FIELDS),
    "x": ("X(Twitter)", "在浏览器登录 x.com 后，按 F12 → 应用/Application → "
                        "Cookies → x.com 中找到以下字段：", X_FIELDS),
    "instagram": ("Instagram", "在浏览器登录 instagram.com 后，按 F12 → 应用/"
                               "Application → Cookies → instagram.com 中找到以下字段：",
                  INS_FIELDS),
    "bilibili": ("Bilibili", "在浏览器登录 bilibili.com 后，按 F12 → 应用/Application "
                             "→ Cookies → bilibili.com 中找到以下字段：", BILI_FIELDS),
    "xiaohongshu": ("小红书", "在浏览器登录 xiaohongshu.com 后，按 F12 → 应用/Application "
                              "→ Cookies → xiaohongshu.com 中找到以下字段：", XHS_FIELDS),
    "jmcomic": ("禁漫", "在浏览器登录 18comic.vip 后，按 F12 → 应用/Application "
                        "→ Cookies 中找到 AVS：", JM_FIELDS),
}

# 登录成功后补一句"拿到了什么"，比单纯「登录成功」有用
_LOGIN_GAIN = {
    "douyin": "可解析受限作品",
    "x": "可解析敏感 / 受限推文",
    "instagram": "可解析需登录的内容",
    "bilibili": "已解锁 1080P 及以上",
    "xiaohongshu": "解析更稳定",
    "jmcomic": "可解析受限本子",
}

_NAME_OF = {spec[0]: spec[2] for spec in _PLATFORM_SPECS}


class SettingInterface(ScrollArea):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._workers = []
        self.accountCards = {}      # platform key -> (card, cfg_key, token, profile_attr)
        self.setObjectName("settingInterface")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.view = QWidget(self)
        self.view.setObjectName("settingView")
        self.setWidget(self.view)
        self.vBox = D.page_layout(self.view)

        self.vBox.addWidget(D.PageHeader("设置", "下载行为、平台登录态与界面外观",
                                        self.view))
        self._build_download_group()
        self._build_account_group()
        self._build_personal_group()
        self._build_about_group()
        self.vBox.addStretch(1)
        self.view.setStyleSheet("QWidget#settingView { background: transparent; }")

    # ------------------------------------------------------------------ #
    def _build_download_group(self):
        cfg = self.ctx.config
        group = SettingCardGroup("下载", self.view)

        self.folderCard = PushSettingCard(
            "选择文件夹", FIF.DOWNLOAD, "下载目录",
            cfg.get("download_path"), group)
        self.folderCard.clicked.connect(self._on_choose_folder)
        group.addSettingCard(self.folderCard)

        self.authorFolderCard = SwitchSettingCard(
            FIF.FOLDER_ADD, "批量下载按作者分文件夹",
            "仅批量下载时生效：作品保存到以作者昵称命名的子文件夹中；单个下载始终直接存到下载目录",
            parent=group)
        self.authorFolderCard.setChecked(bool(cfg.get("create_author_folder")))
        self.authorFolderCard.checkedChanged.connect(
            lambda v: cfg.set("create_author_folder", v))
        group.addSettingCard(self.authorFolderCard)

        self.coverCard = SwitchSettingCard(
            FIF.PHOTO, "同时下载封面",
            "下载视频时附带保存封面图片", parent=group)
        self.coverCard.setChecked(bool(cfg.get("download_cover")))
        self.coverCard.checkedChanged.connect(lambda v: cfg.set("download_cover", v))
        group.addSettingCard(self.coverCard)

        self.copyCard = SwitchSettingCard(
            FIF.COPY, "下载完成后自动复制文件",
            "单个视频下载完成后自动复制到剪贴板，可直接 Ctrl+V 或右键粘贴", parent=group)
        self.copyCard.setChecked(bool(cfg.get("auto_copy_file")))
        self.copyCard.checkedChanged.connect(lambda v: cfg.set("auto_copy_file", v))
        group.addSettingCard(self.copyCard)

        self.ffmpegCard = PushSettingCard(
            "下载组件", FIF.ZIP_FOLDER, "合并组件（ffmpeg）",
            self._ffmpeg_status_text(), group)
        self.ffmpegCard.clicked.connect(self._on_download_ffmpeg)
        group.addSettingCard(self.ffmpegCard)

        self.namingCard = SettingCard(
            FIF.EDIT, "文件命名规则", "文件名统一带「年月日_」前缀，便于按下载日期排序", group)
        self.namingCombo = ComboBox(self.namingCard)
        self.namingCombo.addItems(["日期_时间", "日期_作者_标题", "日期_标题",
                                   "日期_作品ID_标题"])
        rule_map = {"timestamp": 0, "author_title": 1, "title": 2, "id_title": 3}
        rule_keys = ["timestamp", "author_title", "title", "id_title"]
        self.namingCombo.setCurrentIndex(rule_map.get(cfg.get("naming_rule"), 0))
        self.namingCombo.currentIndexChanged.connect(
            lambda i: cfg.set("naming_rule", rule_keys[i]))
        self.namingCard.hBoxLayout.addWidget(self.namingCombo, 0, Qt.AlignRight)
        self.namingCard.hBoxLayout.addSpacing(16)
        group.addSettingCard(self.namingCard)

        self.segmentCard = SwitchSettingCard(
            FIF.SPEED_HIGH, "多连接分段加速",
            "大于 2MB 的文件用多条连接并行下载（IDM 式加速，实测单连接 "
            "13 MB/s → 23.8 MB/s）。服务端不支持分段时自动退回单连接",
            parent=group)
        self.segmentCard.setChecked(bool(cfg.get("segment_download", True)))
        self.segmentCard.checkedChanged.connect(
            lambda v: (cfg.set("segment_download", v),
                       setattr(self.ctx.manager, "_segment_enabled", bool(v))))
        group.addSettingCard(self.segmentCard)

        # 每文件连接数：IDM 的招牌设置项，默认 8，可调 1-32。
        # 数值越大越快，但超过带宽上限后只是排队，不会再提速。
        self.connCard = SettingCard(
            FIF.LINK, "每文件连接数",
            "单个文件拆成几条连接并行下载（IDM 默认 8，范围 1-32）。"
            "并发任务多时会自动摊薄，避免一次开太多连接", group)
        self.connSpin = SpinBox(self.connCard)
        self.connSpin.setRange(1, 32)
        self.connSpin.setValue(int(cfg.get("segment_connections", 8)))
        self.connSpin.valueChanged.connect(self._on_connections_changed)
        self.connCard.hBoxLayout.addWidget(self.connSpin, 0, Qt.AlignRight)
        self.connCard.hBoxLayout.addSpacing(16)
        group.addSettingCard(self.connCard)

        self.concurrentCard = SettingCard(
            FIF.SPEED_HIGH, "同时下载数", "下载队列的最大并发任务数（1-8）", group)
        self.concurrentSpin = SpinBox(self.concurrentCard)
        self.concurrentSpin.setRange(1, 8)
        self.concurrentSpin.setValue(int(cfg.get("max_concurrent", 3)))
        self.concurrentSpin.valueChanged.connect(self._on_concurrent_changed)
        self.concurrentCard.hBoxLayout.addWidget(self.concurrentSpin, 0, Qt.AlignRight)
        self.concurrentCard.hBoxLayout.addSpacing(16)
        group.addSettingCard(self.concurrentCard)

        self.vBox.addWidget(group)

    # ------------------------------------------------------------------ #
    # ffmpeg 组件（B 站 1080P 及以上要它合并音视频）
    def _ffmpeg_status_text(self) -> str:
        from ..core.ffmpeg import ffmpeg_path
        path = ffmpeg_path(self.ctx.config.base_dir)
        if path:
            return f"已安装：{path}（B 站可选 1080P+，视频音频自动合并）"
        return "未安装。B 站 1080P 及以上需要它来合并音视频，点右侧下载（约 30MB）"

    def _on_download_ffmpeg(self):
        from ..core.workers import FFmpegWorker
        self.ffmpegCard.setEnabled(False)
        self.ffmpegCard.setContent("正在下载 ffmpeg …")
        w = FFmpegWorker(self.ctx.config.base_dir, self)
        w.progress.connect(self._on_ffmpeg_progress)
        w.done.connect(self._on_ffmpeg_done)
        w.finished.connect(lambda: self.ffmpegCard.setEnabled(True))
        self._workers.append(w)
        w.start()

    def _on_ffmpeg_progress(self, got: int, total: int, msg: str):
        if total:
            self.ffmpegCard.setContent(f"{msg} {got * 100 // total}%")
        else:
            self.ffmpegCard.setContent(msg)

    def _on_ffmpeg_done(self, ok: bool, msg: str):
        self.ffmpegCard.setContent(self._ffmpeg_status_text())
        if ok:
            InfoBar.success("组件已就绪", "ffmpeg 已安装，B 站可选 1080P 及以上",
                            orient=Qt.Horizontal, isClosable=True,
                            position=InfoBarPosition.TOP, duration=3000, parent=self)
        else:
            InfoBar.error("下载失败", msg, orient=Qt.Horizontal, isClosable=True,
                          position=InfoBarPosition.TOP, duration=5000, parent=self)

    def _build_account_group(self):
        """账号与登录：一张卡 = 一个平台。

        改前：「网络」组把 6 个平台的登录与 Cookie 摊成 11 张结构完全相同的卡片，
        找「小红书 Cookie」要扫 11 行。改后按平台聚合，一屏 6 行，每行右侧直接
        给出该平台的两个入口，左侧是真实登录态。
        """
        group = SettingCardGroup("账号与登录", self.view)
        for key, icon, name, desc, cfg_key, token, profile_attr in _PLATFORM_SPECS:
            card = AccountCard(icon, name, desc, group)
            card.loginRequested.connect(lambda k=key: self._on_login(k))
            card.cookieRequested.connect(lambda k=key: self._on_config_cookie(k))
            group.addSettingCard(card)
            self.accountCards[key] = (card, cfg_key, token, profile_attr)
        self._refresh_account_state()
        self.vBox.addWidget(group)

    # ------------------------------------------------------------------ #
    # 账号：登录态 / 网页登录 / 填 Cookie
    def showEvent(self, event):
        """切回本页时重算登录态。

        抖音的登录也可能是在「批量下载」页扫码完成的，只在本页初始化时算一次
        会显示成过期的「未配置」。
        """
        super().showEvent(event)
        if self.accountCards:
            self._refresh_account_state()

    def _refresh_account_state(self):
        """登录态：浏览器持久化 profile 优先，手填 Cookie 次之

        profile 侧按平台凭据名去 Cookie 库里核对（不能只看 profile 目录在不在，
        见 account_card.profile_logged_in）。
        """
        cfg = self.ctx.config
        for key, (card, cfg_key, token, profile_attr) in self.accountCards.items():
            logged = profile_logged_in(getattr(cfg, profile_attr, "") or "", token)
            filled = token in (cfg.get(cfg_key, "") or "")
            if logged:
                card.set_state(STATE_LOGIN)
            elif filled:
                card.set_state(STATE_COOKIE)
            else:
                card.set_state(STATE_NONE)

    def _on_login(self, key: str):
        """网页登录：开有头浏览器，用户登录完自动落地登录态（推荐路径）"""
        from ..core.workers import LoginWorker
        card, _, _, profile_attr = self.accountCards[key]
        name = _NAME_OF.get(key, "")
        card.set_busy(True)
        w = LoginWorker(getattr(self.ctx.config, profile_attr), site=key, parent=self)
        w.cookie_ready.connect(lambda c, k=key: self._on_login_cookie(k, c))
        w.done.connect(lambda ok, msg, k=key: self._on_login_done(k, ok, msg))
        w.finished.connect(lambda k=key: self.accountCards[k][0].set_busy(False))
        self._workers.append(w)
        w.start()
        InfoBar.info("登录窗口已打开",
                     f"请在浏览器中完成 {name} 登录，成功后窗口会自动关闭",
                     orient=Qt.Horizontal, isClosable=True,
                     position=InfoBarPosition.TOP, duration=4000, parent=self)

    def _on_login_cookie(self, key: str, cookie: str):
        """登录时导出的 Cookie（B 站 / 小红书 / 禁漫走这条）。

        这三个站点的解析走 HTTP 客户端，光有浏览器 profile 不够，必须落 Cookie。
        """
        if not cookie:
            return
        cfg_key = self.accountCards[key][1]
        self.ctx.config.set(cfg_key, cookie)

    def _on_login_done(self, key: str, ok: bool, msg: str):
        name = _NAME_OF.get(key, "")
        if ok:
            gain = _LOGIN_GAIN.get(key, "")
            InfoBar.success(f"{name} 登录成功",
                            f"登录态已保存{'，' + gain if gain else ''}",
                            orient=Qt.Horizontal, isClosable=True,
                            position=InfoBarPosition.TOP, duration=3000, parent=self)
        else:
            InfoBar.warning("登录未完成", msg, orient=Qt.Horizontal,
                            isClosable=True, position=InfoBarPosition.TOP,
                            duration=3000, parent=self)
        self._refresh_account_state()

    def _on_config_cookie(self, key: str):
        """填 Cookie：按字段填，保存时自动拼成标准 `k=v; k=v`"""
        _, cfg_key, _, _ = self.accountCards[key]
        title, guide, fields = _COOKIE_SPECS[key]
        dlg = CookieDialog(f"配置 {title} Cookie", guide, fields,
                           current=self.ctx.config.get(cfg_key, ""),
                           parent=self.window())
        if dlg.exec():
            self.ctx.config.set(cfg_key, dlg.get_cookie_string())
            self._refresh_account_state()
            InfoBar.success("已保存", f"{title} Cookie 已更新",
                            orient=Qt.Horizontal, isClosable=True,
                            position=InfoBarPosition.TOP, duration=2000, parent=self)

    def _build_personal_group(self):
        cfg = self.ctx.config
        group = SettingCardGroup("个性化", self.view)

        self.themeCard = SettingCard(
            FIF.BRUSH, "应用主题", "调整应用的界面外观", group)
        self.themeCombo = ComboBox(self.themeCard)
        self.themeCombo.addItems(["深色", "浅色", "跟随系统"])
        theme_map = {"dark": 0, "light": 1, "auto": 2}
        theme_keys = ["dark", "light", "auto"]
        self.themeCombo.setCurrentIndex(theme_map.get(cfg.get("theme"), 0))
        self.themeCombo.currentIndexChanged.connect(
            lambda i: self._on_theme_changed(theme_keys[i]))
        self.themeCard.hBoxLayout.addWidget(self.themeCombo, 0, Qt.AlignRight)
        self.themeCard.hBoxLayout.addSpacing(16)
        group.addSettingCard(self.themeCard)

        # 强调色：全局只有一个色相，主按钮 / 进度条 / 开关 / 选中态都跟着它走。
        # 切换即时生效（setThemeColor 会重刷所有已注册的样式），不需要重启。
        self.accentCard = SettingCard(
            FIF.PALETTE, "强调色",
            "主按钮、进度条、开关与选中态的颜色。qfluentwidgets 会按主题自动派生深浅两套",
            group)
        self.accentCombo = ComboBox(self.accentCard)
        self.accentCombo.addItems([label for _, label, _ in D.ACCENTS])
        accent_keys = [key for key, _, _ in D.ACCENTS]
        cur = cfg.get("accent_color", D.DEFAULT_ACCENT)
        self.accentCombo.setCurrentIndex(accent_keys.index(cur)
                                        if cur in accent_keys else 0)
        self.accentCombo.currentIndexChanged.connect(
            lambda i: self._on_accent_changed(accent_keys[i]))
        self.accentCard.hBoxLayout.addWidget(self.accentCombo, 0, Qt.AlignRight)
        self.accentCard.hBoxLayout.addSpacing(16)
        group.addSettingCard(self.accentCard)

        # Mica 云母特效：观感好但需要持续合成，低配机器上会拖慢滚动
        self.micaCard = SwitchSettingCard(
            FIF.TRANSPARENT, "窗口云母特效",
            "半透明磨砂背景（需 Win11）。如界面滚动卡顿可关闭以提升流畅度",
            parent=group)
        self.micaCard.setChecked(bool(cfg.get("mica_effect", False)))
        self.micaCard.checkedChanged.connect(self._on_mica_changed)
        group.addSettingCard(self.micaCard)

        # 动效：只做状态反馈与入场（120~300ms，ease-out），不做装饰性动画。
        # 开关语义是「开启动效」，配置里存的是反过来的 reduce_motion。
        self.motionCard = SwitchSettingCard(
            FIF.SYNC, "界面动效",
            "卡片入场与页面切换的过渡动画。远程桌面或低配机器上可关闭，界面会更干脆",
            parent=group)
        self.motionCard.setChecked(not bool(cfg.get("reduce_motion", False)))
        self.motionCard.checkedChanged.connect(self._on_motion_changed)
        group.addSettingCard(self.motionCard)

        self.vBox.addWidget(group)

    def _on_motion_changed(self, on: bool):
        D.set_reduce_motion(not on)
        self.ctx.config.set("reduce_motion", not on)

    def _on_accent_changed(self, key: str):
        """切换强调色：即时生效，且不依赖重启"""
        applied = D.apply_accent(key)
        self.ctx.config.set("accent_color", applied)

    def _on_mica_changed(self, on: bool):
        win = self.window()
        if hasattr(win, "set_mica"):
            win.set_mica(on)
        else:
            self.ctx.config.set("mica_effect", on)

    def _build_about_group(self):
        from .. import __version__
        from ..core.applog import log_path
        group = SettingCardGroup("关于", self.view)
        card = HyperlinkCard(
            "https://github.com/VideoData/DY-Data",
            "功能参考", FIF.GITHUB, f"视频下载器 v{__version__}",
            "功能设计参考 VideoData/DY-Data；界面风格参考 moesnow/March7thAssistant",
            group)
        group.addSettingCard(card)
        self.logCard = PushSettingCard(
            "打开日志文件夹", FIF.FOLDER, "运行日志",
            log_path() or "日志未启用（程序目录不可写）", group)
        self.logCard.clicked.connect(self._on_open_log)
        group.addSettingCard(self.logCard)
        self.vBox.addWidget(group)

    def _on_open_log(self):
        """打开 logs 目录：出错时的现场都写在 app.log 里"""
        from ..core.applog import log_dir
        d = log_dir()
        if not d or not os.path.isdir(d):
            InfoBar.warning("日志不可用", "程序目录不可写，日志没有启用",
                            orient=Qt.Horizontal, isClosable=True,
                            position=InfoBarPosition.TOP, duration=3000, parent=self)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    # ------------------------------------------------------------------ #
    def _on_choose_folder(self):
        path = QFileDialog.getExistingDirectory(
            self, "选择下载目录", self.ctx.config.get("download_path"))
        if path:
            self.ctx.config.set("download_path", path)
            self.folderCard.setContent(path)

    def _on_concurrent_changed(self, v: int):
        self.ctx.config.set("max_concurrent", v)
        self.ctx.manager.max_concurrent = max(1, v)

    def _on_connections_changed(self, v: int):
        """每文件连接数：立即对后续下载生效（已在跑的连接不动）"""
        self.ctx.config.set("segment_connections", v)
        self.ctx.manager.set_connections(v)

    def _on_theme_changed(self, key: str):
        self.ctx.config.set("theme", key)
        theme = {"dark": Theme.DARK, "light": Theme.LIGHT,
                 "auto": Theme.AUTO}[key]
        setTheme(theme)
