package com.xd.vdl.ui

import android.content.Context
import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.ChevronLeft
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChanged
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.xd.vdl.core.AppLog
import com.xd.vdl.core.manga.MangaLibrary
import com.xd.vdl.core.manga.MangaPageSource
import com.xd.vdl.core.manga.OpenedPages
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.InputStream

/**
 * 本地漫画阅读器（全屏对话框）。
 *
 * 与「下载前预览」[PreviewDialog] 的分工：
 *  · [PreviewDialog] 是**任务结果**预览，素材来自任务的 URI 列表，任务一清就没了；
 *  · 这里是**书架**阅读器，素材来自 [MangaPageSource]，带阅读进度、断点续读、
 *    翻页/缩放/章节切换，全程不碰网络。
 *
 * 翻页与缩放的手势实现与 [PreviewDialog] 同一套（单指未放大 → 交给 pager 翻页；
 * 已放大或双指 → 缩放平移），保持两处手感一致。
 *
 * ⚠️ 三个必须小心的地方：
 *  1. **打开可能很慢**（首次要解压整包），必须在 IO 协程里做并显示进度；
 *  2. **解码不能直接 decodeStream 全尺寸**：单页可能几 MB，整本翻几页就 OOM，
 *     必须先读尺寸再按目标采样；
 *  3. **进度只在页稳定后写**（[PROGRESS_DEBOUNCE_MS]），否则快速划过 100 页会写 100 次盘。
 */
@Composable
fun LocalMangaReader(
    bookKey: String,
    title: String,
    source: MangaPageSource,
    /**
     * 首屏等待期间的进度（已写字节, 总字节）。由调用方从页源的回调里接出来 ——
     * 解压在 IO 线程跑，阅读器自己不产生这个数据。null 表示进度未知，走不确定态。
     */
    progress: Pair<Long, Long>? = null,
    /** 用户点「重试」时回调：调用方据此清掉上一轮的进度显示 */
    onReload: () -> Unit = {},
    onClose: () -> Unit,
) {
    var opened by remember(bookKey) { mutableStateOf<OpenedPages?>(null) }
    var error by remember(bookKey) { mutableStateOf("") }
    /** 打开时的起始页；null 表示还没算出来 */
    var startIndex by remember(bookKey) { mutableStateOf<Int?>(null) }
    var reloadTick by remember(bookKey) { mutableStateOf(0) }

    LaunchedEffect(bookKey, reloadTick) {
        opened = null
        error = ""
        startIndex = null
        try {
            val pages = withContext(Dispatchers.IO) {
                source.open().also { opened = it }
            }
            val book = MangaLibrary.find(bookKey)
            val start = book?.restoreIndex(pages.ids) ?: 0
            // 页数变了（文件被替换/换了一版）时把记录刷新成实际值，
            // 但**不动阅读位置** —— 位置已经在 restoreIndex 里兜过底了
            if (book != null && book.pageCount != pages.ids.size) {
                MangaLibrary.setPageCount(bookKey, pages.ids.size)
            }
            startIndex = start
            AppLog.i(
                "打开本地漫画「" + title + "」" + pages.ids.size + " 页，从第 " + (start + 1) +
                    " 页继续（" + source.describe() + "）" +
                    if (pages.note.isEmpty()) "" else "｜" + pages.note,
            )
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            error = e.message ?: e.javaClass.simpleName
            AppLog.e("打开本地漫画失败：" + error)
        }
    }

    Dialog(
        onDismissRequest = onClose,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(Color(0xFF10131B))
                .padding(12.dp),
        ) {
            ReaderBar(
                title = title,
                pages = opened,
                onClose = onClose,
                onReload = {
                    onReload()
                    reloadTick += 1
                },
            )
            Spacer(Modifier.height(8.dp))
            Box(Modifier.fillMaxWidth().weight(1f), contentAlignment = Alignment.Center) {
                val pages = opened
                val start = startIndex
                when {
                    error.isNotEmpty() -> ReaderError(error) {
                        onReload()
                        error = ""
                        reloadTick += 1
                    }
                    pages == null -> ReaderLoading(progress)
                    start == null -> CircularProgressIndicator(color = Color.White)
                    else -> PagedReader(
                        bookKey = bookKey,
                        ids = pages.ids,
                        startIndex = start,
                        openPage = { source.openPage(it) },
                    )
                }
            }
        }
    }
}

