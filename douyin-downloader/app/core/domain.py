# -*- coding: utf-8 -*-
"""域名 / 平台识别工具（下载器与解析器共用，避免各处重复硬编码域名表）"""
import re
import urllib.parse

# 从一段文本（分享文案 / 剪贴板内容）里提取首个 http(s) 链接
URL_PATTERN = re.compile(r'https?://[^\s，。"\']+', re.I)

# 各平台的域名特征。匹配规则见 host_matches：带点的按子域后缀比对，
# 不带点的主干（instagram / xhscdn）按域名标签边界比对。
# 视频/图片 CDN 用的是 douyinvod.com / douyinpic.com，与主站域名不同根，
# 必须显式列出：旧实现靠 `"douyin.com" in host` 的子串匹配侥幸覆盖了它们，
# 换成按标签边界匹配后（见 _host_is）就得写全，否则不带 Referer 会 403。
DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com", "douyinvod.com", "douyinpic.com")
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
    netloc = urllib.parse.urlparse(url or "").netloc.lower()
    # 去掉 userinfo（http://user:pass@host/）与端口，只留纯主机名
    netloc = netloc.rsplit("@", 1)[-1]
    if netloc.startswith("["):          # IPv6 字面量 [::1]:8080
        return netloc.split("]", 1)[0] + "]"
    return netloc.split(":", 1)[0]


# 主干型关键字后面允许出现的官方 TLD。用白名单而不是黑名单，
# 这样 instagram.com.evil.cn（主干后面跟 com 再接恶意域）会被拒。
_OFFICIAL_TLDS = {"com", "tv", "me", "net", "org", "vip"}


def _host_is(host: str, keyword: str) -> bool:
    """host 是否命中域名关键字（按域名标签边界匹配，不做裸子串判断）

    旧实现是 `keyword in host`，于是 `douyin.com.evil.cn`、
    `evilbilibili.com`、`notx.com` 都能被认成官方站点，进而把用户 Cookie
    发到不可控的域名。现在分两类：

    - 带点的关键字是完整域（douyin.com）：host 必须等于它，或是它的子域
      （www.douyin.com / v.douyin.com），`douyin.com.evil.cn` 不命中。
    - 不带点的关键字是域名主干（instagram / xhscdn / bilivideo）：host 中
      某个标签必须以主干结尾，且该标签之后紧接官方 TLD、TLD 之后不能再
      有别的域。于是 www.instagram.com、scontent.cdninstagram.com 命中，
      而 instagram.com.evil.cn、evilbilibili.com、myxiaohongshu.com 不命中。
    """
    if not host or not keyword:
        return False
    kw = keyword.lower().strip().strip(".")
    if not kw:
        return False
    if host == kw:
        return True
    if "." in kw:
        # 完整域名：host 必须以 .kw 结尾（www.douyin.com 命中，
        # douyin.com.evil.cn 不命中）
        return host.endswith("." + kw)
    # 主干型关键字（instagram / xhscdn / bilivideo）：在除 TLD 外的各段里找
    # 以主干结尾的标签，并要求它后面紧跟官方 TLD。于是
    #   www.instagram.com / scontent.cdninstagram.com -> 命中
    #   instagram.com.evil.cn -> 不命中（主干的下一段是 com，但 com 之后
    #     还有恶意域，说明真实注册域是 evil.cn）
    labels = host.split(".")
    if len(labels) < 2:
        return False
    for i in range(len(labels) - 1):
        if labels[i] == kw or labels[i].endswith(kw):
            # 首段必须与主干「等长或带官方前缀」，否则 evilbilibili.com、
            # myxiaohongshu.com 这类拼接的钓鱼域名会被放行。
            # 允许的前缀都是官方 CDN 子域（cdninstagram / sns-img-hw 等由
            # 带点关键字覆盖，这里只需放行首段本身）。
            if i == 0 and labels[i] != kw:
                return False
            # 主干后面必须是官方 TLD，且 TLD 之后不能再有别的域
            return labels[i + 1] in _OFFICIAL_TLDS and i + 2 == len(labels)
    return False


def host_matches(url: str, keywords) -> bool:
    """URL 主机名是否命中任一关键字（按域名标签边界匹配）"""
    host = host_of(url)
    return any(_host_is(host, k) for k in keywords)


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
