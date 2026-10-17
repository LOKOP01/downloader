package com.xd.vdl.core.manga

import android.content.Context
import com.xd.vdl.core.AppLog
import java.io.IOException
import java.io.InputStream

/** 打开一本书后拿到的页序 */
data class OpenedPages(
    /** 有序的页标识（zip 内条目名 / 图片 URI），阅读进度就锚在这些值上 */
    val ids: List<String>,
    /** 章节划分（按 zip 内第一级目录）；单章时只有一项 */
    val chapters: List<MangaChapter>,
    /** 给用户看的补充说明（如「有 3 张图片已不存在」）；没有就是空串 */
    val note: String = "",
)

/**
 * 一页的来源。
 *
 * 阅读器只认这个接口，不关心背后是压缩包还是相册散图 —— 这样「本地阅读」与
 * 后续的其它离线来源可以共用同一套翻页/缩放实现。
 */
interface MangaPageSource {

    /** 书标题（界面用） */
    val title: String

    /**
     * 准备页序。**可能很慢**（zip 首次打开要解压到缓存），调用方必须在 IO 线程
     * 并显示进度。失败必须抛出用户能看懂的原因（文件没了 / 不是压缩包 / 包内没有图片）。
     */
    suspend fun open(): OpenedPages

    /** 取第 [index] 页（下标对应 [OpenedPages.ids] 的顺序）；调用方负责关闭 */
    fun openPage(index: Int): InputStream

    /** 一句话描述数据来源，写日志用 */
    fun describe(): String
}

// ---------------------------------------------------------------------- //
// 压缩包（禁漫本子、官方打包直链成品）
// ---------------------------------------------------------------------- //

/**
 * 压缩包页源。
 *
 * 首次打开把包内图片**解压到应用缓存**并落一份 manifest（见 [ZipPageCache]），
 * 之后翻页只读缓存文件 —— 否则每翻一页都要从头流式扫一遍 zip
 * （zip 只能顺序读，`ZipInputStream` 没有随机访问），几百页的本子会卡到不可用。
 *
 * 缓存键包含**文件大小**：同一个 URI 被重新下载成不同内容时键就变了，
 * 天然不会读到旧缓存。
 */
class ZipPageSource(
    private val ctx: Context,
    private val uri: String,
    override val title: String,
    private val totalBytesHint: Long = 0L,
    private val onProgress: (done: Long, total: Long) -> Unit = { _, _ -> },
) : MangaPageSource {

    @Volatile
    private var opened: ZipPageCache.Cached? = null

    override suspend fun open(): OpenedPages {
        val key = ZipPageCache.keyOf(ctx, uri)
        val c = ZipPageCache.lookup(ctx, key) ?: ZipPageCache.extract(
            ctx = ctx,
            key = key,
            totalHint = totalBytesHint,
            onProgress = onProgress,
            openZip = {
                MangaFiles.open(ctx, uri)
                    ?: throw IOException("打不开这个压缩包（文件可能已被删除或移动）")
            },
        )
        opened = c
        val ids = c.entries.map { it.name }
        return OpenedPages(
            ids = ids,
            chapters = ChapterSplitter.chaptersOf(ids),
            note = c.note,
        )
    }

    override fun openPage(index: Int): InputStream {
        val c = opened ?: throw IllegalStateException("这本书还没打开")
        if (index !in c.entries.indices) {
            throw IOException("第 ${index + 1} 页不存在（共 ${c.entries.size} 页）")
        }
        val f = ZipPageCache.fileAt(c, index)
        if (!f.isFile) {
            throw IOException("第 ${index + 1} 页的缓存已丢失，请退出后重新打开这本书")
        }
        return f.inputStream()
    }

    override fun describe(): String = "zip(" + MangaFiles.shortOf(uri) + ")"
}

// ---------------------------------------------------------------------- //
// 散图（图集下载保存到相册的若干张）
// ---------------------------------------------------------------------- //

/**
 * 散图页源：页标识就是图片 URI 本身。
 *
 * 顺序**严格沿用入库时记录的顺序**（`DownloadTask.savedUris`）：图集的页序来自解析结果，
 * 按文件名重排反而会打乱。
 */
class ImageListPageSource(
    private val ctx: Context,
    override val title: String,
    private val uris: List<String>,
    private val chapterNames: List<MangaChapter> = emptyList(),
) : MangaPageSource {

    /** open() 过滤掉空串后的实际页序 —— openPage 必须用它，否则下标会错位 */
    @Volatile
    private var resolved: List<String> = emptyList()

    override suspend fun open(): OpenedPages {
        val valid = uris.filter { it.isNotEmpty() }
        if (valid.isEmpty()) throw IOException("没有可阅读的图片（记录里没有图片地址）")
        resolved = valid
        // 只探前若干张：全探在几百张时会明显拖慢打开速度，而首个坏页已经足以提示
        val missing = valid.take(PROBE_LIMIT).count { !MangaFiles.exists(ctx, it) }
        return OpenedPages(
            ids = valid,
            chapters = chapterNames.ifEmpty { listOf(MangaChapter("正文", valid.size)) },
            note = if (missing > 0) "有 ${missing} 张图片已不存在（其余仍可阅读）" else "",
        )
    }

    override fun openPage(index: Int): InputStream {
        val uri = resolved.getOrNull(index)
            ?: throw IOException("第 ${index + 1} 张不存在（共 ${resolved.size} 张）")
        return MangaFiles.open(ctx, uri)
            ?: throw IOException("第 ${index + 1} 张图片已不存在（可能被清理或移动）")
    }

    override fun describe(): String = "images(${resolved.size})"

    private companion object {
        /** 打开时最多探多少张存在性 */
        const val PROBE_LIMIT = 12
    }
}

// ---------------------------------------------------------------------- //
// 工厂
// ---------------------------------------------------------------------- //

object MangaSources {

    /**
     * 按书架记录造一个页源。
     *
     * [MangaBook.kind] 是**入库时**判定的，这里不盲信它，而是再看一眼实际文件类型：
     * 用户可能把 zip 换成了同名目录，记录也可能来自更早的版本。
     */
    fun of(
        ctx: Context,
        book: MangaBook,
        onProgress: (done: Long, total: Long) -> Unit = { _, _ -> },
    ): MangaPageSource {
        val looksZip = book.uri.isNotEmpty() && MangaFiles.looksLikeZip(ctx, book.uri)
        if (!looksZip) {
            // 散图库用 uris（下载时保存的全部图片）；旧的单 URI 记录回退到 uri
            val pages = book.uris.ifEmpty { listOf(book.uri) }.filter { it.isNotEmpty() }
            AppLog.i("打开本地漫画《" + book.title + "》散图 " + pages.size + " 张")
            return ImageListPageSource(ctx, book.title, pages, book.chapters)
        }
        val size = MangaFiles.meta(ctx, book.uri).size
        AppLog.i("打开本地漫画《${book.title}》 ${book.kind} ${MangaFiles.shortOf(book.uri)}")
        return ZipPageSource(ctx, book.uri, book.title, size, onProgress)
    }
}
