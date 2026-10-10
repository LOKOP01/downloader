# -*- coding: utf-8 -*-
"""主窗口：FluentWindow 导航式布局（参考 March7thAssistant 的界面组织）"""
import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QVBoxLayout)
from qfluentwidgets import (FluentIcon as FIF, FluentWindow, Theme, setTheme)

from .core.config import Config
from .core.downloader import DownloadManager
from .view import design as D
from .view.batch_interface import BatchInterface
from .view.home_interface import HomeInterface
from .view.perf import tune_scroll_areas
from .view.setting_interface import SettingInterface
from .view.tab_bar import MxuTabBar
from .view.task_interface import TaskInterface
from .view.title_bar import MxuTitleBar


def resource_path(*parts) -> str:
    """兼容源码运行与 PyInstaller 单文件模式，定位随包资源。"""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def app_icon() -> QIcon:
    """优先用自定义 app.ico，缺失时回退到内置图标。"""
    for name in ("assets/app.ico", "assets/app.png"):
        p = resource_path(name.replace("/", os.sep))
        if os.path.exists(p):
            ic = QIcon(p)
            if not ic.isNull():
                return ic
    return FIF.VIDEO.icon()


class AppContext:
    """跨页面共享的运行时上下文"""
    def __init__(self, config: Config, manager: DownloadManager):
        self.config = config
        self.manager = manager


