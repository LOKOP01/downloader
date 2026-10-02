package com.xd.vdl.core.parse

import android.app.Activity
import com.xd.vdl.core.AppLog
import com.xd.vdl.core.Platform
import com.xd.vdl.core.model.VideoInfo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * 禁漫解析（移植自桌面版 `app/core/jmcomic_bridge.py`）。
 *
 * 车号识别 → 本子/章节详情 → 产出图集 [VideoInfo]（`isImage = true`）。
 * 真正的图片下载走 [JmDownloader]，因为要逐张解码（切片重排）。
 *
 * 封面：桌面版用 `jmcover://` 伪协议 + `client.download_album_cover()` 现拉字节；
 * 安卓直接给出真实 CDN 地址 `/media/albums/{album_id}.jpg`（实测可用，见 [coverUrl]），
 * 交给通用图片加载链路即可。
 */
object JmParser : Parser {

    /** 车号：JM123 / jm 123456 */
    private val JM_CODE_RE = Regex("(?:JM)\\s*(\\d{2,})", RegexOption.IGNORE_CASE)
    private val PHOTO_RE = Regex("/photo/(\\d+)", RegexOption.IGNORE_CASE)
    private val ALBUM_RE = Regex("/album/(\\d+)", RegexOption.IGNORE_CASE)

    /**
     * 封面地址：`https://<图片CDN域名>/media/albums/{album_id}.jpg`。
     *
     * 与 jmcomic 的 `JmcomicText.get_album_cover_url()` 完全一致，实测各车号均可取到
     * 真实 JPEG；CDN 域名与图片域名同一批（[JmClient.imageDomains]），固定挑第一个即可
     * （六个域名互为镜像，没必要轮询）。
     */
    internal fun coverUrl(albumId: String): String {
        val dom = JmClient.imageDomains().firstOrNull() ?: return ""
        return "https://$dom/media/albums/$albumId.jpg"
    }

    /** 是不是一个「像禁漫车号」的文本（剪切板嗅探也要用，所以是 public） */
    fun looksLikeCode(text: String): Boolean {
        val raw = text.trim()
        if (raw.isEmpty()) return false
        if (PHOTO_RE.containsMatchIn(raw) || ALBUM_RE.containsMatchIn(raw)) return true
        if (JM_CODE_RE.containsMatchIn(raw)) return true
        // 纯数字：整段就是个 2~10 位数字才算（避免把年份/门牌号当车号）
        val compact = raw.replace(" ", "")
        return compact.length in 2..10 && compact.all { it.isDigit() }
    }

    /** 从分享文案里抠车号。返回 (kind, id)；识别失败返回 ("","") */
    fun parseCode(text: String): Pair<String, String> {
        val raw = text.trim()
        if (raw.isEmpty()) return "" to ""
        PHOTO_RE.find(raw)?.let { return "photo" to it.groupValues[1] }
        ALBUM_RE.find(raw)?.let { return "album" to it.groupValues[1] }
        JM_CODE_RE.find(raw)?.let { return "album" to it.groupValues[1] }
        val compact = raw.replace(" ", "")
        if (compact.length in 2..10 && compact.all { it.isDigit() }) return "album" to compact
        return "" to ""
    }

    override suspend fun parse(activity: Activity, url: String): VideoInfo =
        withContext(Dispatchers.IO) {
            val (kind, jmid) = parseCode(url)
            if (jmid.isEmpty()) {
                throw ParseException("未识别到禁漫车号，请粘贴 JM123、纯数字车号或 18comic 链接")
            }
            val t0 = System.currentTimeMillis()
            try {
                val info = if (kind == "photo") fromChapter(jmid) else fromAlbum(jmid)
                AppLog.i(
                    "禁漫解析完成「${info.title}」$kind $jmid " +
                        "${info.images.size} 张 / ${System.currentTimeMillis() - t0}ms",
                )
                info
            } catch (e: ParseException) {
                throw e
            } catch (e: Exception) {
                throw ParseException(friendly(e), e)
            }
        }

    private fun friendly(e: Exception): String {
        val msg = e.message.orEmpty()
        val low = msg.lowercase()
        return when {
            low.contains("unable to resolve host") || low.contains("connect") ||
                low.contains("timeout") || low.contains("ssl") ->
                "禁漫接口连不上（${e.javaClass.simpleName}）。禁漫需要代理才能访问，请确认代理已开启"

            msg.contains("403") ->
                "禁漫拒绝访问（地区限制或被识别为爬虫）。请更换代理节点"

            msg.contains("code=404") || msg.contains("不存在") ->
                "禁漫车号不存在或已下架"

            else -> "禁漫解析失败：${msg.ifEmpty { e.javaClass.simpleName }}"
        }
    }

