package com.xd.vdl.core.manga

import android.content.ContentResolver
import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import java.io.File
import java.io.InputStream

/** 一个已落盘媒体的客观信息（查不到就给默认值，绝不抛） */
data class MediaMeta(
    val name: String = "",
    val size: Long = 0L,
    /**
     * MediaStore 的 RELATIVE_PATH，形如 `Download/视频下载器`。
     * 低版本（API < 29）、file:// 或查不到时为空 —— 调用方必须容忍空值。
     */
    val relativePath: String = "",
) {
    /** 末级目录名；拿不到路径时返回空串（调用方兜底） */
    val dirName: String get() = relativePath.trimEnd('/').substringAfterLast('/')
}

/**
 * 访问「已下载文件」的统一入口。
 *
 * 为什么需要它：任务表里存的是 MediaStore 的 `content://` URI（形如
 * `content://media/external/downloads/1000000033`，**末尾没有文件名**），
 * 而书架要按目录名+文件名算稳定标识、要判断文件还在不在、要拿到文件大小。
 * 这些都得走 `contentResolver.query`。
 *
 * ⚠️ 本对象的所有方法都可能做磁盘/ContentProvider IO，**不要在主线程调用**。
 * 所有方法内部都吞异常：文件被删、URI 失效、Provider 抛 SecurityException 时
 * 返回 false / null / 默认值，由调用方转成用户能看懂的提示。
 */
object MangaFiles {

    fun isContent(uri: String): Boolean = uri.startsWith("content:")

    fun isFile(uri: String): Boolean = uri.startsWith("file:")

    /**
     * file:// → File；其它返回 null。
     *
     * ⚠️ 这里**不走 `android.net.Uri`**，而是自己解字符串：
     *  · `Uri.path` 在 JVM 单测里是桩，任何调用都会抛 `Stub!`；
     *  · 更要紧的是**中文/空格路径必须解码**。`File.toURI()` 会把中文百分号编码成
     *    `%E4%B8%AD`，若不解码就会去操作一个名字字面量就是 `%E4%B8%AD` 的文件，
     *    结果是「删除静默失败、明明点了没删掉」。
     *
     * 只认 `file://<path>` 与 `file:<path>` 两种形态：
     * `file://host/path` 里的 host 在本地文件场景下要么为空、要么是 localhost，直接忽略。
     */
    fun fileOf(uri: String): File? {
        if (!isFile(uri)) return null
        var rest = uri.removePrefix("file:")
        // file:///a/b → 去掉 file:// 前缀后剩 /a/b；file://localhost/a → 去掉 // 后再去掉 host
        if (rest.startsWith("//")) {
            rest = rest.substring(2)
            val slash = rest.indexOf('/')
            rest = if (slash >= 0) rest.substring(slash) else ""
        }
        if (rest.isEmpty()) return null
        val decoded = runCatching { java.net.URLDecoder.decode(rest, "UTF-8") }.getOrDefault(rest)
        return decoded.takeIf { it.isNotEmpty() }?.let(::File)
    }

    /** 查询显示名 / 大小 / 相对目录；任何一步失败都只丢对应字段 */
    fun meta(ctx: Context, uri: String): MediaMeta {
        if (uri.isEmpty()) return MediaMeta()
        fileOf(uri)?.let { f ->
            return MediaMeta(f.name, f.length(), "")
        }
        if (!isContent(uri)) return MediaMeta()
        val parsed = runCatching { Uri.parse(uri) }.getOrNull() ?: return MediaMeta()
        return runCatching {
            ctx.contentResolver.query(parsed, null, null, null, null)?.use { c ->
                if (!c.moveToFirst()) return@use MediaMeta()
                fun str(col: String): String {
                    val i = c.getColumnIndex(col)
                    return if (i >= 0) c.getString(i).orEmpty() else ""
                }
                val nameIdx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                val sizeIdx = c.getColumnIndex(OpenableColumns.SIZE)
                MediaMeta(
                    name = if (nameIdx >= 0) c.getString(nameIdx).orEmpty() else "",
                    size = if (sizeIdx >= 0) c.getLong(sizeIdx) else 0L,
                    relativePath = str("relative_path"),
                )
            } ?: MediaMeta()
        }.getOrDefault(MediaMeta())
    }

    /** 只查名字（预览对话框等场景用得多） */
    fun displayName(ctx: Context, uri: String): String = meta(ctx, uri).name

    /** 打开输入流；打不开返回 null（文件被删/权限失效） */
    fun open(ctx: Context, uri: String): InputStream? {
        if (uri.isEmpty()) return null
        fileOf(uri)?.let { f ->
            return runCatching { if (f.isFile) f.inputStream() else null }.getOrNull()
        }
        if (!isContent(uri)) return null
        return runCatching {
            ctx.contentResolver.openInputStream(Uri.parse(uri))
        }.getOrNull()
    }

    /**
     * 文件是否还在。
     *
     * content:// 一律**实际开一次流**再关掉：只 query 的话，条目还在但底层文件
     * 已被其它应用删除时仍会返回一行，界面就会显示成「可读」，点进去才报错。
     * 开流的代价可接受（只在打开阅读器/书架刷新时调用）。
     */
    fun exists(ctx: Context, uri: String): Boolean {
        if (uri.isEmpty()) return false
        fileOf(uri)?.let { return it.isFile }
        if (!isContent(uri)) return false
        return runCatching {
            ctx.contentResolver.openInputStream(Uri.parse(uri))?.use { true } ?: false
        }.getOrDefault(false)
    }

    /** 把 content URI 变成可长期显示的字符串（拿不到名字时回退原样） */
    fun describe(ctx: Context, uri: String): String =
        displayName(ctx, uri).ifEmpty { uri.substringAfterLast('/') }

    /**
     * 这个 URI 指向的是不是压缩包（只看显示名后缀）。
     *
     * 用**显示名**而不是 URI 尾巴：MediaStore 的 content URI 末尾是数字 id，
     * `content://media/external/downloads/1000000033` 里既没有 `.zip` 也没有名字。
     */
    fun looksLikeZip(ctx: Context, uri: String): Boolean {
        val name = displayName(ctx, uri).lowercase()
        return name.endsWith(".zip") || name.endsWith(".cbz")
    }

    /** 仅用于日志：不要打印完整 URI（含 MediaStore 数字 id，长且没信息量） */
    fun shortOf(uri: String): String {
        if (uri.isEmpty()) return "(空)"
        val tail = uri.substringAfterLast('/')
        return if (tail.length > 12) tail.take(12) else tail
    }
}
