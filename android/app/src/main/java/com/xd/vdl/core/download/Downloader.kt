package com.xd.vdl.core.download

import com.xd.vdl.core.Platform
import com.xd.vdl.core.net.Http
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.net.URI

/** 单文件流式下载（带 Referer/Cookie，256KB 块，支持取消） */
object Downloader {

    fun hostOf(url: String): String =
        runCatching { URI(url).host }.getOrNull().orEmpty().ifEmpty { url.take(48) }

    /**
     * 按顺序尝试候选地址，一个挂了换下一个。
     * B 站 PCDN 的 baseUrl 经常 TLS 重置，backupUrl 的 upos 镜像是通的。
     */
    suspend fun downloadFirst(
        urls: List<String>,
        platform: Platform,
        dest: File,
        onProgress: (downloaded: Long, total: Long) -> Unit,
        onTry: (index: Int, total: Int, url: String) -> Unit = { _, _, _ -> },
        onFail: (index: Int, total: Int, url: String, err: String) -> Unit = { _, _, _, _ -> },
    ) {
        val cands = urls.filter { it.isNotEmpty() }.distinct()
        if (cands.isEmpty()) throw IOException("没有下载地址")
        var last: Exception? = null
        for ((i, u) in cands.withIndex()) {
            onTry(i + 1, cands.size, u)
            try {
                download(u, platform, dest, onProgress)
                return
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                last = e
                onFail(i + 1, cands.size, u, e.message ?: e.javaClass.simpleName)
                dest.delete()
                File(dest.parentFile, dest.name + ".part").delete()
            }
        }
        throw last ?: IOException("全部候选地址都失败")
    }

    suspend fun download(
        url: String,
        platform: Platform,
        dest: File,
        onProgress: (downloaded: Long, total: Long) -> Unit,
    ) = withContext(Dispatchers.IO) {
        val scope: CoroutineScope = this
        val resp = Http.downloadClient.newCall(Http.request(url, platform)).execute()
        resp.use {
            if (!it.isSuccessful) throw IOException("HTTP ${it.code}")
            val body = it.body ?: throw IOException("响应为空")
            val total = body.contentLength()
            dest.parentFile?.mkdirs()
            val tmp = File(dest.parentFile, dest.name + ".part")
            body.byteStream().use { input ->
                RandomAccessFile(tmp, "rw").use { raf ->
                    raf.setLength(0)
                    val buf = ByteArray(256 * 1024)
                    var got = 0L
                    var lastTick = 0L
                    while (true) {
                        scope.ensureActive()
                        val n = input.read(buf)
                        if (n < 0) break
                        raf.write(buf, 0, n)
                        got += n
                        if (got - lastTick >= 256 * 1024) {
                            lastTick = got
                            onProgress(got, total)
                        }
                    }
                    if (total >= 0 && got != total) {
                        throw IOException("下载不完整：收到 $got/$total 字节")
                    }
                    if (got == 0L) throw IOException("下载文件为空")
                    onProgress(got, total)
                }
            }
            scope.ensureActive()
            if (dest.exists()) dest.delete()
            if (!tmp.renameTo(dest)) {
                tmp.copyTo(dest, overwrite = true)
                tmp.delete()
            }
        }
    }
}
