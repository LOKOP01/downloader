package com.xd.vdl.ui

import android.content.Context
import android.graphics.BitmapFactory
import android.widget.Toast
import androidx.compose.foundation.Image
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.StarBorder
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.xd.vdl.core.manga.MangaBook
import com.xd.vdl.core.manga.MangaFiles
import com.xd.vdl.core.manga.MangaLibrary
import com.xd.vdl.core.manga.MangaSort
import com.xd.vdl.core.manga.MangaSorter
import com.xd.vdl.core.manga.MangaSources
import com.xd.vdl.core.manga.MangaTime
import com.xd.vdl.core.manga.ZipPageCache
import com.xd.vdl.ui.component.GkCard
import com.xd.vdl.ui.component.GkDimens
import com.xd.vdl.ui.component.GkPageTitle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 本地漫画书架。
 *
 * 这是「下载完之后怎么找到这本漫画」的答案：下载完成的禁漫本子 / 图集会自动入库，
 * 这里列出全部本地漫画，断网也能打开阅读。
 *
 * 与桌面端「漫画阅读器」的差异（都是有意为之）：
 *  · **不提供「选择文件夹添加」**：Android 10+ 分区存储下要读任意目录必须走 SAF，
 *    那会引入一套新的持久化授权机制；而本应用下载的东西都在自己写的目录里，
 *    用 [MangaLibrary.scanDownloadDir] 扫描即可覆盖真实场景。见交付报告「遗留问题」。
 *  · **移除只删索引**：绝不动用户的漫画文件。
 */
@Composable
fun LibraryScreen(vm: AppViewModel) {
    val ctx = LocalContext.current
    val books by MangaLibrary.books.collectAsState()
    val notice by MangaLibrary.notice.collectAsState()
    val scanning by MangaLibrary.scanning.collectAsState()

    var sort by remember { mutableStateOf(MangaSort.RECENT) }
    var query by remember { mutableStateOf("") }
    var open by remember { mutableStateOf<MangaBook?>(null) }
    var pendingRemove by remember { mutableStateOf<MangaBook?>(null) }
    var sortMenu by remember { mutableStateOf(false) }
    var cacheBytes by remember { mutableStateOf(-1L) }

    // 首次进入等一次加载完成：MangaLibrary 的初始化是异步的，直接读可能拿到空列表
    LaunchedEffect(Unit) { MangaLibrary.awaitLoaded() }
    LaunchedEffect(books.size) {
        cacheBytes = withContext(Dispatchers.IO) { ZipPageCache.sizeOnDisk(ctx) }
    }
    LaunchedEffect(notice) {
        if (notice.isNotEmpty()) {
            vm.notify(notice)
            MangaLibrary.consumeNotice()
        }
    }

    val shown = remember(books, sort, query) {
        MangaSorter.sort(MangaSorter.filter(books, query), sort)
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(horizontal = GkDimens.screenPadding),
    ) {
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            GkPageTitle("本地漫画")
            Spacer(Modifier.weight(1f))
            TextButton(onClick = { sortMenu = true }) { Text(sort.label) }
            DropdownMenu(expanded = sortMenu, onDismissRequest = { sortMenu = false }) {
                MangaSort.entries.forEach { s ->
                    DropdownMenuItem(
                        text = { Text(s.label) },
                        onClick = { sort = s; sortMenu = false },
                    )
                }
            }
            IconButton(onClick = { MangaLibrary.scanDownloadDir(ctx) }, enabled = !scanning) {
                if (scanning) {
                    CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                } else {
                    Icon(
                        Icons.Filled.Refresh,
                        contentDescription = "扫描下载目录",
                        modifier = Modifier.size(20.dp),
                    )
                }
            }
        }
        Spacer(Modifier.height(6.dp))
        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            shape = RoundedCornerShape(GkDimens.rowCorner),
            leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
            placeholder = { Text("搜索书名") },
        )
        Spacer(Modifier.height(8.dp))

        LazyColumn(
            modifier = Modifier.fillMaxWidth().weight(1f),
            verticalArrangement = Arrangement.spacedBy(GkDimens.cardGap),
        ) {
            if (shown.isEmpty()) {
                item { EmptyLibrary(hasAny = books.isNotEmpty(), scanned = query.isNotEmpty()) }
            } else {
                items(shown, key = { it.key }) { b ->
                    BookRow(
                        book = b,
                        onOpen = { open = it },
                        onFavorite = { MangaLibrary.setFavorite(b.key, !b.favorite) },
                        onRemove = { pendingRemove = b },
                    )
                }
            }
            item {
                Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                    Text(
                        "共 " + books.size + " 本" +
                            if (cacheBytes > 0) {
                                " · 阅读缓存 " + (cacheBytes / 1024 / 1024) + " MB"
                            } else {
                                ""
                            },
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (cacheBytes > 0) {
                        TextButton(onClick = {
                            val freed = ZipPageCache.clearAll(ctx)
                            cacheBytes = 0
                            vm.notify("已清理阅读缓存 " + (freed / 1024 / 1024) + " MB（漫画本身未删除）")
                        }) { Text("清理阅读缓存") }
                    }
                }
            }
        }
    }

    open?.let { b -> BookReaderHost(b, onClose = { open = null }) }

    pendingRemove?.let { b ->
        RemoveBookDialog(
            book = b,
            onDismiss = { pendingRemove = null },
            onConfirm = { deleteFiles ->
                pendingRemove = null
                MangaLibrary.remove(ctx, b.key, deleteFiles = deleteFiles) { result ->
                    vm.notify(
                        when {
                            result == null -> "已从书架移除《" + b.title + "》，文件仍在"
                            result.ok -> "已从书架移除《" + b.title + "》，" + result.summary
                            else -> "已从书架移除《" + b.title + "》，但" + result.summary +
                                "。剩余文件可在文件管理里手动清理"
                        },
                    )
                }
            },
        )
    }
}


