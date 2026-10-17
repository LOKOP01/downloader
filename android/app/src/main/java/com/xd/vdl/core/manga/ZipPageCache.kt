package com.xd.vdl.core.manga

import android.content.Context
import com.xd.vdl.core.AppLog
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.nio.charset.StandardCharsets

/**
 * 压缩包页缓存：把包内图片**一次解压**到应用缓存目录，并落一份 manifest。
 *
 * 为什么不能边翻边读 zip：ZipInputStream 是**顺序流**，没有随机访问能力，
 * 每翻一页都从头扫一遍几百 MB 的包，阅读体验直接崩掉。
 *
 * 布局（app 私有缓存，系统在存储紧张时可能清理，清理后自动重建）：
 *
 *     cacheDir/manga-pages/<key>/manifest.json    页序（条目名 + 文件大小）
 *     cacheDir/manga-pages/<key>/00000.bin        第 1 页原始字节
 *     cacheDir/manga-pages/<key>/00001.bin        …
 *
 * 几条必须遵守的约束：
 *  1. **绝不用 zip 内的条目名当磁盘路径**：`../../foo` 这类名字能写到缓存目录之外
 *     （zip slip）。落盘一律用序号命名，条目名只存进 manifest。
 *  2. **保留原始字节**，不重新编码：禁漫下载时已按切片规则解码并重压成 JPEG，
 *     这里再解一次纯属浪费，二次编码还会掉画质；格式判断交给解码器。
 *  3. **manifest 只在全部条目写完后才落**，因此「manifest 存在」= 缓存完整。
 *     中途被杀只会留下没有 manifest 的目录，下次自动重建。
 *  4. 页数/总字节有上限，超了**明确告知**而不是静默截断（旧预览实现会静默丢掉后半本）。
 */
object ZipPageCache {

    private const val DIR = "manga-pages"
    private const val MANIFEST = "manifest.json"

    /** 单包最多解多少页（超出的部分明确提示，不静默丢弃） */
    internal const val MAX_PAGES = 5000

    /** 单包解压总量上限（1.5 GiB）：缓存盘也要留余量 */
    internal const val MAX_TOTAL_BYTES = 1536L * 1024 * 1024

    /** 认作图片的扩展名 */
    internal val IMAGE_EXT = listOf(".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")

    data class Entry(val name: String, val size: Long)

    data class Cached(
        val key: String,
        val dir: File,
        val entries: List<Entry>,
        /** 超限 / 跳过条目等情况的说明，给界面显示 */
        val note: String = "",
    )

    // ------------------------------------------------------------------ //
    // 键与查询
    // ------------------------------------------------------------------ //

    /**
     * 缓存键 = 「URI 里的字母数字尾段 + 文件大小」。
     *
     * 带上大小是为了**自动失效**：同一本书重新下载后内容变了，大小几乎必然不同
     * （即使碰巧相同，最坏也只是读到一份同样尺寸的旧缓存，不会串到别的书）。
     */
    fun keyOf(ctx: Context, uri: String): String {
        val id = uri.filter { it.isLetterOrDigit() }.takeLast(24).ifEmpty { "x" }
        val size = MangaFiles.meta(ctx, uri).size
        return id + "-" + size
    }

    private fun root(ctx: Context): File = File(ctx.cacheDir, DIR)

    private fun dirOf(ctx: Context, key: String): File = File(root(ctx), key)

    /** 读 manifest；不存在或不合法都返回 null（调用方据此重新解压） */
    fun lookup(ctx: Context, key: String): Cached? {
        val dir = dirOf(ctx, key)
        val mf = File(dir, MANIFEST)
        if (!mf.isFile) return null
        return runCatching {
            val o = JSONObject(mf.readText(StandardCharsets.UTF_8))
            val arr = o.optJSONArray("entries") ?: return null
            val entries = ArrayList<Entry>(arr.length())
            for (i in 0 until arr.length()) {
                val e = arr.optJSONObject(i) ?: continue
                val name = e.optString("name")
                if (name.isEmpty()) continue
                entries.add(Entry(name, e.optLong("size", 0L)))
            }
            if (entries.isEmpty()) return null
            // 抽查首尾两页：缓存被系统清过一半时 manifest 可能还留着
            for (probe in listOf(0, entries.size - 1).distinct()) {
                if (!File(dir, pageName(probe)).isFile) return null
            }
            Cached(key, dir, entries, o.optString("note"))
        }.getOrNull()
    }

