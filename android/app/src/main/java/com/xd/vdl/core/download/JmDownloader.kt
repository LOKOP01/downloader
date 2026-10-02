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
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
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
 */
object JmDownloader {
    private val previewScrambleIds = java.util.concurrent.ConcurrentHashMap<String, String>()

    /**
     * 下载整本或单章，产出**一个 ZIP**。
     *
     * @param onProgress (已完成张数, 总张数) —— 总张数可能随章节推进而更新
     * @param onChapter  当前正在处理的章节名，用于界面提示
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

        // 先把各章的图片文件名都拉出来，这样总进度才是准的
        onChapter("正在获取目录…")
        val plan = mutableListOf<List<String>>()
        var total = 0
        for ((cid, _) in chapters) {
            coroutineContext.ensureActive()
            val imgs = JmParser.fetchChapterImages(cid)
            plan.add(imgs)
            total += imgs.size
            onProgress(0, total)
        }
        if (total == 0) throw IOException("没有取到任何图片")
        AppLog.i("禁漫目录就绪：${chapters.size} 章 / $total 张")

        // scramble_id 逐章可能不同，缓存在这里避免重复请求
        val scrambleCache = HashMap<String, String>()
        if (info.jmScrambleId.isNotEmpty()) {
            scrambleCache[info.jmId] = info.jmScrambleId
        }

        val multiChapter = chapters.size > 1
        // ⚠️ `File.createTempFile` 要求 prefix **至少 3 个字符**，否则抛
        // `IllegalArgumentException: Prefix string "jm" too short: length must be at least 3`
        // （Java 文档明确规定的，不是 bug）。1.0.16 用了 "jm" 导致禁漫下载 100% 失败。
        val zip = File.createTempFile("jmzip", ".zip", ctx.cacheDir)
        var entryCount = 0

        try {
            ZipOutputStream(BufferedOutputStream(FileOutputStream(zip))).use { zos ->
                // 用 STORED（不压缩）：这些是 JPEG，本来就压不动，省 CPU
                val entries = java.util.zip.CRC32()
                var done = 0
                val domains = JmClient.imageDomains()
                var domIdx = 0

                for ((ci, chapter) in chapters.withIndex()) {
                    val (cid, cname) = chapter
                    val files = plan[ci]
                    if (files.isEmpty()) continue

                    val scrambleId = scrambleCache.getOrPut(cid) {
                        runCatching { JmClient.fetchScrambleId(cid) }.getOrDefault("220980")
                    }
                    // 多章时按章建子目录；单章平铺
                    val dir = if (multiChapter) {
                        "第%02d章_%s/".format(ci + 1, FileName.sanitize(cname, 40))
                    } else ""

                    for ((pi, filename) in files.withIndex()) {
                        coroutineContext.ensureActive()
                        onChapter(
                            if (multiChapter) "第 ${ci + 1}/${chapters.size} 章 · ${pi + 1}/${files.size} 张"
                            else "${pi + 1}/${files.size} 张",
                        )

                        val jpg = fetchDecodedJpeg(
                            photoId = cid, filename = filename, scrambleId = scrambleId,
                            domains = domains, startIdx = domIdx,
                        ) { used -> domIdx = used }
                            ?: throw IOException("图片 $filename 下载或解码失败")

                        val entryName = "$dir%04d.jpg".format(pi + 1)
                        writeEntry(zos, entryName, jpg, entries)
                        entryCount++

                        done++
                        onProgress(done, total)
                    }
                }
            }
            if (entryCount == 0) throw IOException("压缩包内容为空")
            AppLog.i("禁漫压缩包完成《${info.title}》$entryCount 张 / ${zip.length()} 字节")
            onZip(zip, entryCount)
        } catch (e: Exception) {
            zip.delete()
            throw e
        }
    }

    /** 把一个 entry 写进 zip（先登记 metadata 再塞数据） */
    private fun writeEntry(
        zos: ZipOutputStream,
        name: String,
        data: ByteArray,
        crc: java.util.zip.CRC32,
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
     * 图片 CDN 有多个域名，逐个试。全部失败返回 null。
     */
    /** 预览时按原下载规则重排切片；调用方在 IO 线程使用。 */
    suspend fun previewPage(info: VideoInfo, chapterId: String, filename: String, index: Int): ByteArray =
        withContext(Dispatchers.IO) {
            val scrambleId = if (chapterId == info.jmId && info.jmScrambleId.isNotEmpty()) {
                info.jmScrambleId
            } else previewScrambleIds.getOrPut(chapterId) { JmClient.fetchScrambleId(chapterId) }
            fetchDecodedJpeg(
                chapterId, filename, scrambleId, JmClient.imageDomains(), 0, {},
            ) ?: throw IOException("第 $index 页预览失败")
        }

    private suspend fun fetchDecodedJpeg(
        photoId: String,
        filename: String,
        scrambleId: String,
        domains: List<String>,
        startIdx: Int,
        onDomain: (Int) -> Unit,
    ): ByteArray? = withContext(Dispatchers.IO) {
        for (k in domains.indices) {
            coroutineContext.ensureActive()
            val di = (startIdx + k) % domains.size
            val url = JmClient.imageUrl(photoId, filename, domains[di])
            val bytes = runCatching { JmClient.fetchImage(url) }.getOrNull() ?: continue

            // 不需要切片的图：原字节直接进包（保留原始画质，不做二次编码）
            val num = JmScramble.segmentationNum(scrambleId, photoId, filename)
            if (num <= 0) {
                onDomain(di)
                return@withContext bytes
            }

            val raw = runCatching { BitmapFactory.decodeByteArray(bytes, 0, bytes.size) }
                .getOrNull() ?: continue
            onDomain(di)

            val fixed = JmScramble.decode(raw, num)
            if (fixed !== raw) raw.recycle()
            val out = ByteArrayOutputStream()
            fixed.compress(Bitmap.CompressFormat.JPEG, 92, out)
            fixed.recycle()
            return@withContext out.toByteArray()
        }
        null
    }
}