/**
 * 打开一本书的宿主：先在 IO 线程构造页源，再交给阅读器。
 *
 * 拆成独立 Composable 是**必须**的：如果把这些 `remember`/`LaunchedEffect` 直接写在
 * `open?.let { ... }` 里，它们就落在 Dialog 的内容 lambda 中，每次重组都会重新执行，
 * 页源会被反复重建（大包会被反复解压）。
 */
@Composable
private fun BookReaderHost(
    book: MangaBook,
    onClose: () -> Unit,
) {
    val ctx = LocalContext.current
    var source by remember(book.key, book.uri) {
        mutableStateOf<com.xd.vdl.core.manga.MangaPageSource?>(null)
    }
    // 首次打开压缩包要整包解压，几百 MB 的本子会明显等待。进度从解压线程回调，
    // 因此用 StateFlow 跨线程传递（Compose 的 mutableStateOf 从非主线程写需要快照，
    // 容易漏掉一次重组；StateFlow 由 collectAsState 保证可见）。
    val progressFlow = remember(book.key) { MutableStateFlow<Pair<Long, Long>?>(null) }
    LaunchedEffect(book.key, book.uri) {
        source = withContext(Dispatchers.IO) {
            MangaSources.of(ctx, book) { done, total -> progressFlow.value = done to total }
        }
    }
    val progress by progressFlow.collectAsState()
    val s = source
    if (s == null) {
        FullScreenLoading()
    } else {
        LocalMangaReader(
            bookKey = book.key,
            title = book.title,
            source = s,
            progress = progress,
            onReload = { progressFlow.value = null },
            onClose = onClose,
        )
    }
}

/**
 * 移除一本书时的选择框。
 *
 * 三个出口，措辞必须让人一眼分清「可逆」和「不可逆」：
 *
 *  · 仅移除书架  —— 只删记录与阅读进度，**文件原样保留**（默认、可逆）
 *  · 移除并删文件 —— 不可逆，所以把「要删什么、删几个」写清楚，并要求二次确认
 *  · 取消
 *
 * 「删文件」不做成默认项，也不与「移除」并排放在同一位置 —— 误触代价太高。
 */
@Composable
private fun RemoveBookDialog(
    book: MangaBook,
    onDismiss: () -> Unit,
    onConfirm: (deleteFiles: Boolean) -> Unit,
) {
    // 选「删文件」后先进入一次确认态，避免连点两下就删掉几百张图
    var confirmingDelete by remember(book.key) { mutableStateOf(false) }
    val targets = remember(book.key) { com.xd.vdl.core.manga.MangaDeleter.describeTargets(book) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (confirmingDelete) "确认删除文件？" else "移除《" + book.title + "》") },
        text = {
            if (confirmingDelete) {
                Text(
                    "将永久删除这本书的 " + targets + "，无法恢复。" +
                        "\n\n其它漫画的文件、以及下载目录里的其它内容都不会被删除。" +
                        "\n\n阅读记录会一并移除。",
                )
            } else {
                Text(
                    "移除后这本漫画不会再出现在书架里。" +
                        "\n\n你可以选择是否同时删除已下载的 " + targets + "：" +
                        "\n· 仅移除书架 —— 文件保留，以后还能从下载目录找回" +
                        "\n· 移除并删文件 —— 永久删除，无法恢复",
                )
            }
        },
        confirmButton = {
            if (confirmingDelete) {
                TextButton(onClick = { onConfirm(true) }) {
                    Text("永久删除", color = MaterialTheme.colorScheme.error)
                }
            } else {
                TextButton(onClick = { onConfirm(false) }) { Text("仅移除书架") }
            }
        },
        dismissButton = {
            if (confirmingDelete) {
                TextButton(onClick = { confirmingDelete = false }) { Text("返回") }
            } else {
                Row {
                    TextButton(onClick = { confirmingDelete = true }) {
                        Text("移除并删文件", color = MaterialTheme.colorScheme.error)
                    }
                    TextButton(onClick = onDismiss) { Text("取消") }
                }
            }
        },
    )
}
@Composable
private fun FullScreenLoading() {
    Box(
        modifier = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center,
    ) { CircularProgressIndicator() }
}

