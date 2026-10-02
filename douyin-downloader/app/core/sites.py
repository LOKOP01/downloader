# -*- coding: utf-8 -*-
"""多平台单作品解析路由：抖音 / X / Instagram / Bilibili / 小红书 / Iwara / Pornhub / hanime1 / 禁漫

- 抖音：走 parser.DouyinParser（分享页 RENDER_DATA，最高码率）
- X：走 syndication 公开接口（免登录），variants 中挑最高码率 mp4
- Instagram：媒体 info 接口 + HTML 内嵌（严格校验 shortcode，避免串号）
- Bilibili：wbi 签名 API + DASH 合并
- 小红书：浏览器渲染后解析 window.__INITIAL_STATE__ 的
  feed.undertakeNote.items[0].noteCard（视频/图集均走 SSR JSON，免登录可用）
- Iwara：纯 HTTP —— /video/<id> 拿详情，带 X-Version 签名取档位表（Source 原始档）
- Pornhub：纯 HTTP —— 页面 flashvars 里拿 get_media 签名端点，换到 4 档 mp4 直链
- hanime1：纯 HTTP + curl_cffi 指纹（站点认 TLS，requests 会 SSL EOF）——
  页面 <video> 里直接挂 3 档 <source>，换到 hembed CDN 的签名 mp4 直链
"""
import hashlib
import json
import re
import threading
import time
import urllib.parse
from typing import List, Optional, Tuple

import requests

from .applog import get_logger
from .domain import platform_of_host, referer_for
from .models import VideoInfo, clean_cookie
from .parser import DouyinParser, ParseError, PC_UA
from .site_ctx import SiteContext, default_site_context

__all__ = ["SiteContext", "default_site_context", "detect_platform",
           "extract_url", "parse_share"]

URL_PATTERN = re.compile(r'https?://[^\s，。"\']+', re.I)


def extract_url(text: str) -> str:
    m = URL_PATTERN.search(text or "")
    if not m:
        raise ParseError("未在文本中检测到链接，请粘贴分享文案或链接")
    return m.group(0).rstrip("。.,;")


def detect_platform(url: str) -> str:
    platform = platform_of_host(url)
    if platform:
        return platform
    from .jmcomic_bridge import try_parse_jm
    kind, jmid = try_parse_jm(url or "")
    if jmid:
        return "jmcomic"
    raise ParseError(
        f"暂不支持该链接（{urllib.parse.urlparse(url).netloc}）。目前支持 "
        "抖音 / X / Instagram / B站 / 小红书 / Iwara / Pornhub / hanime1 / 禁漫")


def _find_key_recursive(node, key, depth: int = 0):
    """在嵌套 JSON 中递归查找某个键的值"""
    if depth > 14:
        return None
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            found = _find_key_recursive(v, key, depth + 1)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_key_recursive(v, key, depth + 1)
            if found is not None:
                return found
    return None


def parse_share(text: str, ctx: SiteContext = None, **legacy) -> VideoInfo:
    """按链接平台分发解析（成功记一条 info，失败记 error + 堆栈）

    `ctx` 为 SiteContext（推荐）；也兼容旧的按字段传参写法
    （cookie=... / x_cookie=... 等），便于外部脚本继续调用。
    """
    log = get_logger("parse")
    brief = " ".join((text or "").split())[:160]
    t0 = time.time()
    try:
        info = _parse_share(text, ctx, **legacy)
    except Exception as e:  # noqa: BLE001 —— 记完日志再抛给界面
        log.error("解析失败（%.1fs）：%s | 输入=%s",
                  time.time() - t0, e, brief, exc_info=True)
        raise
    log.info("解析成功 %s「%s」用时 %.2fs | 画质 %d 档 | 输入=%s",
             info.source, (info.title or "")[:40], time.time() - t0,
             len(info.quality_options or []), brief)
    return info


def _parse_share(text: str, ctx: SiteContext = None, **legacy) -> VideoInfo:
    """真正的分发逻辑（说明见 parse_share）"""
    if ctx is None:
        ctx = SiteContext(**{k: v for k, v in legacy.items() if v is not None})
    elif legacy:
        ctx = ctx.replaced(**legacy)
    from .jmcomic_bridge import parse_jmcomic, try_parse_jm

    url = ""
    m = URL_PATTERN.search(text or "")
    if m:
        url = m.group(0).rstrip("。.,;")
    if url:
        platform = platform_of_host(url)
        if platform == "jmcomic":
            return parse_jmcomic(url, ctx.jm_cookie)
        if platform == "douyin":
            return DouyinParser(ctx.cookie, profile_dir=ctx.dy_profile_dir).parse_video(url)
        if platform == "x":
            return parse_x(url, ctx.x_cookie, ctx.x_profile_dir)
        if platform == "bilibili":
            return parse_bilibili(url, ctx.bili_cookie, ctx.base_dir)
        if platform == "xiaohongshu":
            return parse_xiaohongshu(url, ctx.xhs_cookie, ctx.xhs_profile_dir)
        if platform == "instagram":
            return parse_instagram(url, ctx.ins_cookie, ctx.ins_profile_dir)
        if platform == "iwara":
            return parse_iwara(url)
        if platform == "pornhub":
            return parse_pornhub(url)
        if platform == "hanime1":
            return parse_hanime1(url)
        # 未知域名但路径像禁漫 /album/ /photo/
        kind, jmid = try_parse_jm(url)
        if jmid:
            return parse_jmcomic(url, ctx.jm_cookie)
        raise ParseError(
            f"暂不支持该链接（{urllib.parse.urlparse(url).netloc}）。目前支持 "
            "抖音 / X / Instagram / B站 / 小红书 / Iwara / Pornhub / hanime1 / 禁漫")
    kind, jmid = try_parse_jm(text)
    if jmid:
        return parse_jmcomic(text, ctx.jm_cookie)
    raise ParseError("未在文本中检测到链接，请粘贴分享文案、链接或禁漫车号（JM123）")


# --------------------------------------------------------------------- #
# Bilibili
# --------------------------------------------------------------------- #
BILI_API = "https://api.bilibili.com"

# WBI 签名用的重排表（B 站前端固定值）
_BILI_MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
                   27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
                   37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
                   22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]
_bili_wbi_cache = {"keys": None, "ts": 0}


def _bili_headers(cookie: str = "") -> dict:
    headers = {
        "User-Agent": PC_UA,
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
        "Accept": "application/json, text/plain, */*",
    }
    if cookie:
        headers["Cookie"] = clean_cookie(cookie)
    return headers


def _bili_get(path: str, params: dict, cookie: str = "",
              signed: bool = False) -> dict:
    if signed:
        params = _bili_sign_params(params, cookie)
    try:
        resp = requests.get(BILI_API + path, params=params,
                            headers=_bili_headers(cookie), timeout=15)
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        raise ParseError(f"B 站接口请求失败：{e}")
    if data.get("code") != 0:
        raise ParseError(f"B 站接口返回：{data.get('message') or data.get('code')}")
    return data.get("data") or {}


def _bili_wbi_keys(cookie: str = "") -> Optional[Tuple[str, str]]:
    """取 wbi 签名密钥（来自 nav 接口，缓存 1 小时）"""
    now = time.time()
    if _bili_wbi_cache["keys"] and now - _bili_wbi_cache["ts"] < 3600:
        return _bili_wbi_cache["keys"]
    try:
        data = _bili_get("/x/web-interface/nav", {}, cookie)
        wbi = data.get("wbi_img") or {}
        img = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
        sub = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
        if img and sub:
            _bili_wbi_cache["keys"] = (img, sub)
            _bili_wbi_cache["ts"] = now
            return img, sub
    except ParseError:
        pass
    return None


