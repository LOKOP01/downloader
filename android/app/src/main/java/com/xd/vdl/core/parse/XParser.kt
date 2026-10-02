package com.xd.vdl.core.parse

import android.app.Activity
import com.xd.vdl.core.AppLog
import com.xd.vdl.core.Platform
import com.xd.vdl.core.model.Quality
import com.xd.vdl.core.model.VideoInfo
import com.xd.vdl.core.net.CookieStore
import com.xd.vdl.core.net.Http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * X(Twitter)：三条链路，按「快而稳 → 慢而脆」排序（与桌面版 `app/core/sites.py` 一致）。
 *
 *  1. **GraphQL `TweetResultByRestId`**（纯 HTTP + Cookie）：实测 0.4s 出结果，
 *     variants 带 `bitrate`，是唯一能给出文件大小的来源。有登录态时走这条。
 *  2. **syndication**（`cdn.syndication.twimg.com`，Googlebot UA，免登录）：
 *     敏感推文 X 只回墓碑（`TweetTombstone`），variants 也不带 bitrate。
 *  3. **WebView 渲染拦截**：前两条都挂了才用。慢且脆，仅作兜底。
 *
 * 原来只有第 3 条 —— X 的移动端页面在 WebView 里加载很慢、而且经常一个 mp4 请求都不发，
 * 于是稳定报「未找到视频直链」。
 */
object XParser : Parser {

    /** 与 x.com 前端同步的公开 Bearer（桌面版同款） */
    private const val BEARER =
        "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs" +
            "=1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

    /** queryId 会随 X 前端更新轮换，过期的返回 400/404；逐个换着试 */
    private val QUERY_IDS = listOf(
        "Xl0tsHf4AzflMRjbw9e70A",
        "2ICDjqPd81tulZcYrtpTuQ",
        "nBS-WpgA6ZG0CyNHD517JQ",
        "xOhkmRac04YFZmOzU9PJHg",
    )

    /** 运行时试成功的 queryId，后续优先复用 */
    @Volatile
    private var liveQueryId = ""

    private const val FIELD_TOGGLES = """{"withArticleRichContentState":false}"""

    private val FEATURES = """
        {"creator_subscriptions_tweet_preview_api_enabled":true,
        "tweetypie_unmention_optimization_enabled":true,
        "responsive_web_edit_tweet_api_enabled":true,
        "graphql_is_translatable_rweb_tweet_is_translatable_enabled":true,
        "view_counts_everywhere_api_enabled":true,
        "longform_notetweets_consumption_enabled":true,
        "responsive_web_twitter_article_tweet_consumption_enabled":false,
        "tweet_awards_web_tipping_enabled":false,
        "freedom_of_speech_not_reach_fetch_enabled":true,
        "standardized_nudges_misinfo":true,
        "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled":true,
        "longform_notetweets_rich_text_read_enabled":true,
        "longform_notetweets_inline_media_enabled":true,
        "responsive_web_graphql_exclude_directive_enabled":true,
        "verified_phone_label_enabled":false,
        "responsive_web_media_download_video_enabled":false,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,
        "responsive_web_graphql_timeline_navigation_enabled":true,
        "responsive_web_enhance_cards_enabled":false}
    """.trimIndent().replace("\n", "")

    private val TWEET_ID_RE = Regex("/status(?:es)?/(\\d+)")

    /** twimg 地址路径里的 `/{宽}x{高}/` */
    private val URL_RES_RE = Regex("/(\\d{2,5})x(\\d{2,5})/")

    /** 画质标签里的 `宽×高` */
    private val LABEL_RES_RE = Regex("(\\d{2,5})×(\\d{2,5})")

    override suspend fun parse(activity: Activity, url: String): VideoInfo {
        val tweetId = TWEET_ID_RE.find(url)?.groupValues?.get(1)
            ?: throw ParseException("无法从链接中识别推文 ID")

        val cookie = Http.cookieFor(Platform.X)
        val loggedIn = CookieStore.has(cookie, "auth_token")
        val reasons = mutableListOf<String>()
        val t0 = System.currentTimeMillis()

        // 1) 有登录态先走 GraphQL：最快，且带 bitrate
        if (loggedIn) {
            try {
                val info = fillExactSizes(viaGraphQL(tweetId))
                AppLog.i("X 解析成功(GraphQL) ${info.qualities.size} 档 / ${System.currentTimeMillis() - t0}ms")
                return info
            } catch (e: Exception) {
                reasons += "GraphQL ${e.message}"
            }
        }

        // 2) syndication：免登录，敏感内容取不到
        try {
            val info = fillExactSizes(viaSyndication(tweetId))
            AppLog.i("X 解析成功(syndication) ${info.qualities.size} 档 / ${System.currentTimeMillis() - t0}ms")
            return info
        } catch (e: Exception) {
            reasons += "syndication ${e.message}"
        }

        // 3) WebView 兜底
        try {
            val info = viaWebView(activity, url)
            AppLog.i("X 解析成功(浏览器兜底) ${info.qualities.size} 档")
            return info
        } catch (e: Exception) {
            reasons += "浏览器 ${e.message}"
        }

        val detail = reasons.joinToString("；")
        AppLog.w("X 解析失败：$detail")
        throw ParseException(
            if (loggedIn) "未找到视频直链（$detail）"
            else "未找到视频直链（$detail）。该推文可能需要登录，先在设置里填好 X 的 auth_token / ct0"
        )
    }

