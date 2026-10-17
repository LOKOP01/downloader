# -*- coding: utf-8 -*-
"""Native Qt video player: QMediaPlayer playback with local resume progress.

结构对齐 manga_reader.py 的 MangaReader：顶部工具条 + 中央画面 + 底部控制条，
快捷键、全屏、隐藏工具栏与 Esc 语义都保持一致。进度在暂停/关闭时落盘。
"""
import os
from datetime import datetime

from PySide6.QtCore import QUrl, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QDialog, QHBoxLayout,
                               QMessageBox, QSlider, QVBoxLayout, QWidget)
from qfluentwidgets import ComboBox, PushButton

from ..core.video_library import fmt_time
from . import design as D

# 续播判定：看过结尾 5 秒以内视为播完（从头播），否则恢复到保存位置。
END_EPSILON_MS = 5000
_SPEEDS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]


def _is_editing(widget):
    # 输入控件获得焦点时先禁用字母/空格快捷键（与漫画阅读器同一策略）。
    return isinstance(widget, (QLineEdit_types()))


def QLineEdit_types():
    from PySide6.QtWidgets import QLineEdit
    return (QLineEdit, QAbstractSpinBox, ComboBox)


class VideoPlayer(QDialog):
    changed = Signal()

    def __init__(self, library, item, parent=None):
        super().__init__(parent)
        self.library, self.item = library, item
        self._closed = False
        self._controls_hidden = False
        self._was_maximized = False
        self._duration = 0
        self._seek_restore_ms = item.resume_ms
        self._restored = False
        self._slider_down = False
        self._pending_seek = -1
        self.setWindowTitle(f"{item.title} · 视频播放器")
        self.setWindowFlag(Qt.WindowType.WindowMinMaxButtonsHint, True)
        self.resize(1000, 680)
        self.setMinimumSize(760, 500)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setObjectName("videoPlayer")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.top = QWidget(self)
        row = QHBoxLayout(self.top)
        row.setContentsMargins(16, 12, 16, 12)
        back = PushButton("← 返回视频库", self.top)
        back.clicked.connect(self.close)
        row.addWidget(back)
        self.titleLabel = D.section_label(item.title, self.top)
        self.titleLabel.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.titleLabel, 1)
        fullscreen = PushButton("全屏 F11", self.top)
        fullscreen.clicked.connect(self.toggle_fullscreen)
        row.addWidget(fullscreen)
        help_btn = PushButton("快捷键", self.top)
        help_btn.clicked.connect(self.show_help)
        row.addWidget(help_btn)
        layout.addWidget(self.top)

        self.video = QVideoWidget(self)
        self.video.setAccessibleName("视频画面")
        layout.addWidget(self.video, 1)

        self.message = D.caption("", self)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.message.setContentsMargins(16, 8, 16, 8)
        self.message.hide()
        layout.addWidget(self.message)

        self.bottom = QWidget(self)
        bottom = QVBoxLayout(self.bottom)
        bottom.setContentsMargins(16, 10, 16, 12)
        line = QHBoxLayout()
        self.playBtn = PushButton("播放", self.bottom)
        self.playBtn.clicked.connect(self.toggle_play)
        line.addWidget(self.playBtn)
        self.timeLabel = D.caption("00:00 / --:--", self.bottom)
        line.addWidget(self.timeLabel)
        self.slider = QSlider(Qt.Orientation.Horizontal, self.bottom)
        self.slider.setRange(0, 0)
        self.slider.setAccessibleName("播放进度")
        self.slider.setSingleStep(500)
        self.slider.setPageStep(10000)
        # 用户拖动期间不回写位置，避免画面来回跳。
        self.slider.sliderPressed.connect(self._slider_grabbed)
        self.slider.sliderMoved.connect(self._slider_moved)
        self.slider.sliderReleased.connect(self._slider_released)
        self.slider.valueChanged.connect(self._slider_value_changed)
        line.addWidget(self.slider, 1)
        self.speed = ComboBox(self.bottom)
        self.speed.addItems(["0.5×", "0.75×", "1.0×", "1.25×", "1.5×", "2.0×"])
        self.speed.setCurrentIndex(2)
        self.speed.currentIndexChanged.connect(self._speed_changed)
        self.speed.setAccessibleName("播放速度")
        line.addWidget(self.speed)
        bottom.addLayout(line)
        line = QHBoxLayout()
        self.back10 = PushButton("↺ 10s", self.bottom)
        self.fwd10 = PushButton("10s ↻", self.bottom)
        self.back10.clicked.connect(lambda: self.skip_ms(-10000))
        self.fwd10.clicked.connect(lambda: self.skip_ms(10000))
        line.addWidget(self.back10)
        line.addWidget(self.fwd10)
        self.muteBtn = PushButton("静音", self.bottom)
        self.muteBtn.clicked.connect(self.toggle_mute)
        line.addWidget(self.muteBtn)
        self.volume = QSlider(Qt.Orientation.Horizontal, self.bottom)
        self.volume.setRange(0, 100)
        self.volume.setValue(100)
        self.volume.setFixedWidth(120)
        self.volume.setAccessibleName("音量")
        self.volume.valueChanged.connect(self._volume_changed)
        line.addWidget(self.volume)
        line.addStretch()
        self.restartBtn = PushButton("从头播放", self.bottom)
        self.restartBtn.clicked.connect(self.restart)
        line.addWidget(self.restartBtn)
        bottom.addLayout(line)
        layout.addWidget(self.bottom)

        D.theme_qss(self, "#videoPlayer{background:#FAFAFA;}", "#videoPlayer{background:#09090B;}")
        self._shortcuts = []
        self._editing_shortcuts = []
        for key, action in [("Right", lambda: self.skip_ms(5000)), ("Left", lambda: self.skip_ms(-5000)),
                            ("Up", lambda: self._nudge_volume(5)), ("Down", lambda: self._nudge_volume(-5)),
                            ("Space", self.toggle_play), ("F11", self.toggle_fullscreen),
                            ("Escape", self.escape), ("Tab", self.toggle_controls),
                            ("M", self.toggle_mute), ("0", self.restart),
                            ("PgDown", lambda: self.play_next_file(1)),
                            ("PgUp", lambda: self.play_next_file(-1))]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(action)
            self._shortcuts.append(shortcut)
            if key not in ("F11", "Escape"):
                self._editing_shortcuts.append(shortcut)
        self._focus_connection = QApplication.instance().focusChanged.connect(self._focus_changed)
        # Enter must not activate a dialog's default Back button.
        for button in self.findChildren(PushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

        self.audio = QAudioOutput(self)
        self.audio.setVolume(1.0)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.errorOccurred.connect(self._error_occurred)
        self.player.mediaStatusChanged.connect(self._status_changed)
        self.player.setSource(QUrl.fromLocalFile(item.path))
        self.player.play()

    # ------------------------------------------------------------------ UI
    def _focus_changed(self, old, current):
        editing = _is_editing(current)
        for shortcut in self._editing_shortcuts:
            shortcut.setEnabled(not editing)

    def _state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.playBtn.setText("暂停" if playing else "播放")
        if not playing and not self._closed:
            self._save_position()

    def _position_changed(self, position):
        if self._slider_down:
            return
        self.slider.blockSignals(True)
        self.slider.setValue(position)
        self.slider.blockSignals(False)
        self._update_time_label(position)

    def _duration_changed(self, duration):
        self._duration = duration
        self.slider.setRange(0, max(0, duration))
        self._update_time_label(self.player.position())
        if duration > 0 and not self._restored:
            self._restored = True
            resume = self._seek_restore_ms
            # 播完过的（残留到结尾附近）直接从头播。
            if 0 < resume <= max(0, duration - END_EPSILON_MS):
                self.player.setPosition(resume)
                self.message.setText(f"已恢复到上次播放位置 {fmt_time(resume)}，想从头看请点「从头播放」")
                self.message.show()
                QTimer.singleShot(6000, self._hide_message)

    def _hide_message(self):
        if not self._closed:
            self.message.hide()

    def _update_time_label(self, position):
        self.timeLabel.setText(f"{fmt_time(position)} / {fmt_time(self._duration)}")

    def _error_occurred(self, error, error_string):
        if self._closed or error == QMediaPlayer.Error.NoError:
            return
        self.message.setText(f"无法播放：{error_string or '该格式缺少系统解码器'}")
        self.message.show()

    def _status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.InvalidMedia and not self._closed:
            self.message.setText("无法打开这个文件：路径可能已被移动，或格式不受支持。")
            self.message.show()

    # ------------------------------------------------------------------ 交互
    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()
        self.video.setFocus()

    def skip_ms(self, delta):
        target = self.player.position() + delta
        if self._duration > 0:
            target = max(0, min(target, self._duration))
        self.player.setPosition(max(0, target))
        self.video.setFocus()

    def restart(self):
        self.player.setPosition(0)
        self.player.play()
        self.video.setFocus()

    def play_next_file(self, direction):
        """PgUp/PgDn：在同一文件夹的视频之间切换。"""
        current = os.path.dirname(os.path.abspath(self.item.path))
        videos = sorted((v for v in self.library.videos
                         if os.path.dirname(os.path.abspath(v.path)) == current),
                        key=lambda v: os.path.basename(v.path))
        if len(videos) <= 1:
            return
        index = next((i for i, v in enumerate(videos)
                      if os.path.normcase(v.path) == os.path.normcase(self.item.path)), 0)
        target = videos[max(0, min(index + direction, len(videos) - 1))]
        if os.path.normcase(target.path) == os.path.normcase(self.item.path):
            return
        self._save_position()
        self.item = target
        self._restored = False
        self._duration = 0
        self._seek_restore_ms = target.resume_ms
        self.setWindowTitle(f"{target.title} · 视频播放器")
        self.titleLabel.setText(target.title)
        self.player.setSource(QUrl.fromLocalFile(target.path))
        self.player.play()
        self.changed.emit()

    def _nudge_volume(self, delta):
        self.volume.setValue(max(0, min(100, self.volume.value() + delta)))
        self.video.setFocus()

    def _volume_changed(self, value):
        self.audio.setVolume(value / 100)
        self.audio.setMuted(value == 0)
        self.muteBtn.setText("取消静音" if value == 0 else "静音")

    def toggle_mute(self):
        muted = not self.audio.isMuted()
        self.audio.setMuted(muted)
        self.muteBtn.setText("取消静音" if muted else "静音")
        self.video.setFocus()

    def _speed_changed(self, index):
        self.player.setPlaybackRate(_SPEEDS[index])
        self.video.setFocus()

    # 进度条：拖动期间只改自己的值，松手（或点按后值变化）才真正 seek。
    def _slider_grabbed(self):
        self._slider_down = True

    def _slider_moved(self, value):
        self._pending_seek = value
        self._update_time_label(value)

    def _slider_released(self):
        self._slider_down = False
        if self._pending_seek >= 0:
            self.player.setPosition(self._pending_seek)
            self._pending_seek = -1

    def _slider_value_changed(self, value):
        # 鼠标点击空档（不经过 pressed/released 对）时走这里。
        if not self._slider_down:
            self.player.setPosition(value)

    # ------------------------------------------------------------------ 进度
    def _save_position(self):
        if self._closed:
            return
        position = self._pending_seek if self._pending_seek >= 0 else self.player.position()
        duration = self._duration or self.item.duration_ms
        position = max(0, int(position))
        duration = max(0, int(duration))
        if self.item.resume_ms == position and self.item.duration_ms == duration:
            return
        self.item.resume_ms = position
        self.item.duration_ms = duration
        self.item.last_played = datetime.now().isoformat(timespec="seconds")
        try:
            self.library.save()
        except OSError as error:
            self.message.setText(f"进度保存失败：{error}")
            self.message.show()
        else:
            self.changed.emit()

    # ------------------------------------------------------------------ 窗口
    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized() if self._was_maximized else self.showNormal()
        else:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()

    def toggle_controls(self):
        self._controls_hidden = not self._controls_hidden
        self.top.setVisible(not self._controls_hidden)
        self.bottom.setVisible(not self._controls_hidden)

    def escape(self):
        if self.isFullScreen():
            self.toggle_fullscreen()
        elif self._controls_hidden:
            self.toggle_controls()
        else:
            self.close()

    def reject(self):
        self.escape()

    def show_help(self):
        QMessageBox.information(self, "视频播放器 · 快捷键",
                                "空格：播放 / 暂停\n左右键：快退 / 快进 5 秒；按钮为 10 秒\n"
                                "上下键：音量；M：静音\n"
                                "PageUp / PageDown：切换同文件夹的上一条 / 下一条视频\n"
                                "F11 / 双击：全屏；Tab：隐藏工具栏；Esc：退出全屏或关闭\n"
                                "暂停和关闭时自动保存进度，下次从上次位置继续。")

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_fullscreen()
            event.accept()

    def closeEvent(self, event):
        if self._closed:
            event.accept()
            return
        self._closed = True
        self._save_position()
        QApplication.instance().disconnect(self._focus_connection)
        self.player.stop()
        self.player.setSource(QUrl())
        event.accept()
