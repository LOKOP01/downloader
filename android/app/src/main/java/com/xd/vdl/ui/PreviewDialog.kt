package com.xd.vdl.ui

import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.net.Uri
import android.widget.Toast
import android.widget.VideoView
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
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
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.filled.ChevronLeft
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.pointer.positionChanged
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.xd.vdl.core.MediaKindDetect
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.io.InputStream

/**
 * 应用内预览（全屏对话框）。
 *
 * 三类内容：
 *  · **视频** —— [VideoView] 播放（零依赖）
 *  · **图片** —— [HorizontalPager] 横滑翻页 + 双指缩放（图集散图）
 *  · **压缩包** —— 就地解开、按页翻看包内图片（禁漫本子就是 zip）
 *
 * ⚠️ 几个必须注意的点（都踩过）：
 *  1. **类型判定不能在主线程**：content:// 要 `contentResolver.query` 拿文件名，会卡 UI。
 *     [MediaKindDetect.detect] 已挪进 IO 协程。
 *  2. **图片不能 `BitmapFactory.decodeStream` 直接解**：整本漫画单页可能几 MB、
 *     手机相册里的原图更大，直接解容易 OOM，返回 null 就显示「打不开」。
 *     用 [decodeSampled] 先读尺寸再按目标大小采样。
 *  3. **content:// 权限**：本进程自己 insert 的 URI 天然可读；若失效要给出明确提示，
 *     而不是空白。
 */
@Composable
fun PreviewDialog(
    title: String,
    uris: List<String>,
    startIndex: Int = 0,
    onClose: () -> Unit,
) {
    val ctx = LocalContext.current
    val idx = startIndex.coerceIn(0, (uris.size - 1).coerceAtLeast(0))

    // 类型判定放 IO 线程（会查 MediaStore 拿文件名）
    var resolved by remember(uris, idx) { mutableStateOf<com.xd.vdl.core.MediaKind?>(null) }

    LaunchedEffect(uris, idx) {
        resolved = null
        resolved = if (uris.isEmpty()) null else withContext(Dispatchers.IO) {
            MediaKindDetect.detect(ctx, uris[idx])
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
            // ---- 顶栏 ----
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onClose) {
                    Icon(
                        Icons.AutoMirrored.Filled.ArrowBack,
                        contentDescription = "关闭",
                        tint = Color.White,
                    )
                }
                Text(
                    title,
                    color = Color.White,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (uris.isNotEmpty()) {
                    IconButton(onClick = { openExternally(ctx, uris[idx]) }) {
                        Icon(
                            Icons.AutoMirrored.Filled.OpenInNew,
                            contentDescription = "用其它应用打开",
                            tint = Color.White,
                        )
                    }
                }
            }
            Spacer(Modifier.height(8.dp))

            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f),
                contentAlignment = Alignment.Center,
            ) {
                when {
                    uris.isEmpty() -> Hint("没有可预览的内容")
                    resolved == null -> CircularProgressIndicator(color = Color.White)
                    resolved == com.xd.vdl.core.MediaKind.VIDEO -> VideoPreview(uris[idx])
                    resolved == com.xd.vdl.core.MediaKind.IMAGE -> ImagePager(uris, idx)
                    resolved == com.xd.vdl.core.MediaKind.ZIP -> ZipPreview(uris[idx])
                    else -> Hint("这个文件不支持应用内预览\n（可用右上角按钮交给其它应用打开）")
                }
            }
        }
    }
}

@Composable
private fun Hint(text: String) {
    Text(
        text,
        color = Color(0xFFB0B0B0),
        style = MaterialTheme.typography.bodyMedium,
        textAlign = androidx.compose.ui.text.style.TextAlign.Center,
    )
}

// ---------------------------------------------------------------------- //
// 视频
// ---------------------------------------------------------------------- //

@Composable
private fun VideoPreview(uri: String) {
    var playing by remember(uri) { mutableStateOf(true) }
    var ready by remember(uri) { mutableStateOf(false) }
    var failed by remember(uri) { mutableStateOf("") }

    Box(
        modifier = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center,
    ) {
        AndroidView(
            modifier = Modifier
                .fillMaxSize()
                .pointerInput(uri) {
                    detectTapGestures(onTap = { playing = !playing })
                },
            factory = { c ->
                VideoView(c).apply {
                    setOnPreparedListener { mp ->
                        ready = true
                        mp.isLooping = true
                        if (playing) start()
                    }
                    setOnErrorListener { _, what, extra ->
                        failed = "播放失败（$what/$extra）"
                        true
                    }
                    setVideoURI(Uri.parse(uri))
                }
            },
            update = { vv ->
                if (playing && ready && !vv.isPlaying) runCatching { vv.start() }
                if (!playing && vv.isPlaying) runCatching { vv.pause() }
            },
            onRelease = { it.stopPlayback() },
        )
        if (!ready && failed.isEmpty()) CircularProgressIndicator(color = Color.White)
        if (failed.isNotEmpty()) {
            Text(failed, color = Color(0xFFFF6B6B), style = MaterialTheme.typography.bodyMedium)
        }
    }
}

