package com.xd.vdl.core.parse

import android.app.Activity
import com.xd.vdl.core.AppLog
import com.xd.vdl.core.Platform
import com.xd.vdl.core.model.Quality
import com.xd.vdl.core.model.VideoInfo
import com.xd.vdl.core.net.Http
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLDecoder

/**
 * 抖音解析。
 *
 * **真正有效的来源只有一个**：WebView 打开 PC 作品页、拦 `/aweme/v1/web/aweme/detail/`
 * 拿完整 bit_rate（纯 HTTP 打这个接口会被 403，见下）。
 *
 * 纯 HTTP 那一路（分享页 → iteminfo → detail）保留作兜底，但实测已基本失效：
 *  - 分享页现在只回 `_ROUTER_DATA` 空壳（1399 字符，只有 ua/webId/renderInSSR，无任何视频地址）
 *  - iteminfo 返回 `{"status_code":0,"item_list":[]}` 之类空壳
 *  - iesdouyin 的 detail 返回空体；www.douyin.com 的 detail 无条件 403
 *
 * 所以两路**并行**跑：HTTP 不挡路，WebView 一出结果就用（画质更全者胜）。
 */
object DouyinParser : Parser {

    private val ID_VIDEO = Regex("/(?:video|note|slides)/(\\d+)")
    private val ID_ANY = Regex("(\\d{15,20})")

    private val RE_RENDER = Regex(
        "<script id=\"RENDER_DATA\" type=\"application/json\">(.*?)</script>",
        RegexOption.DOT_MATCHES_ALL)
    private val RE_ROUTER = Regex(
        "window\\._ROUTER_DATA\\s*=\\s*(\\{.*?\\})\\s*</script>",
        RegexOption.DOT_MATCHES_ALL)

    override suspend fun parse(activity: Activity, url: String): VideoInfo =
        withContext(Dispatchers.IO) {
            val t0 = System.currentTimeMillis()

            // 链接自带作品 ID 时不用跳转（省一次往返）；只有短链才 resolve。
            // 原来无条件先 resolve，v.douyin.com 的 302 链本身要 0.2~0.6s。
            var target = url
            var id: String? = ID_VIDEO.find(target)?.groupValues?.get(1)
                ?: ID_ANY.find(target)?.value
            if (id == null) {
                target = Http.resolveFinalUrl(url, Platform.DOUYIN, Http.UA_IPHONE)
                if (target.contains("/user/")) {
                    throw ParseException("这是用户主页链接，请用单个作品链接")
                }
                id = ID_VIDEO.find(target)?.groupValues?.get(1)
                    ?: ID_ANY.find(target)?.value
            }
            val awemeId = id ?: throw ParseException("未识别到作品 ID，请用抖音分享链接")
            val note = isNoteLink(target)

            // 分享页（HTTP）与详情接口（WebView）并行跑，谁先给到更全的画质就用谁。
            // 实测分享页必回 `_ROUTER_DATA` 空壳（1399 字符、无任何视频地址），
            // HTTP 那路基本是陪跑 —— 但留着它当兜底，并行后不再拖慢主路。
            val httpDeferred = async(Dispatchers.IO) {
                runCatching { fromHttp(awemeId, note) }.onFailure {
                    if (it is CancellationException) throw it
                    AppLog.w("抖音分享页兜底失败：${it.message}")
                }.getOrNull()
            }
            val rich = runCatching { fromWebDetail(activity, awemeId, note) }.onFailure {
                if (it is CancellationException) throw it
                AppLog.w("抖音 WebView 详情失败：${it.message}")
            }.getOrNull()

            val http = httpDeferred.await()
            val info: VideoInfo? = when {
                rich == null -> http
                http == null -> rich
                qualityRank(rich) >= qualityRank(http) -> rich.copy(
                    title = rich.title.ifBlank { http.title },
                    author = rich.author.ifBlank { http.author },
                    coverUrl = rich.coverUrl.ifBlank { http.coverUrl },
                )
                else -> http.copy(
                    title = http.title.ifBlank { rich.title },
                    author = http.author.ifBlank { rich.author },
                    coverUrl = http.coverUrl.ifBlank { rich.coverUrl },
                )
            }

            if (info == null) {
                throw ParseException("作品信息获取失败：分享页与详情接口都没有返回数据")
            }
            AppLog.i("抖音解析完成 ${info.qualities.size} 档 / ${System.currentTimeMillis() - t0}ms")
            info
        }

