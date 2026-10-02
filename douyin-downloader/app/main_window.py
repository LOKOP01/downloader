# -*- coding: utf-8 -*-
"""主窗口：FluentWindow 导航式布局（参考 March7thAssistant 的界面组织）"""
import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QAbstractScrollArea, QApplication
from qfluentwidgets import (FluentIcon as FIF, FluentWindow,
                            NavigationItemPosition, Theme, setTheme)

from .core.config import Config
from .core.downloader import DownloadManager
from .view import design as D
from .view.batch_interface import BatchInterface
from .view.home_interface import HomeInterface
from .view.perf import tune_scroll_areas
from .view.setting_interface import SettingInterface
from .view.task_interface import TaskInterface


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
        self.addSubInterface(self.homeInterface, FIF.VIDEO, "视频解析")
        self.addSubInterface(self.batchInterface, FIF.PEOPLE, "批量下载")
        self.addSubInterface(self.taskInterface, FIF.DOWNLOAD, "下载任务")
        self.addSubInterface(self.settingInterface, FIF.SETTING, "设置",
                             position=NavigationItemPosition.BOTTOM)

        self.navigationInterface.setAcrylicEnabled(False)

    def switchTo(self, interface):
        """页面切换：换掉 qfluentwidgets 默认的 InQuad/OutQuad 曲线。

        入场动画应该"先快后慢"（减速），原来的曲线在起步阶段偏软，切页会有一点
        拖泥带水的感觉。这里统一用 ease-out，并复用设计系统的时长与曲线。
        """
        if isinstance(interface, QAbstractScrollArea):
            interface.verticalScrollBar().setValue(0)
        self.stackedWidget.view.setCurrentWidget(
            interface, False, True, D.DUR_ENTER, D.EASE_ENTER)

    def initWindow(self):
        self.resize(1100, 720)
        self.setWindowTitle("视频下载器")
        self.setWindowIcon(app_icon())
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