    // ------------------------------------------------------------------ //
    // 1) GraphQL
    // ------------------------------------------------------------------ //

    private suspend fun viaGraphQL(tweetId: String): VideoInfo = withContext(Dispatchers.IO) {
        val kv = CookieStore.split(Http.cookieFor(Platform.X))
        val headers = mutableMapOf(
            "authorization" to "Bearer $BEARER",
            "content-type" to "application/json",
        )
        if (kv["auth_token"].orEmpty().isNotEmpty()) {
            headers["x-csrf-token"] = kv["ct0"].orEmpty()
        } else {
            // 无登录态：先领一个游客令牌
            runCatching {
                val g = Http.call(
                    "https://api.x.com/1.1/guest/activate.json",
                    Platform.X, extraHeaders = headers, post = true,
                )
                if (g.ok) {
                    JSONObject(g.body).optString("guest_token")
                        .takeIf { it.isNotEmpty() }?.let { headers["x-guest-token"] = it }
                }
            }
        }

        val params = mapOf(
            "variables" to """{"tweetId":"$tweetId","withCommunity":false,""" +
                """"includePromotedContent":false,"withVoice":false}""",
            "features" to FEATURES,
            "fieldToggles" to FIELD_TOGGLES,
        )

        val ids = (listOf(liveQueryId) + QUERY_IDS).filter { it.isNotEmpty() }.distinct()
        var last = 0
        for (qid in ids) {
            val r = Http.call(
                "https://api.x.com/graphql/$qid/TweetResultByRestId",
                Platform.X, extraHeaders = headers, params = params,
            )
            last = r.code
            if (r.code == 400 || r.code == 404) continue      // queryId 过期，换下一个
            if (!r.ok) throw ParseException("HTTP ${r.code}")
            liveQueryId = qid
            return@withContext fromGraphQL(r.body)
        }
        throw ParseException("HTTP $last（queryId 可能已过期）")
    }

    private fun fromGraphQL(body: String): VideoInfo {
        val root = runCatching { JSONObject(body) }.getOrNull()
            ?: throw ParseException("返回不是 JSON")
        var result = root.optJSONObject("data")
            ?.optJSONObject("tweetResult")?.optJSONObject("result")
            ?: throw ParseException("未返回推文数据")

        when (result.optString("__typename")) {
            "TweetWithVisibilityResults" -> result = result.optJSONObject("tweet") ?: JSONObject()
            "TweetUnavailable" -> {
                val reason = result.optString("reason")
                throw ParseException(
                    if (reason.contains("Nsfw")) "敏感内容需要登录态，检查 auth_token 是否有效"
                    else "推文不可用：${reason.ifEmpty { "未知原因" }}"
                )
            }
        }

        val status = result.optJSONObject("legacy")
            ?: throw ParseException("未解析到推文内容，Cookie 可能已过期")
        val user = result.optJSONObject("core")
            ?.optJSONObject("user_results")?.optJSONObject("result")
            ?.optJSONObject("legacy")
        return buildInfo(status, user)
    }

    // ------------------------------------------------------------------ //
    // 2) syndication
    // ------------------------------------------------------------------ //

    private suspend fun viaSyndication(tweetId: String): VideoInfo = withContext(Dispatchers.IO) {
        val r = Http.call(
            "https://cdn.syndication.twimg.com/tweet-result",
            Platform.X,
            ua = "Googlebot",
            params = mapOf("id" to tweetId, "token" to syndicationToken(tweetId)),
        )
        if (!r.ok) throw ParseException("HTTP ${r.code}")
        val data = runCatching { JSONObject(r.body) }.getOrNull()
            ?: throw ParseException("返回不是 JSON")
        if (data.optString("__typename") == "TweetTombstone") {
            throw ParseException("敏感/受限推文，需要登录态")
        }
        fromSyndication(data)
    }

