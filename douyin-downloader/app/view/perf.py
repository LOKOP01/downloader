# -*- coding: utf-8 -*-
"""UI 流畅度优化：滚动、绘制、刷新节流相关的统一设置。

背景：qfluentwidgets 默认配置偏向视觉效果，在滚动和动画上开销较大。
这里提供一组轻量优化，不改变外观，只提升响应速度。
"""
from PySide6.QtCore import Qt, QTimer, QObject, QEvent
from PySide6.QtWidgets import QApplication, QAbstractScrollArea
from qfluentwidgets import ScrollArea


def tune_scroll_area(area: QAbstractScrollArea, step: int = 72) -> None:
    """让滚轮滚动更跟手。

    qfluentwidgets 的 ScrollArea 在初始化时会重置滚动条的 step，
    因此单纯 setSingleStep 会被覆盖；这里改为安装事件过滤器，
    在 wheelEvent 里直接按像素滚动。
    """
    if area is None:
        return
    vbar = area.verticalScrollBar()
    if vbar is not None:
        vbar.setSingleStep(step)
    try:
        area.setVerticalScrollMode(QAbstractScrollArea.ScrollPerPixel)
    except Exception:  # noqa: BLE001
        pass
    if not getattr(area, "_perf_wheel_installed", False):
        area._perf_wheel_step = step
        area.installEventFilter(_WheelSmoother(area))
        area._perf_wheel_installed = True


class _WheelSmoother(QObject):
    """把滚轮事件转成固定像素的平滑滚动，避开 Qt 的按行滚动。"""

    def __init__(self, area):
        super().__init__(area)
        self._area = area

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel:
            try:
                vbar = self._area.verticalScrollBar()
                if vbar is not None:
                    # 按系统一格的 1/8 取整，保证触控板与鼠标手感一致
                    delta = event.angleDelta().y()
                    if delta != 0:
                        step = getattr(self._area, "_perf_wheel_step", 72)
                        # 一格滚轮通常 120，这里换算成像素
                        vbar.setValue(vbar.value() - int(delta / 120 * step))
                        return True
            except Exception:  # noqa: BLE001
                pass
        return super().eventFilter(obj, event)


def tune_scroll_areas(root) -> None:
    """递归给窗口内所有 ScrollArea 应用滚动优化。"""
    for area in root.findChildren(ScrollArea):
        tune_scroll_area(area)


def enable_performance_hints() -> None:
    """开启若干全局绘制优化。"""
    app = QApplication.instance()
    if app is None:
        return
    try:
        app.setEffectEnabled(Qt.UI_AnimateCombo, True)
    except Exception:  # noqa: BLE001
        pass


class ProgressThrottle(QObject):
    """进度刷新节流器。

    下载任务可能以很高的频率发进度信号，但如果每个信号都直接改 UI
    （尤其是重建 QString / 重算统计），UI 线程会被压满。
    这里把同一批更新合并到固定间隔（默认 120ms）统一执行一次。
    """

    def __init__(self, interval_ms: int = 120, parent=None):
        super().__init__(parent)
        self._pending = {}
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush)
        self._handler = None

    def set_handler(self, fn):
        """注册真正的刷新逻辑（接收 batch: list）"""
        self._handler = fn

    def push(self, key, payload):
        """提交一次更新；同 key 会被合并，只保留最新值。"""
        self._pending[key] = payload
        if not self._timer.isActive():
            self._timer.start()

    def flush_now(self):
        if self._timer.isActive():
            self._timer.stop()
        self._flush()

    def _flush(self):
        if not self._pending:
            return
        batch = list(self._pending.values())
        self._pending.clear()
        if self._handler:
            self._handler(batch)