def _bili_sign_params(params: dict, cookie: str = "") -> dict:
    """给请求参数加 wts + w_rid（B 站 wbi 签名），否则拿不到完整清晰度"""
    keys = _bili_wbi_keys(cookie)
    if not keys:
        return params
    img_key, sub_key = keys
    raw = img_key + sub_key
    mixin = "".join(raw[i] for i in _BILI_MIXIN_TAB)[:32]

    signed = dict(params)
    signed["wts"] = int(time.time())
    items = sorted(signed.items())
    query = urllib.parse.urlencode([
        (k, "".join(ch for ch in str(v) if ch not in "!'()*")) for k, v in items])
    signed["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return signed


def _bili_play_url(bvid: str, cid: int, qn: int, cookie: str) -> str:
    """按指定清晰度取 durl 地址（fnval=1 → 音视频合一的 mp4，无需 ffmpeg 合并）"""
    url, _sz, _alts = _bili_play_url_size(bvid, cid, qn, cookie)
    return url


def _bili_play_url_size(bvid: str, cid: int, qn: int,
                        cookie: str) -> Tuple[str, int, List[str]]:
    """按清晰度取 (durl 地址, 字节数, 备用地址)。durl 项自带精确 size"""
    data = _bili_get("/x/player/wbi/playurl", {
        "bvid": bvid, "cid": cid, "qn": qn, "fnval": 1, "fourk": 1,
        "platform": "pc", "otype": "json", "high_quality": 1,
    }, cookie, signed=True)
    durl = data.get("durl") or []
    if not durl:
        return "", 0, []
    try:
        sz = int(durl[0].get("size") or 0)
    except (TypeError, ValueError):
        sz = 0
    url = durl[0].get("url", "") or ""
    return url, sz, _bili_backup_urls(durl[0], url)


def _bili_stream_urls(stream: dict) -> List[str]:
    """一路流（视频 / 音频）的全部候选地址：baseUrl 在前，backupUrl 依次跟上

    实测 baseUrl 指向的 PCDN 节点（cn-jsnj-fx-*）经常直接 TLS 重置
    （SSL EOF）或连不上，而 backupUrl 里的 upos-* 镜像一直稳定（9~10 MB/s）。
    只认 baseUrl 时主地址一挂整单就卡住，两个都得留着。
    """
    out: List[str] = []
    for key in ("baseUrl", "base_url"):
        if stream.get(key):
            out.append(stream[key])
    out.extend(_bili_backup_urls(stream))
    uniq: List[str] = []
    for u in out:
        if u and u not in uniq:
            uniq.append(u)
    return uniq


def _bili_backup_urls(item: dict, skip: str = "") -> List[str]:
    """取一项流里的 backupUrl 列表（去掉与主地址相同的）"""
    out: List[str] = []
    for key in ("backupUrl", "backup_url"):
        for u in (item.get(key) or []):
            if u and u != skip and u not in out:
                out.append(u)
    return out


def _bili_fps(stream: dict) -> int:
    """B 站 DASH 视频流里的帧率；拿不到返回 0

    `frameRate` / `frame_rate` 既有 `"60"` 这种整数，也有 `"16000/672"`
    这种分数（23.976 / 29.97 / 59.94 的真实写法），分数要先除再取整。
    拿到帧率就能把它拼进画质标签，下拉里「有 60fps 就不显示 30fps」的规则
    才认得出来（B 站清晰度描述本身只有 `1080P 高码率` 这种，不带帧率）。
    """
    for key in ("frameRate", "frame_rate"):
        raw = stream.get(key)
        if raw in (None, ""):
            continue
        text = str(raw).strip()
        try:
            value = (float(text.split("/")[0]) / float(text.split("/")[1])
                     if "/" in text else float(text))
        except (ValueError, ZeroDivisionError, IndexError):
            continue
        if value > 0:
            return int(round(value))
    return 0


def parse_bilibili(url: str, cookie: str = "",
                   base_dir: str = "") -> VideoInfo:
    """解析 B 站视频（BV/av 号，支持分 P），默认最高可获得清晰度

    有 ffmpeg 时走 DASH（可达 1080P/1080P+，视频音频分离后合并）；
    否则走 durl（音视频已合并的 mp4，最高 720P）。
    """
    # 短链还原
    if "b23.tv" in urllib.parse.urlparse(url).netloc.lower():
        try:
            resp = requests.get(url, headers=_bili_headers(),
                                timeout=15, allow_redirects=True)
            url = resp.url
        except requests.RequestException as e:
            raise ParseError(f"短链解析失败：{e}")

    params = {}
    m = re.search(r'/(BV[0-9A-Za-z]{10})', url)
    if m:
        params["bvid"] = m.group(1)
    else:
        m = re.search(r'/av(\d+)', url, re.I)
        if not m:
            raise ParseError("无法从链接中识别 B 站视频号（BV/av）")
        params["aid"] = m.group(1)

    view = _bili_get("/x/web-interface/view", params, cookie)
    bvid = view.get("bvid") or params.get("bvid") or ""
    if not bvid:
        raise ParseError("B 站接口未返回视频信息")

    pages = view.get("pages") or []
    if not pages:
        raise ParseError("该视频没有可用分 P")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    try:
        pno = int((q.get("p") or ["1"])[0])
    except (TypeError, ValueError):
        pno = 1
    if not 1 <= pno <= len(pages):
        pno = 1
    page = pages[pno - 1]
    cid = page.get("cid") or view.get("cid")

    owner = view.get("owner") or {}
    stat = view.get("stat") or {}
    info = VideoInfo()
    info.source = "bilibili"
    info.aweme_id = bvid
    part = page.get("part") or ""
    info.title = view.get("title") or ""
    if len(pages) > 1:
        info.title = f"{info.title} P{pno}{(' ' + part) if part else ''}"
    info.author = owner.get("name") or ""
    info.cover_url = view.get("pic") or ""
    info.duration = int((page.get("duration") or view.get("duration") or 0)) * 1000
    info.digg_count = int(stat.get("like") or 0)
    info.comment_count = int(stat.get("reply") or 0)
    info.share_count = int(stat.get("share") or 0)

    # ---- 优先 DASH（有 ffmpeg 才能合并出 1080P+）----
    from .ffmpeg import ffmpeg_path
    if ffmpeg_path(base_dir):
        try:
            dash = _bili_get("/x/player/wbi/playurl", {
                "bvid": bvid, "cid": cid, "qn": 127, "fnval": 4048, "fourk": 1,
                "platform": "pc", "otype": "json", "high_quality": 1,
            }, cookie, signed=True)
            d = dash.get("dash") or {}
            vstreams = d.get("video") or []
            astreams = d.get("audio") or []
            if vstreams and astreams:
                def _bw(x):
                    try:
                        return int(x.get("bandwidth") or 0)
                    except (TypeError, ValueError):
                        return 0
                astream = max(astreams, key=_bw)
                a_cands = _bili_stream_urls(astream)
                audio_url = a_cands[0] if a_cands else ""
                audio_bw = _bw(astream)
                qmap = dict(zip(dash.get("accept_quality") or [],
                                dash.get("accept_description") or []))
                options, audio_map, sizes = [], {}, {}
                url_backups, audio_backups = {}, {}
                if len(a_cands) > 1:
                    audio_backups[audio_url] = a_cands[1:]
                for qid in sorted(qmap.keys(), reverse=True):
                    cands = [v for v in vstreams
                             if int(v.get("id") or 0) == int(qid)]
                    if not cands:
                        continue
                    # 同档位优先 AVC（兼容性最好），再按码率取高
                    avc = [v for v in cands if "avc1" in (v.get("codecs") or "")]
                    pick = max(avc or cands, key=_bw)
                    v_cands = _bili_stream_urls(pick)
                    vurl = v_cands[0] if v_cands else ""
                    if not vurl:
                        continue
                    # baseUrl 挂在 PCDN 节点上，挂了就换 backupUrl 的镜像
                    if len(v_cands) > 1:
                        url_backups[vurl] = v_cands[1:]
                    label = f"{qmap.get(qid) or qid} {pick.get('width')}×{pick.get('height')}"
                    # 帧率拼进标签：B 站清晰度描述（1080P 高码率）本身不带帧率，
                    # 只有 DASH 流里才有，下拉的「有 60fps 就不显示 30fps」靠它
                    fps = _bili_fps(pick)
                    if fps:
                        label += f" · {fps}fps"
                    if int(qid) == max(qmap.keys()):
                        label += "（最高）"
                    options.append((label, vurl))
                    # DASH 无字节数，用 (视频+音频) 码率 × 时长估算大小
                    vbw = _bw(pick)
                    if info.duration > 0 and vbw > 0:
                        est = (vbw + audio_bw) * (info.duration / 1000.0) / 8.0
                        if est > 0:
                            sizes[vurl] = int(est)
                    if audio_url:
                        audio_map[vurl] = audio_url
                if options:
                    info.quality_options = options
                    info.quality_sizes = sizes
                    info.play_url_candidates = [u for _, u in options]
                    info.play_url = options[0][1]
                    info.quality = options[0][0]
                    info.audio_map = audio_map
                    info.url_backups = url_backups
                    info.audio_backups = audio_backups
                    return info
        except ParseError:
            pass  # DASH 失败则回退 durl

    # ---- 回退：durl（音视频合并 mp4，最高 720P）----
    play = _bili_get("/x/player/wbi/playurl", {
        "bvid": bvid, "cid": cid, "qn": 127, "fnval": 1, "fourk": 1,
        "platform": "pc", "otype": "json", "high_quality": 1,
    }, cookie, signed=True)
    qmap = dict(zip(play.get("accept_quality") or [],
                    play.get("accept_description") or []))
    current_qn = int(play.get("quality") or 0)
    durl = play.get("durl") or []
    if not durl:
        raise ParseError("未获取到播放地址，该视频可能需要登录或为付费内容")

    options = []
    sizes = {}
    url_backups = {}
    for qn in sorted(qmap.keys(), reverse=True):
        qn = int(qn)
        try:
            if qn == current_qn and durl:
                # 当前档位直接用响应里的 durl，含精确 size
                u = durl[0].get("url", "") or ""
                alts = _bili_backup_urls(durl[0], u)
                try:
                    dsz = int(durl[0].get("size") or 0)
                except (TypeError, ValueError):
                    dsz = 0
            else:
                u, dsz, alts = _bili_play_url_size(bvid, cid, qn, cookie)
        except ParseError:
            continue
        if u:
            label = qmap.get(qn) or f"{qn}"
            if qn == max(qmap.keys(), default=0):
                label += "（最高）"
            options.append((label, u))
            if alts:
                url_backups[u] = alts
            if dsz:
                sizes[u] = dsz
    if not options:
        raise ParseError("未能获取可用清晰度，请确认 B 站登录态是否有效")

    info.quality_options = options
    info.quality_sizes = sizes
    info.play_url_candidates = [u for _, u in options]
    info.play_url = options[0][1]
    info.quality = options[0][0]
    info.url_backups = url_backups
    return info


# --------------------------------------------------------------------- #
# X (Twitter)
# --------------------------------------------------------------------- #
TW_BEARER = ("AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
             "=1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")
# 与 x.com 前端同步的 GraphQL 端点（queryId 会随 X 前端更新轮换）
# 轮换后旧 id 会返回 HTTP 400 / 404；下方 _refresh_gql_query_id() 会在
# 失败时自己从页面流量里抓新的 id 并缓存，所以不必每次手动改这里。
TW_GQL_ENDPOINT = ("https://api.x.com/graphql/"
                   "Xl0tsHf4AzflMRjbw9e70A/TweetResultByRestId")

# queryId 候选表（官方前端历史上用过的值）。
# 轮换后逐个用纯 HTTP 试，全部失败才去开无头浏览器现场抓 —— 浏览器
# 一次要 3~5 秒，是 X 解析里最慢的一环。
TW_GQL_QUERY_IDS = (
    "Xl0tsHf4AzflMRjbw9e70A",   # 2025-xx 前端在用
    "2ICDjqPd81tulZcYrtpTuQ",   # 旧值（保留作兜底，轮换初期可能仍有效）
    "nBS-WpgA6ZG0CyNHD517JQ",
    "xOhkmRac04YFZmOzU9PJHg",
)

# 运行时捕获到的新 queryId（模块级缓存，避免每次都去开浏览器）
_GQL_QUERY_ID_CACHE = {"id": ""}

_GQL_QUERY = {
    "variables": {
        "tweetId": "",
        "withCommunity": False,
        "includePromotedContent": False,
        "withVoice": False,
    },
    "features": {
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "tweetypie_unmention_optimization_enabled": True,
        "responsive_web_edit_tweet_api_enabled": True,
        "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
        "view_counts_everywhere_api_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "responsive_web_twitter_article_tweet_consumption_enabled": False,
        "tweet_awards_web_tipping_enabled": False,
        "freedom_of_speech_not_reach_fetch_enabled": True,
        "standardized_nudges_misinfo": True,
        "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
        "longform_notetweets_rich_text_read_enabled": True,
        "longform_notetweets_inline_media_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "responsive_web_media_download_video_enabled": False,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "responsive_web_enhance_cards_enabled": False,
    },
    "fieldToggles": {"withArticleRichContentState": False},
}


def _gql_endpoint(query_id: str = "") -> str:
    """拼出 GraphQL 端点地址（query_id 为空时用模块默认值）"""
    qid = query_id or _GQL_QUERY_ID_CACHE.get("id") or ""
    if not qid:
        # 从 TW_GQL_ENDPOINT 里把 id 抠出来
        parts = TW_GQL_ENDPOINT.rstrip("/").split("/")
        qid = parts[-2] if len(parts) >= 2 else ""
    return f"https://api.x.com/graphql/{qid}/TweetResultByRestId"


def _refresh_gql_query_id(profile_dir: str, tweet_id: str) -> str:
    """开一次无头浏览器，从页面真实流量里抓取当前有效的 GraphQL queryId

    X 会不定期轮换 queryId，写在代码里的常量一旦过期，接口就返回
    HTTP 400（`{"errors":[{"message":"Bad Request"}]}`）或 404，表现为
    "Cookie 可能已过期"这种误导性报错。这里直接从浏览器实际发出的
    `/i/api/graphql/<id>/TweetResultByRestId` 请求里取 id，并缓存后续复用。
    """
    if not profile_dir:
        return ""
    cached = _GQL_QUERY_ID_CACHE.get("id") or ""
    if cached:
        return cached
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""
    found = {}
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                profile_dir, channel="msedge", headless=True,
                args=["--disable-blink-features=AutomationControlled"])
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()

                def on_resp(resp):
                    u = resp.url or ""
                    if "/graphql/" in u and "TweetResultByRestId" in u:
                        seg = u.split("/graphql/", 1)[1].split("/")
                        if len(seg) >= 2 and seg[0]:
                            found.setdefault("id", seg[0])

                page.on("response", on_resp)
                try:
                    page.goto(f"https://x.com/i/status/{tweet_id}",
                              wait_until="domcontentloaded", timeout=45000)
                    # 页面自己会发 GraphQL 请求，轮询到即走（原先固定等 8s）
                    waited = 0
                    while not found and waited < 8000:
                        page.wait_for_timeout(200)
                        waited += 200
                except Exception:  # noqa: BLE001 - 拿不到就算了
                    pass
            finally:
                ctx.close()
    except Exception:  # noqa: BLE001
        return ""
    qid = found.get("id") or ""
    if qid:
        _GQL_QUERY_ID_CACHE["id"] = qid
    return qid


def _syndication_token(twid: str) -> str:
    """syndication 接口 token：((id/1e15)*PI).toString(36) 去掉 0 和 ."""
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    value = (int(twid) / 1e15) * 3.141592653589793
    int_part = int(value)
    frac = value - int_part
    s = ""
    n = int_part
    if n == 0:
        s = "0"
    while n > 0:
        s = digits[n % 36] + s
        n //= 36
    if frac > 0:
        s += "."
        for _ in range(20):
            frac *= 36
            d = int(frac)
            s += digits[d]
            frac -= d
            if frac <= 0:
                break
    return s.replace("0", "").replace(".", "")


def _x_quality_label(url: str, bitrate: int) -> str:
    """从 twimg 地址路径中解析分辨率，拼成画质标签"""
    m = re.search(r'/(\d{2,5})x(\d{2,5})/', url)
    label = f"{m.group(1)}×{m.group(2)}" if m else ""
    if bitrate:
        label = f"{label} · {bitrate // 1000}kbps" if label else f"{bitrate // 1000}kbps"
    return label or "视频"


def _x_clean_og_title(text: str) -> str:
    """清掉 X 页面标题里的噪声前缀与尾部附着内容

    浏览器标题通常是 `(2) X 上的 作者："正文" / X` 这种形状：
    - 开头的 `(2)` 是未读通知数，每个人不一样，且会变
    - `X 上的 <昵称>：` 是站点的标题模板
    - 正文里还会粘着推文自身的 t.co 短链（跟正文重复）
    """
    if not text:
        return ""
    s = text.strip()
    # 先剥掉外层成对的引号（og 描述里正文被引号包着）
    for _ in range(2):
        s = s.strip()
        if len(s) >= 2 and (s[0], s[-1]) in (('"', '"'), ("\u201c", "\u201d"),
                                            ("'", "'")):
            s = s[1:-1]
    # 去掉开头的未读计数 "(2) " / "[2] "
    s = re.sub(r'^[\(\[]\d+[\)\]]\s*', "", s)
    # 去掉 "X 上的 <作者>：" / "<作者> on X: " 模板前缀
    s = re.sub(r'^X\s*上的\s*[^：:]{0,40}[：:]\s*', "", s)
    s = re.sub(r'^.{0,40}\s+on\s+X\s*[:：]\s*', "", s, flags=re.I)
    # 去掉尾部的 " / X"
    s = re.sub(r'\s*/\s*X\s*$', "", s)
    # 去掉正文里粘着的 t.co 短链（与正文重复）：扫描到链接就截断
    s = re.split(r'\s*https?://t\.co/\w+', s)[0]
    return s.strip().strip('"“”').strip()


def _tw_build_info(status: dict, user_legacy: dict, tweet_id: str) -> VideoInfo:
    """从 legacy 风格 tweet 对象构建 VideoInfo"""
    info = VideoInfo()
    info.source = "x"
    info.aweme_id = tweet_id
    info.title = _x_clean_og_title(
        status.get("full_text") or status.get("text") or "")
    info.author = (user_legacy.get("name")
                   or user_legacy.get("screen_name") or "")
    info.digg_count = int(status.get("favorite_count", 0) or 0)
    info.comment_count = int(status.get("reply_count", 0) or 0)

    entities = status.get("extended_entities") or status.get("entities") or {}
    for media in entities.get("media") or []:
        vinfo = media.get("video_info") or {}
        variants = vinfo.get("variants") or []
        if variants:
            mp4s = [(int(v.get("bitrate") or 0), v["url"])
                    for v in variants
                    if (v.get("content_type") or "").startswith("video/mp4")
                    and v.get("url")]
            if mp4s:
                mp4s.sort(key=lambda x: x[0], reverse=True)
                dur_ms = int(vinfo.get("duration_millis", 0) or 0)
                options, seen, sizes = [], set(), {}
                for rate, url in mp4s:
                    if url not in seen:
                        seen.add(url)
                        options.append((_x_quality_label(url, rate), url))
                        # X 无字节数，用码率 × 时长估算
                        if rate > 0 and dur_ms > 0:
                            sizes[url] = int(rate * (dur_ms / 1000.0) / 8.0)
                info.quality_options = options
                info.quality_sizes = sizes
                info.play_url_candidates = [u for _, u in options]
                info.play_url = options[0][1]
                info.quality = options[0][0]
                info.duration = dur_ms
        elif media.get("media_url_https"):
            info.image_urls.append(media["media_url_https"])
        if not info.cover_url:
            info.cover_url = media.get("media_url_https", "")

    if not info.play_url and not info.image_urls:
        raise ParseError("该推文不包含可下载的视频或图片")
    if info.image_urls and not info.play_url:
        info.is_image = True
    return info


def _tw_cookie_kv(x_cookie: str) -> dict:
    kv = {}
    for pair in clean_cookie(x_cookie).split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            kv[k.strip()] = v.strip()
    return kv