    /**
     * 纯 HTTP 那一路：分享页优先，iteminfo / detail 兜底。
     *
     * ⚠️ 分享页现在只剩 `_ROUTER_DATA` 外壳（不含任何视频地址），这一路大概率返回 null；
     * 保留是为了「WebView 不可用时还能出个东西」，不再做 `repeat(2)` 重试
     * —— 实测第 2 轮拿到的 HTML 与第 1 轮完全一样，纯浪费一次往返。
     */
    private fun fromHttp(id: String, note: Boolean): VideoInfo? {
        for (name in listOf("share", "iteminfo", "detail")) {
            val r = runCatching {
                when (name) {
                    "share" -> fromSharePage(id, note)
                    "iteminfo" -> fromItemInfo(id)
                    else -> fromDetailApi(id)
                }
            }.onFailure {
                if (it is CancellationException) throw it
                AppLog.w("抖音 HTTP $name 无数据：${it.message}")
            }.getOrNull()
            if (r != null) return r
        }
        return null
    }

    /** 有多少「真档位」（不是兜底的最高画质）+ 最高边长，用来比较两份解析谁更全 */
    private fun qualityRank(info: VideoInfo?): Int {
        if (info == null || info.isImage) return if (info == null) 0 else 1
        val named = info.qualities.count { it.label != "最高画质" }
        val edge = info.qualities.maxOfOrNull { q ->
            val m = Regex("""(\d{2,5})×(\d{2,5})""").find(q.label)
            if (m != null) maxOf(m.groupValues[1].toInt(), m.groupValues[2].toInt()) else 0
        } ?: 0
        return named * 10_000 + edge
    }

    /** 短链跳转后仍保留作品类型；图文必须先加载 /note/ 页面。 */
    internal fun isNoteLink(url: String): Boolean =
        Regex("/(?:share/)?note/\\d+").containsMatchIn(url) ||
            Regex("/slides/\\d+").containsMatchIn(url)

    internal fun detailPages(id: String, note: Boolean): List<String> =
        (if (note) listOf("note", "video") else listOf("video", "note"))
            .map { "https://www.douyin.com/$it/$id" }

    /** 视频详情通常有 bit_rate；图文详情只有 images，也属于完整作品。 */
    internal fun pickDetail(root: JSONObject, id: String): JSONObject? {
        val aweme = root.optJSONObject("aweme_detail") ?: return null
        val itemId = aweme.optString("aweme_id")
        if (itemId.isNotBlank() && itemId != id) return null
        return aweme.takeIf { hasMedia(it) }
    }

    private fun hasMedia(item: JSONObject): Boolean {
        val images = item.optJSONArray("images")
        if (images != null) for (i in 0 until images.length()) {
            if (images.optJSONObject(i)?.optJSONArray("url_list")?.length()?.let { it > 0 } == true)
                return true
        }
        val video = item.optJSONObject("video") ?: return false
        if (video.optJSONObject("play_addr")?.optJSONArray("url_list")?.length()?.let { it > 0 } == true)
            return true
        val rates = video.optJSONArray("bit_rate") ?: return false
        for (i in 0 until rates.length()) {
            if (rates.optJSONObject(i)?.optJSONObject("play_addr")
                    ?.optJSONArray("url_list")?.length()?.let { it > 0 } == true) return true
        }
        return false
    }

    /** 拦截 PC 页面发出的详情请求；未发请求时读取页面内联的作品数据。 */
    private suspend fun fromWebDetail(activity: Activity, id: String, note: Boolean): VideoInfo? {
        val bridge = WebViewBridge(activity)
        for (page in detailPages(id, note)) {
            val item = bridge.loadAndCaptureJson(
                pageUrl = page,
                ua = Http.UA_DESKTOP,
                urlMatch = { it.contains("/aweme/v1/web/aweme/detail/") },
                pick = { pickDetail(it, id) },
                timeoutMs = 8000,
                pageStateJs = """(function() {
                    var data = document.getElementById('RENDER_DATA');
                    if (data && data.textContent) return data.textContent;
                    return JSON.stringify(window._ROUTER_DATA || window._SSR_HYDRATED_DATA || {});
                })()""",
                pageStatePick = { raw ->
                    val json = JSONObject(if (raw.startsWith("%7B", true))
                        URLDecoder.decode(raw, "UTF-8") else raw)
                    findItem(json, id = id)
                },
            )
            if (item != null) return buildInfo(item)
            AppLog.w("抖音详情接口未返回可用数据：$page")
        }
        return null
    }

