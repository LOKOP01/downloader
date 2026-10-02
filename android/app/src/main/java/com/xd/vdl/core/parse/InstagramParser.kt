package com.xd.vdl.core.parse

import android.app.Activity
import com.xd.vdl.core.Platform
import com.xd.vdl.core.model.Quality
import com.xd.vdl.core.model.VideoInfo
import com.xd.vdl.core.net.Http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import okhttp3.Request

/** Instagram 的公开帖子和已配置 Cookie 的帖子，优先读取本条媒体接口。 */
object InstagramParser : Parser {
    private val post = Regex("/(?:p|reel|reels)/([A-Za-z0-9_-]+)")
    private const val alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

    override suspend fun parse(activity: Activity, url: String): VideoInfo = withContext(Dispatchers.IO) {
        val code = post.find(url)?.groupValues?.get(1)
            ?: throw ParseException("请使用 Instagram 单个帖子或 Reel 的链接")
        val id = code.fold(0UL) { value, c -> value * 64UL + alphabet.indexOf(c).toULong() }
        val api = "https://www.instagram.com/api/v1/media/$id/info/"
        val cookie = Http.cookieFor(Platform.INSTAGRAM)
        val headers = Request.Builder().url(api)
            .header("User-Agent", Http.UA_DESKTOP)
            .header("Referer", "https://www.instagram.com/p/$code/")
            .header("X-IG-App-ID", "936619743392459")
        if (cookie.isNotBlank()) headers.header("Cookie", cookie)
        val csrf = com.xd.vdl.core.net.CookieStore.split(cookie)["csrftoken"]
        if (!csrf.isNullOrBlank()) headers.header("X-CSRFToken", csrf)
        val item = runCatching {
            Http.client.newCall(headers.build()).execute().use { response ->
                if (!response.isSuccessful) null
                else JSONObject(response.body?.string().orEmpty()).optJSONArray("items")?.optJSONObject(0)
            }
        }.getOrNull()
        if (item != null && item.optString("code") == code) {
            val result = fromItem(item)
            if (result.qualities.isNotEmpty() || result.images.isNotEmpty()) return@withContext result
        }

        // OkHttp 的会话请求有时被返回登录墙；在真实站点 WebView 内重试，
        // 浏览器自动附带登录 Cookie、CSRF 和站点请求环境。
        val pageUrl = "https://www.instagram.com/p/$code/"
        val browser = runCatching {
            WebViewBridge(activity).fetchInPage(pageUrl, mediaScript(id), cookie)
        }.getOrNull()?.let { runCatching { JSONObject(it) }.getOrNull() }
        val browserItem = browser?.optString("api").orEmpty().takeIf { it.startsWith("{") }
            ?.let { runCatching {
                JSONObject(it).optJSONArray("items")?.optJSONObject(0)
            }.getOrNull() }
        if (browserItem != null && browserItem.optString("code") == code) {
            val info = fromItem(browserItem)
            if (info.qualities.isNotEmpty() || info.images.isNotEmpty()) return@withContext info
        }

        // 某些公开帖子接口不给 items：只取当前帖子 og 标签，拒绝登录页缩略图。
        val html = if (browser?.optString("video").isNullOrBlank() &&
            browser?.optString("image").isNullOrBlank()) runCatching {
            Http.getText(pageUrl, Platform.INSTAGRAM)
        }.getOrDefault("") else ""
        val video = browser?.optString("video").orEmpty().ifBlank { meta(html, "og:video") }
        val image = browser?.optString("image").orEmpty().ifBlank { meta(html, "og:image") }
        val title = browser?.optString("title").orEmpty().ifBlank { meta(html, "og:title") }
        val isVideo = url.contains("/reel/") || url.contains("/reels/") ||
            browser?.optBoolean("isVideo") == true || meta(html, "og:type").contains("video")
        val validImage = image.startsWith("https://") &&
            (Platform.hostOf(image).endsWith("cdninstagram.com") ||
                Platform.hostOf(image).endsWith("fbcdn.net"))
        if (video.isBlank() && (!validImage || isVideo ||
            title.equals("Instagram", ignoreCase = true) || title.contains("Log in", ignoreCase = true))) {
            throw ParseException("未取得 Instagram 媒体：请确认 sessionid 有效且帖子可访问，再重试")
        }
        VideoInfo(Platform.INSTAGRAM, title.ifBlank { "Instagram $code" }, coverUrl = image,
            qualities = if (video.startsWith("https://")) listOf(Quality("视频", video)) else emptyList(),
            images = if (video.isBlank() && validImage) listOf(image) else emptyList())
    }