@Composable
private fun EmptyLibrary(hasAny: Boolean, scanned: Boolean) {
    GkCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                Icons.Filled.FolderOpen,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    when {
                        scanned -> "没有匹配的漫画"
                        hasAny -> "书架是空的"
                        else -> "还没有本地漫画"
                    },
                    style = MaterialTheme.typography.bodyLarge,
                    fontWeight = FontWeight.Medium,
                )
                Text(
                    if (hasAny || scanned) {
                        "换个关键词试试"
                    } else {
                        "下载禁漫本子或图集后会自动出现在这里，断网也能看；" +
                            "也可以点右上角从下载目录找回已有文件"
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun BookRow(
    book: MangaBook,
    onOpen: (MangaBook) -> Unit,
    onFavorite: () -> Unit,
    onRemove: () -> Unit,
) {
    GkCard(onClick = { onOpen(book) }, contentPadding = 12.dp) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            CoverThumb(book)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    book.title,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    book.displaySource + " · " + book.progressText +
                        if (book.chapters.size > 1) " · " + book.chapters.size + " 章" else "",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                val read = MangaTime.display(book.lastRead)
                if (read.isNotEmpty()) {
                    Text(
                        "上次阅读 " + read,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            IconButton(onClick = onFavorite) {
                Icon(
                    if (book.favorite) Icons.Filled.Star else Icons.Filled.StarBorder,
                    contentDescription = if (book.favorite) "取消收藏" else "收藏",
                    modifier = Modifier.size(20.dp),
                    tint = if (book.favorite) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            IconButton(onClick = onRemove) {
                Icon(
                    Icons.Filled.Delete,
                    contentDescription = "从书架移除",
                    modifier = Modifier.size(18.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

/**
 * 封面缩略图。
 *
 * 为了不引入图片加载库（Glide/Coil 都会让包体和启动成本上一个台阶），
 * 这里自己解一张小图：zip → 解出第 1 页缓存；散图 → 直接读首图。
 * 解不出来就退回一个通用图标，**不能让封面失败影响整行**。
 */
@Composable
private fun CoverThumb(book: MangaBook) {
    val ctx = LocalContext.current
    var bmp by remember(book.key) { mutableStateOf<androidx.compose.ui.graphics.ImageBitmap?>(null) }
    var failed by remember(book.key) { mutableStateOf(false) }

    LaunchedEffect(book.key, book.uri) {
        if (!MangaFiles.exists(ctx, book.uri)) {
            failed = true
            return@LaunchedEffect
        }
        bmp = withContext(Dispatchers.IO) { runCatching { decodeCover(ctx, book) }.getOrNull() }
        failed = bmp == null
    }

    Box(
        modifier = Modifier
            .size(width = 64.dp, height = 86.dp)
            .clip(RoundedCornerShape(8.dp)),
        contentAlignment = Alignment.Center,
    ) {
        val b = bmp
        when {
            b != null -> Image(
                bitmap = b,
                contentDescription = null,
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Crop,
            )
            failed -> Icon(
                Icons.Filled.Warning,
                contentDescription = "封面不可用",
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(22.dp),
            )
            else -> CircularProgressIndicator(
                modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
        }
    }
}

private const val COVER_MAX_PX = 320

private fun decodeCover(ctx: Context, book: MangaBook): androidx.compose.ui.graphics.ImageBitmap {
    val bytes = if (MangaFiles.looksLikeZip(ctx, book.uri)) {
        // 复用页缓存：首次会解压整包（打开阅读器时本来也要做，这里只是做了它）
        val key = ZipPageCache.keyOf(ctx, book.uri)
        val cached = ZipPageCache.lookup(ctx, key) ?: ZipPageCache.extract(
            ctx = ctx,
            key = key,
            totalHint = MangaFiles.meta(ctx, book.uri).size,
            onProgress = { _, _ -> },
            openZip = {
                MangaFiles.open(ctx, book.uri) ?: error("封面读取失败：文件不可读")
            },
        )
        ZipPageCache.fileAt(cached, 0).readBytes()
    } else {
        MangaFiles.open(ctx, book.uri)?.use { it.readBytes() } ?: error("封面读取失败")
    }
    val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, opts)
    var sample = 1
    while (opts.outWidth / sample > COVER_MAX_PX || opts.outHeight / sample > COVER_MAX_PX) {
        sample *= 2
    }
    val bmp = BitmapFactory.decodeByteArray(
        bytes, 0, bytes.size,
        BitmapFactory.Options().apply { inSampleSize = sample },
    ) ?: error("封面解码失败")
    return bmp.asImageBitmap()
}
