# -*- coding: utf-8 -*-
"""E-Hentai（e-hentai.org）图集解析与下载。

站点结构（2026-10-11 实测，见 _scratch_x_probe/_eh_probe*.py）：
  - 画廊页 `/g/<gid>/<token>/` 一页 20 张缩略图，分页参数 `?p=N`
  - 每张缩略图链到图片页 `/s/<10位key>/<gid>-<n>`，图片页里 `<img id="img">`
    的 src 是 H@H CDN 的图片直链 —— **裸下即可，不需要 Referer / Cookie**
  - 图片页上的「Download original」指向 `/fullimg/<gid>/<n>/<key>/<文件名>`，
    **未登录会被 302 到 bounce_login.php**，所以只有登录态才拿得到原图；
    拿不到时退回页面给的压缩版（1280 宽 webp）
  - 元数据走 `/api.php`（POST JSON，`method=gdata`），免登录可用，含
    title / category / uploader / filecount / thumb / rating / tags
  - `exhentai.org` 是同站的「无限制」分支，必须登录（还要 igneous）才看得到

和禁漫一个路子：解析只取画廊元数据与封面，逐张图片地址在下载/预览时按页去取
（一页一张图 = 一次图片页请求 + 一次图片下载）。
"""
from __future__ import annotations

import html as html_mod
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, Tuple

import requests

from .models import VideoInfo, clean_cookie
from .parser import ParseError, PC_UA
from .proxy import working_proxies


def _unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def cleanup_parts(folder: str) -> int:
    """清掉画廊目录里残留的 `*.part`（取消 / 失败后调用），返回清理数量。"""
    n = 0
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    for name in names:
        if name.endswith(".part"):
            _unlink(os.path.join(folder, name))
            n += 1
    return n

EH_HOSTS = {
    "e-hentai.org": "https://e-hentai.org",
    "exhentai.org": "https://exhentai.org",
}

# /g/1234567/abcdef0123/ —— gid 是数字，token 是 10 位十六进制
_GALLERY_RE = re.compile(
    r"(e-hentai\.org|exhentai\.org)/g/(\d+)/([0-9a-f]{6,20})", re.I)
# 图片页 /s/<key>/<gid>-<n>
_PAGE_RE = re.compile(r'href="(https?://[a-z0-9.\-]*hentai\.org/s/[0-9a-f]+/\d+-\d+)"',
                      re.I)
_IMG_RE = re.compile(r'<img[^>]*id="img"[^>]*src="([^"]+)"', re.I)
_FULL_RE = re.compile(r'href="(https?://[^"]*?/fullimg/[^"]+)"', re.I)
# 换节点 token：图片页 onerror="nl('…')" / "Reload broken image" 的 onclick
_NL_RE = re.compile(r"nl\('([^']+)'\)")
# 超出图片浏览限额时，#img 的 src 会变成这张 509 占位图
_LIMIT_URL_RE = re.compile(r'ehgt\.org/g/509\.gif|exhentai\.org/img/509\.gif', re.I)
_LIMIT_RE = re.compile(r'exceeded your image (?:viewing|download) limits', re.I)
# 登录失效时，图片页 / fullimg 会返回一张登录页 HTML（**状态码仍是 200**，
# 不校验内容就会把它当图片存下来 —— 2026-10-11 实测踩过：91 张全是 1.3KB HTML）
_LOGIN_PAGE_RE = re.compile(
    r'<title>E-Hentai\.org Login|bounce_login\.php|act=Login', re.I)
_SHOWING_RE = re.compile(r'Showing\s+([\d,]+)\s*-\s*([\d,]+)\s*of\s*([\d,]+)', re.I)
_UNAVAILABLE_RE = re.compile(r'This image is unavailable|Image Not Found', re.I)

_PER_PAGE_UNKNOWN = 0             # 每页条数要抓一次首页才知道（登录后常见 40）
_EXT_RE = re.compile(r'\.(jpe?g|png|gif|webp)(?:$|[?#])', re.I)
_DOWNLOAD_WORKERS = 6           # 图片页并发：太高会触发站点的带宽限制（509）
_ORIGINAL_WORKERS = 2           # 原图模式的并发（高并发下 H@H 节点失败率陡增）
_ORIGINAL_DELAY = 1.2           # 原图模式每张的额外间隔（防被站点限流弹登录页）
_FETCH_ROUNDS = 3               # 主轮换几轮节点；剩下的交给整单末尾的兜底轮
_SAME_URL_TRIES = 2             # 同一节点偶发握手抖动，重试一次多半就成了