// ---------------------------------------------------------------------- //
// 图片（图集散图 + zip 内页共用）
// ---------------------------------------------------------------------- //

/** 缩放上下限；双击落在 [DOUBLE_TAP_SCALE] 上，再双击回 1 倍。
 *  这几个值与本文件里的 [ZoomState] 用 internal 暴露，只为单测能直接覆盖钳制逻辑。*/
internal const val MIN_SCALE = 1f
internal const val MAX_SCALE = 6f
internal const val DOUBLE_TAP_SCALE = 2.5f

/**
 * 缩放 / 平移状态。
 *
 * 必须用可变状态而不是普通局部变量：指针回调是在 `pointerInput` 的 lambda 里跑的，
 * 普通变量会被闭包冻结在创建那一刻，捏合时改的一直是最初那个 1f。
 */
internal class ZoomState {
    var scale by mutableFloatStateOf(1f)
    var offset by mutableStateOf(Offset.Zero)

    val zoomed: Boolean get() = scale > 1.01f

    fun reset() {
        scale = 1f
        offset = Offset.Zero
    }

    fun applyZoom(factor: Float, viewW: Float, viewH: Float) {
        scale = (scale * factor).coerceIn(MIN_SCALE, MAX_SCALE)
        clamp(viewW, viewH)
    }

    fun toggleDoubleTap() {
        if (zoomed) reset() else scale = DOUBLE_TAP_SCALE
    }

    fun panBy(dx: Float, dy: Float, viewW: Float, viewH: Float) {
        offset += Offset(dx, dy)
        clamp(viewW, viewH)
    }

    /** 放大后不许把图拖出可视区：平移量最多到「多出来那部分」的一半 */
    private fun clamp(viewW: Float, viewH: Float) {
        if (!zoomed) {
            offset = Offset.Zero
            return
        }
        val maxX = viewW * (scale - 1f) / 2f
        val maxY = viewH * (scale - 1f) / 2f
        offset = Offset(offset.x.coerceIn(-maxX, maxX), offset.y.coerceIn(-maxY, maxY))
    }
}

/**
 * 缩放 + 平移手势，**按需消费**指针事件。
 *
 * ⚠️ 这里不能用 `detectTransformGestures`：它不管当前缩放比是多少都会把单指拖动
 * 消费掉，套在 HorizontalPager 的页面上就等于把翻页手势整个封死 —— 表现出来就是
 * 「只能点按钮翻页、横滑没反应」。所以自己判条件：
 *
 *  · 单指 + 未放大 → **不消费**，事件继续传给父级 pager，横滑翻页照常
 *  · 双指（捏合）或已经放大 → 消费，做缩放 / 平移
 */
private fun Modifier.zoomAndPan(state: ZoomState): Modifier = this
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

/**
 * 可横滑翻页 + 双指缩放 + 双击缩放的图片浏览器（图集散图与压缩包内页共用）。
 *
 * 未放大时单指横滑交给 [HorizontalPager] 翻页；放大后手势由 [zoomAndPan] 接管，
 * 翻页改用底部按钮（这也是各类漫画阅读器的通行做法，免得放大后误翻页）。
 */
