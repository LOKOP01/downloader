package com.xd.vdl.core

import android.content.ContentResolver
import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import java.io.File

/** 预览时要按哪种方式渲染 */
enum class MediaKind {
    VIDEO,
    IMAGE,
    /** zip（禁漫本子）：就地解压后按页翻看 */
    ZIP,
    /** 其它：不内嵌预览，只给「用其它应用打开」 */
    OTHER,
}

/**
 * 判断一个已保存的媒体该用哪种预览方式。
 *
 * 任务表里存的是 MediaStore 的 `content://` URI，其 path 形如
 * `content://media/external/video/media/1000000033` —— **末尾没有扩展名**，
 * 所以必须靠 `DISPLAY_NAME`（查 MediaStore）或 MIME 判类型。
 *
 * ⚠️ [detect] 会做一次 `contentResolver.query`，**不要在 Compose 主线程调用**，
 * 调用方要自己 `withContext(Dispatchers.IO)`。
 */
object MediaKindDetect {

    private val VIDEO_EXT = setOf("mp4", "m4v", "mkv", "webm", "mov", "avi", "3gp", "flv", "ts")
    private val IMAGE_EXT = setOf("jpg", "jpeg", "png", "webp", "gif", "bmp", "heic", "avif")
    private val ZIP_EXT = setOf("zip", "cbz")

    /**
     * 判定顺序：**文件名扩展名 → URI 里可见的后缀 → MIME 前缀**。
     * 任何一步失败都继续往下走，最后才落到 [MediaKind.OTHER]。
     */
    fun detect(ctx: Context, savedUri: String, mime: String = ""): MediaKind {
        if (savedUri.isEmpty()) return MediaKind.OTHER

        val name = fileNameOf(ctx, savedUri)
        byExtension(name.substringAfterLast('.', ""))?.let { return it }

        // MediaStore 老式 URI 尾巴上可能直接挂着文件名
        val tail = savedUri.substringAfterLast('/').substringBefore('?')
        if (tail.contains('.')) {
            byExtension(tail.substringAfterLast('.', ""))?.let { return it }
        }

        // URI 路径里出现的后缀（兜底，覆盖 content://xx/a.mp4 这种）
        val low = savedUri.lowercase()
        if (VIDEO_EXT.any { low.contains(".$it") }) return MediaKind.VIDEO
        if (IMAGE_EXT.any { low.contains(".$it") }) return MediaKind.IMAGE
        if (ZIP_EXT.any { low.contains(".$it") }) return MediaKind.ZIP

        // 最后问系统要 MIME
        val m = (mime.ifEmpty { mimeOf(ctx, savedUri) }).lowercase()
        return when {
            m.startsWith("video/") -> MediaKind.VIDEO
            m.startsWith("image/") -> MediaKind.IMAGE
            m.contains("zip") -> MediaKind.ZIP
            else -> MediaKind.OTHER
        }
    }

    private fun byExtension(ext: String): MediaKind? {
        if (ext.isEmpty()) return null
        val e = ext.lowercase()
        return when {
            e in VIDEO_EXT -> MediaKind.VIDEO
            e in IMAGE_EXT -> MediaKind.IMAGE
            e in ZIP_EXT -> MediaKind.ZIP
            else -> null
        }
    }

    /** 从 URI（或文件名）里抠出显示名；查不到就回退到 URI 的 path 段 */
    fun fileNameOf(ctx: Context, uri: String): String {
        if (uri.isEmpty()) return ""
        val parsed = runCatching { Uri.parse(uri) }.getOrNull()
        if (parsed != null && parsed.scheme == ContentResolver.SCHEME_CONTENT) {
            // cursor 可能为 null（URI 已失效），必须判空
            runCatching {
                ctx.contentResolver.query(parsed, null, null, null, null)?.use { c ->
                    val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    if (idx >= 0 && c.moveToFirst()) return c.getString(idx).orEmpty()
                }
            }
        }
        if (parsed != null && parsed.scheme == ContentResolver.SCHEME_FILE) {
            return parsed.lastPathSegment.orEmpty()
        }
        val tail = uri.substringAfterLast('/').substringBefore('?')
        if (tail.contains('.')) return runCatching { Uri.decode(tail) }.getOrDefault(tail)
        return parsed?.lastPathSegment?.let {
            runCatching { Uri.decode(it) }.getOrDefault(it)
        }.orEmpty()
    }

    fun mimeOf(ctx: Context, uri: String): String = runCatching {
        ctx.contentResolver.getType(Uri.parse(uri)).orEmpty()
    }.getOrDefault("")

    /** 本地文件也判一下（下载目录里的 zip 等） */
    fun detectFile(f: File): MediaKind = byExtension(f.extension) ?: MediaKind.OTHER
}
