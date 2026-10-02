package com.xd.vdl.core.download

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import com.xd.vdl.core.SaveSettings
import java.io.File

/**
 * 把下载好的成品写入系统公共目录，使其在图库 / 文件管理器可见。
 *
 * 落点由 [SaveSettings] 决定（用户可在设置页改末级文件夹名）：
 * ```
 * 视频   →  Movies/<videoDir>
 * 图片   →  Pictures/<imageDir>
 * 压缩包 →  Download/<archiveDir>
 * ```
 *
 * 目录名一律走 [SaveSettings.sanitize] 兜底，即使配置文件被改坏也不会拼出多级路径。
 */
object MediaStoreExporter {

    // ------------------------------------------------------------------ //
    // 视频
    // ------------------------------------------------------------------ //

    fun exportVideo(context: Context, file: File, mime: String = "video/mp4"): Uri? {
        val resolver = context.contentResolver
        val dir = "Movies/" + subDir(context) { SaveSettings.videoDir(it) }
        val values = ContentValues().apply {
            put(MediaStore.Video.Media.DISPLAY_NAME, file.name)
            put(MediaStore.Video.Media.MIME_TYPE, mime)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                put(MediaStore.Video.Media.RELATIVE_PATH, dir)
                put(MediaStore.Video.Media.IS_PENDING, 1)
            }
        }
        val collection = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        } else {
            MediaStore.Video.Media.EXTERNAL_CONTENT_URI
        }
        return insertAndCopy(context, collection, values, file, MediaStore.Video.Media.IS_PENDING)
    }

    // ------------------------------------------------------------------ //
    // 图片
    // ------------------------------------------------------------------ //

    fun exportImage(context: Context, file: File, mime: String = "image/jpeg"): Uri? {
        val dir = "Pictures/" + subDir(context) { SaveSettings.imageDir(it) }
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, file.name)
            put(MediaStore.Images.Media.MIME_TYPE, mime)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                put(MediaStore.Images.Media.RELATIVE_PATH, dir)
                put(MediaStore.Images.Media.IS_PENDING, 1)
            }
        }
        val collection = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        } else {
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI
        }
        return insertAndCopy(context, collection, values, file, MediaStore.Images.Media.IS_PENDING)
    }

    // ------------------------------------------------------------------ //
    // 压缩包（禁漫本子）
    // ------------------------------------------------------------------ //

    /**
     * 把任意文件写进**下载目录**（`Download/<archiveDir>/`），不经相册。
     *
     * 用于禁漫的压缩包 —— 它是普通文件，放相册里既不好看也难找。
     * Android 10+ 走 MediaStore.Downloads；更早的版本直接写外部存储目录。
     */
    fun exportDownload(context: Context, file: File, mime: String): Uri? {
        val resolver = context.contentResolver
        val name = subDir(context) { SaveSettings.archiveDir(it) }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            // 低版本没有 Downloads 集合，直接落到公共下载目录
            val dir = File(
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
                name,
            )
            if (!dir.exists() && !dir.mkdirs()) return null
            val dst = File(dir, file.name)
            runCatching { file.copyTo(dst, overwrite = true) }.onFailure { return null }
            return Uri.fromFile(dst)
        }
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, file.name)
            put(MediaStore.Downloads.MIME_TYPE, mime)
            put(
                MediaStore.Downloads.RELATIVE_PATH,
                Environment.DIRECTORY_DOWNLOADS + "/" + name,
            )
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val collection = MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        return insertAndCopy(context, collection, values, file, MediaStore.Downloads.IS_PENDING)
    }

    // ------------------------------------------------------------------ //
    // 内部
    // ------------------------------------------------------------------ //

    /**
     * 取末级目录名。**空串会让 RELATIVE_PATH 变成 `Movies/`**（等于直接扔根目录），
     * 那样用户的文件会和别人的混在一起，所以这里强制兜底成默认名。
     */
    private inline fun subDir(context: Context, get: (Context) -> String): String =
        get(context).ifEmpty { SaveSettings.DEFAULT_VIDEO }

    /**
     * insert + 拷贝 + 收尾。Q 以上用 IS_PENDING 标记，写完再置 0 ——
     * 中途失败则把占位行删掉，避免相册里留下 0 字节的僵尸条目。
     *
     * [pendingColumn] 传对应集合的 IS_PENDING 列名；低版本（<Q）不做 pending 处理。
     */
    private fun insertAndCopy(
        context: Context,
        collection: Uri,
        values: ContentValues,
        src: File,
        pendingColumn: String,
    ): Uri? {
        val resolver = context.contentResolver
        val uri = resolver.insert(collection, values) ?: return null
        runCatching {
            resolver.openOutputStream(uri)?.use { out ->
                src.inputStream().use { it.copyTo(out) }
            } ?: throw IllegalStateException("无法打开输出流")
        }.onFailure {
            runCatching { resolver.delete(uri, null, null) }
            return null
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val done = ContentValues().apply { put(pendingColumn, 0) }
            resolver.update(uri, done, null, null)
        }
        return uri
    }
}