@Composable
private fun ZoomPager(
    pageCount: Int,
    startIndex: Int,
    prevLabel: String,
    nextLabel: String,
    uriAt: (Int) -> String,
) {
    if (pageCount <= 0) {
        Hint("没有可翻看的页")
        return
    }
    val pager = rememberPagerState(
        initialPage = startIndex.coerceIn(0, pageCount - 1),
    ) { pageCount }
    val scope = rememberCoroutineScope()
    val zoom = remember { ZoomState() }
    // 换页就复位：否则翻回来会停在一个「滑不动」的放大页上
    LaunchedEffect(pager.currentPage) { zoom.reset() }

    Column(Modifier.fillMaxSize()) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
            contentAlignment = Alignment.Center,
        ) {
            HorizontalPager(state = pager) { page ->
                val current = page == pager.currentPage
                PagedImage(
                    uri = uriAt(page),
                    modifier = Modifier
                        .fillMaxSize()
                        .graphicsLayer {
                            scaleX = if (current) zoom.scale else 1f
                            scaleY = if (current) zoom.scale else 1f
                            translationX = if (current) zoom.offset.x else 0f
                            translationY = if (current) zoom.offset.y else 0f
                        }
                        .then(if (current) Modifier.zoomAndPan(zoom) else Modifier),
                )
            }
        }
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 8.dp),
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
            ) { Icon(Icons.Filled.ChevronLeft, prevLabel, tint = Color.White) }
            Text(
                "${pager.currentPage + 1} / $pageCount",
                color = Color.White,
                fontFamily = FontFamily.Monospace,
                fontSize = 13.sp,
                modifier = Modifier.padding(horizontal = 12.dp),
            )
            IconButton(
                onClick = {
                    scope.launch {
                        pager.animateScrollToPage(
                            (pager.currentPage + 1).coerceAtMost(pageCount - 1),
                        )
                    }
                },
                enabled = pager.currentPage < pageCount - 1,
            ) { Icon(Icons.Filled.ChevronRight, nextLabel, tint = Color.White) }
            Spacer(Modifier.width(8.dp))
            OutlinedButton(onClick = { zoom.reset() }) {
                Text("复位", fontSize = 12.sp)
            }
        }
    }
}
@Composable
private fun ImagePager(uris: List<String>, startIndex: Int) {
    ZoomPager(
        pageCount = uris.size,
        startIndex = startIndex,
        prevLabel = "上一张",
        nextLabel = "下一张",
        uriAt = { uris[it] },
    )
}

/** 本地图片和解出的漫画页统一按 URI 加载 */
@Composable
private fun PagedImage(uri: String, modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    var bmp by remember(uri) { mutableStateOf<androidx.compose.ui.graphics.ImageBitmap?>(null) }
    var err by remember(uri) { mutableStateOf("") }

    LaunchedEffect(uri) {
        val r = withContext(Dispatchers.IO) {
            runCatching { decodeSampled(ctx, uri) }
        }
        bmp = r.getOrNull()
        err = r.exceptionOrNull()?.message.orEmpty()
    }

    Box(modifier, contentAlignment = Alignment.Center) {
        when {
            bmp != null -> Image(
                bitmap = bmp!!,
                contentDescription = null,
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Fit,
            )
            err.isNotEmpty() -> Text(
                "图片打不开\n$err",
                color = Color(0xFFFF6B6B),
                style = MaterialTheme.typography.bodySmall,
                textAlign = androidx.compose.ui.text.style.TextAlign.Center,
            )
            else -> CircularProgressIndicator(color = Color.White)
        }
    }
}

// ---------------------------------------------------------------------- //
// 压缩包（禁漫本子）：就地解出条目，按页翻看
// ---------------------------------------------------------------------- //

@Composable
private fun ZipPreview(uri: String) {
    val ctx = LocalContext.current
    val cacheDir = remember(uri) { File(ctx.cacheDir, "preview-${java.util.UUID.randomUUID()}") }
    DisposableEffect(cacheDir) {
        onDispose {
            kotlinx.coroutines.CoroutineScope(Dispatchers.IO).launch { cacheDir.deleteRecursively() }
        }
    }
    var pages by remember(uri) { mutableStateOf<List<File>?>(null) }
    var err by remember(uri) { mutableStateOf("") }
    var note by remember(uri) { mutableStateOf("") }

    LaunchedEffect(uri) {
        try {
            val (imgs, n) = withContext(Dispatchers.IO) {
                val ins: InputStream = ctx.contentResolver.openInputStream(Uri.parse(uri))
                    ?: throw IllegalStateException("打不开这个压缩包")
                ins.use { readZipImages(it, cacheDir) }
            }
            if (imgs.isEmpty()) err = "压缩包里没有可显示的图片" else {
                pages = imgs; note = n
            }
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            err = e.message ?: e.javaClass.simpleName
        }
    }

    when {
        err.isNotEmpty() -> Hint("压缩包打不开\n$err")
        pages == null -> Column(horizontalAlignment = Alignment.CenterHorizontally) {
            CircularProgressIndicator(color = Color.White)
            Spacer(Modifier.height(8.dp))
            Text("正在解压…", color = Color.White, style = MaterialTheme.typography.bodySmall)
        }
        else -> {
            val imgs = pages!!
            Column(Modifier.fillMaxSize()) {
                if (note.isNotEmpty()) {
                    Text(
                        note,
                        color = Color(0xFFB0B0B0),
                        style = MaterialTheme.typography.labelSmall,
                        modifier = Modifier.padding(bottom = 6.dp),
                    )
                }
                Box(Modifier.fillMaxWidth().weight(1f)) {
                    ZipPager(imgs)
                }
            }
        }
    }
}