    /** token = ((id / 1e15) * PI) 转 36 进制后去掉所有 0 和小数点（与 x.com 前端一致） */
    private fun syndicationToken(twid: String): String {
        val digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        val value = (twid.toDoubleOrNull() ?: 0.0) / 1e15 * Math.PI
        var n = value.toLong()
        var frac = value - n

        val sb = StringBuilder()
        if (n == 0L) sb.append('0')
        while (n > 0) {
            sb.insert(0, digits[(n % 36).toInt()])
            n /= 36
        }
        var guard = 0
        while (frac > 0 && guard < 20) {
            frac *= 36
            val d = frac.toInt()
            sb.append(digits[d.coerceIn(0, 35)])
            frac -= d
            guard++
        }
        return sb.toString().replace("0", "").replace(".", "")
    }

    private fun fromSyndication(data: JSONObject): VideoInfo {
        val video = data.optJSONObject("video")
        val variants = video?.optJSONArray("variants")
        val durationMs = video?.optLong("durationMs", 0L) ?: 0L

        val mp4 = mutableListOf<Pair<Int, String>>()
        var i = 0
        while (variants != null && i < variants.length()) {
            val v = variants.optJSONObject(i)
            i++
            if (v == null) continue
            if (v.optString("type").startsWith("video/mp4")) {
                val src = v.optString("src").replace("\\u0026", "&")
                if (src.isNotEmpty()) mp4.add(v.optInt("bitrate", 0) to src)
            }
        }
        val qualities = toQualities(mp4, durationMs)

        val images = mutableListOf<String>()
        val photos = data.optJSONArray("photos")
        i = 0
        while (photos != null && i < photos.length()) {
            photos.optJSONObject(i)?.optString("url")
                ?.takeIf { it.isNotEmpty() }?.let { images.add(it) }
            i++
        }
        var cover = data.optJSONArray("mediaDetails")?.optJSONObject(0)
            ?.optString("media_url_https").orEmpty()
        if (cover.isEmpty()) cover = images.firstOrNull().orEmpty()

        if (qualities.isEmpty() && images.isEmpty()) {
            throw ParseException("该推文不含视频或图片")
        }
        return VideoInfo(
            platform = Platform.X,
            title = cleanTitle(data.optString("text")),
            author = data.optJSONObject("user")?.let {
                it.optString("name").ifEmpty { it.optString("screen_name") }
            }.orEmpty(),
            coverUrl = cover,
            durationMs = durationMs,
            qualities = qualities,
            images = images,
        )
    }

    // ------------------------------------------------------------------ //
    // 3) WebView 兜底（慢，最后手段）
    // ------------------------------------------------------------------ //

    private const val EXTRACT_JS = """
        (function(){
          try {
            var mv = document.querySelector('meta[property="og:video:url"]')
                  || document.querySelector('meta[property="og:video"]')
                  || document.querySelector('meta[property="twitter:player:stream"]');
            return JSON.stringify({
              title: (document.querySelector('meta[property="og:title"]')||{}).content
                     || document.title || '',
              cover: (document.querySelector('meta[property="og:image"]')||{}).content || '',
              video: (mv||{}).content || ''
            });
          } catch(e){ return JSON.stringify({error:String(e)}); }
        })()
    """

    private suspend fun viaWebView(activity: Activity, url: String): VideoInfo {
        // settleMs 降到 1.2s、timeout 降到 15s：只要拦到 1 条 twimg 的 .mp4 就立刻返回
        // （原来死等 5s 静置，而 mp4 请求往往 1s 内就发了 → 白等 4s）
        val res = WebViewBridge(activity).loadAndExtract(
            url = url,
            settleMs = 1200,
            timeoutMs = 15000,
            interceptFilter = { u -> u.contains("video.twimg.com") && u.contains(".mp4") },
            finishWhen = { urls -> urls.isNotEmpty() },
            extractJs = EXTRACT_JS,
        )
        val env = res.value?.let { runCatching { JSONObject(it) }.getOrNull() }
        val candidates = LinkedHashSet<String>()
        env?.optString("video").orEmpty()
            .takeIf { it.startsWith("http") }?.let { candidates.add(it) }
        res.intercepted.forEach { candidates.add(it) }

        val mp4 = candidates.filter { it.contains(".mp4") && it.contains("twimg.com") }
        if (mp4.isEmpty()) throw ParseException("页面没发出视频请求")

        val qualities = toQualities(mp4.map { 0 to it }, 0L)
        return VideoInfo(
            platform = Platform.X,
            title = env?.optString("title").orEmpty().ifEmpty { "X 视频" },
            coverUrl = env?.optString("cover").orEmpty(),
            qualities = qualities,
        )
    }

    // ------------------------------------------------------------------ //
    // 公共部分
    // ------------------------------------------------------------------ //

