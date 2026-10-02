# -*- coding: utf-8 -*-
"""禁漫天堂（JMComic）解析与下载，封装 hect0x7/JMComic-Crawler-Python。

JM 的原图是切片混淆过的，不能当普通 CDN 直链下；必须走 jmcomic 解码。
解析只取本子/章节元数据，真正下载由 DownloadManager 调 download_jmcomic()。
"""
from __future__ import annotations

import os
import re
import threading
from typing import Callable, Optional, Tuple

from .models import VideoInfo
from .parser import ParseError

_jmcomic = None
_jmcomic_err = ''


def _load_jmcomic():
    """延迟加载 jmcomic，并把真实导入错误带给界面。"""
    global _jmcomic, _jmcomic_err
    if _jmcomic is not None:
        return _jmcomic
    try:
        import jmcomic as mod
        _jmcomic = mod
        return mod
    except Exception as e:  # noqa: BLE001
        _jmcomic_err = f'{type(e).__name__}: {e}'
        return None

# 车号：JM123 / jm 123456
_JM_CODE_RE = re.compile(r"(?:JM)\s*(\d{2,})", re.I)
_PHOTO_RE = re.compile(r"/photo/(\d+)", re.I)
_ALBUM_RE = re.compile(r"/album/(\d+)", re.I)


class DownloadCancelled(Exception):
    """用户取消禁漫下载"""


def try_parse_jm(text: str) -> Tuple[str, str]:
    """从分享文案里抠禁漫车号。返回 (kind, id)，识别失败返回 ("", "")。

    kind: album（整本）/ photo（单章节）。
    """
    raw = (text or "").strip()
    if not raw:
        return "", ""
    m = _PHOTO_RE.search(raw)
    if m:
        return "photo", m.group(1)
    m = _ALBUM_RE.search(raw)
    if m:
        return "album", m.group(1)
    m = _JM_CODE_RE.search(raw)
    if m:
        return "album", m.group(1)
    compact = raw.replace(" ", "")
    if compact.isdigit() and 2 <= len(compact) <= 10:
        return "album", compact
    try:
        from jmcomic.jm_toolkit import JmcomicText
        jmid = JmcomicText.parse_to_jm_id(raw)
        if str(jmid).isdigit():
            return "album", str(jmid)
    except Exception:  # noqa: BLE001
        pass
    return "", ""


def _jm_count(value) -> int:
    """把禁漫的 77K / 1.6万 / 2M / 9801 转成整数。"""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().upper().replace(",", "").replace(" ", "")
    if not text:
        return 0
    try:
        if text.endswith("万"):
            return int(float(text[:-1]) * 10000)
        if text.endswith("K"):
            return int(float(text[:-1]) * 1000)
        if text.endswith("M"):
            return int(float(text[:-1]) * 1000000)
        return int(float(text))
    except ValueError:
        digits = re.sub(r"\D", "", text)
        return int(digits) if digits else 0


def _jm_time(text: str) -> int:
    try:
        from datetime import datetime
        return int(datetime.strptime(str(text)[:10], "%Y-%m-%d").timestamp())
    except Exception:  # noqa: BLE001
        return 0


def _system_proxies() -> dict:
    """读 Windows 系统代理，并补上 http:// 前缀，curl_cffi 才能走 Clash。"""
    try:
        from common import ProxyBuilder
        raw = ProxyBuilder.system_proxy() or {}
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for key, value in raw.items():
        if not value:
            continue
        text = str(value).strip()
        if not text:
            continue
        if '://' not in text:
            text = 'http://' + text
        out[key] = text
    return out