def parse_x(url: str, x_cookie: str = "",
            x_profile_dir: str = "") -> VideoInfo:
    """解析 X 推文：syndication → GraphQL（Cookie/游客）→ 浏览器渲染回退"""
    m = re.search(r'/status(?:es)?/(\d+)', url)
    if not m:
        raise ParseError("无法从链接中识别推文 ID")
    tweet_id = m.group(1)

    info = None
    try:
        info = _x_api(tweet_id, x_cookie, x_profile_dir)
    except ParseError as api_err:
        # 浏览器渲染回退：使用持久化登录态（推荐路径，无需手填 Cookie）
        if x_profile_dir:
            try:
                info = _x_browser(tweet_id, x_profile_dir)
            except ParseError:
                info = None
        if info is None:
            raise api_err
    _x_fill_exact_sizes(info)
    return info


def _x_fill_exact_sizes(info: VideoInfo) -> None:
    """用 HEAD 取每个档位的精确字节数，替换「码率 × 时长」估算

    ⚠️ X 的 `variants[].bitrate` 是**峰值**码率，不是平均码率：
    实测同一条 1080×1800 / 204.3s 的视频标 10368kbps，但实际文件只有
    112.8MB（平均 4413kbps）—— 直接乘时长会高估 2 倍以上，用户会看到
    「预测 252MB、实际只下了 100 多 MB」，误以为没下全。
    CDN 支持 HEAD（实测无 Referer 也回 Content-Length），4 个档位并行取，
    约 0.2~0.3s；拿不到就保留原估算值。
    """
    opts = list(info.quality_options or [])
    urls = [u for _lab, u in opts if u]
    if not urls:
        return

    sizes = {}
    lock = threading.Lock()

    def probe(u):
        headers = {"User-Agent": PC_UA, "Referer": referer_for(u)}
        n = 0
        try:
            r = requests.head(u, headers=headers, timeout=10,
                              allow_redirects=True)
            if r.status_code == 200:
                n = int(r.headers.get("content-length") or 0)
            if n <= 0:
                # 少数边缘节点对 HEAD 不回 Content-Length：
                # 退回「1 字节 Range」，从 Content-Range 里读总长度
                r = requests.get(u, headers=dict(headers, Range="bytes=0-0"),
                                 timeout=10, allow_redirects=True)
                m2 = re.search(r'/(\d+)\s*$',
                               r.headers.get("Content-Range") or "")
                n = int(m2.group(1)) if m2 else 0
                if n <= 0 and r.status_code == 200:
                    n = int(r.headers.get("content-length") or 0)
        except Exception:  # noqa: BLE001 - 拿不到就退回估算
            n = 0
        if n > 0:
            with lock:
                sizes[u] = n

    jobs = [threading.Thread(target=probe, args=(u,), daemon=True)
            for u in dict.fromkeys(urls)]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join(timeout=12)
    if not sizes:
        return

    info.quality_sizes.update(sizes)
    # 码率也换成「按实际字节数算出的平均码率」，否则标签里的 kbps
    # 和后面的 MB 对不上（同一条视频会显示 10368kbps · 107.6MB）
    dur_ms = int(info.duration or 0)
    if dur_ms > 0:
        info.quality_options = [
            (_x_quality_label(u, int(sizes[u] * 8000 / dur_ms)) if u in sizes
             else lab, u)
            for lab, u in opts]
        # 选中档位的标签也要跟着换（info.quality 是拷贝，不会自己更新）
        for lab, u in info.quality_options:
            if u and u == info.play_url:
                info.quality = lab
                break


def _x_api(tweet_id: str, x_cookie: str = "",
           profile_dir: str = "") -> VideoInfo:
    """GraphQL TweetResultByRestId → syndication 兜底

    顺序很关键：**先 GraphQL 再 syndication**。syndication 的 variants
    只给 type/src，没有 bitrate，拿不到文件大小；GraphQL 的
    video_info.variants 带 bitrate，才能算出"2176kbps · 5.1 MB"。
    syndication 只在 GraphQL 不认（敏感内容/未登录）时兜底。

    `profile_dir` 仅在 queryId 过期需要现场刷新时用到（会开一次无头浏览器）。
    """
    kv = _tw_cookie_kv(x_cookie)
    if kv.get("auth_token"):
        # 1) 有登录态：走 GraphQL（带 bitrate，可估算大小）
        try:
            return _x_graphql(tweet_id, x_cookie, kv, profile_dir)
        except ParseError:
            pass

    # 2) syndication：token + Googlebot UA（敏感内容大多也可取到）
    tombstone = False
    try:
        resp = requests.get(
            "https://cdn.syndication.twimg.com/tweet-result",
            params={"id": tweet_id, "token": _syndication_token(tweet_id)},
            headers={"User-Agent": "Googlebot"}, timeout=15)
        if resp.ok:
            data = resp.json()
            if data:
                if (data.get("__typename") or "") == "TweetTombstone":
                    # X 对敏感/受限推文只回墓碑，游客令牌同样拿不到（实测
                    # 紧接着必报「未登录无法解析」）→ 别白等这 1 秒多，
                    # 有登录态 profile 时直接交给浏览器渲染
                    tombstone = True
                else:
                    return _tw_syndication_to_info(data, tweet_id)
    except (requests.RequestException, ValueError, KeyError, ParseError):
        pass

    if tombstone and profile_dir:
        raise ParseError("该推文是 X 的敏感/受限内容，需要登录态才能解析"
                         "（请在「设置 → 登录 X(Twitter)」完成登录后重试）")

    # 3) 无登录态（或 syndication 也没结果）：GraphQL + 游客令牌
    return _x_graphql(tweet_id, x_cookie, kv, profile_dir)


def _x_graphql(tweet_id: str, x_cookie: str, kv: dict,
               profile_dir: str = "") -> VideoInfo:
    """调 GraphQL TweetResultByRestId；queryId 过期时自动刷新一次"""
    headers = {
        "User-Agent": PC_UA,
        "authorization": f"Bearer {TW_BEARER}",
        "content-type": "application/json",
    }
    if kv.get("auth_token"):
        headers["Cookie"] = clean_cookie(x_cookie)
        headers["x-csrf-token"] = kv.get("ct0", "")
    else:
        try:
            g = requests.post("https://api.x.com/1.1/guest/activate.json",
                              headers=headers, timeout=15)
            if g.ok:
                headers["x-guest-token"] = g.json().get("guest_token", "")
        except requests.RequestException:
            pass

    query = json.loads(json.dumps(_GQL_QUERY))
    query["variables"]["tweetId"] = tweet_id

    def _call(endpoint: str):
        return requests.get(
            endpoint,
            params={"variables": json.dumps(query["variables"], separators=(",", ":")),
                    "features": json.dumps(query["features"], separators=(",", ":")),
                    "fieldToggles": json.dumps(query["fieldToggles"], separators=(",", ":"))},
            headers=headers, timeout=20)

    try:
        resp = _call(_gql_endpoint())
    except requests.RequestException as e:
        raise ParseError(f"X 接口请求失败：{e}")

    # queryId 过期时 X 返回 400/404：
    # 1) 先用静态候选表逐个试（纯 HTTP，毫秒级）
    # 2) 都无效再开浏览器现场抓一个（慢，但一定能拿到最新值）
    if resp.status_code in (400, 404):
        known = _gql_endpoint().rstrip("/").rsplit("/", 2)[-2]
        for cand in TW_GQL_QUERY_IDS:
            if cand == known:
                continue
            try:
                retry = _call(_gql_endpoint(cand))
            except requests.RequestException:
                continue
            if retry.ok:
                _GQL_QUERY_ID_CACHE["id"] = cand
                resp = retry
                break
    if resp.status_code in (400, 404):
        fresh = _refresh_gql_query_id(profile_dir, tweet_id)
        if fresh:
            try:
                retry = _call(_gql_endpoint(fresh))
                if retry.ok:
                    resp = retry
            except requests.RequestException:
                pass

    if not resp.ok:
        if not kv.get("auth_token"):
            raise ParseError("该推文可能是敏感/受限内容，未登录无法解析。"
                             "请在「设置 → 登录 X(Twitter)」中登录后重试")
        raise ParseError(f"X 接口返回 HTTP {resp.status_code}，"
                         "Cookie 可能已过期，请更新后重试")

    try:
        result = (resp.json().get("data") or {}).get("tweetResult", {}).get("result") or {}
    except ValueError:
        raise ParseError("X 接口返回异常，无法解析")

    typename = result.get("__typename", "")
    if typename == "TweetWithVisibilityResults":
        result = result.get("tweet") or {}
    elif typename == "TweetUnavailable":
        reason = result.get("reason") or ""
        if "Nsfw" in reason:
            raise ParseError("敏感内容需要登录查看，请检查「设置」中的 X Cookie 是否有效")
        raise ParseError(f"推文不可用：{reason or '未知原因'}")

    status = result.get("legacy") or {}
    user_legacy = (((result.get("core") or {}).get("user_results") or {})
                   .get("result") or {}).get("legacy") or {}
    if not status:
        if not kv.get("auth_token"):
            raise ParseError("该推文可能是敏感/受限内容，未登录无法解析。"
                             "请在「设置 → 登录 X(Twitter)」中登录后重试")
        raise ParseError("X 接口未能解析该推文（Cookie 可能已过期，请更新后重试）")
    return _tw_build_info(status, user_legacy, tweet_id)


