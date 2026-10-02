# -*- coding: utf-8 -*-
"""抖音视频下载器 —— 入口

功能设计参考 VideoData/DY-Data（链接解析、去水印、批量下载）
界面风格参考 moesnow/March7thAssistant（PySide6 + Fluent Widgets）
"""
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

# PyInstaller 打包后，配置文件放到 exe 同级目录；源码运行则放项目根目录
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    from app.core.applog import get_logger, log_path, setup as setup_logging

    # 先装日志：后面任何一步炸了都要能在 logs/app.log 里看到
    setup_logging(BASE_DIR)
    log = get_logger("main")

    # 高 DPI 适配
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName("抖音视频下载器")
    app.setOrganizationName("DouyinDownloader")

    from app.core.config import Config
    from app.main_window import MainWindow
    from app.view import design as D
    from app import __version__

    config = Config(os.path.join(BASE_DIR, "config.json"))
    # 动效开关要在建窗口前落地：首屏的页面切换动效读的就是这个标志
    D.set_reduce_motion(bool(config.get("reduce_motion", False)))
    # 强调色同理：一堆控件的选中态/焦点态在构造时就取这个色
    D.apply_accent(config.get("accent_color", D.DEFAULT_ACCENT))
    log.info("启动 v%s | 打包运行=%s | 程序目录=%s | 日志=%s | Python %s",
             __version__, bool(getattr(sys, "frozen", False)), BASE_DIR,
             log_path() or "（不可写）", sys.version.split()[0])
    window = MainWindow(config)
    window.show()
    code = app.exec()
    log.info("退出，返回码 %s", code)
    sys.exit(code)


if __name__ == "__main__":
    main()