    private fun mediaScript(id: ULong): String = """
        (async () => {
            try {
                const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
                const headers = {'X-IG-App-ID':'936619743392459'};
                if (match) headers['X-CSRFToken'] = decodeURIComponent(match[1]);
                const response = await fetch('/api/v1/media/$id/info/',
                    {headers, credentials:'include'});
                const api = response.ok ? await response.text() : '';
                const meta = key => document.querySelector('meta[property="' + key + '"]')?.content || '';
                const media = document.querySelector('video');
                vdlResult.deliver(JSON.stringify({api,
                    video: meta('og:video') || (media?.currentSrc?.startsWith('https:') ? media.currentSrc : ''),
                    image: meta('og:image'), title: meta('og:title'),
                    isVideo: !!media || meta('og:type').includes('video')}));
            } catch (e) { vdlResult.deliver('{}'); }
        })();
    """.trimIndent()
    private fun fromItem(item: JSONObject): VideoInfo {
        val versions = item.optJSONArray("video_versions")
        val qualities = (0 until (versions?.length() ?: 0)).mapNotNull { i ->
            val v = versions?.optJSONObject(i) ?: return@mapNotNull null
            val u = v.optString("url").replace("&amp;", "&")
            if (!u.startsWith("http")) null else Quality(
                "${v.optInt("width")}×${v.optInt("height")}", u)
        }.distinctBy { it.url }.sortedByDescending { it.label.substringBefore('×').toIntOrNull() ?: 0 }
        val cover = item.optJSONObject("image_versions2")?.optJSONArray("candidates")
            ?.optJSONObject(0)?.optString("url").orEmpty().replace("&amp;", "&")
        val carousel = item.optJSONArray("carousel_media")
        val carouselVideos = (0 until (carousel?.length() ?: 0)).map { i ->
            val variants = carousel?.optJSONObject(i)?.optJSONArray("video_versions")
            (0 until (variants?.length() ?: 0)).mapNotNull { j ->
                val v = variants?.optJSONObject(j) ?: return@mapNotNull null
                v.optString("url").takeIf { it.startsWith("http") }?.let {
                    Quality("${v.optInt("width")}×${v.optInt("height")}", it.replace("&amp;", "&"))
                }
            }
        }
        val videos = (qualities + carouselVideos.firstOrNull { it.isNotEmpty() }.orEmpty())
            .distinctBy { it.url }
        val images = if (videos.isEmpty() && carousel != null) {
            (0 until carousel.length()).mapNotNull { i ->
                carousel.optJSONObject(i)?.optJSONObject("image_versions2")
                    ?.optJSONArray("candidates")?.optJSONObject(0)?.optString("url")
                    ?.replace("&amp;", "&")?.takeIf { it.startsWith("http") }
            }.distinct()
        } else emptyList()
        return VideoInfo(Platform.INSTAGRAM,
            item.optJSONObject("caption")?.optString("text").orEmpty().ifBlank { "Instagram 帖子" },
            author = item.optJSONObject("user")?.optString("username").orEmpty(),
            coverUrl = cover, durationMs = (item.optDouble("video_duration", 0.0) * 1000).toLong(),
            qualities = videos, images = if (videos.isEmpty() && !item.optBoolean("is_video") && item.optInt("media_type") != 2) images.ifEmpty { listOfNotNull(cover.takeIf { it.startsWith("http") }) } else emptyList())
    }

    private fun meta(html: String, property: String): String {
        val tag = Regex("<meta\\b[^>]*>", RegexOption.IGNORE_CASE).findAll(html).firstOrNull {
            Regex("(?:property|name)=[\\\"']${Regex.escape(property)}[\\\"']", RegexOption.IGNORE_CASE)
                .containsMatchIn(it.value)
        }?.value.orEmpty()
        return Regex("content=[\\\"']([^\\\"']+)[\\\"']", RegexOption.IGNORE_CASE)
            .find(tag)?.groupValues?.get(1).orEmpty()
            .replace("&amp;", "&").replace("&quot;", "\"").replace("&#39;", "'")
    }
}






