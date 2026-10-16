package com.xd.vdl.core.parse

import android.app.Activity
import com.xd.vdl.core.Platform
import com.xd.vdl.core.AppLog
import com.xd.vdl.core.model.Quality
import com.xd.vdl.core.model.VideoInfo
import com.xd.vdl.core.net.Http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * B站：不需要浏览器，直接用公开接口。
 *  view 接口拿标题/封面/cid，playurl 接口拿 DASH 音视频流。
 *
 * 每一路流都带 `baseUrl` + `backupUrl[]`。baseUrl 经常指向 PCDN
 * （`cn-*-fx-*.bilivideo.com`），TLS 重置或连不上；backupUrl 的
 * `upos-*` 镜像同一时刻是通的。两个都要留着，下载时按顺序试。
 */
object BiliParser : Parser {

    internal val BV = Regex("(BV[0-9A-Za-z]{10})")
    private val AV = Regex("/av(\\d+)", RegexOption.IGNORE_CASE)

    private fun qName(id: Int): String = when (id) {
        127 -> "8K 超高清"
        126 -> "杜比视界"
        125 -> "HDR 真彩"
        120 -> "4K 超清"
        116 -> "1080P60 高帧率"
        112 -> "1080P+ 高码率"
        80 -> "1080P 高清"
        74 -> "720P60 高帧率"
        64 -> "720P 高清"
        32 -> "480P 清晰"
        16 -> "360P 流畅"
        else -> "清晰度 $id"
    }

    /**
     * DASH 视频流里的帧率；拿不到返回 0。
     *
     * `frameRate` / `frame_rate` 既有 `"60"` 这种整数，也有 `"16000/672"` 这种
     * 分数（23.976 / 29.97 / 59.94 的真实写法），分数要先除再取整。拼进标签后
     * 下拉才能做「有 60fps 就不显示 30fps」（清晰度名本身不带帧率）。
     */
    private fun fpsOf(v: JSONObject): Int {
        for (key in arrayOf("frameRate", "frame_rate")) {
            val raw = v.opt(key)?.toString()?.trim().orEmpty()
            if (raw.isEmpty()) continue
            val value = try {
                if (raw.contains("/")) {
                    val p = raw.split("/")
                    p[0].toDouble() / p[1].toDouble()
                } else {
                    raw.toDouble()
                }
            } catch (e: Exception) {
                continue
            }
            if (value > 0) return Math.round(value).toInt()
        }
        return 0
    }

    /** JSON 字段可能是字符串或字符串数组 */
    private fun jsonUrls(obj: JSONObject, vararg keys: String): List<String> {
        val out = mutableListOf<String>()
        for (key in keys) {
            if (!obj.has(key) || obj.isNull(key)) continue
            when (val v = obj.opt(key)) {
                is String -> if (v.isNotEmpty()) out.add(v)
                is JSONArray -> {
                    for (i in 0 until v.length()) {
                        val s = v.optString(i)
                        if (s.isNotEmpty()) out.add(s)
                    }
                }
            }
        }
        return out
    }

    /** 一路 DASH 流：baseUrl 在前，backupUrl 跟上，去重 */
    internal fun streamUrls(stream: JSONObject): List<String> {
        val out = mutableListOf<String>()
        out += jsonUrls(stream, "baseUrl", "base_url")
        out += jsonUrls(stream, "backupUrl", "backup_url")
        return out.distinct()
    }

    /** durl 项：url 在前，backupUrl 跟上 */
    internal fun durlUrls(item: JSONObject): List<String> {
        val out = mutableListOf<String>()
        val u = item.optString("url")
        if (u.isNotEmpty()) out.add(u)
        out += jsonUrls(item, "backupUrl", "backup_url")
        return out.distinct()
    }

