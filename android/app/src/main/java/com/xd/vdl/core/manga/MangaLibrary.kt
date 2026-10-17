package com.xd.vdl.core.manga

import android.content.ContentUris
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import com.xd.vdl.core.AppLog
import com.xd.vdl.core.SaveSettings
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.io.File

/**
 * 本地漫画库（应用级单例）。
 *
 * 职责边界：
 *  · **只做索引与进度**，不搬动、不改名、不复制任何图片文件；
 *  · 状态通过 [books] 暴露给界面，任何修改都会立刻落盘（见 [MangaStore]）；
 *  · 所有写操作在单条 IO 协程 + [Mutex] 里串行，杜绝「两个下载同时完成」导致的
 *    「后写覆盖前写」丢记录。
 *
 * 与桌面端的关系：桌面端 `app/core/manga.py` 的书架语义（按路径判重、保留用户进度、
 * 坏记录不静默丢弃）在这里逐条对齐，字段名也刻意保持一致（page_index / last_page /
 * last_read / favorite），将来两边可以互读同一份记录。
 */
object MangaLibrary {

    internal const val FILE_NAME = "manga_library.json"

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val lock = Mutex()

    @Volatile private var store: MangaStore? = null
    @Volatile private var loaded = false
    private var prefs = MangaStore.Prefs()

    private val _books = MutableStateFlow<List<MangaBook>>(emptyList())
    /** 书架内容（已按 [MangaSort] 之外的自然顺序存储，界面自行排序） */
    val books: StateFlow<List<MangaBook>> = _books.asStateFlow()

    private val _notice = MutableStateFlow("")
    /** 需要让用户看到的一条提示（读写失败、从备份恢复等）；空串表示没有 */
    val notice: StateFlow<String> = _notice.asStateFlow()

    private val _scanning = MutableStateFlow(false)
    /** 是否正在扫描下载目录 */
    val scanning: StateFlow<Boolean> = _scanning.asStateFlow()

    // ------------------------------------------------------------------ //
    // 初始化
    // ------------------------------------------------------------------ //

    fun init(ctx: Context) {
        initFile(File(ctx.filesDir, FILE_NAME))
    }

    /** 供测试注入临时路径；真实调用走 [init]。重复调用以最后一次为准。 */
    internal fun initFile(file: File) {
        store = MangaStore(file)
        loaded = false
        // 重置可见状态：换一个书架文件就必须丢掉上一份快照，
        // 否则界面会短暂显示「上一个书架」的内容（测试之间也会互相串味）。
        _books.value = emptyList()
        _notice.value = ""
        prefs = MangaStore.Prefs()
        scope.launch { lock.withLock { ensureLoaded() } }
    }

    /** 等首次加载完成（界面 / 测试用）。已加载则立即返回。 */
    internal suspend fun awaitLoaded() = lock.withLock { ensureLoaded() }

    private suspend fun ensureLoaded() {
        if (loaded) return
        val s = store ?: return
        val result = s.load()
        _books.value = result.books
        prefs = result.prefs
        loaded = true
        if (result.warning.isNotEmpty()) _notice.value = result.warning
        AppLog.i("书架载入 " + result.books.size + " 本" +
            if (result.warning.isEmpty()) "" else "（" + result.warning + "）")
    }

    // ------------------------------------------------------------------ //
    // 读
    // ------------------------------------------------------------------ //

    /** 当前内存快照（同步；界面与测试用） */
    fun snapshot(): List<MangaBook> = _books.value

    fun find(key: String): MangaBook? = _books.value.firstOrNull { it.key == key }

    fun consumeNotice() { _notice.value = "" }

    // ------------------------------------------------------------------ //
    // 写（全部串行 + 立即落盘）
    // ------------------------------------------------------------------ //

    /** 入库一批书；按 key 合并，已存在的用户数据（进度/收藏/改名）保留 */
    fun add(incoming: List<MangaBook>) {
        if (incoming.isEmpty()) return
        scope.launch { lock.withLock { mergeNow(incoming) } }
    }

    fun add(book: MangaBook) = add(listOf(book))

    internal suspend fun mergeNow(incoming: List<MangaBook>): List<MangaBook> {
        ensureLoaded()
        val before = _books.value.size
        val merged = MangaStore.merge(_books.value, incoming)
        _books.value = merged
        persist()
        val added = merged.size - before
        if (added > 0) AppLog.i("书架新增 " + added + " 本（共 " + merged.size + " 本）")
        return merged
    }

    /** 记录阅读位置。空页列表直接忽略（没有可锚定的页） */
    fun markRead(key: String, index: Int, pageIds: List<String>) {
        if (pageIds.isEmpty()) return
        scope.launch {
            lock.withLock {
                ensureLoaded()
                val cur = _books.value.firstOrNull { it.key == key } ?: return@withLock
                val updated = cur.markRead(index, pageIds)
                if (updated == cur) return@withLock
                _books.value = _books.value.map { if (it.key == key) updated else it }
                persist()
            }
        }
    }

