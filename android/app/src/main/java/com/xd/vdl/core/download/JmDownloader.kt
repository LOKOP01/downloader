package com.xd.vdl.core.download

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import com.xd.vdl.core.AppLog
import com.xd.vdl.core.FileName
import com.xd.vdl.core.model.VideoInfo
import com.xd.vdl.core.parse.JmClient
import com.xd.vdl.core.parse.JmParser
import com.xd.vdl.core.parse.JmScramble
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import kotlin.coroutines.coroutineContext

/**
 * 禁漫图片下载：逐章取图 → 下载 → 切片解码 → 写进 ZIP 压缩包。
 *
 * 和其他平台不一样的地方：
 *  · 图片列表要**逐章节**去 `/chapter` 接口拉（album 接口的 images 是空的）
 *  · 每张图下下来是**乱序切片**的，必须 [JmScramble.decode] 重排后才能存
 *  · 成品是**一个压缩包**（以车号命名），不是散图
 *
 * 压缩包内部结构：单章直接平铺 `0001.jpg`；多章按章分子目录 `第01章_名称/0001.jpg`。
 *
 * ⚠️ **并发**：最早这里是「取一张 → 存一张」的纯串行循环，而桌面版走 jmcomic 的
 * `threading.image = 8`，8 路并行 —— 这就是「安卓下禁漫明显比 PC 慢」的根因
 * （差距在等待网络的时间全被串起来了，不是压缩包的锅：包内是 STORED 不压缩）。
 * 现在改成滑动窗口并发，写包仍严格按顺序（zip 是流式的，entry 不能乱序）。
 */
object JmDownloader {

    /**
     * 同时保持多少张图「在飞」。
     *
     * 不再往上加的原因在内存：每路都要把原图解码成 ARGB_8888 位图再重排，
     * 一张 1200×1800 的页就是 ~8MB，加上重排的副本与编码缓冲，单路峰值近 20MB；
     * 4 路约 80MB，已经能吃掉串行时的绝大部分等待；8 路在中低端机上容易被系统杀。
     */
    private const val CONCURRENCY = 4

    /** 章节目录（每章一次 `/chapter` 小请求）也并发几路，多章本子不必一章章等 */
    private const val PLAN_CONCURRENCY = 4

    /** 判断「这是不是一页图」用；官方打包直链拿回来的包也要按这个数一数 */
    private val IMAGE_EXT = listOf(".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")

    /** scramble_id 逐章可能不同，但同一章反复取没意义 —— 缓存起来（预览也共用） */
    private val scrambleIds = java.util.concurrent.ConcurrentHashMap<String, String>()

    /** 一页的任务描述：并发跑，但按 [jobs] 的顺序写入 */
    private data class PageJob(
        val entryName: String,
        val photoId: String,
        val filename: String,
        val scrambleId: String,
        val label: String,
    )

