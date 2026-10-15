# -*- coding: utf-8 -*-
"""域名 / 平台识别工具（下载器与解析器共用，避免各处重复硬编码域名表）"""
import re
import urllib.parse

# 从一段文本（分享文案 / 剪贴板内容）里提取首个 http(s) 链接
URL_PATTERN = re.compile(r'https?://[^\s，。"\']+', re.I)

# 各平台的域名特征（子串匹配即可覆盖 www / api / cdn 等前缀）
DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")
X_HOSTS = ("twimg.com", "x.com", "twitter.com", "t.co")
INSTAGRAM_HOSTS = ("instagram", "cdninstagram", "fbcdn")
BILIBILI_HOSTS = ("bilibili", "bilivideo", "hdslb", "akamaized", "b23.tv")
XIAOHONGSHU_HOSTS = ("xhscdn", "xiaohongshu", "xhslink")
JMCOMIC_HOSTS = ("18comic", "jmcomic", "jmapinode", "jmapiproxy", "jm-comic")
# iwara.tv 覆盖 www / api / files / filesq，以及下载用的角色名 CDN（bailu / herta / …）
IWARA_HOSTS = ("iwara.tv",)
# pornhub.com 覆盖 www / 接口；视频直链落在 ev.phncdn.com 等 phncdn 域，
# 该 CDN **不带 Referer 会回 404**，所以必须认出它并按平台给 Referer
PORNHUB_HOSTS = ("pornhub.com", "phncdn.com")
# hanime1.me：站点在 Cloudflare 后面且认 TLS 指纹（python-requests 会 SSL EOF，
# 必须 curl_cffi impersonate），视频直链落在 vdownload.hembed.com。
# 该 CDN 不吃 Referer、也不用 Cookie，普通 requests 就能下。
HANIME1_HOSTS = ("hanime1.me", "hembed.com")

# 平台标识 -> 该平台"需要带 Cookie 请求 CDN"的域名集合
PLATFORM_HOSTS = {
    "douyin": DOUYIN_HOSTS,
    "x": X_HOSTS,
    "instagram": INSTAGRAM_HOSTS,
    "bilibili": BILIBILI_HOSTS,
    "xiaohongshu": XIAOHONGSHU_HOSTS,
    "jmcomic": JMCOMIC_HOSTS,
    "iwara": IWARA_HOSTS,
    "pornhub": PORNHUB_HOSTS,
    "hanime1": HANIME1_HOSTS,
}

# 下载外链时需要携带登录态 Cookie 的平台（其余平台 CDN 不带 Cookie 更稳，
# 且抖音的 Cookie 会给 CDN 请求带来额外风控特征）
COOKIE_PLATFORMS = ("instagram", "bilibili")


def host_of(url: str) -> str:
    """取 URL 的主机名（小写，不带端口）"""
    return urllib.parse.urlparse(url or "").netloc.lower()


def host_matches(url: str, keywords) -> bool:
    """URL 主机名是否命中任一关键字"""
    host = host_of(url)
    return any(k in host for k in keywords)


def platform_of_host(url: str) -> str:
    """按主机名判断平台，未知返回空串"""
    for platform, hosts in PLATFORM_HOSTS.items():
        if host_matches(url, hosts):
            return platform
    return ""


def platform_of_source(source: str) -> str:
    """把 VideoInfo.source 归一化成平台标识（兼容旧值）"""
    src = (source or "").strip().lower()
    if src == "telegram" or src == "tg":
        return ""
    if src in PLATFORM_HOSTS:
        return src
    if src in ("xhs", "redbook"):
        return "xiaohongshu"
    if src in ("bili",):
        return "bilibili"
    if src in ("ins", "ig"):
        return "instagram"
    if src in ("jm", "18comic"):
        return "jmcomic"
    if src in ("iwara",):
        return "iwara"
    if src in ("ph", "pornhub"):
        return "pornhub"
    if src in ("hanime", "hanime1.me", "hanime1me"):
        return "hanime1"
    return src


def platform_of(url: str = "", source: str = "") -> str:
    """优先按 source 归一化，其次按 URL 主机名判断"""
    p = platform_of_source(source)
    return p or platform_of_host(url)


def needs_cookie_for_source(source: str) -> bool:
    """该来源的 CDN 下载是否需要携带 Cookie"""
    return platform_of_source(source) in COOKIE_PLATFORMS


def needs_cookie_for_url(url: str) -> bool:
    """该下载地址所在域名是否需要携带 Cookie"""
    return platform_of_host(url) in COOKIE_PLATFORMS


def referer_for(url: str) -> str:
    """按目标域名给出正确的 Referer（外站 Referer 会被 CDN 403）"""
    platform = platform_of_host(url)
    return {
        "x": "https://x.com/",
        "instagram": "https://www.instagram.com/",
        "bilibili": "https://www.bilibili.com/",
        "xiaohongshu": "https://www.xiaohongshu.com/",
        "jmcomic": "https://18comic.vip/",
        "iwara": "https://www.iwara.tv/",
        "pornhub": "https://www.pornhub.com/",
        "hanime1": "https://hanime1.me/",
    }.get(platform, "https://www.douyin.com/")