class MainWindow(FluentWindow):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.ctx = AppContext(config, DownloadManager(
            int(config.get("max_concurrent", 3)), self,
            segment_enabled=bool(config.get("segment_download", True)),
            segment_connections=int(config.get("segment_connections", 8))))

        self.homeInterface = HomeInterface(self.ctx, self)
        self.batchInterface = BatchInterface(self.ctx, self)
        self.taskInterface = TaskInterface(self.ctx, self)
        self.settingInterface = SettingInterface(self.ctx, self)

        self.initNavigation()
        self.initWindow()
        self._apply_theme()

        # 启动即展示首页
        self.switchTo(self.homeInterface)

    def initNavigation(self):
        # 先把 4 个页面注册进 stackedWidget。addSubInterface 会顺带挂进左侧
        # NavigationInterface —— 那是下一步就要退场的旧壳层，保留对象不销毁，
        # 免得 _onCurrentInterfaceChanged / qrouter 的既有链路断掉。
        self.addSubInterface(self.homeInterface, FIF.VIDEO, "视频解析")
        self.addSubInterface(self.batchInterface, FIF.PEOPLE, "批量下载")
        self.addSubInterface(self.taskInterface, FIF.DOWNLOAD, "下载任务")
        self.addSubInterface(self.settingInterface, FIF.SETTING, "设置")

        self.navigationInterface.setAcrylicEnabled(False)

        # ---------------- 换成 MXU 的两段式壳层：标题栏 → 标签栏 → 内容 ----------------
        # 左侧导航退场（hide + 移出布局，不销毁）
        self.navigationInterface.hide()
        self.hBoxLayout.removeWidget(self.navigationInterface)

        self._pages = [self.homeInterface, self.batchInterface,
                       self.taskInterface, self.settingInterface]
        self.tabBar = MxuTabBar(
            [("home", "视频解析"), ("batch", "批量下载"),
             ("task", "下载任务"), ("setting", "设置")],
            app_name="视频下载器", parent=self)

        # widgetLayout 原本是 [stackedWidget] 的横向布局，改成纵向叠一层标签栏。
        # 页面的横向滚动与拉伸关系都靠 vBoxLayout 的 stretch 承接。
        self.widgetLayout.removeWidget(self.stackedWidget)
        self.vBoxLayout = QVBoxLayout()
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.setSpacing(0)
        self.vBoxLayout.addWidget(self.tabBar)
        self.vBoxLayout.addWidget(self.stackedWidget, 1)
        self.widgetLayout.addLayout(self.vBoxLayout)

        self.tabBar.tabChanged.connect(self._on_tab_changed)
        self.tabBar.themeButton.clicked.connect(self._toggle_theme)
        # 页面若被其它途径切走（设置页跳转等），标签高亮要跟着走
        self.stackedWidget.currentChanged.connect(self._sync_tab)

    # ------------------------------------------------------------------ #
    def _on_tab_changed(self, index: int):
        if 0 <= index < len(self._pages):
            self.switchTo(self._pages[index])

    def _sync_tab(self, index: int):
        if 0 <= index < len(self._pages):
            self.tabBar.set_current(index)

    def _toggle_theme(self):
        """标签栏右侧的明暗切换（对应 MXU 那个太阳/月亮按钮）"""
        cur = self.config.get("theme", "dark")
        self.config.set("theme", "light" if cur != "light" else "dark")
        self._apply_theme()

    def switchTo(self, interface):
        """页面切换：换掉 qfluentwidgets 默认的 InQuad/OutQuad 曲线。

        入场动画应该"先快后慢"（减速），原来的曲线在起步阶段偏软，切页会有一点
        拖泥带水的感觉。这里统一用 ease-out，并复用设计系统的时长与曲线。
        """
        if isinstance(interface, QAbstractScrollArea):
            interface.verticalScrollBar().setValue(0)
        self.stackedWidget.view.setCurrentWidget(
            interface, False, True, D.DUR_ENTER, D.EASE_ENTER)

    def resizeEvent(self, event):
        """标题栏顶到最左。

        FluentWindow.resizeEvent 会固定把标题栏右移 46px，那是给左侧导航的
        "返回"键留位；我们已换成顶部标签栏，那个位置不再需要。
        """
        super().resizeEvent(event)
        if self.titleBar is not None:
            self.titleBar.move(0, 0)
            self.titleBar.resize(self.width(), self.titleBar.height())

    def initWindow(self):
        self.resize(1100, 720)
        self.setWindowTitle("视频下载器")
        self.setWindowIcon(app_icon())
        # 换掉 qfluentwidgets 自带的 48px 标题栏，改用 MXU 规格的 32px 版本
        self.setTitleBar(MxuTitleBar(self))
        # 页面区顶部要让出标题栏高度（FluentWindow 默认按 48 预留，得跟着改）
        self.widgetLayout.setContentsMargins(0, D.TITLEBAR_H, 0, 0)
        # 窗口底色走 MXU 的 bg-primary（浅 #FAFAFA / 深 #09090B）。
        # 不设的话是 qfluentwidgets 自己的 (#F3F3F3, #202020)，深色下偏灰、
        # 跟 MXU 那套"近纯黑"的观感差一截。
        try:
            self.setCustomBackgroundColor(*D.BG_PRIMARY)
        except Exception:  # noqa: BLE001 - 旧版 qfluentwidgets 没有这个 API
            pass
        # 页面底：qfluentwidgets 渲染出来是 #1E1E1E，不是 MXU 的值。
        # 逐页 setStyleSheet（直接设在控件上，优先级高于库的类选择器）——
        # 卡片不用单独改：它是 rgba 白叠加在页面底上的，页面底一对，
        # 卡片自然落到 MXU 的深度（#09090B 上叠 5% 白 ≈ #151515，接近 #18181B）。
        for itf in (self.homeInterface, self.batchInterface,
                    self.taskInterface, self.settingInterface):
            name = itf.objectName()
            D.theme_qss(itf,
                        f"#{name}{{background:{D.BG_PRIMARY[0]};}}",
                        f"#{name}{{background:{D.BG_PRIMARY[1]};}}")
        # Mica 云母特效需要持续采样桌面做模糊合成，在部分机器上是滚动卡顿的主因。
        # 默认关闭，设置页可手动开启（见 setting_interface）。
        self._apply_mica(self.config.get("mica_effect", False))
        # 居中
        desktop = QApplication.primaryScreen().availableGeometry()
        w, h = self.frameGeometry().width(), self.frameGeometry().height()
        self.move(desktop.width() // 2 - w // 2, desktop.height() // 2 - h // 2)
        # 统一优化各页面滚动流畅度
        tune_scroll_areas(self)

    def _apply_mica(self, enabled: bool):
        """安全地开关 Mica 特效（非 Win11 环境会静默失败）"""
        try:
            self.setMicaEffectEnabled(bool(enabled))
        except Exception:  # noqa: BLE001 - 非 Win11 环境静默降级
            pass

    def set_mica(self, enabled: bool):
        """供设置页调用：切换 Mica 并持久化"""
        self._apply_mica(enabled)
        self.config.set("mica_effect", bool(enabled))

    def _apply_theme(self):
        key = self.config.get("theme", "dark")
        theme = {"dark": Theme.DARK, "light": Theme.LIGHT,
                 "auto": Theme.AUTO}.get(key, Theme.DARK)
        setTheme(theme)
