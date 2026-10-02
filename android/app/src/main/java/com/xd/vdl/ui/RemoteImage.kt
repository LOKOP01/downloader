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
import com.xd.vdl.core.parse.JmClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

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
                    Http.client.newCall(req).execute().use { it.body?.bytes() }
                }
                bytes?.let {
                    BitmapFactory.decodeByteArray(it, 0, it.size)?.asImageBitmap()
                }
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