    private fun buildInfo(status: JSONObject, user: JSONObject?): VideoInfo {
        val entities = status.optJSONObject("extended_entities")
            ?: status.optJSONObject("entities")
        val media = entities?.optJSONArray("media")

        val images = mutableListOf<String>()
        var qualities: List<Quality> = emptyList()
        var cover = ""
        var durationMs = 0L

        var i = 0
        while (media != null && i < media.length()) {
            val m = media.optJSONObject(i)
            i++
            if (m == null) continue
            val url = m.optString("media_url_https")
            if (cover.isEmpty()) cover = url

            val vinfo = m.optJSONObject("video_info")
            val variants = vinfo?.optJSONArray("variants")
            if (variants != null && variants.length() > 0) {
                durationMs = vinfo.optLong("duration_millis", 0L)
                val mp4 = mutableListOf<Pair<Int, String>>()
                var j = 0
                while (j < variants.length()) {
                    val v = variants.optJSONObject(j)
                    j++
                    if (v == null) continue
                    val src = v.optString("url")
                    if (v.optString("content_type").startsWith("video/mp4") && src.isNotEmpty()) {
                        mp4.add(v.optInt("bitrate", 0) to src)
                    }
                }
                qualities = toQualities(mp4, durationMs)
                break                      // 单视频推文是绝大多数场景，找到就停
            }
            if (url.isNotEmpty()) images.add(url)
        }

        if (qualities.isEmpty() && images.isEmpty()) {
            throw ParseException("该推文不含视频或图片")
        }
        return VideoInfo(
            platform = Platform.X,
            title = cleanTitle(status.optString("full_text").ifEmpty { status.optString("text") }),
            author = user?.optString("name").orEmpty().ifEmpty {
                user?.optString("screen_name").orEmpty()
            },
            coverUrl = cover,
            durationMs = durationMs,
            qualities = qualities,
            images = images,
        )
    }

    /** 码率降序去重；大小先按「码率 × 时长」估算（X 的 bitrate 是峰值，之后会用 HEAD 换精确值） */
    private fun toQualities(mp4: List<Pair<Int, String>>, durationMs: Long): List<Quality> {
        val seen = HashSet<String>()
        return mp4.sortedByDescending { it.first }
            .filter { seen.add(it.second) }
            .map { (rate, url) ->
                Quality(
                    label = qualityLabel(url, rate),
                    url = url,
                    sizeBytes = if (rate > 0 && durationMs > 0) {
                        (rate * (durationMs / 1000.0) / 8.0).toLong()
                    } else 0L,
                )
            }
    }

    private fun qualityLabel(url: String, bitrate: Int): String {
        val m = URL_RES_RE.find(url)
        val res = if (m != null) "${m.groupValues[1]}×${m.groupValues[2]}" else ""
        val kbps = if (bitrate > 0) "${bitrate / 1000}kbps" else ""
        return when {
            res.isNotEmpty() && kbps.isNotEmpty() -> "$res · $kbps"
            res.isNotEmpty() -> res
            kbps.isNotEmpty() -> kbps
            else -> "视频"
        }
    }

    /**
     * 用 HEAD 把估算换成精确字节数。
     *
     * ⚠️ X 的 `variants[].bitrate` 是**峰值**码率，直接乘时长会高估两倍以上
     * （实测 10368kbps / 204.4s 算出 252MB，实际只有 107.6MB）。拿到字节数后
     * 顺手把标签里的峰值码率也换成平均码率，否则用户会以为没下全。
     */
    private suspend fun fillExactSizes(info: VideoInfo): VideoInfo {
        if (info.qualities.isEmpty()) return info
        val sizes = coroutineScope {
            info.qualities.map { q ->
                async(Dispatchers.IO) { q.url to Http.contentLength(q.url, Platform.X) }
            }.awaitAll()
        }.toMap()

        val fixed = info.qualities.map { q ->
            val n = sizes[q.url] ?: -1L
            if (n > 0) q.copy(sizeBytes = n, label = avgLabel(q.label, n, info.durationMs)) else q
        }
        return info.copy(qualities = fixed)
    }

    private fun avgLabel(label: String, bytes: Long, durationMs: Long): String {
        if (bytes <= 0 || durationMs <= 0) return label
        val kbps = (bytes * 8.0 / (durationMs / 1000.0) / 1000.0).toInt()
        val m = LABEL_RES_RE.find(label)
        return if (m != null) "${m.groupValues[1]}×${m.groupValues[2]} · ${kbps}kbps"
        else "${kbps}kbps"
    }

    /** 去掉推文里附带的 t.co 短链，并把连续空白压成一个空格 */
    private fun cleanTitle(text: String): String =
        text.replace(Regex("https?://t\\.co/\\S+"), "")
            .replace(Regex("\\s+"), " ")
            .trim()
}