    fun setFavorite(key: String, favorite: Boolean) = update(key) { it.copy(favorite = favorite) }

    fun rename(key: String, title: String) {
        val t = title.trim()
        if (t.isEmpty()) return
        update(key) { it.copy(title = t) }
    }

    /** 用实际扫描到的页数刷新记录（不改动阅读位置） */
    fun setPageCount(key: String, count: Int) = update(key) {
        it.copy(pageCount = count, counted = count > 0)
    }

    private fun update(key: String, block: (MangaBook) -> MangaBook) {
        scope.launch {
            lock.withLock {
                ensureLoaded()
                val cur = _books.value.firstOrNull { it.key == key } ?: return@withLock
                val next = block(cur)
                if (next == cur) return@withLock
                _books.value = _books.value.map { if (it.key == key) next else it }
                persist()
            }
        }
    }

    /**
     * 从书架移除一本书。
     *
     * @param deleteFiles **是否同时删除已下载的文件**。由用户在选择框里决定：
     *  · false —— 只移除记录与阅读缓存，下载目录里的东西原样保留（默认，可逆）；
     *  · true  —— 额外删除这本书自己的那些文件（见 [MangaDeleter] 的护栏）。
     *
     * 无论哪种都会清掉**阅读缓存**（那是本应用自己的临时目录，删了随时会重建，
     * 与用户资产无关）。
     *
     * 返回值必须交回界面：删除可能部分失败（权限、文件被占用），
     * 用户需要知道到底删掉了什么。
     */
    fun remove(
        ctx: Context?,
        key: String,
        deleteFiles: Boolean = false,
        alsoCache: Boolean = true,
        onDone: (DeleteResult?) -> Unit = {},
    ) {
        scope.launch {
            val victim = lock.withLock {
                ensureLoaded()
                val v = _books.value.firstOrNull { it.key == key }
                if (v == null) return@withLock null
                _books.value = _books.value.filterNot { it.key == key }
                // ⚠️ 缓存目录名走的是 [ZipPageCache.keyOf]（URI + 文件大小），
                // **不是**书架用的书籍 key。必须用同一个算法重算，
                // 否则移除书架后缓存会永远留着，白占几百 MB。
                if (alsoCache && ctx != null) {
                    runCatching { ZipPageCache.evict(ctx, ZipPageCache.keyOf(ctx, v.uri)) }
                        .onFailure { AppLog.w("清理阅读缓存失败：" + it.message) }
                }
                persist()
                v
            } ?: return@launch

            // 删除文件放在锁外做：几百张图的删除可能要几秒，
            // 不该在此期间阻塞书架的任何其它操作。
            val result = if (deleteFiles && ctx != null) {
                runCatching { MangaDeleter.delete(ctx, victim) }
                    .onFailure { AppLog.e("删除文件出错：" + it.message) }
                    .getOrNull()
            } else {
                null
            }
            AppLog.i(
                if (deleteFiles) {
                    "书架移除 " + key + "，并请求删除文件：" + (result?.summary ?: "未执行")
                } else {
                    "书架移除 " + key + "（仅移除记录，未删除任何文件）"
                },
            )
            onDone(result)
        }
    }

    internal fun setPrefs(p: MangaStore.Prefs) {
        scope.launch { lock.withLock { ensureLoaded(); prefs = p; persist() } }
    }

    fun prefsNow(): MangaStore.Prefs = prefs

    /** 落盘。失败必须让用户看见 —— 静默失败会让「进度没保存」变成玄学 */
    private fun persist() {
        val s = store ?: return
        runCatching { s.save(_books.value, prefs) }
            .onFailure { e ->
                _notice.value = "书架保存失败：" + (e.message ?: e.javaClass.simpleName)
                AppLog.e("书架保存失败：" + e.message)
            }
    }

    // ------------------------------------------------------------------ //
    // 扫描下载目录（本地漫画发现）
    // ------------------------------------------------------------------ //