def _x_browser(tweet_id: str, profile_dir: str) -> VideoInfo:
    """用浏览器渲染 X 推文页（复用持久化登录态），拦截真实视频地址"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ParseError(f"Playwright 未安装：{e}")

    with sync_playwright() as pw:
        dur_box = {"ms": 0}      # 供下方估算文件大小使用，需在 try 外初始化
        try:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=profile_dir, channel="msedge", headless=True,
                user_agent=PC_UA, locale="zh-CN",
                viewport={"width": 1280, "height": 900})
        except Exception as e:  # noqa: BLE001
            raise ParseError(f"无法启动本机 Edge 浏览器：{e}")
        try:
            captured = []
            json_variants = []

            def on_response(resp):
                try:
                    ct = resp.headers.get("content-type", "")
                    if "video/mp4" in ct or "video/quicktime" in ct:
                        size = 0
                        try:
                            size = int(resp.headers.get("content-length") or 0)
                        except (TypeError, ValueError):
                            size = 0
                        captured.append((resp.url, size))
                        return
                    if "json" in ct and ("x.com" in resp.url or "twitter.com" in resp.url):
                        data = resp.json()
                        vinfo = _find_key_recursive(data, "video_info")
                        if isinstance(vinfo, dict):
                            variants = vinfo.get("variants") or []
                            if isinstance(variants, list):
                                json_variants.extend(variants)
                            try:
                                dm = int(vinfo.get("duration_millis") or 0)
                            except (TypeError, ValueError):
                                dm = 0
                            if dm:
                                dur_box["ms"] = dm
                except Exception:  # noqa: BLE001
                    pass

            page = context.new_page()
            page.on("response", on_response)
            page.goto(f"https://x.com/i/status/{tweet_id}",
                      wait_until="domcontentloaded", timeout=45000)
            # 视频信息随页面响应就到（通常 1~2s），轮询到即走，别固定死等 5s；
            # 命中后留 400ms 让 og / <video> 也渲染出来，兜底路径才不至于落空
            waited = 0
            while waited < 6000 and not (captured or json_variants):
                try:
                    page.wait_for_timeout(200)
                except Exception:  # noqa: BLE001
                    break
                waited += 200
            if captured or json_variants:
                page.wait_for_timeout(400)

            og = page.evaluate(
                "() => document.querySelector('meta[property=\"og:video\"]')?.content || ''")
            video_src = page.evaluate(
                "() => { const v = document.querySelector('video'); return v ? (v.currentSrc || v.src || '') : ''; }")
            title = page.evaluate(
                "() => document.querySelector('meta[property=\"og:title\"]')?.content || ''")
            desc = page.evaluate(
                "() => document.querySelector('meta[property=\"og:description\"]')?.content || ''")
            cover = page.evaluate(
                "() => document.querySelector('meta[property=\"og:image\"]')?.content || ''")
            # og:title 会带上"(2) X 上的 作者："这类浏览器标题前缀（数字是
            # 未读通知数，会变），不能直接当作品标题。这里单独抓推文正文。
            tweet_text = page.evaluate("""() => {
                const el = document.querySelector(
                    '[data-testid="tweetText"]');
                return el ? el.innerText : '';
            }""")

            # 优先用接口 JSON 里的完整渐进式 mp4 档位（长视频的分片会被排除）
            options, seen, x_sizes = [], set(), {}
            for v in json_variants:
                ct = (v.get("content_type") or v.get("type") or "")
                if not ct.startswith("video/mp4"):
                    continue          # 排除 m3u8 播放列表
                url = (v.get("url") or v.get("src") or "").replace("\\u0026", "&")
                if not url or "/m3u8" in url or url in seen:
                    continue
                seen.add(url)
                rate = int(v.get("bitrate") or 0)
                options.append((rate, _x_quality_label(url, rate), url))
                # X 无字节数，用码率 × 时长估算
                if rate > 0 and dur_box["ms"] > 0:
                    x_sizes[url] = int(rate * (dur_box["ms"] / 1000.0) / 8.0)
            options.sort(key=lambda x: x[0], reverse=True)
            quality_options = [(lab, url) for _, lab, url in options]

            video_url = quality_options[0][1] if quality_options else ""
            if not video_url and captured:
                # 兜底：排除带分片偏移（/9000/12000/ 这类）的 URL
                full = [u for u, _ in captured if not re.search(r'/\d{3,}/\d{3,}/', u)]
                pool = full or [u for u, _ in captured]
                best = max(((u for u in pool)), key=lambda u: next(
                    (s for u2, s in captured if u2 == u), 0))
                video_url = best
            if not video_url and og.startswith("http"):
                video_url = og
            if not video_url and video_src.startswith("http"):
                video_url = video_src
        finally:
            context.close()

    if not video_url:
        raise ParseError("未能获取该推文的视频地址。若为敏感内容，"
                         "请在「设置 → 登录 X(Twitter)」登录后重试")

    info = VideoInfo()
    info.source = "x"
    info.aweme_id = tweet_id
    # 正文优先（og:title 带"(N) X 上的 作者："前缀，N 是未读通知数会变）
    clean_title = _x_clean_og_title(tweet_text or desc or title)
    info.title = clean_title.strip()
    info.cover_url = cover
    info.play_url = video_url
    info.play_url_candidates = [u for _, u in quality_options] or [video_url]
    if quality_options:
        info.quality_options = quality_options
        info.quality_sizes = {u: s for u, s in x_sizes.items()
                              if u in {x for _l, x in quality_options}}
    else:
        info.quality_options = [("最高画质（原始）", video_url)]
    info.quality = info.quality_options[0][0]
    if dur_box["ms"]:
        info.duration = dur_box["ms"]
    return info


def _tw_syndication_to_info(data: dict, tweet_id: str) -> VideoInfo:
    """从 syndication JSON 构建 VideoInfo"""
    info = VideoInfo()
    info.source = "x"
    info.aweme_id = tweet_id
    info.title = _x_clean_og_title(data.get("text") or "")
    user = data.get("user") or {}
    info.author = user.get("name") or user.get("screen_name") or ""
    info.digg_count = int(data.get("favorite_count", 0) or 0)
    info.comment_count = int(data.get("conversation_count", 0) or 0)

    video = data.get("video") or {}
    variants = video.get("variants") or []
    if variants:
        # 时长要先取出来：下面按 码率 × 时长 / 8 估算文件大小
        info.duration = int(video.get("durationMs", 0) or 0)
        mp4s = []
        for v in variants:
            if (v.get("type") or "").startswith("video/mp4") and v.get("src"):
                mp4s.append((int(v.get("bitrate") or 0),
                             v["src"].replace("\\u0026", "&")))
        if mp4s:
            mp4s.sort(key=lambda x: x[0], reverse=True)
            options, seen, sizes = [], set(), {}
            for rate, url in mp4s:
                if url not in seen:
                    seen.add(url)
                    options.append((_x_quality_label(url, rate), url))
                    # X 不返回字节数，用码率 × 时长估算
                    if rate > 0 and info.duration > 0:
                        sizes[url] = int(rate * (info.duration / 1000.0) / 8.0)
            info.quality_options = options
            info.quality_sizes = sizes
            info.play_url_candidates = [u for _, u in options]
            info.play_url = options[0][1]
            info.quality = options[0][0]

    photos = data.get("photos") or []
    if photos:
        info.image_urls = [p["url"] for p in photos if p.get("url")]
    media = data.get("mediaDetails") or []
    if media and not info.cover_url:
        info.cover_url = media[0].get("media_url_https", "")
    if not info.cover_url and photos:
        info.cover_url = photos[0].get("url", "")

    if not info.play_url and not info.image_urls:
        raise ParseError("该推文不包含可下载的视频或图片")
    if info.image_urls and not info.play_url:
        info.is_image = True
    return info


# --------------------------------------------------------------------- #
# Instagram
# --------------------------------------------------------------------- #
def parse_instagram(url: str, cookie: str = "",
                    profile_dir: str = "") -> VideoInfo:
    """解析 Instagram 帖子（reel/p）：浏览器渲染为主，HTTP 直连并行兜底

    IG 对普通 HTTP 请求只回 JS 空壳（带登录 Cookie 实测同样拿不到数据），
    所以 HTTP 直连不能再串在主力链路前面白等（n≈1s）—— 改为并行：
    浏览器拿到数据就直接返回，只有浏览器失败时才回头看 HTTP 的结果。
    """
    m = re.search(r'/(?:reel|reels|p)/([A-Za-z0-9_-]+)', url)
    if not m:
        raise ParseError("无法从链接中识别 Instagram 帖子 ID")
    shortcode = m.group(1)

    # 1) HTTP 直连：后台线程跑（公开帖子的 og 标签/内嵌 JSON 仍可取到）
    got = {}

    def _http_try():
        try:
            got["info"] = _instagram_http(shortcode, cookie)
        except Exception:  # noqa: BLE001
            pass

    probe = threading.Thread(target=_http_try, daemon=True)
    probe.start()

    # 2) 浏览器渲染（主力链路：登录态 / 受限内容只能靠它）
    try:
        return _instagram_browser(shortcode, cookie, profile_dir or None)
    except ParseError:
        probe.join(timeout=3)
        if got.get("info"):
            return got["info"]
        raise


def _instagram_http(shortcode: str, cookie: str = "") -> VideoInfo:
    # Cookie 先统一清洗（非 Latin-1 字符 percent-encode），再拆键值对，
    # 保证 Cookie header 与 x-csrftoken 用的是同一份值（见 models.clean_cookie）
    cookie = clean_cookie(cookie)
    cookie_kv = {}
    for pair in cookie.split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            cookie_kv[k.strip()] = v.strip()

    session = requests.Session()
    session.headers.update({
        "User-Agent": PC_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.instagram.com/",
    })
    if cookie:
        session.headers["Cookie"] = cookie
    if cookie_kv.get("csrftoken"):
        session.headers["x-csrftoken"] = cookie_kv["csrftoken"]

    page_url = f"https://www.instagram.com/p/{shortcode}/"
    try:
        resp = session.get(page_url, timeout=20, allow_redirects=True)
    except requests.RequestException as e:
        raise ParseError(f"Instagram 请求失败：{e}")
    html = resp.text

    if "accounts/login" in resp.url:
        raise ParseError("HTTP 直连被登录墙拦截")

    info = VideoInfo()
    info.source = "instagram"
    info.aweme_id = shortcode

    mv = re.search(r'<meta property="og:video" content="([^"]+)"', html)
    mt = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    mc = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    if mt:
        title = mt.group(1)
        pm = re.search(r'^(.+?) on Instagram: ?(.*)$', title)
        if pm:
            info.author = pm.group(1).strip()
            info.title = pm.group(2).strip().strip('"')
        else:
            info.title = title
    if mc:
        info.cover_url = mc.group(1).replace("&amp;", "&")
    if mv:
        info.play_url = mv.group(1).replace("&amp;", "&")
        info.play_url_candidates = [info.play_url]
        info.quality_options = [("最高画质（原始）", info.play_url)]
        info.quality = "最高画质（原始）"
        return info

    m2 = re.search(r'"video_versions"\s*:\s*(\[.*?\])', html)
    if m2:
        try:
            versions = json.loads(m2.group(1))
            versions.sort(key=lambda v: int(v.get("width", 0) or 0), reverse=True)
            options, seen = [], set()
            for v in versions:
                url = (v.get("url") or "").replace("&amp;", "&")
                if not url or url in seen:
                    continue
                seen.add(url)
                w = int(v.get("width") or 0)
                h = int(v.get("height") or 0)
                label = f"{w}×{h}" if w and h else (f"{w}p" if w else "视频")
                options.append((label, url))
            if options:
                info.quality_options = options
                info.play_url_candidates = [u for _, u in options]
                info.play_url = options[0][1]
                info.quality = options[0][0]
                return info
        except (ValueError, TypeError, KeyError):
            pass

    disp = re.findall(r'"display_url"\s*:\s*"([^"]+)"', html)
    if disp:
        info.image_urls = [d.replace("\\u0026", "&").replace("&amp;", "&")
                           for d in disp]
        info.is_image = True
        if not info.cover_url and info.image_urls:
            info.cover_url = info.image_urls[0]
        return info

    raise ParseError("HTTP 直连未能提取到内容")


def _instagram_browser(shortcode: str, cookie: str,
                       profile_dir) -> VideoInfo:
    """用浏览器渲染 Instagram 页面（现代 IG 是 JS 壳，requests 拿不到数据）

    有 profile_dir 时用持久化上下文（复用网页登录态），否则注入 cookie。

    ⚠️ 取数必须「精确对应本条帖子」：
    IG 的推荐流会在同一批响应里带回其他帖子的数据，早期版本用
    「递归找第一个 video_versions」会拿到**别人的视频**（封面却是对的，
    因为封面走 og:image）。现在改为按 media_id 走 info 接口，
    并校验返回的 code 与目标 shortcode 一致。

    取数顺序（按代价从低到高，全部走页面内 fetch）：
      1) goto 完立刻用「shortcode 本地解码出的 media_id」调 info 接口
         （实测 0.7s，且不必等页面 JS 渲染出数据）
      2) 拿不到才读页面 HTML 轮询等渲染，再依次尝试
         HTML 里的 media_id → 内嵌 JSON → og 兜底
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ParseError(f"Playwright 未安装：{e}")

    cookies = []
    for pair in (cookie or "").split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            if k.strip():
                cookies.append({"name": k.strip(), "value": v.strip(),
                                "domain": ".instagram.com", "path": "/"})

    with sync_playwright() as pw:
        try:
            if profile_dir:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=profile_dir, channel="msedge", headless=True,
                    user_agent=PC_UA, locale="en-US",
                    viewport={"width": 1280, "height": 900})
                browser = None
            else:
                browser = pw.chromium.launch(channel="msedge", headless=True)
                context = browser.new_context(user_agent=PC_UA, locale="en-US",
                                              viewport={"width": 1280, "height": 900})
                if cookies:
                    try:
                        context.add_cookies(cookies)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001
            raise ParseError(f"无法启动本机 Edge 浏览器：{e}")
        try:
            page = context.new_page()
            page.goto(f"https://www.instagram.com/p/{shortcode}/",
                      wait_until="domcontentloaded", timeout=45000)
            # ① 首选：/api/v1/media/{media_id}/info/（数据最全且有校验字段）
            #    media_id 可由 shortcode 本地解码（IG 的 shortcode 就是
            #    media_id 的 base64url 变体），所以**不必**等页面 JS 把数据
            #    渲染出来再取 id —— 省掉原先固定 5s 的干等。
            media_id = _ig_media_id_from_shortcode(shortcode)
            node = _ig_media_info(page, media_id, shortcode) if media_id else {}

            # ② 接口拿不到（未登录 / 受限内容）才读页面：
            #    HTML 里的 media_id → 内嵌 JSON → og 兜底
            cover = title = ""
            if not node:
                html = _ig_page_html(page)
                cover = _ig_meta(page, "og:image")
                title = _ig_meta(page, "og:title")
                mm = re.search(r'"media_id"\s*:\s*"(\d+)"', html or "")
                if mm and mm.group(1) != media_id:
                    node = _ig_media_info(page, mm.group(1), shortcode)
                if not node:
                    node = _ig_html_node(html, shortcode)
            elif not node.get("cover"):
                # 接口没给封面时用 og:image 补（meta 是服务端直出，代价极小）
                cover = _ig_meta(page, "og:image")

            # IG 接口不给文件大小，批量 HEAD 探 Content-Length（借页面登录态）
            opts = node.get("quality_options") or []
            if opts:
                sizes = _probe_sizes(page, [u for _lab, u in opts])
                if sizes:
                    node["quality_sizes"] = sizes
        finally:
            context.close()
            if browser:
                browser.close()

    if not node and not cover:
        raise ParseError("Instagram 页面未能加载内容。若为私密账号或登录失效，"
                         "请点击「登录 Instagram」重新登录后重试")

    info = VideoInfo()
    info.source = "instagram"
    info.aweme_id = shortcode
    info.title = node.get("title") or ""
    info.author = node.get("author") or ""
    info.digg_count = node.get("like") or 0
    info.comment_count = node.get("comment") or 0
    info.cover_url = node.get("cover") or cover
    if node.get("duration"):
        info.duration = int(node["duration"]) * 1000

    if not info.author or not info.title:
        pm = re.search(r'^(.+?) on Instagram: ?(.*)$', title or "", re.S)
        if pm:
            info.author = info.author or pm.group(1).strip()
            info.title = info.title or pm.group(2).strip().strip('"')

    quality_options = node.get("quality_options") or []
    video_url = quality_options[0][1] if quality_options else ""
    if video_url:
        info.play_url = video_url
        info.play_url_candidates = [u for _, u in quality_options]
        info.quality_options = quality_options
        info.quality_sizes = node.get("quality_sizes") or {}
        info.quality = quality_options[0][0]
        return info
    # 图集：用接口给出的多图候选（精确对应本条）。
    # 单图帖子接口只在 cover 里给一张，用它兜底；og:image 再兜底。
    single = node.get("cover") or cover
    imgs = node.get("image_urls") or ([single] if single else [])
    info.image_urls = imgs
    info.is_image = True
    return info


IG_APP_ID = "936619743392459"

# 页面内发请求：媒体 info 接口 + 文件大小探测。
# ⚠️ 必须走页面内 fetch，不能用 Playwright 的 page.request ——
# 实测本机环境下 page.request 的每一次调用都会一路超时
# （info 接口 20~30s、CDN HEAD 每个 8s 后才 TimeoutError），
# 一次解析光这部分就白等 20~40s；而页面内 fetch 同一条 URL
# 0.1s 内就返回，且自动带登录 Cookie / 站点指纹，CDN 不会 403。
_IG_INFO_JS = r"""async (mid) => {
    try {
        const headers = {'x-ig-app-id': '%s'};
        const m = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
        if (m) headers['x-csrftoken'] = decodeURIComponent(m[1]);
        const r = await fetch(`/api/v1/media/${mid}/info/`,
                              {headers: headers, credentials: 'include'});
        return await r.text();
    } catch (e) {
        return '';
    }
}""" % IG_APP_ID

_IG_SIZE_JS = r"""async (urls) => {
    const out = {};
    await Promise.all(urls.map(async (u) => {
        let n = 0;
        try {
            const r = await fetch(u, {method: 'HEAD'});
            n = parseInt(r.headers.get('content-length') || '0', 10) || 0;
            if (r.status !== 200 || !n) {
                // 部分 CDN 不支持 HEAD：退回只取 1 字节的 Range 请求读总长
                const r2 = await fetch(u, {headers: {'Range': 'bytes=0-0'}});
                const cr = r2.headers.get('content-range') || '';
                const m = cr.match(/\/(\d+)\s*$/);
                n = m ? parseInt(m[1], 10)
                      : (parseInt(r2.headers.get('content-length') || '0', 10) || 0);
                try { if (r2.body) r2.body.cancel(); } catch (e) {}
            }
        } catch (e) {
            n = 0;
        }
        out[u] = n;
    }));
    return out;
}"""