    // ------------------------------------------------------------------ //
    // 三个数据源
    // ------------------------------------------------------------------ //

    private fun fromItemInfo(id: String): VideoInfo? {
        val body = Http.getText(
            "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids=$id",
            Platform.DOUYIN, Http.UA_IPHONE)
        if (body.isBlank()) throw ParseException("iteminfo 返回空")
        val json = JSONObject(body)
        val list = json.optJSONArray("item_list")
        val item = if (list != null && list.length() > 0) list.optJSONObject(0) else null
        return item?.let { buildInfo(it) }
            ?: throw ParseException("iteminfo 无 item_list")
    }

    private fun fromSharePage(id: String, note: Boolean): VideoInfo? {
        // 实测分享页现在只回 `_ROUTER_DATA` 空壳，video/note 两个路径拿到的东西一样。
        // 不再做 repeat(2) 重试（第 2 轮内容与第 1 轮相同），只各打一次就够。
        for (page in if (note) listOf("note", "video") else listOf("video", "note")) {
            val html = runCatching {
                Http.getText("https://www.iesdouyin.com/share/$page/$id/",
                    Platform.DOUYIN, Http.UA_IPHONE)
            }.getOrNull() ?: continue
            val data = extractPageJson(html) ?: continue
            findItem(data, id = id)?.let { return buildInfo(it) }
        }
        throw ParseException("分享页中未找到作品数据")
    }

    private fun fromDetailApi(id: String): VideoInfo? {
        val url = "https://www.iesdouyin.com/aweme/v1/aweme/detail/" +
            "?aweme_id=$id&aid=1128&version_name=23.5.0" +
            "&device_platform=android&os_version=2333"
        val body = Http.getText(url, Platform.DOUYIN, Http.UA_IPHONE)
        if (body.isBlank()) throw ParseException("detail 接口返回空（可能需要登录）")
        val item = JSONObject(body).optJSONObject("aweme_detail")
        return item?.let { buildInfo(it) }
            ?: throw ParseException("detail 无 aweme_detail（可能需要登录）")
    }

    // ------------------------------------------------------------------ //
    // 页面内联 JSON 提取 + 递归查找作品对象
    // ------------------------------------------------------------------ //

    private fun extractPageJson(html: String): JSONObject? {
        RE_RENDER.find(html)?.let { m ->
            runCatching { JSONObject(URLDecoder.decode(m.groupValues[1], "UTF-8")) }
                .onSuccess { return it }
        }
        RE_ROUTER.find(html)?.let { m ->
            runCatching { JSONObject(m.groupValues[1]) }.onSuccess { return it }
        }
        return null
    }

    private fun findItem(node: Any?, depth: Int = 0, id: String = ""): JSONObject? {
        if (node == null || depth > 12) return null
        if (node is JSONObject) {
            if (hasMedia(node) && (id.isEmpty() || node.optString("aweme_id").let {
                    it.isBlank() || it == id
                })) return node
            for (key in listOf("item_list", "itemList", "aweme_list", "aweme_detail")) {
                when (val v = node.opt(key)) {
                    is JSONArray -> if (v.length() > 0) {
                        findItem(v.opt(0), depth + 1, id)?.let { return it }
                    }
                    is JSONObject -> findItem(v, depth + 1, id)?.let { return it }
                }
            }
            val keys = node.keys()
            while (keys.hasNext()) {
                findItem(node.opt(keys.next()), depth + 1, id)?.let { return it }
            }
        } else if (node is JSONArray) {
            for (i in 0 until node.length()) {
                findItem(node.opt(i), depth + 1, id)?.let { return it }
            }
        }
        return null
    }

    // ------------------------------------------------------------------ //
    // 组装 VideoInfo
    // ------------------------------------------------------------------ //