    /** 某页对应的缓存文件 */
    fun fileAt(cached: Cached, index: Int): File = File(cached.dir, pageName(index))

    // ------------------------------------------------------------------ //
    // 解压
    // ------------------------------------------------------------------ //

    /**
     * 解压 [openZip] 给出的压缩包到缓存目录。
     *
     * 必须运行在 IO 线程。**任何异常都会先清理半成品目录再抛出**，
     * 保证下次打开不会读到半截缓存。
     *
     * @param totalHint 压缩包总字节（用于进度显示；<=0 时进度条走不确定态）
     * @param onProgress (已写字节, 总字节)
     */
    fun extract(
        ctx: Context,
        key: String,
        totalHint: Long,
        onProgress: (Long, Long) -> Unit,
        openZip: () -> InputStream,
    ): Cached {
        val dir = dirOf(ctx, key)
        val tmpDir = File(root(ctx), key + ".tmp")
        runCatching { tmpDir.deleteRecursively() }
        if (!tmpDir.isDirectory && !tmpDir.mkdirs()) {
            throw IOException("无法创建阅读缓存目录（存储空间不足？）")
        }
        val started = System.currentTimeMillis()
        try {
            val unpacked = openZip().use { ins ->
                ZipUnpacker.unpack(ins, tmpDir, totalHint, onProgress = onProgress)
            }
            val entries = unpacked.entries
            val total = unpacked.totalBytes
            val note = unpacked.note
            writeManifest(tmpDir, entries, note)
            runCatching { dir.deleteRecursively() }
            // rename 在同一父目录内是原子的；失败则退化为逐页搬（少见）
            if (!tmpDir.renameTo(dir)) {
                if (!dir.exists() && !dir.mkdirs()) throw IOException("无法创建阅读缓存目录")
                for (i in entries.indices) {
                    File(tmpDir, pageName(i)).renameTo(File(dir, pageName(i)))
                }
                writeManifest(dir, entries, note)
                tmpDir.deleteRecursively()
            }
            val noteSuffix = if (note.isEmpty()) "" else "（" + note + "）"
            AppLog.i(
                "解压漫画缓存 " + key + "：" + entries.size + " 页 / " +
                    (total / 1024 / 1024) + " MB / 用时 " +
                    ((System.currentTimeMillis() - started) / 1000.0) + " 秒" + noteSuffix,
            )
            return Cached(key, dir, entries, note)
        } catch (e: Exception) {
            runCatching { tmpDir.deleteRecursively() }
            throw e
        }
    }

    private fun writeManifest(dir: File, entries: List<Entry>, note: String) {
        val data = JSONObject().apply {
            put("version", 1)
            put("note", note)
            put("entries", JSONArray().apply {
                entries.forEach { e ->
                    put(JSONObject().apply { put("name", e.name); put("size", e.size) })
                }
            })
        }.toString()
        File(dir, MANIFEST).writeText(data, StandardCharsets.UTF_8)
    }

    private fun pageName(index: Int): String = ZipUnpacker.pageName(index)

    // ------------------------------------------------------------------ //
    // 维护
    // ------------------------------------------------------------------ //

    /** 全部缓存占用字节（设置页显示 / 清理用） */
    fun sizeOnDisk(ctx: Context): Long = runCatching {
        root(ctx).walkBottomUp().filter { it.isFile }.sumOf { it.length() }
    }.getOrDefault(0L)

    /** 清掉全部页缓存；返回释放的字节数 */
    fun clearAll(ctx: Context): Long {
        val before = sizeOnDisk(ctx)
        runCatching { root(ctx).deleteRecursively() }
        AppLog.i("已清理阅读缓存 " + (before / 1024 / 1024) + " MB")
        return before
    }

    /** 删除单本书的缓存（书架「移除」时调用；失败不抛） */
    fun evict(ctx: Context, key: String) {
        runCatching { dirOf(ctx, key).deleteRecursively() }
    }
}
