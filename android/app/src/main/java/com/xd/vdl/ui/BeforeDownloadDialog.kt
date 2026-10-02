package com.xd.vdl.ui

import android.app.Activity
import android.graphics.BitmapFactory
import android.net.Uri
import android.widget.VideoView
import android.widget.MediaController
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.xd.vdl.core.Platform
import com.xd.vdl.core.download.JmDownloader
import com.xd.vdl.core.model.VideoInfo
import com.xd.vdl.core.net.Http
import com.xd.vdl.core.parse.JmParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

private data class JmPage(val chapterId: String, val name: String, val filename: String, val number: Int)

/** 解析结果的下载前预览与按张选择。 */
@Composable
fun BeforeDownloadDialog(
    info: VideoInfo, qualityIndex: Int, vm: AppViewModel, activity: Activity,
    onClose: () -> Unit,
) {
    var jmPages by remember(info) { mutableStateOf<List<JmPage>?>(null) }
    var error by remember(info) { mutableStateOf("") }
    var selected by remember(info) { mutableStateOf<Set<Int>>(emptySet()) }
    var focused by remember(info) { mutableStateOf<Int?>(null) }
    if (info.platform == Platform.JMCOMIC) {
        LaunchedEffect(info) {
            try {
                jmPages = withContext(Dispatchers.IO) {
                    val chapters = info.jmChapters.ifEmpty { listOf(info.jmId to info.title) }
                    chapters.flatMap { (cid, name) ->
                        JmParser.fetchChapterImages(cid).mapIndexed { i, filename ->
                            JmPage(cid, name, filename, i + 1)
                        }
                    }
                }
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                error = e.message ?: "读取目录失败"
            }
        }
    }
    val count = if (info.platform == Platform.JMCOMIC) jmPages?.size ?: 0 else info.images.size
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(
            Modifier.fillMaxSize().background(MaterialTheme.colorScheme.surface).padding(16.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(info.title, modifier = Modifier.weight(1f), maxLines = 2,
                    style = MaterialTheme.typography.titleMedium)
                OutlinedButton(onClick = onClose) { Text("关闭") }
            }
            Spacer(Modifier.height(8.dp))
            if (!info.isImage) {
                val quality = info.qualities.getOrNull(qualityIndex)
                val playback = info.qualities.firstOrNull { !it.needMerge && it.url.isNotEmpty() }
                    ?: quality
                if (playback == null || playback.url.isEmpty()) {
                    Text("没有可用的预览地址")
                } else {
                    Text(if (playback.needMerge) "预览画面（此画质的音轨单独下载）" else "在线播放预览")
                    NetworkVideoPreview(playback.url, info.platform, Modifier.fillMaxWidth().weight(1f))
                }
            } else {
                when {
                    error.isNotEmpty() -> Text("读取图片失败：$error", color = MaterialTheme.colorScheme.error)
                    info.platform == Platform.JMCOMIC && jmPages == null ->
                        Box(Modifier.fillMaxWidth().weight(1f), contentAlignment = Alignment.Center) {
                            CircularProgressIndicator()
                        }
                    count == 0 -> Text("没有可预览的图片")
                    else -> {
                        Text("点图片放大查看，勾选要保存的图片（已选 ${selected.size} / $count）")
                        Spacer(Modifier.height(8.dp))
                        LazyVerticalGrid(
                            columns = GridCells.Fixed(2),
                            modifier = Modifier.weight(1f),
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            itemsIndexed((0 until count).toList(), key = { _, i -> i }) { _, i ->
                                Column {
                                    Box(Modifier.fillMaxWidth().height(180.dp).clickable { focused = i }) {
                                        if (jmPages != null) {
                                            val page = jmPages!![i]
                                            SelectableImage("jm:${page.chapterId}:${page.number}", page, info)
                                        } else SelectableImage(info.images[i], null, info)
                                        Checkbox(
                                            checked = i in selected,
                                            onCheckedChange = { checked ->
                                                selected = if (checked) selected + i else selected - i
                                            },
                                            modifier = Modifier.align(Alignment.TopEnd),
                                        )
                                    }
                                    Text(if (jmPages != null) "${jmPages!![i].name} · ${jmPages!![i].number}"
                                        else "第 ${i + 1} 张", maxLines = 1)
                                }
                            }
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(onClick = { selected = (0 until count).toSet() }, modifier = Modifier.weight(1f)) {
                                Text("全选")
                            }
                            OutlinedButton(onClick = { selected = emptySet() }, modifier = Modifier.weight(1f)) {
                                Text("清空选择")
                            }
                        }
                        Button(
                            enabled = selected.isNotEmpty(),
                            onClick = {
                                if (jmPages != null) vm.downloadSelected(activity,
                                    jmPages = selected.map { jmPages!![it].let { p -> p.chapterId to p.number } }.toSet())
                                else vm.downloadSelected(activity, imageIndices = selected)
                                onClose()
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text("下载所选 ${selected.size} 张") }
                    }
                }
            }
        }
    }
    focused?.let { i ->
        Dialog(onDismissRequest = { focused = null }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
            Column(Modifier.fillMaxSize().background(Color.Black).padding(12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedButton(onClick = { focused = null }) { Text("返回") }
                    Text("  ${i + 1} / $count", color = Color.White)
                    Spacer(Modifier.weight(1f))
                    OutlinedButton(onClick = {
                        selected = if (i in selected) selected - i else selected + i
                    }) { Text(if (i in selected) "取消选择" else "选择此图") }
                }
                Box(Modifier.weight(1f).fillMaxWidth()) {
                    if (jmPages != null) {
                        val page = jmPages!![i]
                        SelectableImage("jm:${page.chapterId}:${page.number}", page, info)
                    } else SelectableImage(info.images[i], null, info)
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    OutlinedButton(enabled = i > 0, onClick = { focused = i - 1 }) { Text("上一张") }
                    OutlinedButton(enabled = i < count - 1, onClick = { focused = i + 1 }) { Text("下一张") }
                }
            }
        }
    }
}