    private fun buildInfo(item: JSONObject): VideoInfo {
        val video = item.optJSONObject("video") ?: JSONObject()

        // 图集
        val imageUrls = mutableListOf<String>()
        item.optJSONArray("images")?.let { arr ->
            for (i in 0 until arr.length()) {
                val urls = arr.optJSONObject(i)?.optJSONArray("url_list") ?: continue
                if (urls.length() > 0) {
                    // 取最后一张地址（桌面版实测是最高清）
                    imageUrls.add(
                        if (urls.length() > 1) urls.optString(urls.length() - 1)
                        else urls.optString(0))
                }
            }
        }

        val qualities = mutableListOf<Quality>()
        var cover: String

        if (imageUrls.isNotEmpty()) {
            cover = video.optJSONObject("cover")
                ?.optJSONArray("url_list")?.optString(0).orEmpty()
                .ifEmpty { imageUrls.first() }
        } else {
            // 视频：从 bit_rate 生成档位（分辨率 → 帧率 → 码率 降序）
            data class Entry(val w: Int, val h: Int, val fps: Int,
                             val rate: Long, val label: String, val raw: String)
            val entries = mutableListOf<Entry>()
            val sizes = HashMap<String, Long>()
            video.optJSONArray("bit_rate")?.let { br ->
                for (i in 0 until br.length()) {
                    val e = br.optJSONObject(i) ?: continue
                    val pa = e.optJSONObject("play_addr") ?: continue
                    val urls = pa.optJSONArray("url_list") ?: continue
                    if (urls.length() == 0) continue
                    val raw = urls.optString(0)
                    if (raw.isEmpty()) continue
                    val rate = e.optLong("bit_rate")
                    val w = pa.optInt("width")
                    val h = pa.optInt("height")
                    // 帧率：同分辨率抖音常给 30fps 和 60fps 两条，标签里带上
                    // 帧率，下拉才能做「有 60fps 就不显示 30fps」
                    val fps = e.optInt("FPS")
                    val gear = e.optString("gear_name").replace("_", " ").trim()
                    var label = when {
                        w > 0 && h > 0 -> "${w}×${h}"
                        h > 0 -> "${h}p"
                        gear.isNotEmpty() -> gear
                        else -> "${rate / 1000}kbps"
                    }
                    if (rate > 0) label += " · ${rate / 1000}kbps"
                    if (fps > 0) label += " · ${fps}fps"
                    entries.add(Entry(w, h, fps, rate, label, raw))
                    var dsz = e.optLong("data_size")
                    if (dsz <= 0) dsz = pa.optLong("data_size")
                    if (dsz > 0) sizes[raw] = dsz
                }
            }
            // 只按码率排会默认下到 30fps 那条（同分辨率 60fps 的码率反而更低），
            // 所以主序分辨率、次帧率、末码率
            entries.sortWith(
                compareByDescending<Entry> { maxOf(it.w, it.h) }
                    .thenByDescending { minOf(it.w, it.h) }
                    .thenByDescending { it.fps }
                    .thenByDescending { it.rate })
            val seen = HashSet<String>()
            for (e in entries) {
                if (!seen.add(e.raw)) continue
                // playwm → play 即无水印；尺寸是按原始地址记的，键不用改（这里直接用新地址）
                qualities.add(
                    Quality(e.label, e.raw.replace("playwm", "play"),
                        sizeBytes = sizes[e.raw] ?: 0L))
            }
            if (qualities.isEmpty()) {
                val pa = video.optJSONObject("play_addr")
                val u = pa?.optJSONArray("url_list")?.optString(0).orEmpty()
                    .replace("playwm", "play")
                if (u.isNotEmpty()) {
                    val w = pa?.optInt("width") ?: 0
                    val h = pa?.optInt("height") ?: 0
                    val vw = if (w > 0) w else video.optInt("width")
                    val vh = if (h > 0) h else video.optInt("height")
                    val label = if (vw > 0 && vh > 0) "${vw}×${vh}" else "最高画质"
                    qualities.add(
                        Quality(label, u, sizeBytes = pa?.optLong("data_size") ?: 0L))
                }
            }
            val oc = video.optJSONObject("origin_cover")?.optJSONArray("url_list")
            val cv = video.optJSONObject("cover")?.optJSONArray("url_list")
            cover = when {
                oc != null && oc.length() > 0 -> oc.optString(0)
                cv != null && cv.length() > 0 -> cv.optString(0)
                else -> ""
            }
        }

        if (qualities.isEmpty() && imageUrls.isEmpty()) {
            throw ParseException("作品数据中无有效下载地址")
        }

        return VideoInfo(
            platform = Platform.DOUYIN,
            title = item.optString("desc").ifEmpty { "抖音作品" },
            author = item.optJSONObject("author")?.optString("nickname").orEmpty(),
            coverUrl = cover,
            durationMs = video.optLong("duration"),
            qualities = qualities,
            images = imageUrls,
        )
    }
}