def _cffi():
    """curl_cffi 会话工厂（浏览器 TLS 指纹 + 系统代理）。

    两件事都是实测踩出来的（2026-10-11）：
      1. e-hentai 系（含 H@H 图片节点）**认 TLS 指纹**：普通 requests 会
         `SSLError: UNEXPECTED_EOF_WHILE_READING`，同一台机器上浏览器 /
         curl_cffi 正常。主站与图片节点都得用浏览器指纹（同 hanime1 的处理
         方式）；curl_cffi 已在打包清单里（禁漫也依赖它）。
      2. H@H 节点常带**非标准端口**（`…hath.network:59910`），得走系统代理。
    """
    from curl_cffi import requests as cffi_requests

    session = cffi_requests.Session(impersonate="chrome")
    proxies = working_proxies()
    if proxies:
        session.proxies = proxies
    return session


# 图片下载会话按线程复用（见 EhentaiGallery._stream_to 的说明）
_IMG_TLS = threading.local()


def _img_tls():
    return _IMG_TLS


class DownloadCancelled(Exception):
    """用户取消 e-hentai 下载"""


class NotAnImage(Exception):
    """下回来的不是图片（多半是登录页 / 限额页被当成了图片直链）"""


# 落盘前必须校验文件头。站点有些情况下会用 200 返回一张 HTML 页面
# （登录失效 / 超限额），不校验就会把 1~2 KB 的 HTML 存成 .jpg，
# 用户看到的就是「全是 2KB、预览不出来」。
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a",
                b"BM", b"II*\x00", b"MM\x00*")
_MIN_IMAGE_BYTES = 1024          # 低于这个大小基本不可能是正常的画廊图


def looks_like_image(head: bytes, size: int = 0) -> bool:
    """按文件头判断是不是真图片（RIFF 还要看 8~12 位是不是 WEBP/AVI）。"""
    if size and size < _MIN_IMAGE_BYTES:
        return False
    if not head:
        return False
    if head[:4] == b"RIFF":
        return head[8:12] in (b"WEBP", b"WAVE", b"AVI ")
    return any(head.startswith(magic) for magic in _IMAGE_MAGIC)


def ext_for_type(content_type: str, default: str = ".jpg") -> str:
    """按响应 Content-Type 定扩展名（存错扩展名也会让预览打不开）。"""
    ct = (content_type or "").split(";")[0].strip().lower()
    return {
        "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
        "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp",
        "image/tiff": ".tif",
    }.get(ct, default)


def _maybe_image_head(head: bytes) -> bool:
    """只拿到前 16 字节时的宽松判断（用于写盘途中提前止损）。"""
    if not head:
        return True                      # 还没拿到，不做判断
    if head[:4] == b"RIFF":
        return len(head) < 12 or head[8:12] in (b"WEBP", b"WAVE", b"AVI ")
    if any(head.startswith(m) for m in _IMAGE_MAGIC):
        return True
    if _LOGIN_PAGE_RE.search(head.decode("latin-1", "ignore")):
        return False
    # HTML/文本一律否；其余（还没凑够 16 字节）先放过
    return len(head) < 16 and not head[:1].isspace()


def _bad_content(resp) -> str:
    """给「下回来的不是图片」补一句可读的原因。"""
    ct = (resp.headers.get("content-type") or "").lower()
    url = getattr(resp, "url", "") or ""
    if "text/html" in ct or "forums" in url or "bounce_login" in url:
        return f"服务端返回了登录页（{ct or 'html'}）"
    if "509" in url:
        return "超出图片浏览限额"
    return f"返回的不是图片（{ct or '未知类型'}）"


# --------------------------------------------------------------------- #
# 入口：从文本里认链接
# --------------------------------------------------------------------- #
def try_parse_eh(text: str) -> Tuple[str, str, str]:
    """从分享文案里抠画廊信息，返回 (host, gid, token)；识别失败返回 ("", "", "")。"""
    m = _GALLERY_RE.search(text or "")
    if not m:
        return "", "", ""
    host = m.group(1).lower()
    return host, m.group(2), m.group(3)


