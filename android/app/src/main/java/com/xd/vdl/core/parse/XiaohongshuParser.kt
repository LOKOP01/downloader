package com.xd.vdl.core.parse

import android.app.Activity
import com.xd.vdl.core.Platform
import com.xd.vdl.core.model.Quality
import com.xd.vdl.core.model.VideoInfo
import com.xd.vdl.core.net.Http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** 读取分享笔记详情页中的 SSR noteCard。 */
object XiaohongshuParser : Parser {
    private val noteId = Regex("/(?:explore|discovery/item|item)/([0-9a-fA-F]{24})")
    private val invalid = Regex("(?<=[:,\\[])\\s*(?:undefined|NaN)(?=\\s*[,}\\]])")

    override suspend fun parse(activity: Activity, url: String): VideoInfo = withContext(Dispatchers.IO) {
        val target = if (Platform.hostOf(url).endsWith("xhslink.com") ||
            Platform.hostOf(url).endsWith("xhslink.cn"))
            Http.resolveFinalUrl(url, Platform.XIAOHONGSHU) else url
        val id = noteId.find(target)?.groupValues?.get(1)
            ?: throw ParseException("请使用小红书 App 分享的单条笔记链接")
        if (Platform.of(target) != Platform.XIAOHONGSHU || target.contains("/404"))
            throw ParseException("小红书分享链接已失效，请重新复制笔记链接")

        // 保留 xsec_token 原样访问，否则详情页可能只返回空壳。
        val html = runCatching { Http.getText(target, Platform.XIAOHONGSHU) }.getOrDefault("")
        fromHtml(html, id)?.let { return@withContext it }
        val rendered = WebViewBridge(activity).loadAndExtract(target, ua = Http.UA_DESKTOP,
            settleMs = 1800, timeoutMs = 25000,
            extractJs = "document.documentElement.outerHTML").value.orEmpty()
        fromHtml(rendered, id) ?: throw ParseException(
            "未取得小红书笔记媒体，请用 App 分享的链接（含 xsec_token）重试，或填写 Cookie")
    }

    internal fun fromHtml(html: String, id: String): VideoInfo? {
        if (html.isBlank()) return null
        for (key in listOf("\"noteCard\"", "\"note\"")) {
            var pos = html.indexOf(key)
            while (pos >= 0) {
                val card = extractObject(html, pos)
                if (card != null && card.optString("noteId") == id) {
                    fromCard(card)?.let { return it }
                }
                pos = html.indexOf(key, pos + key.length)
            }
        }
        return null
    }

    /** 按引号和转义状态配对花括号，避免在标题中误截断 JSON。 */
    private fun extractObject(html: String, pos: Int): JSONObject? {
        val start = html.indexOf('{', pos)
        if (start < 0 || start - pos > 100) return null
        var depth = 0
        var quoted = false
        var escaped = false
        for (i in start until minOf(html.length, start + 400_000)) {
            val c = html[i]
            if (quoted) {
                if (escaped) escaped = false
                else if (c == '\\') escaped = true
                else if (c == '"') quoted = false
            } else when (c) {
                '"' -> quoted = true
                '{' -> depth++
                '}' -> {
                    depth--
                    if (depth == 0) return runCatching {
                        JSONObject(html.substring(start, i + 1).replace(invalid, "null"))
                    }.getOrNull()
                }
            }
        }
        return null
    }

    internal fun fromCard(card: JSONObject): VideoInfo? {
        if (card.optString("noteId").isEmpty()) return null
        val images = card.optJSONArray("imageList")
        val imageUrls = (0 until (images?.length() ?: 0)).mapNotNull { i ->
            val image = images?.optJSONObject(i) ?: return@mapNotNull null
            cleanUrl(image.optString("urlDefault").ifEmpty { image.optString("urlPre") })
                .takeIf { it.startsWith("https://") }
        }.distinct()
        val video = card.optJSONObject("video")
        val stream = video?.optJSONObject("media")?.optJSONObject("stream")
        val qualities = mutableListOf<Quality>()
        val labels = mutableSetOf<String>()
        if (stream != null) for (codec in stream.keys()) {
            val variants = stream.optJSONArray(codec) ?: continue
            for (i in 0 until variants.length()) {
                val v = variants.optJSONObject(i) ?: continue
                val backup = v.optJSONArray("backupUrls")
                val urls = (listOf(v.optString("masterUrl")) +
                    (0 until (backup?.length() ?: 0)).map { backup?.optString(it).orEmpty() })
                    .map(::cleanUrl).filter { it.startsWith("https://") }.distinct()
                if (urls.isEmpty()) continue
                val w = v.optInt("width")
                val h = v.optInt("height")
                val label = if (w > 0 && h > 0) "${w}×${h}" else "视频"
                val type = v.optString("videoCodec").ifEmpty { codec }
                if (!labels.add("$label ($type)")) continue
                qualities.add(Quality("$label ($type)", urls.first(), sizeBytes = v.optLong("size"),
                    urlBackups = urls.drop(1)))
            }
        }
        qualities.sortByDescending { it.sizeBytes }
        if (qualities.isEmpty() && imageUrls.isEmpty()) return null
        val title = card.optString("title").ifBlank { card.optString("desc").take(60) }
            .ifBlank { "小红书笔记" }
        val seconds = video?.optJSONObject("capa")?.optDouble("duration", 0.0) ?: 0.0
        return VideoInfo(Platform.XIAOHONGSHU, title,
            author = card.optJSONObject("user")?.optString("nickname").orEmpty(),
            coverUrl = imageUrls.firstOrNull().orEmpty(), durationMs = (seconds * 1000).toLong(),
            qualities = qualities, images = if (qualities.isEmpty()) imageUrls else emptyList())
    }

    private fun cleanUrl(value: String): String {
        val url = value.replace("\\u002F", "/").replace("\\u002f", "/")
            .replace("\\u0026", "&").replace("\\/", "/").replace("&amp;", "&")
        return when {
            url.startsWith("//") -> "https:$url"
            url.startsWith("http://") -> "https://${url.removePrefix("http://")}" 
            else -> url
        }
    }
}