def _ig_media_id_from_shortcode(shortcode: str) -> str:
    """由 shortcode 本地解码出 media_id

    IG 的 shortcode 就是把 media_id 按 base64url 变体（A-Za-z0-9-_）编码
    后的字符串，因此不需要先抓页面 HTML 再正则找 `"media_id"` ——
    省掉「等页面渲染」这一步。解错也无妨：info 接口返回的 code 与目标
    shortcode 不一致时会被丢弃，自动走页面解析兜底。
    """
    alphabet = ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                "0123456789-_")
    if not shortcode:
        return ""
    n = 0
    for ch in shortcode:
        idx = alphabet.find(ch)
        if idx < 0:
            return ""
        n = n * 64 + idx
    return str(n)


def _ig_page_html(page, timeout_ms: int = 5000) -> str:
    """轮询等本条帖子的数据渲染进页面，出现即返回（不再固定 sleep）"""
    waited, html = 0, ""
    while True:
        try:
            html = page.content()
        except Exception:  # noqa: BLE001
            return ""
        if any(k in html for k in ('"media_id"', '"video_versions"',
                                   '"display_url"', '"image_versions2"')):
            return html
        if waited >= timeout_ms:
            return html
        try:
            page.wait_for_timeout(250)
        except Exception:  # noqa: BLE001
            return html
        waited += 250


def _ig_meta(page, prop: str) -> str:
    """读页面 og meta（仅接口不可用时兜底用）"""
    try:
        return page.evaluate(
            "p => document.querySelector('meta[property=\"' + p + '\"]')"
            "?.content || ''", prop) or ""
    except Exception:  # noqa: BLE001
        return ""


def _probe_sizes(page, urls) -> dict:
    """借页面登录态批量 HEAD 取 Content-Length（拿不到的不返回）

    用于接口不返回文件大小的平台（如 Instagram）。
    """
    todo = [u for u in dict.fromkeys(urls or []) if u]
    if not todo:
        return {}
    try:
        res = page.evaluate(_IG_SIZE_JS, todo) or {}
    except Exception:  # noqa: BLE001
        return {}
    return {u: int(n) for u, n in res.items() if int(n or 0) > 0}


def _ig_media_info(page, media_id: str, shortcode: str) -> dict:
    """调 /api/v1/media/{id}/info/ 取本条帖子的精确数据。

    返回统一结构的 dict（空 dict 表示不可用）。
    关键校验：返回的 code 必须等于目标 shortcode，否则丢弃。
    """
    if not media_id:
        return {}
    try:
        text = page.evaluate(_IG_INFO_JS, str(media_id))
        data = json.loads(text) if text else {}
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}

    items = data.get("items") or []
    if not items:
        return {}
    it = items[0]

    # 严格校验：必须是目标帖子，避免拿到推荐流里的其他内容
    code = it.get("code") or ""
    if code and code != shortcode:
        return {}

    out = {}
    vs = it.get("video_versions") or []
    options, seen = [], set()
    for v in sorted(vs, key=lambda x: int(x.get("width") or 0), reverse=True):
        url = (v.get("url") or "").replace("\\u0026", "&").replace("&amp;", "&")
        if not url or url in seen:
            continue
        seen.add(url)
        w = int(v.get("width") or 0)
        h = int(v.get("height") or 0)
        options.append((f"{w}×{h}" if w and h else "视频", url))
    if options:
        out["quality_options"] = options

    cands = ((it.get("image_versions2") or {}).get("candidates")) or []
    if cands:
        out["cover"] = (cands[0].get("url") or "").replace("&amp;", "&")
        # 图集：carousel_media 才是真正的多图
        carousel = it.get("carousel_media") or []
        if carousel:
            imgs = []
            for c in carousel:
                cc = ((c.get("image_versions2") or {}).get("candidates")) or []
                if cc and cc[0].get("url"):
                    imgs.append(cc[0]["url"])
            if imgs:
                out["image_urls"] = imgs

    out["like"] = it.get("like_count") or 0
    out["comment"] = it.get("comment_count") or 0
    out["duration"] = it.get("video_duration") or 0
    cap = it.get("caption") or {}
    if isinstance(cap, dict) and cap.get("text"):
        out["title"] = cap["text"]
    elif isinstance(cap, str):
        out["title"] = cap
    user = it.get("user") or {}
    if user.get("username"):
        out["author"] = user["username"]
    elif user.get("full_name"):
        out["author"] = user["full_name"]
    return out


def _ig_html_node(html: str, shortcode: str) -> dict:
    """从页面 HTML 内嵌 JSON 里取本条帖子的视频/图片（本页数据，非推荐流）。

    HTML 里的 video_versions 只对应当前页面，但仍校验 code 一致才采用。
    """
    out = {}
    if not html:
        return out

    # 校验 HTML 里的 code 确实是目标帖子
    code_ok = bool(re.search(r'"code"\s*:\s*"%s"' % re.escape(shortcode), html))
    if not code_ok:
        return out

    idx = html.find('"video_versions"')
    if idx >= 0:
        start = html.find("[", idx)
        if start > 0:
            depth, end = 0, start
            while end < len(html) and end < start + 40000:
                c = html[end]
                if c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                    if depth == 0:
                        break
                end += 1
            try:
                arr = json.loads(html[start:end + 1])
                options, seen = [], set()
                for v in arr:
                    url = (v.get("url") or "").replace("\\u0026", "&").replace("&amp;", "&")
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    w = int(v.get("width") or 0)
                    h = int(v.get("height") or 0)
                    options.append((f"{w}×{h}" if w and h else "视频", url))
                if options:
                    out["quality_options"] = options
            except (ValueError, TypeError):
                pass

    m = re.search(r'"like_count"\s*:\s*(\d+)', html)
    if m:
        out["like"] = int(m.group(1))
    m = re.search(r'"comment_count"\s*:\s*(\d+)', html)
    if m:
        out["comment"] = int(m.group(1))
    m = re.search(r'"video_duration"\s*:\s*([\d.]+)', html)
    if m:
        out["duration"] = float(m.group(1))
    return out


# --------------------------------------------------------------------- #
# 小红书（xiaohongshu）
# --------------------------------------------------------------------- #
XHS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")
XHS_HOST = "https://www.xiaohongshu.com"


def parse_xiaohongshu(url: str, cookie: str = "",
                      profile_dir: str = "") -> VideoInfo:
    """解析小红书笔记（视频 / 图集）。

    数据来源：小红书 PC 详情页是 SSR，完整 note 数据内联在
    `window.__INITIAL_STATE__.feed.undertakeNote.items[0].noteCard`，
    含全部画质档位（1440p / 1080p / 720p）。该 JSON 只能靠浏览器
    渲染后读 `page.content()` 取得（运行时的 state 是 Vue 响应式对象，
    含循环引用无法序列化；`feed.undertakeNote.items` 运行时也为空）。

    **重要**：小红书服务端只对携带有效 `xsec_token` 的详情页下发 SSR 数据，
    否则跳 `/404?error_code=300031`（当前笔记暂时无法浏览）。
    `xsec_token` 只能来自 App「分享 → 复制链接」得到的链接，
    因此本平台请粘贴 App 分享出来的链接（xhslink.com 短链或带
    `xsec_token=` 的长链接）。浏览器会自动完成短链跳转。

    流程：
      1) 用持久化 profile 打开分享链接（自动跟随 xhslink 短链跳转）
      2) 轮询 page.content() 直到 SSR 的 noteCard 出现（含多档画质）
      3) 解析 noteCard → VideoInfo；图集走 imageList，视频走 video.media.stream
      4) 兜底：og:video / og:title（画质较低但可用）
    """
    note_id, token = _xhs_ids(url)

    # 1) 浏览器渲染（小红书对请求签名敏感，且 SSR 只在浏览器里完整）
    try:
        info = _xhs_browser(url, note_id, token, profile_dir or None)
        if info and (info.play_url or info.image_urls):
            return info
    except ParseError:
        raise
    except Exception:
        pass

    # 2) HTTP 直连兜底（部分公开笔记 SSR 直接可见）
    try:
        return _xhs_http(url, note_id, token, cookie)
    except ParseError:
        raise
    except Exception as e:
        raise ParseError(f"小红书解析失败：{e}")


def _xhs_has_device_cookie(ctx) -> bool:
    """判断当前上下文是否已具备小红书设备 Cookie

    小红书的服务端只有在识别到设备 Cookie（a1 为主，配合 webId /
    web_session）时才下发含 noteCard 的完整 SSR。全新 profile 的首次
    导航必须先去站点主页把这些 Cookie 落盘，否则笔记页只能拿到空壳。
    """
    try:
        names = {c.get("name") for c in ctx.cookies()
                 if "xiaohongshu" in (c.get("domain") or "")}
    except Exception:  # noqa: BLE001
        return False
    return "a1" in names


def _xhs_ids(url: str) -> tuple:
    """从各种小红书链接形态中取 note_id 与 xsec_token"""
    note_id = ""
    m = re.search(r'/(?:explore|discovery/item|item)/([0-9a-fA-F]{24})', url)
    if m:
        note_id = m.group(1)
    if not note_id:
        m = re.search(r'([0-9a-fA-F]{24})', url)
        if m:
            note_id = m.group(1)
    token = ""
    q = urllib.parse.urlparse(url).query
    if q:
        token = (urllib.parse.parse_qs(q).get("xsec_token") or [""])[0]
    return note_id, token