    /**
     * 扫描本应用的「压缩包下载目录」，把还不在书架里的压缩包补进去。
     *
     * 场景：应用重装、书架文件丢失、用户手动把本子丢进下载目录 —— 这些情况下
     * 文件明明在，界面上却找不到。
     *
     * ⚠️ **只扫描用户配置的压缩包目录**（`Download/<archiveDir>`），不会全盘扫描，
     * 也不会把相册里的任意图片当成漫画。
     * API < 29 上读公共目录需要 READ_EXTERNAL_STORAGE；本应用**不申请**该权限，
     * 所以那里直接返回「不支持」而不是弹权限框（见报告：不做无必要的广泛存储权限）。
     *
     * @return 新增的本数；失败返回 -1 并给出 [notice]
     */
    fun scanDownloadDir(ctx: Context) {
        if (_scanning.value) return
        _scanning.value = true
        scope.launch {
            try {
                val found = queryArchives(ctx)
                if (found == null) {
                    _notice.value = "这个 Android 版本上无法扫描下载目录（需要存储读取权限），" +
                        "已下载的漫画仍可从「任务」页打开"
                    return@launch
                }
                if (found.isEmpty()) {
                    _notice.value = "下载目录里没有压缩包；确认下载位置设置是否正确"
                    return@launch
                }
                // 新增数必须在 merge **之前**算：merge 之后所有 key 都在书架里了
                val before = lock.withLock { ensureLoaded(); _books.value.map { it.key }.toSet() }
                val added = found.count { it.key !in before }
                lock.withLock { mergeNow(found) }
                _notice.value = "扫描完成：下载目录里 " + found.size + " 个压缩包" +
                    if (added > 0) "，新增 " + added + " 本" else "（都已收录）"
            } catch (e: Exception) {
                _notice.value = "扫描下载目录失败：" + (e.message ?: e.javaClass.simpleName)
                AppLog.w("扫描下载目录失败：" + e.message)
            } finally {
                _scanning.value = false
            }
        }
    }


    /** 查询 `Download/<archiveDir>` 下的压缩包；不支持时返回 null */
    private suspend fun queryArchives(ctx: Context): List<MangaBook>? {
        val dirName = SaveSettings.archiveDir(ctx).ifEmpty { SaveSettings.DEFAULT_ARCHIVE }
        val out = ArrayList<MangaBook>()
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            // 低版本只能直接列目录，且需要 READ_EXTERNAL_STORAGE（本应用不申请）
            val dir = File(
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
                dirName,
            )
            if (!dir.isDirectory) return emptyList()
            if (!dir.canRead()) return null
            dir.listFiles()?.forEach { f ->
                if (f.isFile && f.name.lowercase().endsWith(".zip")) {
                    out.add(bookOf(ctx, Uri.fromFile(f).toString(), f.name, dirName, f.length()))
                }
            }
            return out
        }
        val collection = MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        val rel = Environment.DIRECTORY_DOWNLOADS + "/" + dirName + "/"
        val projection = arrayOf(
            MediaStore.Downloads._ID,
            MediaStore.Downloads.DISPLAY_NAME,
            MediaStore.Downloads.SIZE,
        )
        runCatching {
            ctx.contentResolver.query(collection, projection, null, null, null)?.use { c ->
                val idIdx = c.getColumnIndex(MediaStore.Downloads._ID)
                val nameIdx = c.getColumnIndex(MediaStore.Downloads.DISPLAY_NAME)
                val sizeIdx = c.getColumnIndex(MediaStore.Downloads.SIZE)
                val pathIdx = c.getColumnIndex("relative_path")
                while (c.moveToNext()) {
                    val name = if (nameIdx >= 0) c.getString(nameIdx).orEmpty() else ""
                    if (!name.lowercase().endsWith(".zip")) continue
                    // 只看目标目录；没拿到 relative_path 时（极少数机型）不猜，宁可少扫
                    if (pathIdx >= 0) {
                        val path = c.getString(pathIdx).orEmpty()
                        if (!path.startsWith(rel) && !path.trimEnd('/').endsWith(dirName)) continue
                    }
                    val id = if (idIdx >= 0) c.getLong(idIdx) else -1L
                    if (id < 0) continue
                    val uri = ContentUris.withAppendedId(collection, id).toString()
                    val size = if (sizeIdx >= 0) c.getLong(sizeIdx) else 0L
                    out.add(bookOf(ctx, uri, name, dirName, size))
                }
            }
        }.onFailure { AppLog.w("查询下载目录失败：" + it.message) }
        return out
    }

    private fun bookOf(ctx: Context, uri: String, name: String, dir: String, size: Long): MangaBook {
        // 0 字节的条目意味着「MediaStore 里登记了但内容没写成功」，打开必然报错。
        // 记一条日志便于排查，但仍然入库 —— 让用户在界面上看到并自行删除更友好。
        if (size <= 0L) AppLog.w("下载目录里的压缩包大小为 0，可能没下完：" + name)
        val title = name.substringBeforeLast('.').ifEmpty { name }
        return MangaBook(
            key = MangaKeys.zip(dir, name),
            title = title,
            platform = com.xd.vdl.core.Platform.UNKNOWN,
            kind = BookKind.ZIP,
            uri = uri,
            uris = listOf(uri),
            addedAt = System.currentTimeMillis(),
            // 页数要真正解包才知道；扫描阶段不做（几万个包里挑一个解压代价太大），
            // 由打开阅读器时补齐。size 只用于日志，便于排查「扫到了 0 字节条目」。
        )
    }
}