def build_option(base_dir: str, cookie: str = "", retry_times: int = 5):
    """组装 jmcomic Option：解码、jpg、限并发，避免把禁漫打爆。"""
    if _load_jmcomic() is None:
        detail = _jmcomic_err or 'ImportError'
        raise ParseError(f'缺少 jmcomic 组件（{detail}）。打包版请重新安装，源码运行请 pip install jmcomic')
    from jmcomic import JmModuleConfig, JmOption

    JmModuleConfig.FLAG_ENABLE_JM_LOG = False

    os.makedirs(base_dir, exist_ok=True)
    meta = {
        "impersonate": "chrome",
        "timeout": 8,
        "verify": False,
    }
    proxies = _system_proxies()
    if proxies:
        meta["proxies"] = proxies
    option = JmOption.construct({
        "log": False,
        "dir_rule": {
            "base_dir": os.path.abspath(base_dir),
            "rule": "Bd / Ptitle",
        },
        "download": {
            "cache": True,
            "image": {"decode": True, "suffix": ".jpg"},
            "threading": {"image": 8, "photo": 2},
        },
        "client": {
            "impl": "api",
            "retry_times": int(retry_times),
            "postman": {
                "type": "curl_cffi",
                "meta_data": meta,
            },
        },
    })
    kv = _cookie_dict(cookie)
    if kv:
        option.update_cookies(kv)
    return option


def _cookie_dict(cookie: str) -> dict:
    kv = {}
    for pair in (cookie or "").split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        k, v = k.strip(), v.strip()
        if k:
            kv[k] = v
    return kv



def _friendly_jm_error(exc: BaseException) -> str:
    msg = str(exc) or type(exc).__name__
    low = msg.lower()
    if 'ssl' in low or 'curl: (35)' in low or 'connection closed' in low:
        return '禁漫接口连不上（SSL 被重置）。请把代理节点换成能打开 18comic 的再试'
    if '403' in msg or 'ip地区' in msg or '爬虫' in msg:
        return '禁漫拒绝访问（地区限制或被识别）。请更换代理节点，或到设置里登录禁漫后再试'
    if 'timeout' in low or 'timed out' in low:
        return '禁漫接口超时。请先确认代理已开启，再重试'
    if 'login' in low or '登錄' in msg or '登录' in msg:
        return f'该本子可能需要登录：{msg}。请到设置里配置禁漫 Cookie（AVS）'
    # 重试失败详情太长，toast 里只留第一行
    first = msg.splitlines()[0].strip()
    return f'禁漫解析失败：{first}'


def parse_jmcomic(text: str, cookie: str = "") -> VideoInfo:
    """解析禁漫本子或章节，返回 VideoInfo（is_image=True，真正下载走 jmcomic）。"""
    kind, jmid = try_parse_jm(text)
    if not jmid:
        raise ParseError("未识别到禁漫车号，请粘贴 JM123、纯数字车号或 18comic 链接")
    try:
        import tempfile
        option = build_option(tempfile.gettempdir(), cookie, retry_times=2)
        client = option.new_jm_client()
        if kind == "photo":
            return _from_photo(client, jmid)
        return _from_album(client, jmid)
    except ParseError:
        raise
    except Exception as e:  # noqa: BLE001
        name = type(e).__name__
        if "MissingAlbumPhoto" in name:
            raise ParseError(f"禁漫车号 {jmid} 不存在或已下架") from e
        raise ParseError(_friendly_jm_error(e)) from e


def _from_album(client, album_id: str) -> VideoInfo:
    album = client.get_album_detail(album_id)
    chapters = len(album)
    pages = _jm_count(getattr(album, "page_count", 0))
    title = (getattr(album, "name", None) or f"JM{album_id}").strip()
    author = (getattr(album, "author", None) or "").strip()
    tags = getattr(album, "tags", None) or []
    return VideoInfo(
        aweme_id=str(getattr(album, "album_id", album_id)),
        title=title,
        author=author,
        author_uid=author,
        cover_url=f"jmcover://{album_id}",
        play_url=f"jmcomic://album/{album_id}",
        source="jmcomic",
        is_image=True,
        comment_count=_jm_count(getattr(album, "comment_count", 0)),
        digg_count=_jm_count(getattr(album, "likes", 0)),
        share_count=_jm_count(getattr(album, "views", 0)),
        create_time=_jm_time(getattr(album, "pub_date", "") or ""),
        music_title=" / ".join(str(t) for t in tags[:6]),
        extra={
            "kind": "album",
            "id": str(album_id),
            "pages": str(pages),
            "chapters": str(chapters),
        },
    )


