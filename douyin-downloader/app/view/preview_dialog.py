# -*- coding: utf-8 -*-
"""解析后、下载前的独立预览窗口。视频经本机临时代理保留 CDN 所需请求头。"""
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                               QSlider, QStyle, QVBoxLayout)

from ..core.domain import needs_cookie_for_url
from ..core.downloader import headers_for
from ..core.models import clean_cookie
from ..core.workers import JmPreviewOpenWorker, JmPreviewPageWorker

# 后台线程活到自然结束：窗口关掉时线程可能还在跑，跟着窗口一起析构会触发
# 「QThread: Destroyed while thread is still running」并把进程带崩。
_LIVE_WORKERS = set()


def _keep(worker):
    _LIVE_WORKERS.add(worker)
    worker.finished.connect(lambda: _LIVE_WORKERS.discard(worker))
    return worker


def media_headers(url, cookies="", byte_range=""):
    """和下载任务使用相同的 Referer / 按目标 CDN 判断是否带 Cookie。"""
    headers = headers_for(url)
    if cookies and needs_cookie_for_url(url):
        headers["Cookie"] = clean_cookie(cookies)
    if byte_range:
        headers["Range"] = byte_range
    return headers


class ClickableSlider(QSlider):
    """点击轨道任意位置即跳到该处。

    原生 QSlider 在轨道上的点击只做「翻一页」（page step），既不跳到点击处，
    也不发 sliderMoved —— 于是表现出来就是「只能长按手柄拖」。这里按下时先把
    值直接定到点击位置并上报，同时保持按住后继续拖动的原生手感。
    """

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._scrubbing = False

    def _value_at(self, x: float) -> int:
        """把点击的横坐标换算成滑块值（按整条轨道线性映射）"""
        span = max(1, self.width())
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), int(x), span)

    def _jump_to(self, x: float) -> None:
        value = self._value_at(x)
        if value != self.value():
            self.setValue(value)
        # 值没变也要上报：外部靠这个信号去 seek 播放器
        self.sliderMoved.emit(self.value())

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self.maximum() <= self.minimum():
            super().mousePressEvent(event)      # 时长未知时保持原生行为
            return
        self._scrubbing = True
        # 标记为「滑块被按住」：_update_seek 会跳过自动回写，避免和手动定位打架
        self.setSliderDown(True)
        self._jump_to(event.position().x())
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._scrubbing:
            super().mouseMoveEvent(event)
            return
        self._jump_to(event.position().x())
        event.accept()

    def mouseReleaseEvent(self, event):
        if not self._scrubbing:
            super().mouseReleaseEvent(event)
            return
        self._jump_to(event.position().x())
        self._scrubbing = False
        self.setSliderDown(False)               # 恢复播放器的进度回写
        event.accept()


class _LocalMedia(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, tracks, cookies):
        super().__init__(("127.0.0.1", 0), _MediaHandler)
        self.tracks = tracks
        self.cookies = cookies
        self.token = secrets.token_urlsafe(24)
        self.thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.05},
                                       daemon=True)
        self.thread.start()

    def url(self, track):
        return QUrl(f"http://127.0.0.1:{self.server_port}/{self.token}/{track}")

    def stop(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=1)


