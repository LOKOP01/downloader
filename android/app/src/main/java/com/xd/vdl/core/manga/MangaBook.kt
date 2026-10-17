package com.xd.vdl.core.manga

import com.xd.vdl.core.Platform
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 本地漫画的来源形态 */
enum class BookKind {
    /** 下载产物是一个压缩包（禁漫本子、官方打包直链的成品） */
    ZIP,

    /** 一堆散图（图集下载 → 相册散图；桌面端书架里的文件夹） */
    FOLDER,
}

/** 章节摘要（章节名 + 页数）。页数未知时为 0，读取时再补齐。 */
data class MangaChapter(val name: String, val pages: Int) {
    override fun toString(): String = if (pages > 0) "${name}（${pages} 页）" else name
}

/**
 * 本地漫画库中的一本。
 *
 * 只存**能离线复现阅读所需的元数据**：定位用的 [uri] / [folder]、页序提示
 * [pageCount] / [chapters]、以及阅读进度 [pageIndex] / [lastPage]。
 * 图片本身永远读原始文件，书架不复制、不搬运、不改名 —— 与桌面端
 * `app/core/manga.py` 的 `MangaBook` 同一套语义。
 *
 * ⚠️ [key] 是稳定标识：判重、增量入库、进度落盘全靠它。**不要**用标题当 key
 * （同名本子很常见），也不要用随机任务 id（重复下载同一本会产生第二条记录）。
 * 见 [MangaKeys]。
 */