@Composable
private fun SelectableImage(url: String, page: JmPage?, info: VideoInfo) {
    var bitmap by remember(url) { mutableStateOf<androidx.compose.ui.graphics.ImageBitmap?>(null) }
    var failed by remember(url) { mutableStateOf(false) }
    LaunchedEffect(url) {
        try {
            bitmap = withContext(Dispatchers.IO) {
                val bytes = if (page == null) {
                    Http.client.newCall(Http.request(url, info.platform)).execute().use { response ->
                        if (!response.isSuccessful) error("HTTP ${response.code}")
                        response.body?.bytes() ?: error("没有图片")
                    }
                } else JmDownloader.previewPage(info, page.chapterId, page.filename, page.number)
                val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
                var sample = 1
                while (bounds.outWidth / sample > 1800 || bounds.outHeight / sample > 1800) sample *= 2
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size,
                    BitmapFactory.Options().apply { inSampleSize = sample })?.asImageBitmap()
                    ?: error("图片解码失败")
            }
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (_: Exception) { failed = true }
    }
    Box(Modifier.fillMaxSize().background(Color(0xFF25252A)), contentAlignment = Alignment.Center) {
        when {
            bitmap != null -> Image(bitmap!!, contentDescription = "图片预览",
                contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize())
            failed -> Text("预览失败", color = Color.White)
            else -> CircularProgressIndicator()
        }
    }
}

@Composable
private fun NetworkVideoPreview(url: String, platform: Platform, modifier: Modifier) {
    var error by remember(url) { mutableStateOf("") }
    val headers = remember(url) {
        buildMap {
            put("User-Agent", Http.UA_DESKTOP)
            if (platform.referer.isNotEmpty()) put("Referer", platform.referer)
            Http.cookieFor(platform).takeIf { it.isNotEmpty() }?.let { put("Cookie", it) }
        }
    }
    Box(modifier.background(Color.Black), contentAlignment = Alignment.Center) {
        AndroidView(
            factory = { ctx -> VideoView(ctx).apply {
                setMediaController(MediaController(ctx))
                setOnPreparedListener { start() }
                setOnErrorListener { _, _, _ -> error = "在线播放失败，请尝试其它清晰度"; true }
                setVideoURI(Uri.parse(url), headers)
            } },
            modifier = Modifier.fillMaxSize(),
            onRelease = { it.stopPlayback() },
        )
        if (error.isNotEmpty()) Text(error, color = Color.White)
    }
}