@Composable
private fun ZipPager(imgs: List<File>) {
    ZoomPager(
        pageCount = imgs.size,
        startIndex = 0,
        prevLabel = "上一页",
        nextLabel = "下一页",
        uriAt = { Uri.fromFile(imgs[it]).toString() },
    )
}

/**
 * 读 zip 里的所有图片条目（禁漫的包是 STORED，直接读）。
 *
 * 上限 [MAX_PAGES] 页，逐页写缓存，避免把整本图片同时放进内存。
 */
private const val MAX_PAGES = 500
private const val MAX_TOTAL_BYTES = 240L * 1024 * 1024  // 240MB 缓冲区上限

private suspend fun readZipImages(ins: InputStream, dir: File): Pair<List<File>, String> {
    val out = mutableListOf<File>()
    var total = 0L
    var truncated = false
    if (!dir.mkdirs()) throw IllegalStateException("无法创建预览缓存")
    try {
        java.util.zip.ZipInputStream(ins).use { zis ->
            while (true) {
                currentCoroutineContext().ensureActive()
                val e = zis.nextEntry ?: break
                if (e.isDirectory) { zis.closeEntry(); continue }
                val name = e.name.lowercase()
                val isImg = name.endsWith(".jpg") || name.endsWith(".jpeg") ||
                    name.endsWith(".png") || name.endsWith(".webp")
                if (!isImg) { zis.closeEntry(); continue }
                if (out.size >= MAX_PAGES || total + e.size.coerceAtLeast(0) > MAX_TOTAL_BYTES) {
                    truncated = true; break
                }
                // Never use the archive entry name as a filesystem path.
                val page = File(dir, "${out.size}.img")
                val buf = ByteArray(64 * 1024)
                var withinLimit = true
                page.outputStream().buffered().use { dest ->
                    while (true) {
                        currentCoroutineContext().ensureActive()
                        val n = zis.read(buf)
                        if (n < 0) break
                        total += n
                        if (total > MAX_TOTAL_BYTES) { withinLimit = false; truncated = true; break }
                        dest.write(buf, 0, n)
                    }
                }
                if (!withinLimit) { page.delete(); break }
                zis.closeEntry()
                if (page.length() > 0) out.add(page) else page.delete()
            }
        }
    } catch (e: Exception) {
        dir.deleteRecursively()
        throw e
    }
    val note = buildString {
        append("压缩包内 ${out.size} 页")
        if (truncated) append("（过大，仅显示前 ${out.size} 页）")
    }
    return out to note
}

// ---------------------------------------------------------------------- //
// 解码：先读尺寸再采样，避免大图 OOM
// ---------------------------------------------------------------------- //

/** 单张图最长边解码上限（手机屏幕够用，内存友好） */
private const val MAX_DECODE_PX = 2560

private fun decodeSampled(ctx: Context, uri: String): androidx.compose.ui.graphics.ImageBitmap {
    val p = Uri.parse(uri)
    fun open(): InputStream = ctx.contentResolver.openInputStream(p)
        ?: throw IllegalStateException("读不到这个文件（权限或已被删除）")
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

// ---------------------------------------------------------------------- //

private fun openExternally(ctx: Context, uri: String) {
    // 失败要说出来：以前这里是个空的 runCatching，没有能打开该类型的应用、
    // 或 URI 不允许跨应用传递（低版本 file:// 会抛 FileUriExposedException）时，
    // 按钮点下去毫无反应，用户只能干瞪眼。
    val ok = runCatching {
        val p = Uri.parse(uri)
        ctx.startActivity(
            Intent.createChooser(
                Intent(Intent.ACTION_VIEW).apply {
                    setDataAndType(
                        p,
                        if (p.scheme == "file") "*/*"
                        else ctx.contentResolver.getType(p) ?: "*/*",
                    )
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
                },
                "选择应用",
            ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }.isSuccess
    if (!ok) {
        Toast.makeText(ctx, "没有能打开该文件的应用", Toast.LENGTH_SHORT).show()
    }
}