class _MediaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_HEAD(self):
        self._serve(head_only=True)

    def do_GET(self):
        self._serve(head_only=False)

    def _serve(self, head_only=False):
        parts = self.path.split("?")[0].split("/")
        if len(parts) != 3 or parts[1] != self.server.token or parts[2] not in self.server.tracks:
            self.send_error(404)
            return
        urls = self.server.tracks[parts[2]]
        byte_range = self.headers.get("Range", "")
        for url in urls:
            try:
                with requests.request("HEAD" if head_only else "GET", url,
                                      headers=media_headers(url, self.server.cookies, byte_range),
                                      stream=True, timeout=(8, 20)) as upstream:
                    if upstream.status_code not in (200, 206):
                        continue
                    if byte_range and upstream.status_code != 206:
                        continue   # Qt seek 请求不能误回整段媒体
                    self.send_response(upstream.status_code)
                    for name in ("Content-Type", "Content-Length", "Content-Range",
                                 "Accept-Ranges"):
                        value = upstream.headers.get(name)
                        if value:
                            self.send_header(name, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    if not head_only:
                        for chunk in upstream.iter_content(64 * 1024):
                            if chunk:
                                self.wfile.write(chunk)
                    return
            except (requests.RequestException, BrokenPipeError, ConnectionResetError,
                    OSError):
                # 连接中断（关闭窗口/拖进度）无需继续尝试其它 CDN。
                return
        self.send_error(502, "媒体源不可用")


class PreviewDialog(QDialog):
    """视频流预览及图集翻页；accept 表示用户在预览中确认下载。"""

    def __init__(self, info, url="", label="", cookies="", cover=None, parent=None):
        super().__init__(parent)
        self.info = info
        self.cookies = cookies
        self.index = 0
        self._reply = None
        self._proxy = None
        self._player = None
        self._audio_player = None
        self._network = QNetworkAccessManager(self)
        self._original_pix = QPixmap()
        # 禁漫预览状态：会话由后台线程建好后回填
        self._jm = None
        self._jm_cover = cover
        self._jm_chapter = 0
        self._jm_page = 0
        self._jm_pages = 0
        self._jm_chapters = 0
        self._jm_busy = False
        self.setWindowTitle("下载前预览")
        self.resize(760, 640)
        layout = QVBoxLayout(self)
        heading = QLabel(info.title or "（无标题）", self)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.detail = QLabel(label or info.type_text, self)
        layout.addWidget(self.detail)
        self.stage = QLabel("加载中…", self)
        self.stage.setAlignment(Qt.AlignCenter)
        self.stage.setMinimumHeight(380)
        self.stage.setStyleSheet("background: #181818; color: #eee; border-radius: 8px")
        layout.addWidget(self.stage, 1)
        self.controls = QHBoxLayout()
        layout.addLayout(self.controls)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        actions.addStretch()
        close = QPushButton("关闭", self)
        close.clicked.connect(self.reject)
        download = QPushButton("下载当前作品", self)
        download.clicked.connect(self.accept)
        actions.addWidget(close)
        actions.addWidget(download)
        layout.addLayout(actions)
        if info.is_image:
            if info.source == "jmcomic":
                self._setup_jm(info, cover)
            else:
                prev = QPushButton("上一张", self)
                next_image = QPushButton("下一张", self)
                self.counter = QLabel("", self)
                prev.clicked.connect(lambda: self._change_image(-1))
                next_image.clicked.connect(lambda: self._change_image(1))
                self.controls.addWidget(prev)
                self.controls.addStretch()
                self.controls.addWidget(self.counter)
                self.controls.addStretch()
                self.controls.addWidget(next_image)
                self._load_image()
        elif url:
            tracks = {"video": [url] + info.backups_of(url)}
            audio_url = info.audio_map.get(url, "")
            if audio_url:
                tracks["audio"] = [audio_url] + info.audio_backups_of(audio_url)
            self._proxy = _LocalMedia(tracks, cookies)
            self.stage.hide()
            video_widget = QVideoWidget(self)
            video_widget.setMinimumHeight(380)
            layout.insertWidget(2, video_widget, 1)
            self._player = QMediaPlayer(self)
            output = QAudioOutput(self)
            self._player.setAudioOutput(output)
            self._player.setVideoOutput(video_widget)
            self._player.setSource(self._proxy.url("video"))
            if audio_url:
                self._audio_player = QMediaPlayer(self)
                self._audio_output = QAudioOutput(self)
                self._audio_player.setAudioOutput(self._audio_output)
                self._audio_player.setSource(self._proxy.url("audio"))
                self._player.positionChanged.connect(self._sync_audio)
                self._audio_player.errorOccurred.connect(
                    lambda *_: self.status.setText("音轨预览失败：" +
                                                   self._audio_player.errorString()))
            self._player.errorOccurred.connect(
                lambda *_: self.status.setText("视频预览失败：" + self._player.errorString()))
            play = QPushButton("暂停", self)
            play.clicked.connect(lambda: self._toggle_play(play))
            self.seek = ClickableSlider(Qt.Horizontal, self)
            self.seek.setRange(0, 0)
            self._player.durationChanged.connect(self.seek.setMaximum)
            self._player.positionChanged.connect(self._update_seek)
            self.seek.sliderMoved.connect(self._seek)
            self.controls.addWidget(play)
            self.controls.addWidget(self.seek, 1)
            self.status.setText("流式预览当前画质；关闭窗口即停止播放")
            self._player.play()
            if self._audio_player:
                self._audio_player.play()
        else:
            self.stage.setText("没有可预览的媒体")

    def _sync_audio(self, position):
        if self._audio_player and abs(self._audio_player.position() - position) > 1200:
            self._audio_player.setPosition(position)

    def _toggle_play(self, button):
        if self._player.isPlaying():
            self._player.pause()
            if self._audio_player:
                self._audio_player.pause()
            button.setText("播放")
        else:
            self._player.play()
            if self._audio_player:
                self._audio_player.play()
            button.setText("暂停")

    def _update_seek(self, position):
        if not self.seek.isSliderDown():
            self.seek.setValue(position)

    def _seek(self, position):
        self._player.setPosition(position)
        if self._audio_player:
            self._audio_player.setPosition(position)

    def _change_image(self, step):
        self.index = (self.index + step) % len(self.info.image_urls)
        self._load_image()

    def _load_image(self):
        if self._reply:
            old_reply, self._reply = self._reply, None
            old_reply.abort()
            old_reply.deleteLater()
        urls = self.info.image_urls
        self.counter.setText(f"{self.index + 1} / {len(urls)}")
        self._original_pix = QPixmap()
        self.stage.setPixmap(QPixmap())
        self.stage.setText("图片加载中…")
        self.status.setText("")
        request = QNetworkRequest(QUrl(urls[self.index]))
        for key, value in media_headers(urls[self.index], self.cookies).items():
            request.setRawHeader(key.encode(), value.encode("latin-1", errors="ignore"))
        request.setTransferTimeout(15000)
        reply = self._network.get(request)
        self._reply = reply
        reply.finished.connect(lambda: self._image_ready(reply))

    def _image_ready(self, reply):
        if reply is not self._reply:
            return
        self._reply = None
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.stage.setText("图片预览失败")
            self.status.setText(reply.errorString())
        else:
            pix = QPixmap()
            if pix.loadFromData(reply.readAll().data()):
                self._show_image(pix)
            else:
                self.stage.setText("图片格式暂不支持预览")
        reply.deleteLater()

    def _show_image(self, pix):
        self._original_pix = pix
        self.stage.setPixmap(pix.scaled(self.stage.size(), Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation))

    # ------------------------------------------------------------------ 禁漫
    # 禁漫的封面之外的页都在服务器上、而且原图是切片逆序混淆过的（防爬），
    # 没法像普通图集那样把 URL 交给 QNetworkAccessManager 直接拉出来看。
    # 这里走 core.jmcomic_bridge.JmPreview：逐页取回原图 + 解码成 PNG，
    # 取页动作全部放后台线程，避免联网时界面卡住。
    def _setup_jm(self, info, cover):
        self.detail.setText("内容预览（内容页需联网逐页取回）")
        self.stage.setText("正在连接禁漫…")
        self.prevButton = QPushButton("上一页", self)
        self.nextButton = QPushButton("下一页", self)
        self.counter = QLabel("加载中…", self)
        self.prevButton.clicked.connect(lambda: self._jm_go(-1))
        self.nextButton.clicked.connect(lambda: self._jm_go(1))
        self.controls.addWidget(self.prevButton)
        self.controls.addStretch()
        self.controls.addWidget(self.counter)
        self.controls.addStretch()
        self.controls.addWidget(self.nextButton)
        self._jm_set_busy(True)
        self.status.setText("首次打开要拉本子目录和第一页，稍等")
        worker = _keep(JmPreviewOpenWorker(info.play_url, self.cookies))
        worker.opened.connect(self._jm_opened)
        worker.failed.connect(self._jm_failed)
        worker.start()

    def _jm_set_busy(self, busy):
        self._jm_busy = bool(busy)
        for name in ("prevButton", "nextButton"):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(not busy)
        if not busy:
            self._jm_refresh()

    def _jm_opened(self, session, chapters, pages, data):
        self._jm = session
        self._jm_chapters = max(1, int(chapters or 1))
        self._jm_pages = max(1, int(pages or 1))
        self._jm_chapter = 0
        self._jm_page = 0
        self.status.setText("")
        self._jm_show(data)
        self._jm_set_busy(False)

    def _jm_failed(self, message):
        self._jm_set_busy(False)
        self.status.setText(message)
        if self._jm is None and self._jm_cover is not None \
                and not self._jm_cover.isNull():
            # 内容拉不到就退回封面，至少让用户看到这本是什么
            self._show_image(self._jm_cover)
            self.detail.setText("内容读取失败，仅显示封面")

    def _jm_show(self, data) -> bool:
        pix = QPixmap()
        if not data or not pix.loadFromData(data):
            self.stage.setText("这一页解不出来")
            return False
        self._show_image(pix)
        return True

    def _jm_refresh(self):
        if self._jm is None:
            return
        if self._jm_chapters > 1:
            title = self._jm.chapter_title(self._jm_chapter)
            head = f"第 {self._jm_chapter + 1}/{self._jm_chapters} 话" + (
                f" {title}" if title else "")
            self.counter.setText(f"{head} · {self._jm_page + 1}/{self._jm_pages} 页")
        else:
            self.counter.setText(f"{self._jm_page + 1}/{self._jm_pages}")
        at_first = self._jm_chapter == 0 and self._jm_page == 0
        at_last = (self._jm_chapter >= self._jm_chapters - 1
                   and self._jm_page >= self._jm_pages - 1)
        self.prevButton.setEnabled(not self._jm_busy and not at_first)
        self.nextButton.setEnabled(not self._jm_busy and not at_last)

    def _jm_go(self, step):
        if self._jm is None or self._jm_busy:
            return
        chapter, page = self._jm_chapter, self._jm_page + step
        if page < 0:
            if chapter == 0:
                return
            chapter, page = chapter - 1, -1      # -1 = 该话最后一页，页数由后台定
        elif page >= self._jm_pages:
            if chapter >= self._jm_chapters - 1:
                return
            chapter, page = chapter + 1, 0
        self._jm_request(chapter, page)

    def _jm_request(self, chapter, page):
        self._jm_set_busy(True)
        self.status.setText("正在取页…")
        worker = _keep(JmPreviewPageWorker(self._jm, chapter, page))
        worker.loaded.connect(self._jm_loaded)
        worker.failed.connect(self._jm_failed)
        worker.start()

    def _jm_loaded(self, chapter, page, total, data):
        self._jm_chapter, self._jm_page = chapter, page
        self._jm_pages = max(1, int(total or 1))
        self.status.setText("")
        self._jm_show(data)
        self._jm_set_busy(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._original_pix.isNull():
            self.stage.setPixmap(self._original_pix.scaled(
                self.stage.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def done(self, result):
        if self._reply:
            old_reply, self._reply = self._reply, None
            old_reply.abort()
            old_reply.deleteLater()
        if self._player:
            self._player.stop()
            self._player.setSource(QUrl())
        if self._audio_player:
            self._audio_player.stop()
            self._audio_player.setSource(QUrl())
        if self._proxy:
            self._proxy.stop()
            self._proxy = None
        super().done(result)

