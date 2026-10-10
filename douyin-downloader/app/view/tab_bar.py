# -*- coding: utf-8 -*-
"""顶部标签栏：对齐 MaaEnd / MXU 的规格

MaaEnd 的界面完全来自 MistEO/MXU（Tauri + React），其 `src/components/TabBar.tsx`
定义了一条 40px 高的顶部标签条，取代了传统侧边导航：

- 条高 **40px**（`h-10`），底色 `bg-secondary`，底部 1px 分隔线
- 标签 `min-w-[120px]`、文字 `text-sm`，标签之间 1px 右分隔线
- 选中：`bg-primary`（与下方页面同色，视觉上"连成一片"）+ 强调色文字 + **2px 强调色下边框**
- 未选中：`bg-tertiary` + 次级文字，hover 变 `bg-hover`
- 右侧：应用名 + 工具按钮（`p-2` 圆角方块 = 32×32，hover 变 `bg-hover`）

与 MXU 的差异：它的一条标签 = 一个运行实例（可新建/拖动/关闭），本项目的
4 条标签 = 4 个固定功能页，所以标签区不带新建 `+` 与关闭 `×`，右侧工具区
只保留"主题切换"（MXU 同款：一个太阳/月亮图标按钮）。

全部用 `paintEvent` 自绘：省掉 `WA_StyledBackground` 那套开关，主题切换后
`qconfig.themeChangedFinished → update()` 立即生效，也绕开了裸 QWidget 上
`setCustomStyleSheet` 不生效的坑（见 design.theme_qss 的说明）。
"""
from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets.common.config import qconfig
from qfluentwidgets.common.icon import drawIcon
from qfluentwidgets.common.style_sheet import isDarkTheme

from . import design as D


def _col(token) -> QColor:
    """(浅色, 深色) 二元组 → 当前主题下应取的 QColor"""
    return QColor(token[1] if isDarkTheme() else token[0])


class _Tab(QWidget):
    """单个标签：左键点击 → clicked(index)"""

    clicked = Signal(int)

    def __init__(self, text: str, index: int, parent=None):
        super().__init__(parent)
        self._index = index
        self._text = text
        self._active = False
        self._hover = False
        self.setFixedHeight(D.TABBAR_H)
        self.setMinimumWidth(D.TABBAR_MIN_W)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 字号跟正文一致（MXU 是 text-sm），比正文再重一档以便在灰底上站得住
        self.setFont(D.font(D.FONT_BODY, D.W_MEDIUM))
        qconfig.themeChangedFinished.connect(self.update)

    # ------------------------------------------------------------- 状态
    def set_active(self, on: bool) -> None:
        on = bool(on)
        if on != self._active:
            self._active = on
            self.update()

    @property
    def text(self) -> str:
        return self._text

    # ------------------------------------------------------------- 交互
    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and \
                self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self._index)

    # ------------------------------------------------------------- 绘制
    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()

        if self._active:
            painter.fillRect(rect, _col(D.BG_PRIMARY))
        elif self._hover:
            painter.fillRect(rect, _col(D.BG_HOVER))
        else:
            painter.fillRect(rect, _col(D.BG_TERTIARY))

        # 标签之间的 1px 右分隔线
        painter.setPen(_col(D.BORDER))
        x = rect.right()
        painter.drawLine(x, rect.top(), x, rect.bottom())

        # 选中态：底部 2px 强调色下边框
        if self._active:
            accent = _col(D.TEXT_ACCENT)
            painter.fillRect(QRect(rect.left(), rect.bottom() - 1, rect.width(), 2), accent)

        painter.setPen(_col(D.TEXT_ACCENT if self._active else D.TEXT_SECONDARY))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)


class _ToolButton(QWidget):
    """右侧工具按钮：32×32 圆角方块，hover 变 bg-hover（MXU 的 `p-2 rounded-md`）"""

    clicked = Signal()

    def __init__(self, icon, tooltip: str = "", parent=None):
        super().__init__(parent)
        self._icon = icon
        self._hover = False
        self.setFixedSize(D.TABBAR_TOOL_W, D.TABBAR_TOOL_W)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        qconfig.themeChangedFinished.connect(self.update)

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and \
                self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        if self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_col(D.BG_HOVER))
            painter.drawRoundedRect(rect, D.RADIUS_MD, D.RADIUS_MD)

        side = D.TABBAR_ICON
        x = (rect.width() - side) / 2
        y = (rect.height() - side) / 2
        drawIcon(self._icon, painter, QRect(int(x), int(y), side, side))


class MxuTabBar(QWidget):
    """MXU 规格的顶部标签栏。

    tabChanged(int) 在用户点击标签时发出，由主窗口负责真正的页面切换。
    """

    tabChanged = Signal(int)

    def __init__(self, tabs, app_name: str = "", parent=None):
        """tabs: [(key, 显示文字), ...]"""
        super().__init__(parent)
        self.setFixedHeight(D.TABBAR_H)
        self.setObjectName("mxuTabBar")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(0)

        self._tabs = []
        for i, (_key, text) in enumerate(tabs):
            tab = _Tab(text, i, self)
            tab.clicked.connect(self._on_tab_clicked)
            lay.addWidget(tab)
            self._tabs.append(tab)

        lay.addStretch(1)

        if app_name:
            self.appLabel = QLabel(app_name, self)
            self.appLabel.setFont(D.font(D.FONT_BODY, D.W_MEDIUM))
            self.appLabel.setStyleSheet(
                f"color:{D.TEXT_SECONDARY[1] if isDarkTheme() else D.TEXT_SECONDARY[0]};"
                "padding:0 8px;")
            qconfig.themeChangedFinished.connect(self._refresh_app_label)
            lay.addWidget(self.appLabel, 0, Qt.AlignmentFlag.AlignVCenter)

        self.themeButton = _ToolButton(FIF.CONSTRACT, "切换浅色 / 深色主题", self)
        lay.addWidget(self.themeButton, 0, Qt.AlignmentFlag.AlignVCenter)

        self.set_current(0)

    # ------------------------------------------------------------- 对外
    def set_current(self, index: int) -> None:
        for i, tab in enumerate(self._tabs):
            tab.set_active(i == index)

    def count(self) -> int:
        return len(self._tabs)

    # ------------------------------------------------------------- 内部
    def _on_tab_clicked(self, index: int) -> None:
        if 0 <= index < len(self._tabs) and not self._tabs[index]._active:
            self.tabChanged.emit(index)

    def _refresh_app_label(self) -> None:
        lab = getattr(self, "appLabel", None)
        if lab is not None:
            lab.setStyleSheet(
                f"color:{_col(D.TEXT_SECONDARY).name()};padding:0 8px;")

    def paintEvent(self, event):
        """底色 + 底部 1px 分隔线（整条贯通，标签之间没有缝）"""
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, _col(D.BG_SECONDARY))
        painter.setPen(_col(D.BORDER))
        y = rect.height() - 1
        painter.drawLine(rect.left(), y, rect.right(), y)
