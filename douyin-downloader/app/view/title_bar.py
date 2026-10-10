# -*- coding: utf-8 -*-
"""标题栏：32px 紧凑自绘标题栏

- 高 **32px**（Tailwind `h-8`），底色用面板级（`BG_SECONDARY`）+ 底部 1px 分隔线
- 左侧：16px 窗口图标 + 12px 次级色标题，都靠左；图标前留 10px
- 右侧：三个 **48px 宽**（`w-12`）全高按钮；普通按钮 hover 用 `BG_HOVER`，
  **关闭键 hover 变红底白字**（Tailwind red-500）

窗口拖动、双击最大化、按钮到窗口操作的连线都由 `TitleBarBase` 提供，
这里只换外观。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import QLabel

from qfluentwidgets import CaptionLabel
from qfluentwidgets.common.config import qconfig
from qfluentwidgets.common.style_sheet import isDarkTheme
from qframelesswindow import TitleBar

from . import design as D


def _color(token) -> QColor:
    """(浅色, 深色) 二元组 → 当前主题下应取的 QColor"""
    return QColor(token[1] if isDarkTheme() else token[0])


class MxuTitleBar(TitleBar):
    """32px 紧凑标题栏"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedHeight(D.TITLEBAR_H)

        # ---------------- 左侧：图标 + 标题 ----------------
        self.iconLabel = QLabel(self)
        self.iconLabel.setFixedSize(16, 16)
        self.hBoxLayout.insertSpacing(0, 10)
        self.hBoxLayout.insertWidget(1, self.iconLabel, 0,
                                     Qt.AlignLeft | Qt.AlignVCenter)

        self.titleLabel = CaptionLabel(self)
        self.titleLabel.setTextColor(*D.TEXT_SECONDARY)
        self.hBoxLayout.insertWidget(2, self.titleLabel, 0,
                                     Qt.AlignLeft | Qt.AlignVCenter)
        self.hBoxLayout.insertSpacing(3, 8)

        win = self.window()
        win.windowTitleChanged.connect(self.setTitle)
        win.windowIconChanged.connect(self.setIcon)
        self.setTitle(win.windowTitle())
        self.setIcon(win.windowIcon())

        # ---------------- 右侧：窗口按钮 ----------------
        for btn in (self.minBtn, self.maxBtn, self.closeBtn):
            btn.setFixedSize(D.TITLEBAR_BTN_W, D.TITLEBAR_H)

        hover, pressed = _color(D.BG_HOVER), _color(D.BG_ACTIVE)
        for btn in (self.minBtn, self.maxBtn):
            btn.setHoverBackgroundColor(hover)
            btn.setPressedBackgroundColor(pressed)

        # 关闭键：hover / 按下都是一整块红底 + 白图标
        close = QColor(D.CLOSE_HOVER)
        self.closeBtn.setHoverBackgroundColor(close)
        self.closeBtn.setPressedBackgroundColor(close)
        self.closeBtn.setHoverColor(QColor("#FFFFFF"))
        self.closeBtn.setPressedColor(QColor("#FFFFFF"))

        # 主题切换后底色/分隔线颜色要跟着变
        qconfig.themeChangedFinished.connect(self.update)

    # ------------------------------------------------------------------ #
    def setTitle(self, title: str):
        self.titleLabel.setText(title)

    def setIcon(self, icon):
        ic = QIcon(icon)
        if not ic.isNull():
            self.iconLabel.setPixmap(ic.pixmap(16, 16))

    def paintEvent(self, event):
        """自绘底色与底部分隔线 —— 走 paintEvent 而不是 QSS，
        省掉 WA_StyledBackground 那套开关，主题切换后重绘即生效。"""
        painter = QPainter(self)
        painter.fillRect(self.rect(), _color(D.BG_SECONDARY))
        painter.setPen(_color(D.BORDER))
        y = self.height() - 1
        painter.drawLine(0, y, self.width(), y)
