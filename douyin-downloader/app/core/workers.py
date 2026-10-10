# -*- coding: utf-8 -*-
"""后台工作线程：解析、批量列表、封面加载"""
import requests
from PySide6.QtCore import QThread, Signal

from .applog import get_logger
from .parser import DouyinParser, ParseError

log = get_logger("worker")


class ParseWorker(QThread):
    """解析单个作品（抖音 / X / Instagram / B站 / 小红书 / Iwara / Pornhub / hanime1 / 禁漫，自动路由）"""
    succeeded = Signal(object)   # VideoInfo
    failed = Signal(str)

    def __init__(self, ctx, share_text: str, parent=None):
        """ctx: SiteContext（站点凭据与路径的唯一载体，见 core/site_ctx.py）"""
        super().__init__(parent)
        self.ctx = ctx
        self.share_text = share_text

    def run(self):
        from .sites import parse_share
        try:
            self.succeeded.emit(parse_share(self.share_text, self.ctx))
        except ParseError as e:
            log.warning("解析失败：%s | 输入=%s", e,
                        " ".join((self.share_text or "").split())[:160])
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("解析异常")
            self.failed.emit(f"解析异常：{e}")


class FFmpegWorker(QThread):
    """下载 ffmpeg 合并组件（B 站 1080P 需要）"""
    progress = Signal(int, int, str)   # 已下载, 总量, 提示
    done = Signal(bool, str)

    def __init__(self, base_dir: str, parent=None):
        super().__init__(parent)
        self.base_dir = base_dir

    def run(self):
        from .ffmpeg import download_ffmpeg
        try:
            path = download_ffmpeg(
                self.base_dir,
                progress_cb=lambda got, total, msg: self.progress.emit(got, total, msg))
            self.done.emit(True, path)
        except Exception as e:  # noqa: BLE001
            log.exception("下载 ffmpeg 组件失败")
            self.done.emit(False, str(e))


class UserResolveWorker(QThread):
    """解析用户主页 -> sec_uid"""
    succeeded = Signal(str)      # sec_uid
    failed = Signal(str)

    def __init__(self, cookie: str, share_text: str, parent=None):
        super().__init__(parent)
        self.cookie = cookie
        self.share_text = share_text

    def run(self):
        try:
            parser = DouyinParser(self.cookie)
            sec_uid, _ = parser.resolve_user(self.share_text)
            self.succeeded.emit(sec_uid)
        except ParseError as e:
            log.warning("用户主页解析失败：%s", e)
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("用户主页解析异常")
            self.failed.emit(f"解析异常：{e}")


class UserVideosWorker(QThread):
    """抓取用户全部作品（经浏览器桥，2026 年起 API 直连被风控拦截）"""
    succeeded = Signal(list, int, bool, str)   # infos, next_cursor, has_more, nickname
    failed = Signal(str)
    progress = Signal(int, int)                # 已抓取数量, 作者作品总数(0 为未知)
    limited = Signal()                         # 疑似被登录墙截断（只有第一页）

    def __init__(self, cookie: str, sec_uid: str, max_cursor: int = 0,
                 profile_dir: str = "", parent=None):
        super().__init__(parent)
        self.cookie = cookie
        self.sec_uid = sec_uid
        self.max_cursor = max_cursor
        self.profile_dir = profile_dir

    def run(self):
        from .browser_bridge import BrowserBridgeError, fetch_user_posts
        try:
            items, nickname, is_limited, expected = fetch_user_posts(
                self.sec_uid, self.cookie,
                profile_dir=self.profile_dir or None,
                progress_cb=lambda n, total: self.progress.emit(n, total))
            parser = DouyinParser(self.cookie)
            infos = []
            for it in items:
                try:
                    infos.append(parser._build_info(it))
                except ParseError:
                    continue
            self.succeeded.emit(infos, 0, False, nickname)
            if is_limited:
                self.limited.emit()
        except BrowserBridgeError as e:
            log.error("获取用户作品失败：%s", e)
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("获取用户作品异常")
            self.failed.emit(f"获取失败：{e}")