data class MangaBook(
    val key: String,
    val title: String,
    val platform: Platform = Platform.UNKNOWN,
    val kind: BookKind = BookKind.ZIP,
    /** 压缩包（或散图首张）的 content:// / file:// URI；文件被移走时可能失效 */
    val uri: String = "",
    /**
     * 散图（图集下载）的全部页码 URI，顺序即阅读顺序。
     *
     * 压缩包漫画为空（页在包内，靠 [ZipPageCache] 解压）。
     * [uri] 恒等于本列表的第一项，保留它只是为了兼容「只有单一文件」的压缩包场景。
     */
    val uris: List<String> = emptyList(),
    /** 桌面端兼容字段：文件夹漫画的目录 */
    val folder: String = "",
    val pageIndex: Int = 0,
    val pageCount: Int = 0,
    /** 上次读到的**页标识**（zip 内条目名 / 文件名）。文件重排后仍能定位 */
    val lastPage: String = "",
    /** 最近阅读时间（ISO，本地时区，字典序即时间序） */
    val lastRead: String = "",
    val favorite: Boolean = false,
    val chapters: List<MangaChapter> = emptyList(),
    /** 回源地址（如 `jmcomic://album/422866`），仅用于展示与重新下载 */
    val sourceUrl: String = "",
    val addedAt: Long = 0L,
    /** 页数可信（由实际读取过文件得出）；false 时界面显示「未知页数」而不是 0 */
    val counted: Boolean = false,
) {

    /**
     * 把 [lastPage] 映射回页下标；映射不上再退回 [pageIndex]。
     *
     * 与桌面端 `MangaBook.restore_index` 等价：只认「页标识」相等，
     * 页数变化时用下标兜底并夹紧到合法范围。
     */
    fun restoreIndex(pages: List<String>): Int {
        if (pages.isEmpty()) return 0
        if (lastPage.isNotEmpty()) {
            val i = pages.indexOfFirst { it == lastPage }
            if (i >= 0) return i
        }
        return pageIndex.coerceIn(0, pages.size - 1)
    }

    /** 记录一次阅读位置；[pages] 是当前实际页序（页标识列表） */
    fun markRead(index: Int, pages: List<String>): MangaBook {
        if (pages.isEmpty()) return this
        val i = index.coerceIn(0, pages.size - 1)
        return copy(
            pageIndex = i,
            pageCount = pages.size,
            lastPage = pages[i],
            lastRead = MangaTime.now(),
            counted = true,
        )
    }

    /** 进度文案：0 页时给「未读」，避免显示成 0/0 */
    val progressText: String
        get() = when {
            !counted || pageCount <= 0 -> "未读"
            pageIndex + 1 >= pageCount -> "已读完 · ${pageCount} 页"
            else -> "读至 ${pageIndex + 1}/${pageCount} 页"
        }

    val displaySource: String
        get() = if (platform == Platform.UNKNOWN) "本地" else platform.label

    // ---------------------------------------------------------------- //
    // 序列化
    // ---------------------------------------------------------------- //

    fun toJson(): JSONObject = JSONObject().apply {
        put("key", key)
        put("title", title)
        put("platform", platform.name)
        put("kind", kind.name)
        put("uri", uri)
        put("uris", JSONArray().apply { uris.forEach { put(it) } })
        put("folder", folder)
        // 与桌面端 manga_library.json 同名同义，便于将来两边互读
        put("page_index", pageIndex)
        put("page_count", pageCount)
        put("last_page", lastPage)
        put("last_read", lastRead)
        put("favorite", favorite)
        put("source_url", sourceUrl)
        put("added_at", addedAt)
        put("counted", counted)
        put("chapters", JSONArray().apply {
            chapters.forEach { put(JSONObject().apply { put("name", it.name); put("pages", it.pages) }) }
        })
    }

    companion object {

        /**
         * 从 JSON 还原一本。**任何字段坏掉都只丢该字段**，不抛异常 ——
         * 一条坏记录不该让整个书架打不开（桌面端就是这么做的：坏记录跳过并保留原文件）。
         */
        fun fromJson(o: JSONObject): MangaBook? {
            val key = o.optString("key").trim()
            if (key.isEmpty()) return null
            val uri = o.optString("uri").trim()
            val folder = o.optString("folder").trim()
            // 既没有 URI 也没有目录的记录没有任何阅读可能，直接丢弃
            if (uri.isEmpty() && folder.isEmpty()) return null
            return MangaBook(
                key = key,
                title = o.optString("title").trim().ifEmpty { key },
                platform = runCatching { Platform.valueOf(o.optString("platform")) }
                    .getOrDefault(Platform.UNKNOWN),
                kind = runCatching { BookKind.valueOf(o.optString("kind")) }
                    .getOrDefault(BookKind.ZIP),
                uri = uri,
                uris = stringsOf(o.optJSONArray("uris")).ifEmpty {
                    if (uri.isNotEmpty()) listOf(uri) else emptyList()
                },
                folder = folder,
                pageIndex = nonNegative(o.opt("page_index")),
                pageCount = nonNegative(o.opt("page_count")),
                lastPage = o.optString("last_page"),
                lastRead = o.optString("last_read"),
                favorite = o.optBoolean("favorite", false),
                chapters = chaptersOf(o.optJSONArray("chapters")),
                sourceUrl = o.optString("source_url"),
                addedAt = nonNegativeLong(o.opt("added_at")),
                counted = o.optBoolean("counted", false),
            )
        }

        private fun stringsOf(arr: JSONArray?): List<String> {
            if (arr == null) return emptyList()
            val out = ArrayList<String>(arr.length())
            for (i in 0 until arr.length()) {
                val s = arr.optString(i)
                if (s.isNotEmpty()) out.add(s)
            }
            return out
        }

        private fun chaptersOf(arr: JSONArray?): List<MangaChapter> {
            if (arr == null) return emptyList()
            val out = ArrayList<MangaChapter>(arr.length())
            for (i in 0 until arr.length()) {
                val e = arr.optJSONObject(i) ?: continue
                val name = e.optString("name")
                if (name.isEmpty()) continue
                out.add(MangaChapter(name, nonNegative(e.opt("pages"))))
            }
            return out
        }

        /** 容忍字符串形式的数字（别处手改过 JSON 时不至于整条丢掉） */
        internal fun nonNegative(v: Any?): Int = when (v) {
            is Number -> v.toInt().coerceAtLeast(0)
            is String -> v.trim().toIntOrNull()?.coerceAtLeast(0) ?: 0
            else -> 0
        }

        /**
         * 时间戳必须按 Long 读：毫秒时间戳约 1.7e12，走 [nonNegative] 会被 Int 截断成
         * 一个随机小数字（表现为「加入时间」排序彻底乱掉）。
         */
        internal fun nonNegativeLong(v: Any?): Long = when (v) {
            is Number -> v.toLong().coerceAtLeast(0L)
            is String -> v.trim().toLongOrNull()?.coerceAtLeast(0L) ?: 0L
            else -> 0L
        }
    }
}