    override suspend fun parse(activity: Activity, url: String): VideoInfo =
        withContext(Dispatchers.IO) {
            val finalUrl = if (Platform.hostOf(url).endsWith("b23.tv")) {
                Http.resolveFinalUrl(url, Platform.BILIBILI)
            } else url

            val idQuery = BV.find(finalUrl)?.let { "bvid=${it.value}" }
                ?: AV.find(finalUrl)?.let { "aid=${it.groupValues[1]}" }
                ?: throw ParseException("未识别到 BV/av 号，请用视频详情页链接")

            val view = Http.getJson(
                "https://api.bilibili.com/x/web-interface/view?$idQuery",
                Platform.BILIBILI)
            if (view.optInt("code") != 0) {
                throw ParseException("取视频信息失败：${view.optString("message", "未知错误")}")
            }
            val data = view.optJSONObject("data") ?: throw ParseException("视频信息为空")
            val bvid = data.optString("bvid")
            val picked = pickPage(finalUrl, data)
            val cid = picked.cid
            val title = picked.title
            val author = data.optJSONObject("owner")?.optString("name").orEmpty()
            val cover = data.optString("pic")
            val durationSec = picked.durationSec

            val qualities = fetchQualities(bvid, cid, durationSec)
            if (qualities.isEmpty()) throw ParseException("没有取到可用播放地址（可能需登录）")

            VideoInfo(
                platform = Platform.BILIBILI,
                title = title,
                author = author,
                coverUrl = cover,
                durationMs = durationSec * 1000,
                qualities = qualities,
            )
        }

    /** 链接里的分 P 序号：查询参数 p=3，缺省或非法值都按第 1 P。 */
    internal fun pageIndex(url: String): Int =
        Regex("[?&]p=(\\d+)").find(url)?.groupValues?.get(1)?.toIntOrNull()?.coerceAtLeast(1) ?: 1

    /** 分 P 选择结果。 */
    internal data class PickedPage(val cid: Long, val title: String, val durationSec: Long)

    /**
     * 按链接里的分 P 参数选 P。
     *
     * 原来直接读顶层 data.cid（等于第 1 P），用户打开第 3 P 的链接会被静默下成第 1 P；
     * 标题也不带分 P 名，几个 P 下载后会在同一目录重名。没有 pages 时回退顶层字段。
     */
    internal fun pickPage(url: String, data: JSONObject): PickedPage {
        val pages = data.optJSONArray("pages")
        val index = pageIndex(url)
        val page = pages?.optJSONObject((index - 1).coerceAtLeast(0))
        val cid = page?.optLong("cid")?.takeIf { it > 0 } ?: data.optLong("cid")
        val base = data.optString("title").ifEmpty { "B站视频" }
        val part = page?.optString("part").orEmpty()
        val title = if ((pages?.length() ?: 0) > 1 && part.isNotEmpty()) {
            "$base P$index $part"
        } else {
            base
        }
        val duration = page?.optLong("duration")?.takeIf { it > 0 } ?: data.optLong("duration")
        return PickedPage(cid, title, duration)
    }