@Composable
private fun ReaderBar(
    title: String,
    pages: OpenedPages?,
    onClose: () -> Unit,
    onReload: () -> Unit,
) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onClose) {
            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "关闭", tint = Color.White)
        }
        Column(Modifier.weight(1f)) {
            Text(
                title,
                color = Color.White,
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            if (pages != null && pages.chapters.size > 1) {
                Text(
                    pages.chapters.joinToString(" · ") { it.name },
                    color = Color(0xFFB0B0B0),
                    style = MaterialTheme.typography.labelSmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
        IconButton(onClick = onReload) {
            Icon(Icons.Filled.Refresh, contentDescription = "重新加载", tint = Color.White)
        }
    }
}

@Composable
private fun ReaderLoading(progress: Pair<Long, Long>?) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        val p = progress
        if (p != null && p.second > 0) {
            LinearProgressIndicator(
                progress = { (p.first.toFloat() / p.second.toFloat()).coerceIn(0f, 1f) },
                modifier = Modifier.width(220.dp),
            )
            Spacer(Modifier.height(10.dp))
            Text(
                "正在解压：" + (p.first / 1024 / 1024) + " / " + (p.second / 1024 / 1024) + " MB",
                color = Color(0xFFB0B0B0),
                style = MaterialTheme.typography.bodySmall,
            )
        } else {
            CircularProgressIndicator(color = Color.White)
            Spacer(Modifier.height(10.dp))
            Text(
                "正在准备本地页面…",
                color = Color(0xFFB0B0B0),
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

/** 明确的错误提示 + 重试入口；绝不无限转圈 */
@Composable
private fun ReaderError(message: String, onRetry: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            "打不开这本漫画",
            color = Color(0xFFFF6B6B),
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.height(6.dp))
        Text(
            message,
            color = Color(0xFFB0B0B0),
            style = MaterialTheme.typography.bodySmall,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(horizontal = 24.dp),
        )
        Spacer(Modifier.height(12.dp))
        OutlinedButton(onClick = onRetry) { Text("重试") }
    }
}

// ---------------------------------------------------------------------- //
// 翻页
// ---------------------------------------------------------------------- //

/** 写入阅读进度的防抖间隔：翻页停稳这么久才落盘 */
private const val PROGRESS_DEBOUNCE_MS = 700L

@Composable
private fun PagedReader(
    bookKey: String,
    ids: List<String>,
    startIndex: Int,
    openPage: (Int) -> InputStream,
) {
    if (ids.isEmpty()) {
        Text("没有可阅读的页", color = Color(0xFFB0B0B0))
        return
    }
    val pager = rememberPagerState(initialPage = startIndex.coerceIn(0, ids.size - 1)) { ids.size }
    val scope = rememberCoroutineScope()
    val zoom = remember { ZoomState() }

    LaunchedEffect(pager.currentPage) {
        zoom.reset()
    }
    // 防抖落盘：快速连翻时只在停下来后写一次
    LaunchedEffect(pager.currentPage, pager.isScrollInProgress) {
        if (pager.isScrollInProgress) return@LaunchedEffect
        delay(PROGRESS_DEBOUNCE_MS)
        MangaLibrary.markRead(bookKey, pager.currentPage, ids)
    }

    Column(Modifier.fillMaxSize()) {
        Box(Modifier.fillMaxWidth().weight(1f), contentAlignment = Alignment.Center) {
            HorizontalPager(state = pager) { page ->
                val current = page == pager.currentPage
                LocalPageImage(
                    openPage = openPage,
                    index = page,
                    modifier = Modifier
                        .fillMaxSize()
                        .graphicsLayer {
                            scaleX = if (current) zoom.scale else 1f
                            scaleY = if (current) zoom.scale else 1f
                            translationX = if (current) zoom.offset.x else 0f
                            translationY = if (current) zoom.offset.y else 0f
                        }
                    .then(if (current) Modifier.readerZoomAndPan(zoom) else Modifier),
                )
            }
        }
        Row(
            modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(
                onClick = {
                    scope.launch {
                        pager.animateScrollToPage((pager.currentPage - 1).coerceAtLeast(0))
                    }
                },
                enabled = pager.currentPage > 0,
            ) { Icon(Icons.Filled.ChevronLeft, "上一页", tint = Color.White) }
            Text(
                (pager.currentPage + 1).toString() + " / " + ids.size,
                color = Color.White,
                fontFamily = FontFamily.Monospace,
                fontSize = 13.sp,
                modifier = Modifier.padding(horizontal = 12.dp),
            )
            IconButton(
                onClick = {
                    scope.launch {
                        pager.animateScrollToPage(
                            (pager.currentPage + 1).coerceAtMost(ids.size - 1),
                        )
                    }
                },
                enabled = pager.currentPage < ids.size - 1,
            ) { Icon(Icons.Filled.ChevronRight, "下一页", tint = Color.White) }
            Spacer(Modifier.width(8.dp))
            OutlinedButton(onClick = { zoom.reset() }) { Text("复位", fontSize = 12.sp) }
        }
        // 读完之后给一句明确的收尾提示，而不是让用户停在最后一页反复滑
        if (pager.currentPage == ids.size - 1 && ids.isNotEmpty()) {
            Text(
                "已是最后一页",
                color = Color(0xFFB0B0B0),
                style = MaterialTheme.typography.labelSmall,
                modifier = Modifier.padding(bottom = 6.dp),
            )
        }
    }
}

/**
 * 单页图片。
 *
 * 解码走 [decodeSampledFrom]：先读尺寸再采样，避免整本漫画把内存打满。
 * 解码失败**只影响这一页**，给出「这一页打不开」而不是让整个阅读器退出。
 */
@Composable
private fun LocalPageImage(
    openPage: (Int) -> InputStream,
    index: Int,
    modifier: Modifier = Modifier,
) {
    var bmp by remember(index) { mutableStateOf<androidx.compose.ui.graphics.ImageBitmap?>(null) }
    var err by remember(index) { mutableStateOf("") }

    LaunchedEffect(index) {
        val r = withContext(Dispatchers.IO) {
            runCatching { decodeSampledFrom { openPage(index) } }
        }
        bmp = r.getOrNull()
        err = r.exceptionOrNull()?.message.orEmpty()
    }

    Box(modifier, contentAlignment = Alignment.Center) {
        val b = bmp
        when {
            b != null -> Image(
                bitmap = b,
                contentDescription = null,
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Fit,
            )
            err.isNotEmpty() -> Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(
                    "这一页打不开（第 " + (index + 1) + " 页）",
                    color = Color(0xFFFF6B6B),
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    err,
                    color = Color(0xFFB0B0B0),
                    style = MaterialTheme.typography.labelSmall,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(horizontal = 24.dp),
                )
            }
            else -> CircularProgressIndicator(color = Color.White)
        }
    }
}

// ---------------------------------------------------------------------- //
// 缩放 / 平移
// ---------------------------------------------------------------------- //

// 缩放状态直接复用 PreviewDialog 里的 internal ZoomState —— 两处必须是同一套
// 手感（缩放上限、双击倍率、边界钳制），各写一份迟早会漂移。

/**
 * 缩放 + 平移手势，**按需消费**指针事件。
 *
 * 不能用 detectTransformGestures：它不管当前缩放比都会吃掉单指拖动，
 * 套在 pager 页面上等于把横滑翻页整个封死（表现成「只能点按钮翻页」）。
 */
private fun Modifier.readerZoomAndPan(state: ZoomState): Modifier = this
    .pointerInput(Unit) {
        awaitEachGesture {
            awaitFirstDown(requireUnconsumed = false)
            while (true) {
                val event = awaitPointerEvent()
                val pressed = event.changes.count { it.pressed }
                if (pressed == 0) break
                if (pressed < 2 && !state.zoomed) continue   // 交给 pager 翻页
                val w = size.width.toFloat()
                val h = size.height.toFloat()
                val zoomChange = event.calculateZoom()
                val panChange = event.calculatePan()
                if (zoomChange != 1f) state.applyZoom(zoomChange, w, h)
                if (panChange != Offset.Zero) state.panBy(panChange.x, panChange.y, w, h)
                event.changes.forEach { if (it.positionChanged()) it.consume() }
            }
        }
    }
    .pointerInput(Unit) {
        detectTapGestures(onDoubleTap = { state.toggleDoubleTap() })
    }

// ---------------------------------------------------------------------- //
// 解码
// ---------------------------------------------------------------------- //

/** 单张图最长边解码上限（手机屏幕够用，内存友好） */
private const val MAX_DECODE_PX = 2560

internal fun decodeSampledFrom(open: () -> InputStream): androidx.compose.ui.graphics.ImageBitmap {
    val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    open().use { BitmapFactory.decodeStream(it, null, opts) }
    val w = opts.outWidth
    val h = opts.outHeight
    if (w <= 0 || h <= 0) throw IllegalStateException("不是有效的图片")
    var sample = 1
    while (w / sample > MAX_DECODE_PX || h / sample > MAX_DECODE_PX) sample *= 2
    val opts2 = BitmapFactory.Options().apply { inSampleSize = sample }
    val bmp = open().use { BitmapFactory.decodeStream(it, null, opts2) }
        ?: throw IllegalStateException("图片解码失败")
    return bmp.asImageBitmap()
}