/** 时间戳工具：minSdk 24 用不了 java.time，统一走 SimpleDateFormat（每次新建，非线程安全） */
object MangaTime {

    private const val PATTERN = "yyyy-MM-dd'T'HH:mm:ss"

    fun now(): String = fmt().format(Date())

    fun of(epochMs: Long): String = fmt().format(Date(epochMs))

    /** 解析 ISO 时间串；坏值返回 0（排序时排到最早） */
    fun parse(text: String): Long = runCatching { fmt().parse(text)?.time ?: 0L }.getOrDefault(0L)

    private fun fmt() = SimpleDateFormat(PATTERN, Locale.ROOT)

    /** 界面用的短格式：今天只给时间，其余给「月-日 时:分」 */
    fun display(iso: String): String {
        val epoch = parse(iso)
        if (epoch <= 0) return ""
        val target = java.util.Calendar.getInstance().apply { timeInMillis = epoch }
        val today = java.util.Calendar.getInstance()
        val sameDay = target.get(java.util.Calendar.YEAR) == today.get(java.util.Calendar.YEAR) &&
            target.get(java.util.Calendar.DAY_OF_YEAR) == today.get(java.util.Calendar.DAY_OF_YEAR)
        val pattern = if (sameDay) "HH:mm" else "MM-dd HH:mm"
        return runCatching { SimpleDateFormat(pattern, Locale.ROOT).format(Date(epoch)) }
            .getOrDefault("")
    }
}

/**
 * 书籍稳定标识。
 *
 * 目标：同一本书**重复下载/重复入库只留一条记录**，且标识不依赖随机任务 id，
 * 重启、换任务、重新扫描都能算出同一个值。
 *
 * · 压缩包：`zip|目录名|文件名` —— 文件名是产物名（禁漫是车号），
 *   MediaStore 去重后同一车号会变成 `422866(1).zip`，因此两张包会各自成书（正确）；
 * · 散图：`img|平台|业务 id` —— 用禁漫车号 / 平台内标题，
 *   没有业务 id 时退回标题的稳定哈希。
 */
object MangaKeys {

    fun zip(dirName: String, fileName: String): String =
        "zip|" + dirName.trim() + "|" + fileName.trim()

    fun images(platform: Platform, businessId: String, title: String): String {
        val id = businessId.trim().ifEmpty { "t:" + stableHash(title) }
        return "img|${platform.name}|${id}"
    }

    /** 文件夹漫画：按规范化后的绝对路径（桌面端同样以路径为准） */
    fun folder(path: String): String =
        "dir|" + path.trim().trimEnd('/', '\\').lowercase(Locale.ROOT)

    /**
     * 稳定哈希（FNV-1a 64 位）。**不用 `String.hashCode()`**：32 位碰撞概率高，
     * 两个不同标题撞成同一本书会让书架互相覆盖。
     */
    internal fun stableHash(s: String): String {
        // FNV offset basis 用有符号写法，避免无符号字面量在 Kotlin 里的类型告警
        var h = -0x340d631b7bdddcdbL
        for (b in s.toByteArray(Charsets.UTF_8)) {
            h = h xor (b.toLong() and 0xFF)
            h *= 0x100000001b3L
        }
        return java.lang.Long.toHexString(h)
    }
}
