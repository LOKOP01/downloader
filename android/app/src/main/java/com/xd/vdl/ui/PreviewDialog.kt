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
import androidx.compose.foundation.gestures.detectTransformGestures
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

@Composable
private fun ImagePager(uris: List<String>, startIndex: Int) {
    val pager = rememberPagerState(initialPage = startIndex) { uris.size }
    val scope = rememberCoroutineScope()
    var scale by remember(pager.currentPage) { mutableFloatStateOf(1f) }
    var offsetX by remember(pager.currentPage) { mutableFloatStateOf(0f) }
    var offsetY by remember(pager.currentPage) { mutableFloatStateOf(0f) }

    Column(Modifier.fillMaxSize()) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
            contentAlignment = Alignment.Center,
        ) {
            HorizontalPager(state = pager) { page ->
                PagedImage(
                    uri = uris[page],
                    modifier = Modifier
                        .fillMaxSize()
                        .graphicsLayer(
                            scaleX = if (page == pager.currentPage) scale else 1f,
                            scaleY = if (page == pager.currentPage) scale else 1f,
                            translationX = if (page == pager.currentPage) offsetX else 0f,
                            translationY = if (page == pager.currentPage) offsetY else 0f,
                        )
                        .pointerInput(page) {
                            detectTransformGestures { _, pan, zoom, _ ->
                                scale = (scale * zoom).coerceIn(1f, 6f)
                                if (scale <= 1.02f) {
                                    offsetX = 0f; offsetY = 0f
                                } else {
                                    offsetX += pan.x; offsetY += pan.y
                                }
                            }
                        },
                )
            }
        }
        if (uris.size > 1) {
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
                ) { Icon(Icons.Filled.ChevronLeft, "上一张", tint = Color.White) }
                Text(
                    "${pager.currentPage + 1} / ${uris.size}",
                    color = Color.White,
                    fontFamily = FontFamily.Monospace,
                    fontSize = 13.sp,
                    modifier = Modifier.padding(horizontal = 12.dp),
                )
                IconButton(
                    onClick = {
                        scope.launch {
                            pager.animateScrollToPage(
                                (pager.currentPage + 1).coerceAtMost(uris.size - 1),
                            )
                        }
                    },
                    enabled = pager.currentPage < uris.size - 1,
                ) { Icon(Icons.Filled.ChevronRight, "下一张", tint = Color.White) }
                Spacer(Modifier.width(8.dp))
                OutlinedButton(onClick = { scale = 1f; offsetX = 0f; offsetY = 0f }) {
                    Text("复位", fontSize = 12.sp)
                }
            }
        }
    }
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
    val pager = rememberPagerState(initialPage = 0) { imgs.size }
    val scope = rememberCoroutineScope()
    var scale by remember(pager.currentPage) { mutableFloatStateOf(1f) }
    var offsetX by remember(pager.currentPage) { mutableFloatStateOf(0f) }
    var offsetY by remember(pager.currentPage) { mutableFloatStateOf(0f) }

    Column(Modifier.fillMaxSize()) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
            contentAlignment = Alignment.Center,
        ) {
            HorizontalPager(state = pager) { page ->
                PagedImage(
                    uri = Uri.fromFile(imgs[page]).toString(),
                    modifier = Modifier
                        .fillMaxSize()
                        .graphicsLayer(
                            scaleX = if (page == pager.currentPage) scale else 1f,
                            scaleY = if (page == pager.currentPage) scale else 1f,
                            translationX = if (page == pager.currentPage) offsetX else 0f,
                            translationY = if (page == pager.currentPage) offsetY else 0f,
                        )
                        .pointerInput(page) {
                            detectTransformGestures { _, pan, zoom, _ ->
                                scale = (scale * zoom).coerceIn(1f, 6f)
                                if (scale <= 1.02f) {
                                    offsetX = 0f; offsetY = 0f
                                } else {
                                    offsetX += pan.x; offsetY += pan.y
                                }
                            }
                        },
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
            ) { Icon(Icons.Filled.ChevronLeft, "上一页", tint = Color.White) }
            Text(
                "${pager.currentPage + 1} / ${imgs.size}",
                color = Color.White,
                fontFamily = FontFamily.Monospace,
                fontSize = 13.sp,
                modifier = Modifier.padding(horizontal = 12.dp),
            )
            IconButton(
                onClick = {
                    scope.launch {
                        pager.animateScrollToPage(
                            (pager.currentPage + 1).coerceAtMost(imgs.size - 1),
                        )
                    }
                },
                enabled = pager.currentPage < imgs.size - 1,
            ) { Icon(Icons.Filled.ChevronRight, "下一页", tint = Color.White) }
            Spacer(Modifier.width(8.dp))
            OutlinedButton(onClick = { scale = 1f; offsetX = 0f; offsetY = 0f }) {
                Text("复位", fontSize = 12.sp)
            }
        }
    }
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