    internal fun fetchQualities(bvid: String, cid: Long, durationSec: Long): List<Quality> {
        val play = Http.getJson(
            "https://api.bilibili.com/x/player/playurl?bvid=$bvid&cid=$cid" +
                "&fnval=4048&fnver=0&fourk=1&qn=127",
            Platform.BILIBILI)
        if (play.optInt("code") != 0)
            throw ParseException("B站播放接口失败：" + play.optString("message"))
        val data = play.optJSONObject("data") ?: return emptyList()
        val streams = data.optJSONObject("dash")?.optJSONArray("video")
        val ids = linkedSetOf<Int>()
        if (streams != null) for (i in 0 until streams.length()) {
            streams.optJSONObject(i)?.let { ids.add(it.optInt("id")) }
        }
        AppLog.i("B站实际码流档位：" + ids.sortedDescending().joinToString(","))
        if (ids.isNotEmpty() && ids.all { it <= 32 }) {
            // 只在返回低清时确认服务端登录状态，正常解析不增加一次请求。
            runCatching {
                val nav = Http.getJson("https://api.bilibili.com/x/web-interface/nav", Platform.BILIBILI)
                val loggedIn = nav.optJSONObject("data")?.optBoolean("isLogin") == true
                AppLog.i("B站服务端登录状态：" + if (loggedIn) "有效（当前视频/权限仅返回低清）" else "未登录或Cookie已失效，请重新保存SESSDATA")
            }.onFailure { AppLog.w("B站登录状态检查失败（不影响已有地址）：" + it.javaClass.simpleName) }
        }
        val dur = durationSec
        val out = mutableListOf<Quality>()

        // 1) DASH：视频流与音频流分离，下载后需合并
        val dash = data.optJSONObject("dash")
        if (dash != null) {
            val audioArr = dash.optJSONArray("audio")
            var audioCands = emptyList<String>()
            var bestAudioBw = 0L
            if (audioArr != null) {
                for (i in 0 until audioArr.length()) {
                    val a = audioArr.optJSONObject(i) ?: continue
                    val bw = a.optLong("bandwidth")
                    if (bw > bestAudioBw) {
                        bestAudioBw = bw
                        audioCands = streamUrls(a)
                    }
                }
            }
            val bestAudio = audioCands.firstOrNull().orEmpty()
            val audioBackups = if (audioCands.size > 1) audioCands.drop(1) else emptyList()

            val videoArr = dash.optJSONArray("video")
            val seen = HashMap<Int, JSONObject>()
            if (videoArr != null) {
                for (i in 0 until videoArr.length()) {
                    val v = videoArr.optJSONObject(i) ?: continue
                    val id = v.optInt("id")
                    val cur = seen[id]
                    if (cur == null || v.optLong("bandwidth") > cur.optLong("bandwidth")) {
                        seen[id] = v
                    }
                }
            }
            seen.values
                .sortedWith(compareByDescending<JSONObject> { it.optInt("height") }
                    .thenByDescending { it.optLong("bandwidth") })
                .forEach { v ->
                    val cands = streamUrls(v)
                    val u = cands.firstOrNull().orEmpty()
                    if (u.isEmpty()) return@forEach
                    val wh = "${v.optInt("width")}×${v.optInt("height")}"
                    val bw = v.optLong("bandwidth")
                    val mbps = bw / 1000.0 / 1000.0
                    val est = ((bw + bestAudioBw) * dur / 8)
                    val fps = fpsOf(v)
                    var label = "${qName(v.optInt("id"))} · $wh · %.1f Mbps".format(mbps)
                    if (fps > 0) label += " · ${fps}fps"
                    out.add(
                        Quality(
                            label = label,
                            url = u,
                            audioUrl = bestAudio,
                            sizeBytes = est,
                            urlBackups = if (cands.size > 1) cands.drop(1) else emptyList(),
                            audioBackups = audioBackups,
                        )
                    )
                }
        }

        // 2) durl：音视频已合并（低清兜底）。长视频会被切成多段，
        //    这些段是「同一路视频的前后片段」，必须拼接成一个文件。
        if (out.isEmpty()) {
            val durl = data.optJSONArray("durl")
            if (durl != null && durl.length() > 0) {
                val segments = mutableListOf<String>()
                val backups = mutableListOf<List<String>>()
                var totalSize = 0L
                for (i in 0 until durl.length()) {
                    val d = durl.optJSONObject(i) ?: continue
                    val cands = durlUrls(d)
                    val u = cands.firstOrNull().orEmpty()
                    if (u.isEmpty()) continue
                    segments.add(u)
                    backups.add(if (cands.size > 1) cands.drop(1) else emptyList())
                    totalSize += d.optLong("size")
                }
                if (segments.isNotEmpty()) {
                    out.add(
                        Quality(
                            label = if (segments.size > 1) {
                                "已合并 · ${segments.size} 段"
                            } else {
                                "已合并"
                            },
                            url = segments.first(),
                            sizeBytes = totalSize,
                            urlBackups = backups.first(),
                            segmentUrls = segments,
                            segmentBackups = backups,
                        )
                    )
                }
            }
        }
        return out
    }
}