def _xhs_browser(url: str, note_id: str, token: str,
                 profile_dir: Optional[str]) -> Optional[VideoInfo]:
    """用持久化 Edge 打开笔记页并读取 SSR state"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ParseError("缺少 playwright，无法渲染小红书页面")

    from .browser_bridge import launch_clean

    pw = sync_playwright().start()
    ctx = None
    br = None
    try:
        # 小红书会识别 UA 覆盖 / navigator.webdriver 篡改等自动化特征，
        # 必须用原生指纹启动（套用抖音那套 stealth 参数会被跳登录页）
        # 用无头模式：解析时不留任何可见窗口（有头会"闪一下"浏览器）。
        # 实测无头下 SSR 的 noteCard 照常下发（含全新 profile 的预热路径），
        # 与有头结果完全一致。
        ctx, br = launch_clean(pw, profile_dir, True)
        # 必须复用首个页签直接 goto 笔记页：
        #   - 新建页签 / 先跳 about:blank 会被 profile 的登录墙拦截，
        #     落到 /login?redirectPath=... 拿不到 SSR 数据
        #   - 带 xsec_token 的笔记 URL 游客可直接访问
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # ---- 关键：冷启动预热 ----
        # 实测：全新 profile 的"第一次导航"若直接打笔记页，服务端不下发
        # 含 noteCard 的 SSR（页面只有 360KB 的空壳，noteCard=0）；
        # 但先用同一个会话访问一次站点主页拿到设备 Cookie
        # （a1 / webId / web_session 等）后，同一个笔记链接 1 秒内就能拿到
        # noteCard=1 的完整 SSR（含全部图集与各档画质）。
        # 因此这里先预热：仅在缺 a1/webId 时访问首页并等 Cookie 落盘。
        if not _xhs_has_device_cookie(ctx):
            try:
                page.goto(XHS_HOST + "/explore",
                          wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            for _ in range(10):
                if _xhs_has_device_cookie(ctx):
                    break
                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    break

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            raise ParseError(f"小红书页面打开失败：{e}")

        # 小红书的 SSR script 文本在 SPA 完成路由规范化后才完整
        # （短链会 302 -> /discovery/item -> /explore/{id}）。
        # 轮询等 noteCard 出现：视频等 masterUrl，图集等 imageList。
        # 兜底也认 noteDetailMap 里的 note（部分会话只给这个槽位）。
        waited = 0
        ready = False
        while waited < 20000:
            try:
                h = page.content()
            except Exception:
                h = ""
            if (('"noteCard"' in h and ('"masterUrl"' in h
                                        or '"imageList"' in h))
                    or ('"noteDetailMap"' in h and '"noteId"' in h
                        and '"imageList"' in h)):
                ready = True
                break
            page.wait_for_timeout(1000)
            waited += 1000
        if waited:
            page.wait_for_timeout(600)

        cur = page.url or ""
        cur_dec = urllib.parse.unquote(cur)
        # ⚠️ 重要（2026-09-28 实测）：**风控 / 登录墙的落地页里仍可能带着完整 SSR**
        # —— error_code=300012 的错误页 HTML（95052 字节）里 noteCard / imageList
        # 一应俱全，直接就能解析出图集。所以顺序必须是「先看有没有数据」，
        # 只有真的没数据时才按页面状态去报错；否则会把可用数据白白丢掉。
        if not ready:
            if "error_code=300012" in cur_dec or "IP存在风险" in cur_dec:
                raise ParseError(
                    "小红书按网络出口 IP 拒绝了本次访问"
                    "（error_code=300012「IP存在风险」）。"
                    "常见原因是系统代理 / 加速器把小红书流量送去了海外节点。"
                    "请把 xiaohongshu.com、xhslink.cn、xhscdn.com 设为直连"
                    "（或暂时关闭代理）后重试，切换网络后也可能需要等几分钟")
            if "website-login/error" in cur_dec:
                raise ParseError(
                    "小红书拦截了本次访问（网络环境被判定有风险）。"
                    "请检查代理设置：把小红书相关域名设为直连后重试")
            # 只有明确跳到登录页才算登录墙（页面 HTML 里含 /login 属正常）
            if urllib.parse.urlparse(cur).path.rstrip("/").endswith("/login"):
                raise ParseError(
                    "小红书要求登录：请在设置页点「登录 小红书」完成扫码后重试")
            if "/404" in cur and "error_code=300031" in cur:
                raise ParseError(
                    "小红书拒绝了这次访问（error_code=300031）。"
                    "该平台必须使用手机 App「分享 → 复制链接」得到的链接"
                    "（形如 xhslink.cn/o/xxxx 或 xhslink.com/a/xxxx），"
                    "网页地址栏里复制的链接无效")
            raise ParseError(
                "小红书页面未返回笔记数据。请确认链接来自 App"
                "「分享 → 复制链接」（形如 xhslink.cn/o/xxxx）；"
                "若链接确实来自 App 仍失败，多半是网络出口被风控，"
                "请关闭代理 / 加速器后重试")

        # 注意：运行时的 __INITIAL_STATE__ 是 Vue 响应式对象，含循环引用，
        # JSON.stringify 会抛 "Converting circular structure to JSON"；
        # 且实测 feed.undertakeNote.items 在运行时为空，
        # 因此以 page.content() 里的 SSR script 文本为唯一主数据源。
        html = ""
        try:
            html = page.content()
        except Exception:
            pass
    finally:
        for closer in (ctx, br):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
        try:
            pw.stop()
        except Exception:
            pass

    info = None
    # 1) page.content() 的 SSR 文本（唯一可靠来源，字段最全、含最高画质）
    if html:
        info = _xhs_from_html(html, note_id)
    # 2) og 兜底（画质较低，但至少能下）
    if (info is None or (not info.play_url and not info.image_urls)) and html:
        og = _xhs_from_og(html, note_id)
        if og.play_url or og.image_urls:
            info = og
    return info


def _xhs_from_card(card: dict, note_id: str) -> Optional[VideoInfo]:
    """校验 noteId 后把 noteCard 转成 VideoInfo（串号保护）"""
    if not isinstance(card, dict) or not card.get("noteId"):
        return None
    if note_id and str(card["noteId"]) != str(note_id):
        return None
    return _xhs_card_to_info(card)


def _xhs_from_state(state: dict, note_id: str) -> Optional[VideoInfo]:
    """从 __INITIAL_STATE__ 里取 noteCard（小红书 PC 的 SSR 槽位）"""
    if not isinstance(state, dict):
        return None
    feed = state.get("feed") or {}

    items = ((feed.get("undertakeNote") or {}).get("items")) or []
    for it in items:
        if not isinstance(it, dict):
            continue
        nc = it.get("noteCard")
        if isinstance(nc, dict) and nc.get("noteId"):
            return _xhs_from_card(nc, note_id)

    # 备用：noteDetailMap（部分登录态下才有）
    ndm = state.get("noteDetailMap") or {}
    for _k, v in ndm.items():
        note = (v or {}).get("note") if isinstance(v, dict) else None
        if isinstance(note, dict) and note.get("noteId"):
            return _xhs_from_card(note, note_id)
    return None


def _xhs_from_html(html: str, note_id: str) -> Optional[VideoInfo]:
    """从页面 HTML 的 SSR script 文本里提取 noteCard

    注意：这里解析的是 page.content() 的原始文本，其中的中文/引号仍是
    原样，只有少数 \\u002F 之类的转义，_xhs_clean 处理 undefined 即可。
    优先取 `feed.undertakeNote.items[0].noteCard`，其次 noteDetailMap.note。
    """
    if not html:
        return None

    # 优先：undertakeNote 里的 noteCard
    idx = html.find('"noteCard"')
    if idx >= 0:
        card = _xhs_extract_object(html, idx)
        if card:
            got = _xhs_from_card(card, note_id)
            if got:
                return got

    # 备用：noteDetailMap 下的 note
    idx = html.find('"noteDetailMap"')
    if idx >= 0:
        m = re.search(r'"note"\s*:\s*\{', html[idx:idx + 4000])
        if m:
            card = _xhs_extract_object(html, idx + m.start() + len('"note"'))
            if card:
                got = _xhs_from_card(card, note_id)
                if got:
                    return got
    return None


def _xhs_extract_object(html: str, pos: int, limit: int = 400000) -> Optional[dict]:
    """从 html[pos] 起找最近的 { 并做括号配对 + json 解析"""
    start = html.find("{", pos)
    if start < 0:
        return None
    depth = 0
    in_str = None
    esc = False
    end = -1
    for k in range(start, min(len(html), start + limit)):
        ch = html[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'"):
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = k + 1
                break
    if end <= 0:
        return None
    try:
        obj = json.loads(_xhs_clean(html[start:end]))
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _xhs_http(url: str, note_id: str, token: str,
              cookie: str = "") -> VideoInfo:
    """HTTP 直连兜底：带 xsec_token 时 SSR 可能直接下发"""
    headers = {
        "User-Agent": XHS_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": XHS_HOST + "/",
    }
    if cookie:
        headers["Cookie"] = clean_cookie(cookie)
    target = url
    if note_id and token and "xsec_token" not in url:
        target = (f"{XHS_HOST}/explore/{note_id}"
                  f"?xsec_token={urllib.parse.quote(token)}&xsec_source=pc_share")
    try:
        resp = requests.get(target, headers=headers, timeout=20,
                            allow_redirects=True)
    except requests.RequestException as e:
        raise ParseError(f"小红书请求失败：{e}")

    html = resp.text or ""
    final_url = urllib.parse.unquote(getattr(resp, "url", "") or "")
    # 同 _xhs_browser：先认数据，再认页面状态
    info = _xhs_from_html(html, note_id)
    if info is not None and (info.play_url or info.image_urls):
        return info
    if "error_code=300012" in final_url or "IP存在风险" in final_url:
        raise ParseError(
            "小红书按网络出口 IP 拒绝了本次访问"
            "（error_code=300012「IP存在风险」）。"
            "请把 xiaohongshu.com、xhslink.cn、xhscdn.com 设为直连"
            "（或暂时关闭代理）后重试")
    if info is not None:
        return info

    # 最后兜底：og 标签（画质较低，但至少能下）
    return _xhs_from_og(html, note_id)


def _xhs_clean(text: str) -> str:
    """小红书的内联 JS 对象不是严格 JSON：undefined/NaN 需替换"""
    text = re.sub(r"\bundefined\b", "null", text)
    text = re.sub(r"\bNaN\b", "null", text)
    text = re.sub(r"\bInfinity\b", "null", text)
    return text


def _xhs_from_og(html: str, note_id: str) -> VideoInfo:
    """og 标签兜底解析（og:image 是平台默认图，不可当封面）"""
    info = VideoInfo()
    info.source = "xiaohongshu"
    info.aweme_id = note_id

    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', html)
    if m:
        info.title = (m.group(1).replace("&amp;", "&")
                      .replace(" - 小红书", "").strip())
    m = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"',
                  html)
    if m and not info.title:
        info.title = m.group(1).replace("&amp;", "&").strip()

    for key, attr in (("note_like", "digg_count"), ("note_comment", "comment_count"),
                      ("note_collect", "share_count")):
        mm = re.search(
            r'<meta[^>]+property="og:xhs:%s"[^>]+content="([^"]*)"' % key, html)
        if mm:
            setattr(info, attr, _xhs_num(mm.group(1)))

    mv = re.search(r'<meta[^>]+property="og:video"[^>]+content="([^"]*)"', html)
    if mv:
        u = _xhs_url(mv.group(1))
        info.play_url = u
        info.play_url_candidates = [u]
        info.quality_options = [("标准画质", u)]
        info.quality = "标准画质"

    mt = re.search(r'<meta[^>]+property="og:videotime"[^>]+content="([^"]*)"',
                   html)
    if mt:
        info.duration = _xhs_duration(mt.group(1))

    if not info.play_url:
        # 图集：og:image 只在无视频时才是真实首图
        mi = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]*)"',
                       html)
        if mi:
            u = _xhs_url(mi.group(1))
            if "picasso-static" not in u:
                info.is_image = True
                info.image_urls = [u]
                info.cover_url = u
    return info


def _xhs_card_to_info(card: dict) -> VideoInfo:
    """把 noteCard（或 noteDetailMap 的 note）统一映射为 VideoInfo"""
    info = VideoInfo()
    info.source = "xiaohongshu"
    info.aweme_id = str(card.get("noteId") or "")
    info.title = (card.get("title") or "").strip()
    desc = (card.get("desc") or "").strip()
    if not info.title:
        info.title = desc[:60]

    user = card.get("user") or {}
    info.author = user.get("nickname") or ""
    info.author_uid = user.get("userId") or ""

    ii = card.get("interactInfo") or {}
    info.digg_count = _xhs_num(ii.get("likedCount"))
    info.comment_count = _xhs_num(ii.get("commentCount"))
    info.share_count = _xhs_num(ii.get("collectedCount") or ii.get("shareCount"))

    ct = card.get("time")
    try:
        info.create_time = int(ct) if ct else 0
    except (TypeError, ValueError):
        info.create_time = 0

    # 封面：优先 imageList[0].urlDefault（视频封面与图文首图都在这里）
    imgs = card.get("imageList") or []
    if imgs:
        first = imgs[0] or {}
        info.cover_url = _xhs_url(
            first.get("urlDefault") or first.get("urlPre") or "")

    video = card.get("video") or {}
    media = video.get("media") or {}
    dur = ((video.get("capa") or {}).get("duration")
           or (media.get("video") or {}).get("duration"))
    try:
        info.duration = int(float(dur) * 1000) if dur else 0
    except (TypeError, ValueError):
        info.duration = 0

    # ---- 视频流：跨 h264/h265/h266/av1 数组按 size 去重排序 ----
    stream = media.get("stream") or {}
    by_type = {}
    for _codec, arr in stream.items():
        if not isinstance(arr, list):
            continue
        for item in arr:
            if not isinstance(item, dict):
                continue
            url = _xhs_url(item.get("masterUrl") or "")
            if not url:
                bu = item.get("backupUrls") or []
                if bu:
                    url = _xhs_url(bu[0])
            if not url:
                continue
            st = item.get("streamType")
            key = st if st is not None else url
            size = int(item.get("size") or 0)
            # 同一 streamType 重复出现时保留 size 更大的一份
            old = by_type.get(key)
            if old is None or size > int(old.get("size") or 0):
                by_type[key] = dict(item, __url__=url, __size__=size)

    options, sizes = [], {}
    for item in sorted(by_type.values(),
                       key=lambda x: (x.get("__size__") or 0), reverse=True):
        w = int(item.get("width") or 0)
        h = int(item.get("height") or 0)
        codec = (item.get("videoCodec") or "").lower()
        label = f"{h}p" if h else "视频"
        if w and h and w != h:
            label = f"{w}×{h}"
        if codec == "hevc":
            label += " (H.265)"
        elif codec == "h264":
            label += " (H.264)"
        elif codec == "av1":
            label += " (AV1)"
        options.append((label, item["__url__"]))
        # 小红书 SSR 直接给了精确字节数，直接用于下拉展示
        if item.get("__size__"):
            sizes[item["__url__"]] = int(item["__size__"])

    if options:
        # 相同标签去重，避免下拉里出现两个 720p
        uniq, seen = [], set()
        for label, u2 in options:
            if label in seen:
                continue
            seen.add(label)
            uniq.append((label, u2))
        info.quality_options = uniq
        info.quality_sizes = {u: s for u, s in sizes.items()
                              if u in {x for _l, x in uniq}}
        info.play_url = uniq[0][1]
        info.play_url_candidates = [u2 for _l, u2 in uniq]
        info.quality = uniq[0][0]
        return info

    # ---- 图集 ----
    urls = []
    for im in imgs:
        u = _xhs_url((im or {}).get("urlDefault") or (im or {}).get("urlPre") or "")
        if u and u not in urls:
            urls.append(u)
    if urls:
        info.is_image = True
        info.image_urls = urls
        if not info.cover_url:
            info.cover_url = urls[0]
        # 小红书对同一张图有两种处理版本（2026-09-28 实测）：
        #   !nd_dft_wlteh_webp_3 —— 登录态下发，实测**无「小红书」水印**
        #   !nd_dft_wgth_webp_3  —— 游客态下发，右下角**带「小红书」水印**
        # 构造不了对方的地址（hash 按完整路径签名，换后缀一律 403），
        # 所以只能靠登录态，这里至少把情况记下来。
        if any("_wgth_" in u for u in urls):
            get_logger("parse").warning(
                "小红书图集下发的是「带水印」版本（!nd_dft_wgth_webp_3）："
                "服务端按游客态返回，图片右下角会带「小红书」水印。"
                "在设置页点「登录 小红书」完成扫码后重新解析，"
                "即可拿到无水印版本（!nd_dft_wlteh_webp_3）")
    return info


def _xhs_url(u: str) -> str:
    """统一转成可直连的 https 地址并去掉转义"""
    if not u:
        return ""
    # 先还原所有 JS 转义（\\u002F 是 /、\\u0026 是 &），再处理协议
    u = (u.replace("\\u002F", "/").replace("\\u002f", "/")
          .replace("\\u0026", "&").replace("\\/", "/")
          .replace("&amp;", "&").strip())
    # 去掉可能残留的反斜杠转义
    u = u.replace("\\", "")
    if u.startswith("//"):
        u = "https:" + u
    elif u.startswith("http://"):
        u = "https://" + u[7:]
    return u


def _xhs_num(v) -> int:
    """小红书互动数是中文缩写字符串：'1.6万' / '1091' / '1.2亿'"""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return 0
    try:
        if s.endswith("亿"):
            return int(float(s[:-1]) * 100000000)
        if s.endswith("万"):
            return int(float(s[:-1]) * 10000)
        if s.endswith("w") or s.endswith("W"):
            return int(float(s[:-1]) * 10000)
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def _xhs_duration(text: str) -> int:
    """'00:23' -> 23000 毫秒"""
    if not text:
        return 0
    parts = str(text).split(":")
    try:
        parts = [int(float(x)) for x in parts]
    except (ValueError, TypeError):
        return 0
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec * 1000


# --------------------------------------------------------------------- #
# Iwara（iwara.tv）
# --------------------------------------------------------------------- #
IWARA_HOST = "https://www.iwara.tv"
IWARA_API = "https://api.iwara.tv"
# 请求 fileUrl 时必须带的 X-Version 签名后缀。2026-09 实测值，前端 main.js 里
# 明文写着：sha1(`${file_id}_${expires}_${SALT}`)。**换过一轮**——旧的
# `5nFp9kmbNnHdAFhaqMvt` 仍能返回 200，但只给 360/preview 两档（静默降级，
# 不报错），带上正确签名才会返回含 Source 的完整档位。
IWARA_SIGN_SALT = "mSvL05GfEmeEmsEYfGCnVpEjYgTJraJN"
# 作品 ID：/video/<id> 与 /image/<id>（id 是大小写敏感的字母数字串）
IWARA_ID_RE = re.compile(r'iwara\.tv/(?:video|image)/([A-Za-z0-9]+)', re.I)
# `preview` 是站点自己的预览片段，不算可选画质
IWARA_SKIP_FORMATS = ("preview",)
# 档位接口每请求一次就重新签发一批角色名 CDN 域名（bailu / herta / naja / …），
# 多取一轮就多一组备用地址 —— 实测个别域名会连不上，有备用才不至于整单失败
IWARA_FORMAT_ROUNDS = 2


def iwara_video_id(url: str) -> str:
    """从 iwara 链接里取作品 ID（video / image 均可），识别失败返回空串"""
    m = IWARA_ID_RE.search(url or "")
    return m.group(1) if m else ""


def _iwara_headers() -> dict:
    return {
        "User-Agent": PC_UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": IWARA_HOST + "/",
        "Origin": IWARA_HOST,
    }


def _iwara_sign(file_id: str, expires: str) -> str:
    """X-Version 签名（算法来自前端 main.js）"""
    raw = f"{file_id}_{expires}_{IWARA_SIGN_SALT}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _iwara_abs(u: str) -> str:
    """档位地址以 //host/path 给出，补上 scheme"""
    u = (u or "").strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return IWARA_HOST + u
    return u


def _iwara_epoch(text: str) -> int:
    """'2026-09-26T08:45:45.000Z' -> epoch 秒"""
    if not text:
        return 0
    try:
        import datetime as _dt
        s = str(text).replace("Z", "+00:00")
        return int(_dt.datetime.fromisoformat(s).timestamp())
    except Exception:  # noqa: BLE001
        return 0


def _iwara_detail(vid: str) -> dict:
    """取作品详情（含 fileUrl / file / user / title）"""
    try:
        r = requests.get(f"{IWARA_API}/video/{vid}", headers=_iwara_headers(),
                         timeout=(10, 25))
    except Exception as e:  # noqa: BLE001
        raise ParseError(
            f"Iwara 接口连接失败（{e}）。iwara.tv 需要能出网，请确认代理已开启"
        ) from e
    if r.status_code == 404:
        raise ParseError(f"Iwara 作品 {vid} 不存在或已删除")
    if r.status_code != 200:
        raise ParseError(f"Iwara 详情接口返回 HTTP {r.status_code}")
    try:
        return r.json()
    except Exception as e:  # noqa: BLE001
        raise ParseError("Iwara 详情接口返回的不是 JSON（可能被拦截，检查代理）") from e


def _iwara_formats(detail: dict) -> dict:
    """请求 fileUrl 拿档位表：{档位名: [主地址, 备用地址, …]}（保持服务端顺序）

    服务端每次都给一批随机角色名 CDN 域名，多请求一轮就多一组备用地址。
    拿不到任何档位时返回空字典，由调用方决定是否重取详情。
    """
    file_url = (detail.get("fileUrl") or "").strip()
    if not file_url:
        return {}
    u = urllib.parse.urlparse(file_url)
    q = urllib.parse.parse_qs(u.query)
    expires = (q.get("expires") or [""])[0]
    fid = u.path.rstrip("/").split("/")[-1]
    if not expires or not fid:
        return {}
    base = (f"{u.scheme}://{u.netloc}{u.path}?expires={expires}"
            f"&hash={(q.get('hash') or [''])[0]}")
    # 前端会把 download 参数设成 "Iwara - <标题> [<id>].mp4"（空格用 + 编码）
    dl_name = urllib.parse.quote_plus(
        f"Iwara - {detail.get('title') or ''} [{detail.get('id') or ''}].mp4")
    headers = dict(_iwara_headers(), **{"X-Version": _iwara_sign(fid, expires)})

    out: dict = {}
    for _ in range(IWARA_FORMAT_ROUNDS):
        try:
            r = requests.get(base + "&download=" + dl_name, headers=headers,
                             timeout=(10, 25))
            if r.status_code != 200:
                break
            fmts = r.json()
        except Exception:  # noqa: BLE001 - 一轮失败不影响已拿到的
            break
        if not isinstance(fmts, list) or not fmts:
            break
        for f in fmts:
            if not isinstance(f, dict):
                continue
            name = str(f.get("name") or "").strip()
            src = f.get("src") or {}
            url = _iwara_abs(src.get("download") or src.get("view") or "")
            if not name or not url:
                continue
            bucket = out.setdefault(name, [])
            if url not in bucket:
                bucket.append(url)
    return out


def _iwara_format_rank(name: str) -> int:
    """档位排序键（越大越好）：Source 最高，然后按分辨率数字降序"""
    low = (name or "").lower()
    if low == "source":
        return 10 ** 6
    m = re.search(r'(\d{3,4})', low)
    return int(m.group(1)) if m else 0


def _iwara_quality_label(name: str, width: int, height: int) -> str:
    """档位名 -> 画质标签。`Source` 用作品的原始分辨率标注"""
    low = (name or "").lower()
    if low == "source":
        if width and height:
            return f"{width}×{height} 原始"
        return "原始画质"
    m = re.search(r'(\d{3,4})', low)
    if m:
        return f"{m.group(1)}p"
    return name or "视频"


def _iwara_probe_sizes(urls: List[str]) -> dict:
    """并发探针取每档精确字节数

    档位接口不返回文件大小，只能自己问：发 `Range: bytes=0-0` 从
    Content-Range 里读总长度（实测 CDN 对 HEAD 回 405，不能用 HEAD）。
    """
    sizes: dict = {}
    if not urls:
        return sizes
    lock = threading.Lock()

    def probe(u: str) -> None:
        headers = {"User-Agent": PC_UA, "Referer": IWARA_HOST + "/",
                   "Range": "bytes=0-0"}
        n = 0
        try:
            r = requests.get(u, headers=headers, timeout=12, allow_redirects=True)
            m = re.search(r'/(\d+)\s*$', r.headers.get("Content-Range") or "")
            n = int(m.group(1)) if m else 0
            if n <= 0 and r.status_code == 200:
                n = int(r.headers.get("content-length") or 0)
        except Exception:  # noqa: BLE001 - 拿不到就不显示大小
            n = 0
        if n > 0:
            with lock:
                sizes[u] = n

    jobs = [threading.Thread(target=probe, args=(u,), daemon=True)
            for u in dict.fromkeys(urls)]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join(timeout=14)
    return sizes


def parse_iwara(url: str) -> VideoInfo:
    """解析 Iwara 作品（纯 HTTP，免登录）

    链路（2026-09 实测）：
      1) `GET api.iwara.tv/video/<id>` → 标题 / 作者 / 原始文件信息 + fileUrl
      2) 带 X-Version 签名请求 fileUrl → 档位表（Source / 540 / 360 / preview）
      3) 并发探针取每档精确字节数

    `preview` 是站点预览片段，直接丢掉；`Source` 就是上传的原始文件。
    """
    vid = iwara_video_id(url)
    if not vid:
        raise ParseError("无法从链接中识别 Iwara 作品 ID（应形如 iwara.tv/video/xxxx）")

    detail = _iwara_detail(vid)
    if detail.get("embedUrl"):
        raise ParseError("该 Iwara 作品是外链投稿（正文里给了网盘/外部链接），无法直接下载")

    file_obj = detail.get("file") or {}
    formats = _iwara_formats(detail)
    if not formats:
        # fileUrl 的签名可能过期：重取一次详情再试
        detail = _iwara_detail(vid)
        file_obj = detail.get("file") or {}
        formats = _iwara_formats(detail)

    info = VideoInfo()
    info.source = "iwara"
    info.aweme_id = vid
    info.title = (detail.get("title") or "").strip() or vid
    user = detail.get("user") or {}
    info.author = (user.get("name") or user.get("username") or "").strip()
    info.author_uid = str(user.get("id") or "")
    info.digg_count = int(detail.get("numLikes") or 0)
    info.comment_count = int(detail.get("numComments") or 0)
    info.share_count = int(detail.get("numViews") or 0)
    info.create_time = _iwara_epoch(detail.get("createdAt"))
    if isinstance(file_obj.get("duration"), (int, float)):
        info.duration = int(file_obj["duration"]) * 1000

    fid = str(file_obj.get("id") or "")
    thumb = detail.get("thumbnail")
    if fid and isinstance(thumb, int):
        info.cover_url = (f"https://files.iwara.tv/image/original/{fid}"
                          f"/thumbnail-{thumb:02d}.jpg")

    if not formats:
        raise ParseError(
            "Iwara 没返回可下载档位。可能作品刚上传还在转码，或需要登录才能查看")

    # 按画质从高到低排队：Source 在最前，然后 540 / 360
    names = [n for n in formats if n.lower() not in IWARA_SKIP_FORMATS]
    names.sort(key=_iwara_format_rank, reverse=True)
    if not names:
        raise ParseError("Iwara 只返回了预览片段，没有可下载的完整视频")

    width = int(file_obj.get("width") or 0)
    height = int(file_obj.get("height") or 0)
    options: List[Tuple[str, str]] = []
    backups: dict = {}
    for name in names:
        urls = [u for u in formats.get(name, []) if u]
        if not urls:
            continue
        label = _iwara_quality_label(name, width, height)
        options.append((label, urls[0]))
        if len(urls) > 1:
            backups[urls[0]] = urls[1:]

    if not options:
        raise ParseError("Iwara 未解析到可用的下载地址")

    info.quality_options = options
    info.url_backups = backups
    info.play_url = options[0][1]
    info.quality = options[0][0]
    info.play_url_candidates = [u for _lab, u in options]
    # 精确字节数（拿不到就不显示，不影响下载）。
    # 只探主地址：同一档位的主/备地址是同一个文件，多探一轮纯属浪费
    sizes = _iwara_probe_sizes([u for _lab, u in options])
    if sizes:
        info.quality_sizes.update(sizes)
        # 同一档位的主/备地址大小应当一致：备用地址缺大小时用主地址补齐
        for main_url, extras in backups.items():
            n = sizes.get(main_url) or 0
            if n > 0:
                for u in extras:
                    sizes.setdefault(u, n)
    return info


# --------------------------------------------------------------------- #
# Pornhub（pornhub.com）
# --------------------------------------------------------------------- #
PH_HOST = "https://www.pornhub.com"
PH_VIEWKEY_RE = re.compile(r'viewkey=([A-Za-z0-9]+)', re.I)
PH_EMBED_RE = re.compile(r'pornhub\.com/embed/([A-Za-z0-9]+)', re.I)
# 整段就是一个 viewkey（13 位十六进制，可能带 ph 前缀），允许用户直接粘 ID
PH_BARE_KEY_RE = re.compile(r'(ph)?([0-9a-f]{13})', re.I)
# 该站会限流：短时间连续请求后，同样的页面会变成「能打开但拿不到 flashvars」。
# 实测隔几秒重试即可恢复，所以要退避重试而不是一次失败就报错。
PH_RETRIES = 3
PH_RETRY_WAIT = 1.5


def pornhub_viewkey(text: str) -> str:
    """从链接 / 分享文案 / 纯 viewkey 里取视频 ID，识别失败返回空串"""
    raw = (text or "").strip()
    m = PH_VIEWKEY_RE.search(raw)
    if m:
        return m.group(1)
    m = PH_EMBED_RE.search(raw)
    if m:
        return m.group(1)
    m = PH_BARE_KEY_RE.fullmatch(raw)
    if m:
        return (m.group(1) or "") + m.group(2)
    return ""


def _ph_headers() -> dict:
    return {
        "User-Agent": PC_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _ph_flashvars(html: str) -> dict:
    """从页面里抠出 `flashvars_<id>` 这个 JS 对象

    它是个几千字符的大 JSON，**不能靠贪婪/懒惰正则括到结尾** —— 页面里
    还有其他 `};` 会提前截断。这里用花括号配对扫描（跳过字符串内部的括号
    与转义），拿到对象的确切边界再 json.loads。
    """
    m = re.search(r'flashvars_\d+\s*=\s*\{', html)
    if not m:
        return {}
    start = html.index("{", m.start())
    depth, in_str, esc = 0, False, False
    for i in range(start, len(html)):
        c = html[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(html[start:i + 1])
                    except Exception:  # noqa: BLE001
                        return {}
                    return data if isinstance(data, dict) else {}
    return {}


def _ph_duration_ms(html: str) -> int:
    """页面里的时长（毫秒）。`video_duration` 是秒数，ld+json 是 ISO 8601 时长"""
    m = re.search(r'"video_duration"\s*:\s*(\d+)', html)
    if m:
        return int(m.group(1)) * 1000
    m = re.search(r'"duration"\s*:\s*"PT(\d+)H(\d+)M(\d+)S"', html)
    if m:
        return (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))) * 1000
    return 0


def _ph_og_title(html: str) -> str:
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', html, re.I)
    if not m:
        return ""
    import html as _html
    return _html.unescape(m.group(1)).strip()


def _ph_uploader(html: str) -> str:
    """上传者昵称（尽力而为，拿不到就留空）"""
    for pat in (r'"author"\s*:\s*"([^"]{1,80})"',
                r'class="videoUploaderLink"[^>]*>([^<]{1,80})<'):
        m = re.search(pat, html)
        if m:
            return m.group(1).strip()
    return ""


def _ph_rank(label: str) -> int:
    m = re.search(r'(\d{3,4})', label or "")
    return int(m.group(1)) if m else 0


def _ph_probe_sizes(urls: List[str], page_url: str) -> dict:
    """并发探针取每档精确字节数

    ⚠️ phncdn 的直链**不带 Referer 会回 404**，探针必须带上视频页作 Referer；
    另外它也不吃 HEAD，只能 1 字节 Range 从 Content-Range 读总长。
    """
    sizes: dict = {}
    if not urls:
        return sizes
    lock = threading.Lock()

    def probe(u: str) -> None:
        headers = {"User-Agent": PC_UA, "Referer": page_url, "Range": "bytes=0-0"}
        n = 0
        try:
            r = requests.get(u, headers=headers, timeout=15, allow_redirects=True)
            m = re.search(r'/(\d+)\s*$', r.headers.get("Content-Range") or "")
            n = int(m.group(1)) if m else 0
            if n <= 0 and r.status_code == 200:
                n = int(r.headers.get("content-length") or 0)
        except Exception:  # noqa: BLE001 - 拿不到就不显示大小
            n = 0
        if n > 0:
            with lock:
                sizes[u] = n

    jobs = [threading.Thread(target=probe, args=(u,), daemon=True)
            for u in dict.fromkeys(urls)]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join(timeout=16)
    return sizes


def parse_pornhub(url: str) -> VideoInfo:
    """解析 Pornhub 视频（纯 HTTP，免登录）

    链路（2026-09 实测 5/5 稳定）：
      1) `GET /view_video.php?viewkey=<id>` → 页面里的 `flashvars_<id>`
      2) `mediaDefinitions` 里 `format=="mp4"` 那项是个 `get_media?s=<base64>`
         签名端点（`hls` 那几项是 m3u8，不用）
      3) `GET get_media`（**必须带视频页 Referer + X-Requested-With**）
         → 4 档 mp4 直链（240/480/720/1080），链接带 validfrom/validto 时效
      4) 并发 Range 探针取每档精确字节数

    直链落在 `ev.phncdn.com` 等 CDN，**不带 Referer 一律 404**，所以
    `domain.referer_for()` 认了 phncdn 这个域名。
    """
    vk = pornhub_viewkey(url)
    if not vk:
        raise ParseError("无法从链接中识别 Pornhub 视频 ID"
                         "（应形如 view_video.php?viewkey=xxxx）")
    page_url = f"{PH_HOST}/view_video.php?viewkey={vk}"
    # ⚠️ 全程用同一个 Session：
    #  · get_media 认会话 Cookie —— 裸 requests.get 会回**空数组**（不是报错，
    #    很容易误判成「这视频没档位」）
    #  · 站点按 IP 限流高频请求，保持同一会话更接近真实浏览器
    session = requests.Session()
    session.headers.update(_ph_headers())

    html, flashvars, last_err = "", {}, ""
    for attempt in range(PH_RETRIES):
        try:
            r = session.get(page_url, timeout=(10, 30))
        except Exception as e:  # noqa: BLE001
            last_err = f"连接失败（{e}）"
            r = None
        if r is not None:
            if r.status_code == 404:
                raise ParseError(f"Pornhub 视频 {vk} 不存在或已删除")
            if r.status_code == 200:
                html = r.text
                flashvars = _ph_flashvars(html)
                if flashvars:
                    break
                last_err = "页面能打开但没带视频数据（多为限流）"
            else:
                last_err = f"页面返回 HTTP {r.status_code}"
        if attempt < PH_RETRIES - 1:
            time.sleep(PH_RETRY_WAIT * (attempt + 1))
    if not flashvars:
        raise ParseError(
            f"Pornhub 没返回视频数据：{last_err}。"
            "该站会限流，稍等几秒再试；若一直失败请确认代理能打开 pornhub.com")

    defs = flashvars.get("mediaDefinitions") or []
    endpoints = [d.get("videoUrl") for d in defs
                 if isinstance(d, dict) and (d.get("format") or "").lower() == "mp4"
                 and d.get("videoUrl")]
    if not endpoints:
        live = [d.get("quality") for d in defs if isinstance(d, dict) and d.get("format") == "hls"]
        raise ParseError(
            "该 Pornhub 视频没有可直接下载的 mp4 地址"
            + (f"（站点只给了 HLS 流 {live}，多为会员专享）" if live else "（可能已下架或受限）"))

    api_headers = dict(_ph_headers(), **{
        "Referer": page_url, "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/plain, */*"})

    rows: List[dict] = []
    for ep in endpoints:
        for attempt in range(PH_RETRIES):
            try:
                rr = session.get(ep, headers=api_headers, timeout=(10, 30))
            except Exception:  # noqa: BLE001 - 端点挂了就换下一个
                break
            if rr.status_code != 200:
                break
            try:
                data = rr.json()
            except Exception:  # noqa: BLE001
                data = None
            if isinstance(data, list) and data:
                rows.extend([x for x in data
                             if isinstance(x, dict) and x.get("videoUrl")])
                break
            # 空数组同样多为限流，退避后重试
            if attempt < PH_RETRIES - 1:
                time.sleep(PH_RETRY_WAIT * (attempt + 1))
        if rows:
            break
    if not rows:
        raise ParseError("Pornhub 没有返回可下载档位（该站会限流，稍等几秒重试）")

    seen, options = set(), []
    for item in rows:
        u = item["videoUrl"]
        if u in seen:
            continue
        seen.add(u)
        q = str(item.get("quality") or "").strip()
        options.append((f"{q}p" if q.isdigit() else (q or "视频"), u))
    options.sort(key=lambda it: _ph_rank(it[0]), reverse=True)
    if not options:
        raise ParseError("Pornhub 未解析到可用的下载地址")

    info = VideoInfo()
    info.source = "pornhub"
    info.aweme_id = vk
    info.title = ((flashvars.get("video_title") or "").strip()
                  or _ph_og_title(html) or vk)
    info.author = _ph_uploader(html)
    info.duration = _ph_duration_ms(html)
    info.cover_url = (flashvars.get("image_url") or "").strip()
    info.quality_options = options
    info.play_url = options[0][1]
    info.quality = options[0][0]
    info.play_url_candidates = [u for _lab, u in options]

    sizes = _ph_probe_sizes([u for _lab, u in options], page_url)
    if sizes:
        info.quality_sizes.update(sizes)
    return info


# --------------------------------------------------------------------- #
# hanime1.me（hanime1.me）—— 成年向番剧，免登录
# --------------------------------------------------------------------- #
HN_HOST = "https://hanime1.me"
# 只认 /watch?v=<id>。实测：路径式 /watch/<id> 与 /v/<id> 都是 404，
# 参数名换成 ?id= 直接 403 —— 别按"直觉"多写几种形式，站点不认。
HN_ID_RE = re.compile(r'/watch\?v=([A-Za-z0-9_\-]+)', re.I)
HN_BARE_ID_RE = re.compile(r'^(\d{4,10})$')
HN_TITLE_SUFFIX = " - Hanime1.me"
HN_RETRIES = 2
HN_RETRY_WAIT = 1.2


def hanime1_video_id(text: str) -> str:
    """从链接或纯数字 id 里取视频 id，识别失败返回空串"""
    raw = (text or "").strip()
    m = HN_ID_RE.search(raw)
    if m:
        return m.group(1)
    m = HN_BARE_ID_RE.fullmatch(raw)
    return m.group(1) if m else ""


def _hn_session():
    """建会话。

    ⚠️ 这个站点在 Cloudflare 后面且**认 TLS 指纹**：python-requests 的
    ClientHello 会被直接断开（`SSLError: UNEXPECTED_EOF_WHILE_READING`），
    同一台机器上 curl 却能正常 200。必须用 curl_cffi 的浏览器指纹。
    curl_cffi 已在打包清单里（禁漫也依赖它），所以不算新增依赖。

    注意只有**页面**需要这层指纹；视频直链所在的 vdownload.hembed.com
    用普通 requests 就能下（HEAD / 1 字节 Range 都正常，也不吃 Referer），
    所以下载链路不用改。
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError as e:  # pragma: no cover - 打包时必带
        raise ParseError(f"缺少 curl_cffi 组件，无法解析 hanime1（{e}）") from e
    session = cffi_requests.Session(impersonate="chrome")
    session.headers.update({
        "User-Agent": PC_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    })
    return session


def _hn_sources(html: str) -> List[Tuple[int, str]]:
    """抠出所有 <source type="video/mp4">，返回 [(档位高度, 地址)]。

    属性顺序不保证（src/size/type 谁先谁后都可能），所以逐个标签内部单独
    取属性，不写成一整条正则。size 是**档位高度**（1080/720/480），
    不是字节数；页面里的排列顺序是 720 → 480 → 1080，必须自己排。
    """
    out: List[Tuple[int, str]] = []
    for tag in re.findall(r'<source\b[^>]*>', html, re.I):
        m = re.search(r'src="([^"]+)"', tag, re.I)
        if not m:
            continue
        url = m.group(1).strip()
        if ".mp4" not in url.lower():
            continue
        s = re.search(r'size="(\d+)"', tag, re.I)
        out.append((int(s.group(1)) if s else 0, url))
    return out


def _hn_title(html: str) -> str:
    """标题：详情块的 h3，兜底 og:title 去掉 ' - Hanime1.me' 后缀"""
    import html as _html
    m = re.search(r'<h3[^>]*id="shareBtn-title"[^>]*>(.*?)</h3>', html, re.S | re.I)
    if m:
        text = _html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()
        if text:
            return _html.unescape(text)
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', html, re.I)
    if not m:
        return ""
    title = _html.unescape(m.group(1)).strip()
    if title.endswith(HN_TITLE_SUFFIX):
        title = title[:-len(HN_TITLE_SUFFIX)].strip()
    return title


def _hn_author(html: str) -> str:
    """上传者：详情块的 a#video-artist-name，兜底头像 img 的 alt"""
    import html as _html
    m = re.search(r'<a[^>]*id="video-artist-name"[^>]*>(.*?)</a>', html, re.S | re.I)
    if m:
        text = _html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()
        if text:
            return text
    m = re.search(r'<img[^>]*id="video-user-avatar"[^>]*alt="([^"]*)"', html, re.I)
    if not m:
        m = re.search(r'<img[^>]*alt="([^"]*)"[^>]*id="video-user-avatar"', html, re.I)
    return _html.unescape(m.group(1)).strip() if m else ""


def _hn_cover(html: str) -> str:
    """封面：<video poster> 优先（带 secure 签名），兜底 og:image"""
    m = re.search(r'<video[^>]+poster="([^"]+)"', html, re.I)
    if m and m.group(1).startswith("http"):
        return m.group(1)
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]*)"', html, re.I)
    return m.group(1).strip() if m else ""


