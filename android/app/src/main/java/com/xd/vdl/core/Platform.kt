package com.xd.vdl.core

import java.net.URI

/** 支持的平台。域名判定集中在这里，避免各处硬编码。 */
enum class Platform(val label: String) {
    DOUYIN("抖音"),
    BILIBILI("B站"),
    X("X"),
    INSTAGRAM("Instagram"),
    XIAOHONGSHU("小红书"),
    JMCOMIC("禁漫"),
    UNKNOWN("未知");

    /** 下载/接口请求要带的 Referer，缺失会被 CDN 403 */
    val referer: String
        get() = when (this) {
            DOUYIN -> "https://www.douyin.com/"
            BILIBILI -> "https://www.bilibili.com/"
            X -> "https://x.com/"
            INSTAGRAM -> "https://www.instagram.com/"
            XIAOHONGSHU -> "https://www.xiaohongshu.com/"
            JMCOMIC -> "https://18comic.vip/"
            UNKNOWN -> ""
        }

    /** 读 Cookie 用哪个 URL（CookieManager 按域取） */
    val cookieUrl: String
        get() = when (this) {
            DOUYIN -> "https://www.douyin.com"
            BILIBILI -> "https://www.bilibili.com"
            X -> "https://x.com"
            INSTAGRAM -> "https://www.instagram.com"
            XIAOHONGSHU -> "https://www.xiaohongshu.com"
            JMCOMIC -> "https://18comic.vip"
            UNKNOWN -> ""
        }

    companion object {
        /**
         * 分享文案里通常混着中文和空格（抖音尤其明显），
         * 直接拿整段去解析 host 会失败，先把 URL 抠出来。
         */
        private val URL_RE =
            Regex("https?://[^\\s\\u4e00-\\u9fff，。！？；：、（）【】《》\"']+",
                RegexOption.IGNORE_CASE)

        /** 禁漫的常见域名段（与桌面版 `domain.JMCOMIC_HOSTS` 一致） */
        private val JM_HOSTS = listOf(
            "18comic", "jmcomic", "jmapinode", "jmapiproxy", "jm-comic",
        )

        fun extractUrls(text: String): List<String> = URL_RE.findAll(text.trim())
            .map { it.value.trimEnd('.', ',', ';', ':', '!', '?', ')', ']', '}') }
            .toList()

        fun extractUrl(text: String): String? = extractUrls(text).firstOrNull()

        fun hostOf(url: String): String = runCatching {
            val s = if (url.startsWith("http")) url else "https://$url"
            URI(s).host ?: ""
        }.getOrDefault("").lowercase()

        fun of(url: String): Platform {
            val h = hostOf(url)
            if (h.isEmpty()) return platformOfId(url)
            return when {
                h == "douyin.com" || h.endsWith(".douyin.com") ||
                    h == "iesdouyin.com" || h.endsWith(".iesdouyin.com") -> DOUYIN

                h == "bilibili.com" || h.endsWith(".bilibili.com") ||
                    h == "b23.tv" || h.endsWith(".b23.tv") -> BILIBILI

                h == "x.com" || h.endsWith(".x.com") ||
                    h == "twitter.com" || h.endsWith(".twitter.com") -> X

                h == "instagram.com" || h.endsWith(".instagram.com") -> INSTAGRAM
                h == "xiaohongshu.com" || h.endsWith(".xiaohongshu.com") ||
                    h == "xhslink.com" || h.endsWith(".xhslink.com") ||
                    h == "xhslink.cn" || h.endsWith(".xhslink.cn") -> XIAOHONGSHU

                JM_HOSTS.any { h.contains(it) } -> JMCOMIC

                else -> platformOfId(url)
            }
        }

        /**
         * 没有 URL 时靠内容判断 —— 禁漫车号（`JM123` / 纯数字）是常见粘贴形态。
         * 命中才返回 [JMCOMIC]，否则 [UNKNOWN]。
         */
        private fun platformOfId(text: String): Platform =
            if (com.xd.vdl.core.parse.JmParser.looksLikeCode(text)) JMCOMIC else UNKNOWN
    }
}