    /**
     * 下载整本或单章，产出**一个 ZIP**。
     *
     * @param onProgress (已完成张数, 总张数) —— 总张数在目录拉全后就是准的
     * @param onChapter  当前正在处理的页/章，用于界面提示
     * @param onZip      ZIP 写完后交出来（临时文件，调用方负责搬运/删除）
     */
    suspend fun download(
        ctx: Context,
        info: VideoInfo,
        onProgress: (done: Int, total: Int) -> Unit,
        onChapter: (text: String) -> Unit,
        onZip: (file: File, entryCount: Int) -> Unit,
    ) = withContext(Dispatchers.IO) {
        val chapters = info.jmChapters.ifEmpty {
            listOf(info.jmId to info.title)
        }
        AppLog.i(
            "禁漫开始下载《${info.title}》${info.jmKind} ${info.jmId} " +
                "共 ${chapters.size} 章",
        )

        // 0) 先试官方「整本打包直链」：一次请求把整本拿回来，比逐张快一个数量级。
        //    只有本子有这接口，而且要登录；取不到就静默往下走逐张，不算失败。
        if (info.jmKind == "album" && JmDownloadMode.officialZipFirst(ctx)) {
            if (tryOfficialZip(ctx, info, onProgress, onChapter, onZip)) return@withContext
        }

        // 1) 各章的图片文件名都拉出来（并发），这样总进度才是准的
        onChapter("正在获取目录…")
        val plan = fetchPlan(chapters)

        // 2) 摊平成有序的待办列表：并发跑、顺序写
        val multiChapter = chapters.size > 1
        val jobs = mutableListOf<PageJob>()
        for ((ci, chapter) in chapters.withIndex()) {
            val (cid, cname) = chapter
            val files = plan[ci]
            if (files.isEmpty()) continue
            val scrambleId = scrambleOf(info, cid)
            // 多章时按章建子目录；单章平铺
            val dir = if (multiChapter) {
                "第%02d章_%s/".format(ci + 1, FileName.sanitize(cname, 40))
            } else ""
            for ((pi, filename) in files.withIndex()) {
                jobs.add(
                    PageJob(
                        entryName = "$dir%04d.jpg".format(pi + 1),
                        photoId = cid,
                        filename = filename,
                        scrambleId = scrambleId,
                        label = if (multiChapter) {
                            "第 ${ci + 1}/${chapters.size} 章 · ${pi + 1}/${files.size} 张"
                        } else {
                            "${pi + 1}/${files.size} 张"
                        },
                    ),
                )
            }
        }
        if (jobs.isEmpty()) throw IOException("没有取到任何图片")
        AppLog.i("禁漫目录就绪：${chapters.size} 章 / ${jobs.size} 张，并发 $CONCURRENCY")
        onProgress(0, jobs.size)

        // ⚠️ `File.createTempFile` 要求 prefix **至少 3 个字符**，否则抛
        // `IllegalArgumentException: Prefix string "jm" too short: length must be at least 3`
        // （Java 文档明确规定的，不是 bug）。1.0.16 用了 "jm" 导致禁漫下载 100% 失败。
        val zip = File.createTempFile("jmzip", ".zip", ctx.cacheDir)
        var entryCount = 0

        try {
            ZipOutputStream(BufferedOutputStream(FileOutputStream(zip))).use { zos ->
                val crc = CRC32()
                val domains = JmClient.imageDomains()
                var done = 0
                // 滑动窗口并发：最多 CONCURRENCY 张同时在飞，写包仍严格按页序
                pipelineOrdered(
                    items = jobs,
                    concurrency = CONCURRENCY,
                    // 起始域名按页号散开：六个镜像等价，摊开能多吃几条连接；
                    // 某个镜像挂了也不怕，fetchDecodedJpeg 会自己往后逐个试
                    onStart = { _idx, job ->
                        coroutineContext.ensureActive()
                        onChapter(job.label)
                    },
                    transform = { idx, job ->
                        fetchDecodedJpeg(job, domains, idx % domains.size)
                    },
                    consume = { _idx, job, bytes ->
                        val data = bytes
                            ?: throw IOException("图片 ${job.filename} 下载或解码失败")
                        writeEntry(zos, job.entryName, data, crc)
                        entryCount++
                        done++
                        onProgress(done, jobs.size)
                    },
                )
            }
            if (entryCount == 0) throw IOException("压缩包内容为空")
            AppLog.i("禁漫压缩包完成《${info.title}》$entryCount 张 / ${zip.length()} 字节")
            onZip(zip, entryCount)
        } catch (e: Exception) {
            zip.delete()
            throw e
        }
    }

    /**
     * 试官方「整本打包直链」。
     *
     * 成功（真的拿到一个里面带图片的 zip，并已交给 [onZip]）返回 true；
     * 未配置 Cookie / 未登录 / 接口异常 / 拿回来的不是压缩包 → 返回 false，
     * 由调用方回退到逐张下载。**任何一步都不抛异常**，回退是正常路径。
     *
     * 进度只能按字节报 —— 那边只有一个文件，没有「第几张」可数。文件大小由
     * Content-Length 给，所以进度条是能填满的。
     */
    private suspend fun tryOfficialZip(
        ctx: Context,
        info: VideoInfo,
        onProgress: (done: Int, total: Int) -> Unit,
        onChapter: (text: String) -> Unit,
        onZip: (file: File, entryCount: Int) -> Unit,
    ): Boolean {
        onChapter("正在申请官方打包…")
        val meta = JmClient.albumDownload(info.jmId)
        if (meta == null) {
            AppLog.w("禁漫打包接口不可用，改为逐张下载 | ${JmClient.cookieDebug()}")
            onChapter("官方打包接口没通，改为逐张下载…")
            return false
        }
        if (!meta.usable) {
            // status == "0" 就是服务端说的「請先登入」。把原文回显出来，
            // 免得用户看到一句笼统的「未登录」不知道到底是没填 Cookie 还是 Cookie 失效。
            val why = meta.msg.ifEmpty { if (meta.loggedIn) "没拿到直链" else "未登录" }
            AppLog.w("禁漫官方打包不可用：$why | ${JmClient.cookieDebug()}")
            onChapter("官方打包不可用（$why），改为逐张下载…")
            return false
        }

        val url = if (meta.downloadUrl.startsWith("http")) {
            meta.downloadUrl
        } else {
            JmClient.apiBase() + "/" + meta.downloadUrl.trimStart('/')
        }
        val zip = File.createTempFile("jmzip", ".zip", ctx.cacheDir)
        return try {
            onChapter("官方打包下载中…" + (if (meta.fileSize.isNotEmpty()) " ${meta.fileSize}" else ""))
            AppLog.i("禁漫走官方打包直链：${meta.title} ${meta.fileSize} $url")
            // 阻塞下载里没法直接读协程上下文（`coroutineContext` 是 suspend 属性，
            // 在普通 lambda 里取不到），先把 Job 抓出来，回调里拿它判取消
            val scopeJob = coroutineContext[kotlinx.coroutines.Job]
            JmClient.downloadTo(url, zip) { done, total ->
                scopeJob?.ensureActive()
                onProgress(
                    done.coerceAtMost(Int.MAX_VALUE.toLong()).toInt(),
                    total.coerceAtMost(Int.MAX_VALUE.toLong()).toInt(),
                )
            }
            val entries = countImageEntries(zip)
            if (entries <= 0) {
                // 可能是一段 HTML 错误页，也可能是空包 —— 都不能当成品
                AppLog.w("禁漫打包直链拿到的不是有效压缩包（${zip.length()} 字节），改为逐张下载")
                zip.delete()
                false
            } else {
                AppLog.i("禁漫官方打包完成：$entries 张 / ${zip.length()} 字节")
                onZip(zip, entries)
                true
            }
        } catch (e: kotlinx.coroutines.CancellationException) {
            zip.delete()
            throw e
        } catch (e: Exception) {
            AppLog.w("禁漫打包直链失败（${e.javaClass.simpleName}: ${e.message}），改为逐张下载")
            zip.delete()
            false
        }
    }

