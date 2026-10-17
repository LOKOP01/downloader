package com.xd.vdl.core.manga

import android.content.Context
import com.xd.vdl.core.AppLog
import java.io.File

/** 一次删除尝试的结果 */
data class DeleteResult(
    /** 真正删掉的条目数 */
    val deleted: Int,
    /** 删除失败的条目（URI 摘要），给用户看「哪些没删掉」 */
    val failed: List<String>,
    /** 删掉的字节数（按删除前的大小估算） */
    val freedBytes: Long,
    /** 因为安全护栏被拒绝、没有去碰的条目 */
    val skipped: List<String>,
) {
    val ok: Boolean get() = failed.isEmpty() && skipped.isEmpty()

    /** 给界面的结果文案 */
    val summary: String
        get() {
            if (deleted == 0 && failed.isEmpty() && skipped.isEmpty()) return "没有需要删除的文件"
            val mb = freedBytes / 1024.0 / 1024.0
            return buildString {
                append("已删除 ")
                append(deleted)
                append(" 个文件")
                if (freedBytes > 0) append("，释放 " + "%.1f".format(mb) + " MB")
                if (skipped.isNotEmpty()) append("；跳过 " + skipped.size + " 个（不像可删文件）")
                if (failed.isNotEmpty()) append("；" + failed.size + " 个删除失败")
            }
        }
}

/**
 * 删除「某一本书自己的」已下载文件。
 *
 * ⚠️ 这是不可逆操作，所以护栏按「宁可少删、绝不误删」来设计：
 *
 *  1. **只删记录里列出的 URI**，不推断路径、不按名字匹配、不扫目录。
 *     记录来自下载当时的真实产物（zip 或逐张图片），这就是白名单本身。
 *  2. **绝不递归删目录**。`file://` 指向目录时直接跳过并报告 —— 目录里可能有
 *     别的漫画或用户自己的东西，递归删除是不可接受的风险。
 *  3. **条目数设上限**。书架文件损坏导致 `uris` 被写成一堆无关 URI 时，
 *     不做「尽力而为」的批量删除，而是整体拒绝。
 *  4. 删除失败（权限/已被外部应用占据）只报告，不重试、不绕过。
 */
object MangaDeleter {

    /** 单本书允许一次删除的最大条目数；超过就当作记录异常，整体拒绝 */
    internal const val MAX_TARGETS = 5000

    /**
     * 这本书要删哪些 URI。
     *
     * 纯函数（可单测）：顺序稳定、去重、丢掉空串。
     * 压缩包只有 1 项；散图是全部图片。
     */
    fun targets(book: MangaBook): List<String> =
        (book.uris + book.uri)
            .filter { it.isNotBlank() }
            .distinct()

    /** 这些目标里，有多少是**看起来可以删**的（用于界面提示） */
    fun describeTargets(book: MangaBook): String {
        val n = targets(book).size
        if (n <= 0) return "没有登记文件"
        return if (book.kind == BookKind.ZIP) {
            "1 个压缩包"
        } else {
            n.toString() + " 张图片"
        }
    }

    /**
     * 执行删除。**必须运行在 IO 线程**。
     *
     * 删除失败不会抛异常 —— 调用方需要的是「删掉了几个、哪些没删掉」，
     * 而不是一个异常把整个界面卡住。
     */
    fun delete(ctx: Context, book: MangaBook): DeleteResult {
        val list = targets(book)
        if (list.isEmpty()) return DeleteResult(0, emptyList(), 0, emptyList())
        if (list.size > MAX_TARGETS) {
            AppLog.w(
                "拒绝删除《" + book.title + "》：登记了 " + list.size + " 个文件，超过安全上限 " +
                    MAX_TARGETS + "（记录可能已损坏）",
            )
            return DeleteResult(0, emptyList(), 0, list.take(8).map { MangaFiles.shortOf(it) })
        }
        var deleted = 0
        var freed = 0L
        val failed = ArrayList<String>()
        val skipped = ArrayList<String>()
        for (uri in list) {
            val tag = MangaFiles.shortOf(uri)
            // ① file:// ：先判目录，绝不递归删
            val f = MangaFiles.fileOf(uri)
            if (f != null) {
                if (f.isDirectory) {
                    // 目录可能是「一本漫画一个文件夹」的形态，里面可能还有别的东西
                    skipped.add(tag)
                    AppLog.w("跳过目录（不递归删除）：" + f.path)
                    continue
                }
                if (!f.exists()) continue
                val size = f.length()
                if (runCatching { f.delete() }.getOrDefault(false)) {
                    deleted++
                    freed += size
                } else {
                    failed.add(tag)
                }
                continue
            }
            // ② content:// ：交给 MediaStore / 对应 Provider
            if (!MangaFiles.isContent(uri)) {
                skipped.add(tag)
                continue
            }
            val size = runCatching { MangaFiles.meta(ctx, uri).size }.getOrDefault(0L)
            val rows = runCatching {
                ctx.contentResolver.delete(android.net.Uri.parse(uri), null, null)
            }
            if (rows.getOrDefault(0) > 0) {
                deleted++
                freed += size
            } else {
                // 0 行 = 条目已不存在（不算错），抛异常 = 真的失败（权限等）
                val err = rows.exceptionOrNull()
                if (err != null) {
                    failed.add(tag)
                    AppLog.w("删除失败 " + tag + "：" + err.javaClass.simpleName + ": " + err.message)
                }
            }
        }
        AppLog.i(
            "删除《" + book.title + "》的文件：成功 " + deleted + " / 共 " + list.size +
                "，释放 " + (freed / 1024 / 1024) + " MB" +
                (if (failed.isEmpty()) "" else "，失败 " + failed.size) +
                (if (skipped.isEmpty()) "" else "，跳过 " + skipped.size),
        )
        return DeleteResult(deleted, failed, freed, skipped)
    }

    /** 只为单测暴露的目录判定护栏（file:// 指向目录时不允许删） */
    internal fun shouldSkipDirectory(file: File): Boolean = file.isDirectory
}