def _int(text: str) -> int:
    try:
        return int(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


def _session(cookie: str = ""):
    """建站点会话（浏览器指纹 + 登录态）。站点不吃 Referer。"""
    session = _cffi()
    session.headers.update({
        "User-Agent": PC_UA,
        "Accept-Language": "en-US,en;q=0.9",
    })
    cookie = clean_cookie(cookie or "")
    if cookie:
        session.headers["Cookie"] = cookie
    return session


def _logged_in(cookie: str) -> bool:
    """是否带上了登录态（决定要不要走原图）。"""
    return "ipb_member_id" in (cookie or "")


def _ext_of(url: str, default: str = ".jpg") -> str:
    m = _EXT_RE.search(url or "")
    return "." + m.group(1).lower().replace("jpeg", "jpg") if m else default


class EhentaiGallery:
    """一个画廊的会话：元数据 + 缩略图分页 + 逐张图片地址。

    缩略图分页按需取：`/g/<gid>/<token>/?p=N` 一次给 20 张 `/s/…` 链接，
    拿到后整页缓存，翻到哪一页才请求哪一页。
    """

    def __init__(self, host: str, gid: str, token: str, cookie: str = "",
                 total: int = 0, prefer_original: bool = False):
        self.host = (host or "e-hentai.org").lower()
        if self.host not in EH_HOSTS:
            self.host = "e-hentai.org"
        self.gid = str(gid or "").strip()
        self.token = str(token or "").strip()
        self.cookie = cookie or ""
        self.lock = threading.Lock()
        # 每线程一个会话：下载是多线程的，curl_cffi 的 Session 不是线程安全的
        self._tls = threading.local()
        self.session = _session(self.cookie)      # 主线程 / 解析阶段用
        self._listing: dict = {}        # p -> [(index, page_url)]
        self._per_page = _PER_PAGE_UNKNOWN
        self._total = int(total or 0)
        # 原图通道要「开关打开 + 有登录态」才启用：/fullimg 对没登录的会话
        # 会 302 到登录页，而且它吃站点配额、并发下还更容易失败，所以默认
        # 走图片页给的 1280 宽压缩版（稳定），原图交给设置页开关决定
        self._original_ok = bool(prefer_original) and _logged_in(self.cookie)
        self.login_stale = False         # 运行中发现登录态失效（会降级到压缩版）

    def _http(self):
        """取当前线程的会话（懒建，登录态与主会话一致）。"""
        session = getattr(self._tls, "session", None)
        if session is None:
            session = _session(self.cookie)
            self._tls.session = session
        return session

    # ------------------------------------------------------------------ 地址
    @property
    def base(self) -> str:
        return EH_HOSTS[self.host]

    @property
    def gallery_url(self) -> str:
        return f"{self.base}/g/{self.gid}/{self.token}/"

    @property
    def want_original(self) -> bool:
        return self._original_ok

    def _listing_page(self, p: int):
        """取第 p 页缩略图列表 -> [(页码 1 起, 图片页地址)]，顺带回填总数/每页数。"""
        with self.lock:
            hit = self._listing.get(p)
        if hit is not None:
            return hit
        resp = self._http().get(self.gallery_url, params={"p": p}, timeout=30)
        resp.raise_for_status()
        html = resp.text
        pairs = []
        seen = set()
        for url in _PAGE_RE.findall(html):
            m = re.search(r"-(\d+)$", url)
            if not m:
                continue
            index = int(m.group(1))
            if url in seen:
                continue
            seen.add(url)
            pairs.append((index, url))
        m = _SHOWING_RE.search(html)
        if m:
            self._per_page = max(1, _int(m.group(2)) - _int(m.group(1)) + 1)
            self._total = _int(m.group(3))
        items = sorted(pairs)
        with self.lock:
            self._listing[p] = items
        return items

    def page_count(self) -> int:
        """画廊图片总数（触发一次首页请求，拿到 Showing x - y of N）。"""
        if self._total <= 0:
            self._listing_page(0)
        return self._total

    def set_total(self, total: int) -> None:
        """api.php 已知总数时直接回填，省掉一次首页请求。"""
        if int(total or 0) > 0:
            self._total = int(total)

    def _page_url(self, index: int) -> str:
        """第 index 张（0 起）的图片页地址；翻到新的一页才发请求。"""
        if self.page_count() <= 0:
            raise ParseError("这个画廊没有可下载的图片")
        if index < 0:
            index = 0
        if index >= self._total:
            index = self._total - 1
        if self._per_page <= 0:
            self._listing_page(0)      # 首次抓首页，顺带把每页条数校准
        p = index // self._per_page
        for number, url in self._listing_page(p):
            if number - 1 == index:
                return url
        raise ParseError(f"e-hentai 画廊里找不到第 {index + 1} 张图")

    # ------------------------------------------------------------------ 图片
    def image_url(self, index: int) -> str:
        """第 index 张图的直链。登录态优先原图（/fullimg），否则用页面压缩版。"""
        url, _ = self.image_source(index)
        return url

    def image_source(self, index: int, use_nl: bool = False,
                     nl_token: str = "") -> Tuple[str, str]:
        """取第 index 张图的直链，返回 (直链, 页面里的 nl token)。

        `use_nl=True` 时带 `?nl=<token>` 重取 —— 这是站点自带的**换节点**
        机制（图片页 `ehg_show.c.js` 里 `nl(a)` 就是给当前 URL 追加这个参数
        再刷新）。实测（2026-10-11）：同一张图的图片直链默认固定分配到一个
        H@H 节点，约三成节点连不上；带 nl 重取会换到别的节点，救回率很高。
        随便加随机参数**没用**（站点按 URL 缓存），只有 nl 有效。
        """
        page_url = self._page_url(index)
        if use_nl:
            sep = "&" if "?" in page_url else "?"
            page_url = f"{page_url}{sep}nl={nl_token or '0'}"
        resp = self._http().get(page_url, timeout=30)
        resp.raise_for_status()
        html = resp.text
        # 登录态失效时，图片页本身也可能直接返回登录页（状态码仍是 200）
        if _LOGIN_PAGE_RE.search(html[:4000]):
            if self._original_ok:
                self._original_ok = False      # 降级：后面改用压缩版继续下
                self.login_stale = True
            raise ParseError("E-Hentai 原图通道不可用（服务端返回了登录页，"
                             "一般是登录态失效或站点配额用尽）。本次已改用 "
                             "1280 宽压缩版继续下载；可在「设置」里重新登录")
        m_nl = _NL_RE.search(html)
        token = m_nl.group(1) if m_nl else nl_token
        if self._original_ok:
            m = _FULL_RE.search(html)
            if m:
                return m.group(1), token
            # 登录态失效（Cookie 过期）→ 退回压缩版，别让整单失败
            self._original_ok = False
        m = _IMG_RE.search(html)
        if not m:
            if _LIMIT_RE.search(html) or _LIMIT_URL_RE.search(html):
                raise ParseError("已超出 e-hentai 的图片浏览限额。"
                                 "请到设置里登录 e-hentai 后再试")
            if _UNAVAILABLE_RE.search(html):
                raise ParseError(f"第 {index + 1} 张图已被删除或不可用")
            raise ParseError(f"第 {index + 1} 张图取不到直链（可能被限流，稍后重试）")
        src = html_mod.unescape(m.group(1))
        if _LIMIT_URL_RE.search(src):
            raise ParseError("已超出 e-hentai 的图片浏览限额。"
                             "请到设置里登录 e-hentai 后再试")
        return src, token

    def fetch(self, index: int, folder: str, cancel=None) -> str:
        """下第 index 张图到 folder，返回落盘路径。

        重试策略（都是实测逼出来的）：
          1. 同一节点**偶发**握手失败 —— 同一 URL 直接重试一次就能成；
          2. 节点真的不通 —— 带 `?nl=` 重取图片页换一个节点再来；
          3. 两样都试过还不行才算这张失败（失败的张留给整单末尾统一再试）。
        """
        ext = ""
        path = tmp = ""
        last = ""
        token = ""
        for round_no in range(_FETCH_ROUNDS):
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled()
            try:
                url, token = self.image_source(index, use_nl=round_no > 0,
                                               nl_token=token)
            except ParseError:
                raise                      # 限额 / 图片已删：换节点也没用
            except Exception as e:         # noqa: BLE001 - 图片页本身取不到
                last = f"{type(e).__name__}: {e}"[:120]
                time.sleep(0.4 * (round_no + 1))
                continue
            if not ext:
                ext = _ext_of(url)
                path = os.path.join(folder, f"{index + 1:03d}{ext}")
                tmp = path + ".part"
            for attempt in range(_SAME_URL_TRIES):
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled()
                try:
                    got = self._stream_to(url, tmp, cancel)
                    if got <= 0:
                        raise ValueError("图片返回了空内容")
                    os.replace(tmp, path)
                    return path
                except DownloadCancelled:
                    raise
                except NotAnImage as e:
                    # 下回来的是登录页/限额页：多半是 Cookie 失效了。
                    # 关掉「走原图」这条路，本轮换压缩版继续，别让整单废掉。
                    last = str(e)
                    if tmp:
                        _unlink(tmp)
                    if self._original_ok:
                        self._original_ok = False
                        self.login_stale = True
                    ext = ""          # 扩展名跟着新直链重算（原图 .jpg / 压缩版 .webp）
                    break
                except Exception as e:  # noqa: BLE001
                    last = f"{type(e).__name__}: {e}"[:120]
                    if tmp:
                        _unlink(tmp)
                if attempt + 1 < _SAME_URL_TRIES:
                    time.sleep(0.3)
            if round_no + 1 < _FETCH_ROUNDS:
                time.sleep(0.3)
        raise ValueError(f"第 {index + 1} 张图下载失败（{last}）")

    @staticmethod
    def _stream_to(url: str, tmp: str, cancel=None) -> int:
        """流式落盘；**边写边校验文件头**，不是图片就当失败抛出去。

        不校验的后果很具体：登录态失效时 `/fullimg/` 会用 200 返回一张
        1329 字节的登录页 HTML，`raise_for_status()` 拦不住，于是整本
        下成「91 个 1.3KB 的 .jpg」，用户看到的全是坏文件。
        """
        # 每线程复用一个图片会话：H@H 节点对「短时间大量新建 TLS 连接」很敏感
        # （表现为握手被掐），复用长连接后失败率和耗时都明显下降
        tls = _img_tls()
        session = getattr(tls, "session", None)
        if session is None:
            session = _cffi()
            tls.session = session
        resp = session.get(url, headers={"User-Agent": PC_UA},
                           timeout=(15, 90), stream=True)
        try:
            resp.raise_for_status()
            got = 0
            head = b""
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(64 * 1024):
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled()
                    if not chunk:
                        continue
                    if len(head) < 16:
                        head = (head + chunk)[:16]
                        if not _maybe_image_head(head):
                            raise NotAnImage(_bad_content(resp))
                    f.write(chunk)
                    got += len(chunk)
            if not looks_like_image(head, got):
                raise NotAnImage(_bad_content(resp))
            return got
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass


def _from_spec(spec: str, cookie: str = "",
               prefer_original: bool = False) -> EhentaiGallery:
    """`ehentai://<host>/<gid>/<token>` -> 会话对象。"""
    raw = (spec or "").strip()
    for prefix in ("ehentai://", "https://", "http://"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
            break
    parts = [p for p in raw.split("/") if p]
    if len(parts) < 3:
        raise ParseError(f"无效的 e-hentai 地址：{spec}")
    return EhentaiGallery(parts[0], parts[1], parts[2], cookie,
                          prefer_original=prefer_original)


# --------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------- #
def _gdata(gallery: EhentaiGallery) -> dict:
    """`/api.php` gdata：免登录就能拿到的结构化元数据。"""
    payload = {"method": "gdata",
               "gidlist": [[int(gallery.gid), gallery.token]],
               "namespace": 1}
    resp = gallery.session.post(f"{gallery.base}/api.php", json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("gmetadata") or []
    if not items or not isinstance(items[0], dict):
        raise ParseError(f"e-hentai 画廊 {gallery.gid} 不存在或已下架")
    return items[0]


def parse_ehentai(url: str, cookie: str = "",
                  prefer_original: bool = False) -> VideoInfo:
    """解析 e-hentai 画廊，返回 VideoInfo（is_image=True，逐张下载走本模块）。"""
    host, gid, token = try_parse_eh(url)
    if not gid:
        raise ParseError("未识别到 e-hentai 画廊链接"
                         "（应形如 e-hentai.org/g/1234567/abcdef0123/）")
    if host == "exhentai.org" and not _logged_in(cookie):
        # EX 站没登录会被弹到 Sad Panda 验证页（连 api.php 都不给内容）
        raise ParseError("ExHentai 需要登录才能访问。请到「设置」里登录或填 "
                         "e-hentai 的 Cookie 后再试")
    gallery = EhentaiGallery(host, gid, token, cookie)
    try:
        meta = _gdata(gallery)
    except ParseError:
        raise
    except Exception as e:  # noqa: BLE001
        # 连接类（DNS/TLS/超时）与 JSON 解析都在这里兜住：
        # 该站认 TLS 指纹也吃代理，失败多半是链路问题，提示要直指代理开关
        name = type(e).__name__
        if name == "ValueError":
            raise ParseError("e-hentai 接口返回了非预期的内容，稍后重试") from e
        raise ParseError(f"e-hentai 接口连不上（{name}: {str(e)[:80]}）。"
                         "该站需要代理，请确认 Clash 等代理已开启") from e
    if meta.get("expunged"):
        raise ParseError(f"e-hentai 画廊 {gid} 已被移除（expunged）")

    title = str(meta.get("title") or "").strip() or f"{gid}"
    author = str(meta.get("uploader") or "").strip()
    pages = _int(meta.get("filecount"))
    gallery.set_total(pages)
    category = str(meta.get("category") or "").strip()
    rating = str(meta.get("rating") or "").strip()
    tags = meta.get("tags") or []
    tag_text = []
    for group in ("language", "artist", "female", "male", "other"):
        for t in (tags.get(group) or []) if isinstance(tags, dict) else []:
            tag_text.append(f"{group}:{t}")

    info = VideoInfo()
    info.source = "ehentai"
    info.aweme_id = gid
    info.title = html_mod.unescape(title)
    info.author = html_mod.unescape(author)
    info.cover_url = str(meta.get("thumb") or "").strip()
    info.play_url = f"ehentai://{host}/{gid}/{token}"
    info.is_image = True
    info.create_time = _int(meta.get("posted"))
    info.music_title = " / ".join(tag_text[:4])
    info.extra = {
        "pages": str(pages),
        "category": category,
        "rating": rating,
        "original": "1" if (prefer_original and _logged_in(cookie)) else "0",
    }
    return info


# --------------------------------------------------------------------- #
# 预览会话
# --------------------------------------------------------------------- #
class EhentaiPreview:
    """预览用会话：翻到第 n 张时按需取直链并把图片读成字节。"""

    def __init__(self, spec: str, cookie: str = "",
                 prefer_original: bool = False):
        self.gallery = _from_spec(spec, cookie, prefer_original)
        self.page_count = self.gallery.page_count()

    @property
    def original(self) -> bool:
        return self.gallery.want_original

    def page_bytes(self, index: int) -> bytes:
        """取第 index 张（0 起）的图片字节（同 fetch 的重试策略，轮数更多）。

        预览是「一张张翻」，某一张卡在不通的节点上不该把整个预览废掉，
        所以这里比下载多换几轮节点；实在不行抛错，由界面提示翻下一页。
        """
        last = ""
        token = ""
        for round_no in range(_FETCH_ROUNDS + 3):
            try:
                url, token = self.gallery.image_source(
                    index, use_nl=round_no > 0, nl_token=token)
            except ParseError:
                raise
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"[:120]
                continue
            for _ in range(_SAME_URL_TRIES):
                try:
                    tls = _img_tls()
                    session = getattr(tls, "session", None)
                    if session is None:
                        session = _cffi()
                        tls.session = session
                    resp = session.get(url, headers={"User-Agent": PC_UA},
                                       timeout=(15, 90))
                    resp.raise_for_status()
                    data = resp.content
                    if not looks_like_image(data[:16], len(data)):
                        # 同 fetch：登录页/限额页不能当图片显示，顺手降级
                        if self.gallery._original_ok:
                            self.gallery._original_ok = False
                            self.gallery.login_stale = True
                        last = _bad_content(resp)
                        break
                    return data
                except Exception as e:  # noqa: BLE001
                    last = f"{type(e).__name__}: {e}"[:120]
                    time.sleep(0.3)
        raise ParseError(f"这张图所在的 H@H 节点连不上（{last[:60]}），"
                         "可点「下一张」跳过")


# --------------------------------------------------------------------- #
# 下载
# --------------------------------------------------------------------- #
def download_ehentai(spec: str, save_dir: str, cookie: str = "",
                     cancel: Optional[threading.Event] = None,
                     progress_cb: Optional[Callable[[int, int], None]] = None,
                     limit: int = 0,
                     on_notice: Optional[Callable[[str], None]] = None,
                     prefer_original: bool = False) -> str:
    """下载整个画廊。spec 形如 ehentai://e-hentai.org/1234567/abcdef0123。

    `limit` > 0 时只下前 limit 张（调试 / 试水用）。
    `on_notice` 用于把「登录态失效已降级」这类中途提示递回界面。
    """
    gallery = _from_spec(spec, cookie, prefer_original)
    total = gallery.page_count()
    if limit and limit > 0:
        total = min(total, int(limit))
    if total <= 0:
        raise ValueError("这个画廊没有可下载的图片")
    os.makedirs(save_dir, exist_ok=True)

    state = {"done": 0, "total": total}
    lock = threading.Lock()
    failed: dict = {}
    noticed = {"stale": False}

    def worker(index: int, report: bool = True):
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled()
        try:
            if gallery.want_original:
                # 原图通道对连续请求很敏感：实测并发下几乎必被弹回登录页，
                # 串行 + 间隔才有 decent 成功率，所以这一路单独限速
                time.sleep(_ORIGINAL_DELAY)
            gallery.fetch(index, save_dir, cancel)
            with lock:
                failed.pop(index, None)
        except DownloadCancelled:
            raise
        except Exception as e:  # noqa: BLE001 - 单张失败不该拖垮整单
            with lock:
                failed[index] = str(e)[:120]
        finally:
            if gallery.login_stale and not noticed["stale"]:
                noticed["stale"] = True
                if on_notice:
                    on_notice("E-Hentai 原图通道不可用（登录态失效或站点配额用尽），"
                              "已自动改用 1280 宽压缩版继续下载；"
                              "要原图请到「设置」重新登录")
            if report:
                with lock:
                    state["done"] += 1
                    if progress_cb:
                        progress_cb(state["done"], total)

    def run_pool(indices, workers, report=True):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # 图片页地址来自列表页，边下边补，避免一次性把上千张都塞进队列
            futures = []
            for index in indices[:workers * 2]:
                futures.append(pool.submit(worker, index, report))
            pos = workers * 2
            while futures:
                done = [f for f in futures if f.done()]
                futures = [f for f in futures if not f.done()]
                for f in done:
                    f.result()          # 让取消异常冒出来
                while pos < len(indices) and len(futures) < workers:
                    futures.append(pool.submit(worker, indices[pos], report))
                    pos += 1

    # 原图走 H@H 的另一套分配，并发高时失败率明显上升，降一档更稳
    workers = _ORIGINAL_WORKERS if gallery.want_original else _DOWNLOAD_WORKERS
    run_pool(list(range(total)), max(1, min(workers, total)))
    if cancel is not None and cancel.is_set():
        raise DownloadCancelled()

    # 兜底：H@H 节点会随时间上下线，隔一会儿再来一遍往往就通了（低并发，
    # 免得刚换的节点又被自己打爆；这一轮不再重复报进度）
    retry = sorted(failed)
    if retry:
        time.sleep(2.0)
        run_pool(retry, 2, report=False)

    if failed:
        first = failed[min(failed)]
        detail = f"：{first}" if len(failed) == 1 else "，可稍后重试该画廊"
        raise ValueError(f"{len(failed)}/{total} 张图片下载失败{detail}")
    return save_dir