    /** 数一数 zip 里有几个图片条目（只看中央目录，不解压） */
    internal fun countImageEntries(zip: File): Int = runCatching {
        java.util.zip.ZipFile(zip).use { zf ->
            zf.entries().asSequence().count { e ->
                !e.isDirectory && IMAGE_EXT.any { e.name.lowercase().endsWith(it) }
            }
        }
    }.getOrDefault(0)

    /** 并发拉各章的图片文件名列表，返回顺序与 [chapters] 一致 */
    private suspend fun fetchPlan(chapters: List<Pair<String, String>>): List<List<String>> =
        coroutineScope {
            val gate = Semaphore(PLAN_CONCURRENCY)
            chapters.map { (cid, _) ->
                async { gate.withPermit { JmParser.fetchChapterImages(cid) } }
            }.awaitAll()
        }

    /** 取某一章的 scramble_id（本子级优先，其次单独请求，失败回退默认值） */
    private fun scrambleOf(info: VideoInfo, chapterId: String): String {
        if (chapterId == info.jmId && info.jmScrambleId.isNotEmpty()) {
            return info.jmScrambleId
        }
        return scrambleIds.getOrPut(chapterId) {
            runCatching { JmClient.fetchScrambleId(chapterId) }.getOrDefault("220980")
        }
    }

    /** 把一个 entry 写进 zip（先登记 metadata 再塞数据） */
    private fun writeEntry(
        zos: ZipOutputStream,
        name: String,
        data: ByteArray,
        crc: CRC32,
    ) {
        crc.reset()
        crc.update(data)
        val entry = ZipEntry(name).apply {
            method = ZipEntry.STORED
            size = data.size.toLong()
            compressedSize = data.size.toLong()
            this.crc = crc.value
        }
        zos.putNextEntry(entry)
        zos.write(data)
        zos.closeEntry()
    }

    /**
     * 取一张图并解码，直接返回 **JPEG 字节**（省掉中间的临时文件）。
     * 图片 CDN 有多个域名，从 [startIdx] 起逐个试。全部失败返回 null。
     */
    private suspend fun fetchDecodedJpeg(
        job: PageJob,
        domains: List<String>,
        startIdx: Int,
    ): ByteArray? = withContext(Dispatchers.IO) {
        for (k in domains.indices) {
            coroutineContext.ensureActive()
            val di = (startIdx + k) % domains.size
            val url = JmClient.imageUrl(job.photoId, job.filename, domains[di])
            val bytes = runCatching { JmClient.fetchImage(url) }.getOrNull() ?: continue

            // 不需要切片的图：原字节直接进包（保留原始画质，不做二次编码）
            val num = JmScramble.segmentationNum(job.scrambleId, job.photoId, job.filename)
            if (num <= 0) return@withContext bytes

            val raw = runCatching { BitmapFactory.decodeByteArray(bytes, 0, bytes.size) }
                .getOrNull() ?: continue

            val fixed = JmScramble.decode(raw, num)
            if (fixed !== raw) raw.recycle()
            val out = ByteArrayOutputStream()
            fixed.compress(Bitmap.CompressFormat.JPEG, 92, out)
            fixed.recycle()
            return@withContext out.toByteArray()
        }
        null
    }

    /** 预览时按原下载规则重排切片；调用方在 IO 线程使用。 */
    suspend fun previewPage(info: VideoInfo, chapterId: String, filename: String, index: Int): ByteArray =
        withContext(Dispatchers.IO) {
            val job = PageJob(
                entryName = "",
                photoId = chapterId,
                filename = filename,
                scrambleId = scrambleOf(info, chapterId),
                label = "",
            )
            fetchDecodedJpeg(job, JmClient.imageDomains(), 0)
                ?: throw IOException("第 $index 页预览失败")
        }
}
