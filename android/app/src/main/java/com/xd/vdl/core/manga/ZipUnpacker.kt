package com.xd.vdl.core.manga

import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.util.zip.ZipInputStream

/** 一次解包的结果 */
data class Unpacked(val entries: List<ZipPageCache.Entry>, val totalBytes: Long, val note: String)

/**
 * 压缩包解包核心。**不依赖 Android**，因此可以直接在 JVM 单测里跑。
 *
 * 与 [ZipPageCache] 分开的理由：缓存目录、manifest、清理策略都属于 Android 层，
 * 而「怎么把一个 zip 安全地摊成有序的页文件」是本项目最容易出错、也最需要测试的部分：
 *  · zip slip：条目名 `../../x.jpg` 会写到目标目录之外；
 *  · 顺序：页序必须严格等于包内顺序，任何排序都会打乱阅读顺序；
 *  · 半成品：中途失败必须清理，否则下次打开会读到不完整的缓存。
 */
internal object ZipUnpacker {

    /**
     * 把 [ins] 指向的 zip 解到 [destDir]。
     *
     * 落盘文件名一律用**序号**（`%05d.bin`），条目名只写进返回的 [Unpacked.entries]，
     * 这是防 zip slip 的关键：任何来自压缩包的字符串都不参与路径拼接。
     *
     * @param onProgress (已写字节, 总字节)；totalHint <= 0 时第二项为 0
     * @return 页序（与包内顺序一致）、总字节、给用户的说明
     * @throws IOException 包内没有图片、目录不可写等
     */
    fun unpack(
        ins: InputStream,
        destDir: File,
        totalHint: Long = 0L,
        maxPages: Int = ZipPageCache.MAX_PAGES,
        maxBytes: Long = ZipPageCache.MAX_TOTAL_BYTES,
        imageExt: List<String> = ZipPageCache.IMAGE_EXT,
        onProgress: (Long, Long) -> Unit = { _, _ -> },
    ): Unpacked {
        if (!destDir.isDirectory && !destDir.mkdirs()) {
            throw IOException("无法创建解压目录：" + destDir.path)
        }
        val entries = ArrayList<ZipPageCache.Entry>(256)
        var total = 0L
        var skipped = 0
        var truncated = false
        ZipInputStream(ins.buffered(64 * 1024)).use { zis ->
            val buf = ByteArray(64 * 1024)
            while (true) {
                val e = zis.nextEntry ?: break
                val lower = e.name.lowercase()
                val isImage = !e.isDirectory && imageExt.any { lower.endsWith(it) }
                if (!isImage) {
                    if (!e.isDirectory) skipped++
                    zis.closeEntry()
                    continue
                }
                if (entries.size >= maxPages) {
                    truncated = true
                    break
                }
                val page = File(destDir, pageName(entries.size))
                var written = 0L
                // ⚠️ **一页必须写完**：中途按字节上限截断会留下半张图，
                // 解码时要么报错、要么显示半幅，比直接不要这一页更糟。
                FileOutputStream(page).use { out ->
                    while (true) {
                        val n = zis.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        written += n
                    }
                }
                if (written <= 0L) {
                    // 0 字节条目不是有效页（下载中途被杀会留下这种条目）
                    page.delete()
                } else if (total + written > maxBytes) {
                    // 这一页会把总量撑过上限 → 整页丢弃，并把已计入的字节退回去
                    page.delete()
                    truncated = true
                    zis.closeEntry()
                    break
                } else {
                    total += written
                    entries.add(ZipPageCache.Entry(e.name, written))
                    onProgress(total, if (totalHint > 0) totalHint else 0L)
                }
                zis.closeEntry()
            }
        }
        if (entries.isEmpty()) throw IOException("压缩包里没有可显示的图片")
        return Unpacked(entries, total, buildNote(skipped, truncated, maxPages, maxBytes))
    }

    /** 页文件名：序号，不带任何来自压缩包的信息 */
    fun pageName(index: Int): String = "%05d.bin".format(index)

    internal fun buildNote(skipped: Int, truncated: Boolean, maxPages: Int, maxBytes: Long): String =
        buildString {
            if (skipped > 0) append("跳过 " + skipped + " 个非图片条目")
            if (truncated) {
                if (isNotEmpty()) append("；")
                append("压缩包过大，本次只解出前 " + maxPages + " 页或 " +
                    (maxBytes / 1024 / 1024) + " MB")
            }
        }
}

/**
 * 章节划分：按 zip 内**第一级目录**分组（`第01章_名称/0001.jpg` 算一章）。
 *
 * 纯函数，单独放出来是为了能直接测「多章 / 单章 / 目录名带斜杠」这些边界。
 */
internal object ChapterSplitter {

    fun chaptersOf(names: List<String>): List<MangaChapter> {
        if (names.isEmpty()) return emptyList()
        val counts = LinkedHashMap<String, Int>()
        for (n in names) {
            val slash = n.indexOf('/')
            val dir = if (slash > 0) n.substring(0, slash) else ""
            counts[dir] = (counts[dir] ?: 0) + 1
        }
        if (counts.size <= 1) return listOf(MangaChapter("正文", names.size))
        return counts.map { (dir, n) -> MangaChapter(dir.ifEmpty { "正文" }, n) }
    }
}