def _from_photo(client, photo_id: str) -> VideoInfo:
    photo = client.get_photo_detail(photo_id, fetch_album=True)
    album_id = str(getattr(photo, "album_id", "") or photo_id)
    title = (getattr(photo, "name", None) or f"JM{photo_id}").strip()
    author = (getattr(photo, "author", None) or "").strip()
    pages = 0
    try:
        pages = len(photo)
    except Exception:  # noqa: BLE001
        pages = _jm_count(getattr(photo, "page_count", 0))
    tags = getattr(photo, "tags", None) or []
    return VideoInfo(
        aweme_id=str(getattr(photo, "photo_id", photo_id)),
        title=title,
        author=author,
        author_uid=author,
        cover_url=f"jmcover://{album_id}",
        play_url=f"jmcomic://photo/{photo_id}",
        source="jmcomic",
        is_image=True,
        music_title=" / ".join(str(t) for t in tags[:6]),
        extra={
            "kind": "photo",
            "id": str(photo_id),
            "pages": str(pages),
            "chapters": "1",
        },
    )


def fetch_cover_bytes(album_id: str, cookie: str = "") -> bytes:
    """下载本子封面到内存。失败返回空字节。"""
    import tempfile
    try:
        option = build_option(tempfile.gettempdir(), cookie)
        client = option.new_jm_client()
        path = os.path.join(tempfile.gettempdir(), f"jm_cover_{album_id}.jpg")
        client.download_album_cover(album_id, path)
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            try:
                os.remove(path)
            except OSError:
                pass
            return data
    except Exception:  # noqa: BLE001
        return b""
    return b""


def download_jmcomic(spec: str, save_dir: str, cookie: str = "",
                     cancel: Optional[threading.Event] = None,
                     progress_cb: Optional[Callable[[int, int], None]] = None
                     ) -> str:
    """下载整本或单章节。spec 形如 jmcomic://album/123。返回保存目录。"""
    if _load_jmcomic() is None:
        detail = _jmcomic_err or 'ImportError'
        raise ParseError(
            f'缺少 jmcomic 组件（{detail}）。打包版请重新安装，源码运行请 pip install jmcomic')
    from jmcomic import download_album, download_photo

    kind, _, jmid = spec.replace("jmcomic://", "").partition("/")
    kind = (kind or "album").strip().lower()
    jmid = (jmid or "").strip()
    if not jmid:
        raise ValueError(f"无效的禁漫地址：{spec}")

    option = build_option(save_dir, cookie)

    def factory(opt):
        return _ProgressDownloader(opt, cancel, progress_cb)

    try:
        if kind == "photo":
            download_photo(jmid, option, downloader=factory, check_exception=True)
        else:
            download_album(jmid, option, downloader=factory, check_exception=True)
    except DownloadCancelled:
        raise
    except Exception as e:  # noqa: BLE001
        if isinstance(e, DownloadCancelled) or cancel is not None and cancel.is_set():
            raise DownloadCancelled() from e
        name = type(e).__name__
        if "PartialDownloadFailed" in name:
            raise ValueError(f"禁漫部分图片下载失败：{e}") from e
        raise ValueError(f"禁漫下载失败：{e}") from e
    return save_dir


class _ProgressDownloader:
    """延迟绑定的 JmDownloader 子类工厂产物，避免模块 import 时强依赖。"""

    def __new__(cls, option, cancel=None, progress_cb=None):
        from jmcomic import JmDownloader

        class ProgressDownloader(JmDownloader):
            def __init__(self, opt):
                super().__init__(opt)
                self._done = 0
                self._total = 0
                self._cancel = cancel
                self._progress_cb = progress_cb

            def _check(self):
                if self._cancel is not None and self._cancel.is_set():
                    raise DownloadCancelled()

            def _report(self):
                if self._progress_cb:
                    self._progress_cb(self._done, self._total)

            def before_album(self, album):
                self._check()
                super().before_album(album)
                try:
                    self._total = int(album.page_count) or self._total
                except Exception:  # noqa: BLE001
                    pass
                self._report()

            def before_photo(self, photo):
                self._check()
                super().before_photo(photo)
                if not self._total:
                    try:
                        self._total = len(photo)
                    except Exception:  # noqa: BLE001
                        pass
                self._report()

            def before_image(self, image, img_save_path):
                self._check()
                super().before_image(image, img_save_path)

            def after_image(self, image, img_save_path):
                super().after_image(image, img_save_path)
                self._done += 1
                if self._total < self._done:
                    self._total = self._done
                self._report()
                self._check()

        inst = ProgressDownloader(option)
        return inst