class LoginWorker(QThread):
    """打开有头 Edge 供用户登录（douyin / instagram / x / bilibili / xiaohongshu / jmcomic）"""
    done = Signal(bool, str)
    cookie_ready = Signal(str)      # 登录后导出的 Cookie（B 站 / 小红书用）

    def __init__(self, profile_dir: str, site: str = "douyin", parent=None):
        super().__init__(parent)
        self.profile_dir = profile_dir
        self.site = site

    def run(self):
        from .browser_bridge import (BrowserBridgeError, dump_cookies, login,
                                     login_bilibili, login_instagram,
                                     login_jmcomic, login_x, login_xiaohongshu)
        try:
            if self.site == "instagram":
                ok, msg = login_instagram(self.profile_dir)
            elif self.site == "x":
                ok, msg = login_x(self.profile_dir)
            elif self.site == "bilibili":
                ok, msg = login_bilibili(self.profile_dir)
            elif self.site == "xiaohongshu":
                ok, msg = login_xiaohongshu(self.profile_dir)
            elif self.site == "jmcomic":
                ok, msg = login_jmcomic(self.profile_dir)
            else:
                ok, msg = login(self.profile_dir)
            if ok and self.site in ("bilibili", "xiaohongshu", "jmcomic"):
                # 解析走 HTTP 客户端，需要把浏览器登录态导成 Cookie
                domain, label = {
                    "bilibili": ("bilibili.com", "B 站"),
                    "xiaohongshu": ("xiaohongshu.com", "小红书"),
                    "jmcomic": ("18comic", "禁漫"),
                }[self.site]
                cookie = dump_cookies(self.profile_dir, domain)
                if cookie:
                    self.cookie_ready.emit(cookie)
                    msg = f"登录成功，已保存{label}登录态"
                else:
                    ok = False
                    msg = f"登录成功但未取到 Cookie，请重试"
            self.done.emit(ok, msg)
        except BrowserBridgeError as e:
            log.error("登录窗口异常（%s）：%s", self.site, e)
            self.done.emit(False, str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("登录异常")
            self.done.emit(False, f"登录窗口异常：{e}")


class JmPreviewOpenWorker(QThread):
    """建立禁漫预览会话并取第一页。

    首次打开要连服务器拿本子目录，还要下第一页原图并解码，都在后台做，
    否则界面会白等好几秒。
    """
    opened = Signal(object, int, int, bytes)   # session, 话数, 首话页数, 首页 PNG
    failed = Signal(str)

    def __init__(self, spec: str, cookie: str = "", parent=None):
        super().__init__(parent)
        self.spec = spec
        self.cookie = cookie

    def run(self):
        from .jmcomic_bridge import JmPreview
        try:
            session = JmPreview(self.spec, self.cookie)
            pages = session.page_count(0)
            self.opened.emit(session, session.chapter_count, pages,
                             session.page_bytes(0, 0))
        except Exception as e:  # noqa: BLE001
            log.exception("禁漫预览打开失败")
            self.failed.emit(f"禁漫预览打不开：{e}")


class JmPreviewPageWorker(QThread):
    """取禁漫预览的某一页（必要时先把该话的图片列表拉下来）"""
    loaded = Signal(int, int, int, bytes)   # 话下标, 页下标, 该话页数, PNG
    failed = Signal(str)

    def __init__(self, session, chapter: int, page: int, parent=None):
        super().__init__(parent)
        self.session = session
        self.chapter = chapter
        self.page = page

    def run(self):
        try:
            total = self.session.page_count(self.chapter)
            # 跨话回退时页号是「-1 = 最后一页」的占位（上一话有多少页，
            # 得先把这一话拉下来才知道），这里统一收敛到合法范围。
            page = total - 1 if self.page < 0 else min(self.page, total - 1)
            page = max(0, page)
            self.loaded.emit(self.chapter, page, total,
                             self.session.page_bytes(self.chapter, page))
        except Exception as e:  # noqa: BLE001
            log.exception("禁漫预览取页失败")
            self.failed.emit(f"这一页取不到：{e}")


class CoverWorker(QThread):
    """加载封面图片字节"""
    loaded = Signal(bytes)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            from .downloader import headers_for
            headers = headers_for(self.url)
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " \
                                    "AppleWebKit/537.36 (KHTML, like Gecko) " \
                                    "Chrome/131.0.0.0 Safari/537.36"
            if (self.url or "").startswith("jmcover://"):
                from .jmcomic_bridge import fetch_cover_bytes
                data = fetch_cover_bytes(self.url.split("://", 1)[1])
                if data:
                    self.loaded.emit(data)
                return
            resp = requests.get(self.url, timeout=15, headers=headers)
            if resp.ok and resp.content:
                self.loaded.emit(resp.content)
        except requests.RequestException as e:
            log.debug("封面加载失败：%s（%s）", e, self.url)