    // ------------------------------------------------------------------ //
    // 本子（album）：拿元数据 + 章节目录
    // ------------------------------------------------------------------ //

    /**
     * album 接口返回的 `images` 是**空的**（实测），章节列表在 `series` 里。
     * 所以整本下载要先把章节 id 都收集起来，再逐章取图。
     */
    private fun fromAlbum(albumId: String): VideoInfo {
        val body = JmClient.apiGet("/album", mapOf("id" to albumId)).first
        val d = JSONObject(body)

        val title = d.optString("name").ifEmpty { "JM$albumId" }
        val author = jsonStrings(d, "author").joinToString("、")
        val tags = jsonStrings(d, "tags") + jsonStrings(d, "works")

        // 章节：series[] 里每项有 id / name / sort
        val chapters = mutableListOf<Pair<String, String>>()   // id to name
        val series = d.optJSONArray("series")
        if (series != null) {
            for (i in 0 until series.length()) {
                val s = series.optJSONObject(i) ?: continue
                val cid = s.optString("id")
                if (cid.isEmpty()) continue
                chapters.add(cid to s.optString("name").ifEmpty { "第 ${i + 1} 章" })
            }
        }
        // 没有 series 的短篇：本子 id 本身就是章节 id（实测 total_photos 为 2 的单话本子即如此）
        if (chapters.isEmpty()) chapters.add(albumId to title)

        val covers = coverUrl(chapters.first().first)
        return VideoInfo(
            platform = Platform.JMCOMIC,
            title = title,
            author = author,
            coverUrl = covers,
            qualities = emptyList(),
            // 这里先放「章节占位」：images 留空，真实图片列表由下载阶段逐章拉。
            // 用 images 装 `jmcomic://photo/<id>` 形式的伪 URL 会被 DownloadManager 误当图片下。
            images = emptyList(),
            durationMs = 0,
            jmKind = "album",
            jmId = albumId,
            jmChapters = chapters,
            jmTags = tags.take(8),
            jmPageCount = d.optInt("total_photos", 0),
            jmChapterCount = chapters.size,
            jmViews = d.optInt("total_views", 0),
            jmLikes = d.optInt("likes", 0),
            jmComments = d.optInt("comment_total", 0),
        ).also { AppLog.i("禁漫本子《$title》$covers 共 ${chapters.size} 章") }
    }

    /** 单章节：拿这一章的图片文件名列表 */
    internal fun fetchChapterImages(photoId: String): List<String> {
        val body = JmClient.apiGet("/chapter", mapOf("id" to photoId)).first
        val d = JSONObject(body)
        val arr = d.optJSONArray("images") ?: return emptyList()
        val out = mutableListOf<String>()
        for (i in 0 until arr.length()) {
            val s = arr.optString(i)
            if (s.isNotEmpty()) out.add(s)
        }
        return out
    }

    internal fun fetchChapterName(photoId: String): String {
        return runCatching {
            val d = JSONObject(JmClient.apiGet("/chapter", mapOf("id" to photoId)).first)
            d.optString("name")
        }.getOrDefault("")
    }

    private fun fromChapter(photoId: String): VideoInfo {
        val body = JmClient.apiGet("/chapter", mapOf("id" to photoId)).first
        val d = JSONObject(body)
        val title = d.optString("name").ifEmpty { "JM$photoId" }
        val images = fetchChapterImages(photoId)
        if (images.isEmpty()) throw ParseException("这一章没有图片")
        val scrambleId = JmClient.fetchScrambleId(photoId)
        return VideoInfo(
            platform = Platform.JMCOMIC,
            title = title,
            author = "",
            coverUrl = coverUrl(photoId),
            qualities = emptyList(),
            images = emptyList(),
            jmKind = "photo",
            jmId = photoId,
            jmChapters = listOf(photoId to title),
            jmScrambleId = scrambleId,
            jmPageCount = images.size,
            jmChapterCount = 1,
        )
    }

    private fun jsonStrings(o: JSONObject, key: String): List<String> {
        val out = mutableListOf<String>()
        when (val v = o.opt(key)) {
            is JSONArray -> for (i in 0 until v.length()) {
                val s = v.optString(i)
                if (s.isNotEmpty()) out.add(s)
            }
            is String -> if (v.isNotEmpty()) out.add(v)
        }
        return out
    }
}