def _hn_probe_sizes(urls: List[str]) -> dict:
    """并发探针取每档精确字节数。

    直链带 `?secure=<签名>,<过期时间>`，**签名是强制的**（去掉 query 或改一个
    字符都回 403），所以只能用页面刚给的那条地址。
    该 CDN 支持 HEAD 也支持 Range，这里用 1 字节 Range（更保险，个别节点挡 HEAD），
    并且**不需要 Referer / Cookie**，所以直接用普通 requests。
    """
    sizes: dict = {}
    if not urls:
        return sizes
    lock = threading.Lock()

    def probe(u: str) -> None:
        n = 0
        try:
            r = requests.get(u, headers={"User-Agent": PC_UA, "Range": "bytes=0-0"},
                             timeout=15, allow_redirects=True)
            m = re.search(r'/(\d+)\s*$', r.headers.get("Content-Range") or "")
            n = int(m.group(1)) if m else 0
            if n <= 0 and r.status_code == 200:
                n = int(r.headers.get("content-length") or 0)
        except Exception:  # noqa: BLE001 - 拿不到就不显示大小
            n = 0
        if n > 0:
            with lock:
                sizes[u] = n

    jobs = [threading.Thread(target=probe, args=(u,), daemon=True)
            for u in dict.fromkeys(urls)]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join(timeout=16)
    return sizes


