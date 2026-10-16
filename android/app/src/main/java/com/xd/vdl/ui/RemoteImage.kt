package com.xd.vdl.ui

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import okhttp3.Request
import com.xd.vdl.core.net.Http
import com.xd.vdl.core.net.await
import com.xd.vdl.core.parse.JmClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** 封面内存上限：超过就放弃（封面只是缩略图，不值得为它冒 OOM 风险）。 */
private const val MAX_COVER_BYTES = 12L * 1024 * 1024

/** 封面最长边上限（像素）。 */
private const val COVER_MAX_EDGE = 1080

/**
 * 采样解码：先读图片头拿原始尺寸，再按目标边长算 inSampleSize。
 *
 * 原来直接 decodeByteArray 全尺寸解码 —— 一张 4000×3000 的封面解出来就是
 * 48 MB（ARGB_8888），列表里几张就能把内存顶爆。
 */
internal fun decodeSampled(bytes: ByteArray, maxEdge: Int): android.graphics.Bitmap? {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
    var sample = 1
    while (bounds.outWidth / (sample * 2) >= maxEdge || bounds.outHeight / (sample * 2) >= maxEdge) {
        sample *= 2
    }
    val opts = BitmapFactory.Options().apply { inSampleSize = sample }
    return BitmapFactory.decodeByteArray(bytes, 0, bytes.size, opts)
}

/** 极简远程图片加载（避免为一个封面引入 Coil 依赖） */
@Composable
fun RemoteImage(
    url: String,
    modifier: Modifier = Modifier,
    contentScale: ContentScale = ContentScale.Crop,
) {
    var bmp by remember(url) { mutableStateOf<androidx.compose.ui.graphics.ImageBitmap?>(null) }
    LaunchedEffect(url) {
        if (url.isEmpty()) return@LaunchedEffect
        bmp = withContext(Dispatchers.IO) {
            runCatching {
                // 禁漫图片 CDN 证书非常规 + 只认 App UA/Referer，必须走它自己的 client
                val bytes = if (JmClient.isJmImageHost(url)) {
                    JmClient.fetchImageBytes(url)
                } else {
                    val req = Request.Builder()
                        .url(url)
                        .header("User-Agent", Http.UA_DESKTOP)
                        .build()
                    // 可取消：页面关闭 / 换 URL 时立刻断开，不留悬挂连接（B-09）。
                    Http.client.newCall(req).await().use { response ->
                        // 封面不该无限读：超大响应直接放弃，避免把几十 MB 读进内存。
                        val length = response.body?.contentLength() ?: 0L
                        if (length > MAX_COVER_BYTES) return@use null
                        response.body?.bytes()?.takeIf { it.size <= MAX_COVER_BYTES }
                    }
                }
                bytes?.let { decodeSampled(it, COVER_MAX_EDGE) }?.asImageBitmap()
            }.getOrNull()
        }
    }
    Box(
        modifier = modifier.background(MaterialTheme.colorScheme.surfaceVariant),
    ) {
        bmp?.let {
            Image(
                bitmap = it,
                contentDescription = null,
                modifier = Modifier.fillMaxSize(),
                contentScale = contentScale,
            )
        }
    }
}
