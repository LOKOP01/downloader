# -*- coding: utf-8 -*-
"""抖音分享链接解析核心（参考 VideoData/DY-Data 的去水印/批量下载思路）

解析链路：
1. 从分享文案中提取短链（v.douyin.com / iesdouyin.com / douyin.com）
2. 跟随 302 重定向拿到真实地址与作品 ID（/video/{id}、/note/{id}、modal_id=）
3. 依次尝试多个数据源获取作品详情：
   - iesdouyin web api v2 iteminfo
   - 分享页 HTML（RENDER_DATA / window._ROUTER_DATA）
   - douyin.com web detail api（带 Cookie，部分场景需要）
4. play_addr 中的 playwm 替换为 play 即为无水印地址
"""
import json
import re
import threading
import time
import urllib.parse
from typing import List, Optional, Tuple

import requests

from .models import VideoInfo, clean_cookie

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
PC_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

URL_PATTERN = re.compile(r'https?://[^\s，。"\']+', re.I)


class ParseError(Exception):
    pass


class DouyinParser:
    def __init__(self, cookie: str = "", timeout: int = 15,
                 profile_dir: str = ""):
        self.cookie = cookie.strip()
        self.timeout = timeout
        self.profile_dir = profile_dir or ""
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": PC_UA,
            "Referer": "https://www.douyin.com/",
            "Accept": "*/*",
        })
        # Cookie 里可能含非 Latin-1 字符（昵称类字段），requests 发 header
        # 时按 latin-1 编码会抛 UnicodeEncodeError，必须先清洗
        self.cookie = clean_cookie(self.cookie)
        if self.cookie:
            self.session.headers["Cookie"] = self.cookie

    # ------------------------------------------------------------------ #
    # 链接处理
    # ------------------------------------------------------------------ #
    @staticmethod
    def extract_url(text: str) -> str:
        """从分享文案中提取第一个抖音链接"""
        m = URL_PATTERN.search(text or "")
        if not m:
            raise ParseError("未在文本中检测到链接，请粘贴包含抖音链接的分享文案")
        url = m.group(0).rstrip("。.,;")
        host = urllib.parse.urlparse(url).netloc
        if not any(k in host for k in ("douyin.com", "iesdouyin.com")):
            raise ParseError(f"暂不支持该链接：{host}（目前仅支持抖音）")
        return url

    def resolve(self, url: str) -> str:
        """跟随短链重定向，返回最终 URL"""
        try:
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            return resp.url
        except requests.RequestException as e:
            raise ParseError(f"链接请求失败：{e}")

    @staticmethod
    def parse_ids(url: str) -> Tuple[str, str]:
        """从最终 URL 解析 (类型, 作品ID/用户sec_uid)。类型: video/note/user"""
        # 用户主页：带 sec_uid 查询参数的，或 sec_uid 本身以 MS4 开头（抖音的
        # sec_uid 前缀）。这里必须写成 `m and (...)` —— 原先的
        # `m and A or B` 会被解析成 `(m and A) or B`，B 命中而 m 为 None 时
        # 会在 m.group(1) 上抛 AttributeError（当前靠正则包含关系侥幸不触发）。
        m = re.search(r'/user/([A-Za-z0-9_-]+)', url)
        if m and ("sec_uid" in urllib.parse.urlparse(url).query
                  or m.group(1).startswith("MS4")):
            return "user", m.group(1)
        # modal_id=xxxx
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if q.get("modal_id"):
            return "video", q["modal_id"][0]
        for kind in ("video", "note", "share/video", "share/note"):
            m = re.search(rf'/{kind}/(\d+)', url)
            if m:
                t = "note" if "note" in kind else "video"
                return t, m.group(1)
        m = re.search(r'/user/([A-Za-z0-9_-]+)', url)
        if m:
            return "user", m.group(1)
        raise ParseError("无法从链接中识别作品 ID，请确认链接有效")

    # ------------------------------------------------------------------ #
    # 单作品解析
    # ------------------------------------------------------------------ #
    def parse_video(self, share_text: str) -> VideoInfo:
        url = self.extract_url(share_text)
        # 链接里已经带作品 ID（/video/{id}、/note/{id}、modal_id、/share/video/{id}）
        # 时不必再发一次请求跟随重定向；只有 v.douyin.com 之类的短链才需要 resolve
        try:
            kind, item_id = self.parse_ids(url)
        except ParseError:
            kind, item_id = self.parse_ids(self.resolve(url))
        if kind == "user":
            raise ParseError("检测到用户主页链接，请前往「批量下载」页面使用")

        # 浏览器详情接口（带完整 bit_rate 档位 + 文件大小）与 HTTP 三路并行跑：
        # 它要启动 Edge + 加载页面（约 1.5s），串行会白等 HTTP 那一头（约 0.7s）
        rich_box = {}

        def _browser_try():
            try:
                rich_box["info"] = self._from_browser_detail(item_id)
            except Exception:  # noqa: BLE001 - 补全失败保留 HTTP 结果
                pass

        browser_job = None
        if self.profile_dir:
            browser_job = threading.Thread(target=_browser_try, daemon=True)
            browser_job.start()

        errors = []
        info: Optional[VideoInfo] = None
        # 分享页是当前唯一稳定可用的数据源，先试（把链接类型当提示，先请求
        # 对应类型的分享页，少发一次注定拿不到数据的请求）；iteminfo /
        # detail 接口自 2026 年起被风控门禁（返回 200 空体），留作兜底
        for name, fetcher in (
                ("_from_share_page", lambda: self._from_share_page(item_id, kind)),
                ("_from_iteminfo_api", lambda: self._from_iteminfo_api(item_id)),
                ("_from_detail_api", lambda: self._from_detail_api(item_id))):
            try:
                info = fetcher()
                if info:
                    break
            except Exception as e:  # noqa: BLE001 - 聚合所有尝试的失败原因
                errors.append(f"{name}: {e}")
        if not info:
            # HTTP 三路全军覆没：并行的浏览器详情接口若已拿到数据就直接用
            # （它本来只为补全高清档而启动，顺手当兜底，别白扔）
            if browser_job is not None:
                browser_job.join(timeout=30)
                rich = rich_box.get("info")
                if rich:
                    return rich
            raise ParseError("作品信息获取失败。" + "；".join(errors[-2:]))

        # 分享页/列表接口的 bit_rate 常缺高清档，用真实浏览器的详情接口补全
        if browser_job is not None and not info.is_image:
            browser_job.join(timeout=30)
            rich = rich_box.get("info")
            # 分辨率相同时帧率更高也算"更全"（分享页常只有 1080p30，
            # 浏览器详情接口才给 1080p60 那条）
            if rich and self._quality_key(rich) > self._quality_key(info):
                return rich
        return info

    @staticmethod
    def _quality_key(info: VideoInfo) -> Tuple[int, int]:
        """从清晰度标签取 (最大分辨率, 最大帧率)，用来判断两份解析结果谁更全

        标签形如 `1920×1080 · 3041kbps · 60fps`；分辨率与帧率都只用严格
        匹配的规则解析，避免把 "1080p · 60fps" 里的 60 当成分辨率。
        """
        res, fps = 0, 0
        for label, _ in info.quality_options or []:
            m = re.search(r'(\d{2,5})×(\d{2,5})', label or "")
            if m:
                res = max(res, max(int(m.group(1)), int(m.group(2))))
            else:
                m = re.search(r'(\d{3,4})p\b', label or "")
                if m:
                    res = max(res, int(m.group(1)))
            mf = re.search(r'(\d{2,3})fps', label or "")
            if mf:
                fps = max(fps, int(mf.group(1)))
        return res, fps

    def _from_browser_detail(self, item_id: str) -> Optional[VideoInfo]:
        """浏览器打开作品页 → 拦截 web 详情接口 → 拿到完整 bit_rate 档位"""
        from .browser_bridge import fetch_aweme_detail
        item = fetch_aweme_detail(item_id, self.profile_dir or None)
        if not item:
            raise ParseError("浏览器详情接口无数据")
        return self._build_info(item)

    def _from_iteminfo_api(self, item_id: str) -> Optional[VideoInfo]:
        api = f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={item_id}"
        resp = self.session.get(api, headers={"User-Agent": MOBILE_UA}, timeout=self.timeout)
        data = resp.json()
        items = data.get("item_list") or []
        if not items:
            raise ParseError("iteminfo 接口无数据")
        return self._build_info(items[0])

    def _from_share_page(self, item_id: str, kind: str = "video",
                         attempts: int = 2) -> Optional[VideoInfo]:
        """分享页 HTML 内联 JSON（RENDER_DATA / _ROUTER_DATA）

        ⚠️ 抖音对「冷会话」（还没拿到 ttwid Cookie）经常只回空壳页：HTML 里
        `_ROUTER_DATA` 在、但 loaderData 里没有作品数据，也不给任何错误码，
        表现为随机的「分享页中未找到作品数据」。实测**同一 session 内重试**
        第 2 次起就稳定命中（ttwid 已落盘），所以这里带重试；重试次数不宜多
        （连发请求会触发 Argus 风控，整页变成 2.5KB 的空壳）。

        `kind` 是链接类型（video / note），用来决定先请求哪种分享页。
        """
        pages = ("note", "video") if kind == "note" else ("video", "note")
        for attempt in range(attempts):
            for page in pages:
                url = f"https://www.iesdouyin.com/share/{page}/{item_id}/"
                resp = self.session.get(url, headers={"User-Agent": MOBILE_UA},
                                        timeout=self.timeout)
                data = self._extract_page_json(resp.text)
                if data:
                    item = self._find_item(data)
                    if item:
                        return self._build_info(item)
            if attempt < attempts - 1:
                time.sleep(0.2)
        raise ParseError("分享页中未找到作品数据")

    def _from_detail_api(self, item_id: str) -> Optional[VideoInfo]:
        params = {
            "aweme_id": item_id,
            "aid": "1128",
            "version_name": "23.5.0",
            "device_platform": "android",
            "os_version": "2333",
        }
        api = "https://www.iesdouyin.com/aweme/v1/aweme/detail/"
        resp = self.session.get(api, params=params, headers={"User-Agent": MOBILE_UA},
                                timeout=self.timeout)
        data = resp.json()
        item = data.get("aweme_detail")
        if not item:
            raise ParseError("detail 接口无数据（可能需要在设置中配置 Cookie）")
        return self._build_info(item)

    @staticmethod
    def _extract_page_json(html: str) -> Optional[dict]:
        """从分享页 HTML 提取内嵌 JSON（RENDER_DATA 或 _ROUTER_DATA）"""
        m = re.search(r'<script id="RENDER_DATA" type="application/json">(.*?)</script>',
                      html, re.S)
        if m:
            try:
                return json.loads(urllib.parse.unquote(m.group(1)))
            except (ValueError, TypeError):
                pass
        m = re.search(r'window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>', html, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except (ValueError, TypeError):
                pass
        return None

    @staticmethod
    def _find_item(node, depth: int = 0) -> Optional[dict]:
        """递归在 JSON 树中查找包含 play_addr/images 的作品对象"""
        if depth > 12 or not isinstance(node, (dict, list)):
            return None
        if isinstance(node, dict):
            if ("video" in node and isinstance(node.get("video"), dict)
                    and node["video"].get("play_addr")) or node.get("images"):
                return node
            for key in ("item_list", "itemList", "aweme_list", "aweme_detail"):
                val = node.get(key)
                if isinstance(val, list) and val:
                    found = DouyinParser._find_item(val[0], depth + 1)
                    if found:
                        return found
                elif isinstance(val, dict):
                    found = DouyinParser._find_item(val, depth + 1)
                    if found:
                        return found
            for val in node.values():
                found = DouyinParser._find_item(val, depth + 1)
                if found:
                    return found
        else:
            for val in node:
                found = DouyinParser._find_item(val, depth + 1)
                if found:
                    return found
        return None

    def _build_info(self, item: dict) -> VideoInfo:
        info = VideoInfo()
        info.aweme_id = str(item.get("aweme_id", ""))
        info.title = item.get("desc", "") or ""
        author = item.get("author") or {}
        info.author = author.get("nickname", "")
        info.author_uid = str(author.get("uid", ""))
        info.sec_uid = author.get("sec_uid", "")
        info.create_time = int(item.get("create_time", 0) or 0)
        stats = item.get("statistics") or {}
        info.digg_count = int(stats.get("digg_count", 0) or 0)
        info.comment_count = int(stats.get("comment_count", 0) or 0)
        info.share_count = int(stats.get("share_count", 0) or 0)
        music = item.get("music") or {}
        info.music_title = music.get("title", "")

        video = item.get("video") or {}
        images = item.get("images") or []
        if images:
            info.is_image = True
            for img in images:
                urls = (img.get("url_list") or [])
                if urls:
                    info.image_urls.append(urls[-1] if len(urls) > 1 else urls[0])
            covers = (video.get("cover") or {}).get("url_list") or []
            info.cover_url = covers[0] if covers else (info.image_urls[0] if info.image_urls else "")
        else:
            # 从 bit_rate 列表按码率从高到低生成候选地址（首个为最高画质）
            options, sizes_raw = self._quality_options(video)
            if not options:
                play_addr = video.get("play_addr") or {}
                url_list = play_addr.get("url_list") or []
                options = [("最高画质", url_list[0])] if url_list else []
            options = [(label, u.replace("playwm", "play"))
                       for label, u in options if u]
            info.quality_options = options
            info.play_url_candidates = [u for _, u in options]
            # data_size 是按原始地址记录的，替换 playwm→play 后要同步改键
            info.quality_sizes = {
                u.replace("playwm", "play"): sz
                for u, sz in sizes_raw.items() if u and sz
            }
            info.play_url = options[0][1] if options else ""
            info.quality = options[0][0] if options else ""
            info.raw_play_url = info.play_url
            info.duration = int(video.get("duration", 0) or 0)
            covers = (video.get("origin_cover") or {}).get("url_list") or \
                     (video.get("cover") or {}).get("url_list") or []
            info.cover_url = covers[0] if covers else ""
        if not info.play_url and not info.image_urls:
            raise ParseError("作品数据中无有效下载地址")
        return info

    @staticmethod
    def _quality_options(video: dict) -> Tuple[List[Tuple[str, str]], dict]:
        """从 video.bit_rate 生成按「分辨率 → 帧率 → 码率」降序的 [(画质标签, 地址)] 与 {地址: 字节数}

        抖音的 bit_rate 项自带 data_size（精确字节），直接用于下拉展示。

        ⚠️ **不能只按 bit_rate 排序**：抖音对同一分辨率会给多条码流，其中
        60fps 那条的码率反而更低 —— 实测本作品 1920×1080 有
        `normal_1080_0`（30fps · 3041kbps）和 `adapt_lowest_1080_1`
        （60fps · 1700kbps）两条。只按码率排会默认下到 30fps 那条，
        用户就会觉得"帧率有点低"。所以主序是分辨率，其次帧率，最后码率。
        """
        entries = []
        sizes = {}
        for entry in video.get("bit_rate") or []:
            if not isinstance(entry, dict):
                continue
            try:
                rate = int(entry.get("bit_rate") or 0)
            except (TypeError, ValueError):
                rate = 0
            try:
                fps = int(float(entry.get("FPS") or 0))
            except (TypeError, ValueError):
                fps = 0
            play_addr = entry.get("play_addr") or {}
            urls = play_addr.get("url_list") or []
            if urls:
                width = int(play_addr.get("width") or 0)
                height = int(play_addr.get("height") or 0)
                gear = (entry.get("gear_name") or "").replace("_", " ").strip()
                if height and width:
                    label = f"{width}×{height}"
                elif height:
                    label = f"{height}p"
                elif gear:
                    label = gear
                else:
                    label = f"{rate // 1000}kbps"
                if rate:
                    label += f" · {rate // 1000}kbps"
                if fps:
                    label += f" · {fps}fps"
                entries.append((max(width, height), min(width, height),
                                fps, rate, label, urls[0]))
                try:
                    dsz = int(entry.get("data_size") or 0)
                except (TypeError, ValueError):
                    dsz = 0
                if not dsz:
                    # 部分来源只有 play_addr.data_size
                    try:
                        dsz = int(play_addr.get("data_size") or 0)
                    except (TypeError, ValueError):
                        dsz = 0
                if dsz:
                    sizes[urls[0]] = dsz
        # 主序分辨率、次帧率、末码率（都是越大越好）
        entries.sort(key=lambda it: it[:4], reverse=True)
        options, seen = [], set()
        for _, _, _, _, label, url in entries:
            if url not in seen:
                seen.add(url)
                options.append((label, url))
        return options, sizes

    # ------------------------------------------------------------------ #
    # 用户主页批量作品
    # ------------------------------------------------------------------ #
    def resolve_user(self, share_text: str) -> Tuple[str, str]:
        """从分享文案解析 (sec_uid, 昵称占位)。返回 sec_uid 供批量接口使用"""
        url = self.extract_url(share_text)
        final_url = self.resolve(url)
        kind, value = self.parse_ids(final_url)
        if kind != "user":
            raise ParseError("该链接不是用户主页链接，请在「首页」解析单个作品")
        return value, final_url

    def get_user_videos(self, sec_uid: str, max_cursor: int = 0,
                        count: int = 18) -> Tuple[List[VideoInfo], int, bool, str]:
        """获取用户作品列表，返回 (作品列表, 下一游标, 是否还有更多, 用户昵称)"""
        api = "https://www.iesdouyin.com/web/api/v2/aweme/post/"
        params = {
            "sec_uid": sec_uid,
            "count": count,
            "max_cursor": max_cursor,
            "aid": "1128",
            "_signature": "",
        }
        resp = self.session.get(api, params=params,
                                headers={"User-Agent": MOBILE_UA}, timeout=self.timeout)
        data = resp.json()
        aweme_list = data.get("aweme_list") or []
        nickname = ""
        infos: List[VideoInfo] = []
        for item in aweme_list:
            try:
                info = self._build_info(item)
                nickname = nickname or info.author
                infos.append(info)
            except ParseError:
                continue
        has_more = bool(data.get("has_more"))
        next_cursor = int(data.get("max_cursor", 0) or 0)
        if not infos and max_cursor == 0:
            raise ParseError("未获取到作品列表。若为私密账号或接口受限，"
                             "请在「设置」中填入登录后的 Cookie 再试")
        return infos, next_cursor, has_more, nickname