def parse_hanime1(url: str) -> VideoInfo:
    """解析 hanime1.me 视频（纯 HTTP + curl_cffi 指纹，免登录）

    链路（2026-09 实测）：
      1) `GET /watch?v=<id>` —— **必须 curl_cffi impersonate**（见 _hn_session）
      2) 页面 `<video>` 里直接挂着 3 个 `<source type="video/mp4" size="…">`
         （1080 / 720 / 480，HTML 里的顺序是乱的），指向
         `vdownload.hembed.com/<id>-<画质>p.mp4?secure=<签名>,<过期>`
      3) 并发 1 字节 Range 取每档精确字节数

    已知边界：
      - 页面**没有时长字段**（详情块和 JSON 里都翻过），所以 duration 留 0，
        界面显示 `--:--`。宁可不显示，也别去猜一个错的。
      - 直链是**签名 + 时效**的：解析后搁太久再下会 403。重试/续传依赖重新解析。
      - 没有备用 CDN 地址，所以 url_backups 为空。
    """
    vid = hanime1_video_id(url)
    if not vid:
        raise ParseError("无法从链接中识别 hanime1 视频 ID"
                         "（应形如 hanime1.me/watch?v=123456）")
    page_url = f"{HN_HOST}/watch?v={vid}"
    session = _hn_session()

    html, last_err = "", ""
    for attempt in range(HN_RETRIES):
        try:
            r = session.get(page_url, timeout=(10, 30))
        except Exception as e:  # noqa: BLE001
            last_err = f"连接失败（{type(e).__name__}: {e}）"
            r = None
        if r is not None:
            if r.status_code == 404:
                raise ParseError(f"hanime1 视频 {vid} 不存在或已删除")
            if r.status_code == 200:
                html = r.text
                if _hn_sources(html):
                    break
                last_err = "页面能打开但没有视频源（可能被风控拦了）"
            else:
                last_err = f"页面返回 HTTP {r.status_code}"
        if attempt < HN_RETRIES - 1:
            time.sleep(HN_RETRY_WAIT * (attempt + 1))

    sources = _hn_sources(html)
    if not sources:
        raise ParseError(
            f"hanime1 没返回可下载的视频源：{last_err}。"
            "该站有 Cloudflare 风控，稍后重试；若一直失败请确认代理能打开 hanime1.me")

    # size 属性是档位高度（1080/720/480），直接拿来当画质标签并按高度降序
    seen, options = set(), []
    for height, u in sources:
        if u in seen:
            continue
        seen.add(u)
        options.append((f"{height}p" if height else "视频", u))
    options.sort(key=lambda it: int(it[0][:-1]) if it[0].endswith("p") and
                 it[0][:-1].isdigit() else 0, reverse=True)
    if not options:
        raise ParseError("hanime1 未解析到可用的下载地址")

    info = VideoInfo()
    info.source = "hanime1"
    info.aweme_id = vid
    info.title = _hn_title(html) or vid
    info.author = _hn_author(html)
    info.duration = 0          # 站点不提供时长，见 docstring
    info.cover_url = _hn_cover(html)
    info.quality_options = options
    info.play_url = options[0][1]
    info.quality = options[0][0]
    info.play_url_candidates = [u for _lab, u in options]

    sizes = _hn_probe_sizes([u for _lab, u in options])
    if sizes:
        info.quality_sizes.update(sizes)
    return info
