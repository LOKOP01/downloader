package com.xd.vdl.core.manga

import com.xd.vdl.core.AppLog
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.nio.charset.StandardCharsets

/**
 * 本地漫画书架的持久化层：单文件 JSON，原子替换，保留上一份有效备份。
 *
 * 设计直接对齐桌面端 app/core/manga.py 的 MangaLibrary：
 *
 *   filesDir/manga_library.json          主文件（写入前先把旧的挪成 .bak）
 *   filesDir/manga_library.json.bak      备份（主文件损坏时回退）
 *   filesDir/manga_library.json.corrupt  已损坏且备份也救不回时的原始留档
 *
 * 几条硬约束（都是桌面端踩过坑之后写下来的）：
 *  1. 写入必须是原子替换：直接开目标文件覆写，进程被杀会留下半截 JSON，
 *     书架连同一本书记录一起丢。
 *  2. 坏的主文件绝不静默丢弃：改名留档（.corrupt）再回退到 .bak，
 *     用户还有手工抢救的机会。
 *  3. 读失败时禁止保存：load() 返回 readable=false，调用方据此拒绝落盘 ——
 *     否则「读失败 → 空书架 → 保存」会把真正有记录的文件覆盖成空。
 *     对应桌面端那个 self._revision = None 的语义。
 *
 * 本类不做压缩、不做迁移、不触碰漫画原文件。
 */
class MangaStore(val file: File) {

    /**
     * 上一次 [load] 是否读得出内容。false 时 [save] 会**拒绝写入**。
     *
     * 这是「读失败 → 空书架 → 保存 → 真记录被清空」这条事故链的最后一道闸：
     * 调用方（[MangaLibrary]）本来就不该在 readable=false 时落盘，但持久化层自己
     * 也必须拦住 —— 否则任何一处漏判都会真实毁掉用户的书架。
     */
    @Volatile
    var lastLoadReadable: Boolean = true
        private set

    /** 界面偏好：与桌面端 manga_library.json 的 preferences 同名同义 */
    data class Prefs(val fit: String = "page", val rtl: Boolean = false, val paper: Boolean = true) {
        fun toJson(): JSONObject = JSONObject().apply {
            put("fit", fit)
            put("rtl", rtl)
            put("paper", paper)
        }

        companion object {
            val FITS = setOf("page", "width", "actual")

            fun fromJson(o: JSONObject?): Prefs {
                if (o == null) return Prefs()
                val fit = o.optString("fit").let { if (it in FITS) it else "page" }
                return Prefs(fit, o.optBoolean("rtl", false), o.optBoolean("paper", true))
            }
        }
    }

    /**
     * 一次读取的结果。
     *
     * readable = false 表示「磁盘上有东西但读不出来」—— 此时必须禁止保存，
     * 否则一次瞬时读失败就会把用户的书架清空。
     *
     * warning 是给界面看的原话，含「已从备份恢复」「原始文件已留档」这类信息。
     */
    data class Loaded(
        val books: List<MangaBook>,
        val prefs: Prefs,
        val readable: Boolean,
        val warning: String = "",
    )

    // ------------------------------------------------------------------ //
    // 读
    // ------------------------------------------------------------------ //

    fun load(): Loaded {
        if (!file.exists()) {
            lastLoadReadable = true
            return Loaded(emptyList(), Prefs(), readable = true)
        }

        val primary = runCatching { read(file) }
        val err = primary.exceptionOrNull()
        if (err == null) {
            lastLoadReadable = true
            val (books, prefs) = primary.getOrThrow()
            return Loaded(books, prefs, readable = true)
        }

        AppLog.w("书架主文件读取失败：${err.javaClass.simpleName}: ${err.message}")

        // 主文件坏了：先把原始内容留档，再试备份。留档失败不阻塞恢复流程。
        val backup = File(file.path + ".bak")
        val archived = archiveCorrupt()
        val fromBak = runCatching { read(backup) }
        return if (fromBak.isSuccess) {
            lastLoadReadable = true
            val (books, prefs) = fromBak.getOrThrow()
            AppLog.w("书架已从备份恢复：${books.size} 本")
            Loaded(
                books, prefs, readable = true,
                warning = buildString {
                    append("书架记录损坏，已从备份恢复 ${books.size} 本")
                    if (archived) append("；原始文件已留档为 .corrupt")
                },
            )
        } else {
            // 两份都读不出来：不回退成空书架，而是禁止保存并明确告知。
            lastLoadReadable = false
            AppLog.e("书架主文件与备份均不可读，已禁止保存以免覆盖")
            Loaded(
                emptyList(), Prefs(), readable = false,
                warning = "书架记录无法读取（主文件与备份均损坏），已暂停保存以免覆盖；" +
                    "原始文件保留在 ${file.name}",
            )
        }
    }

    /** 把损坏的主文件改名留档；返回是否成功 */
    private fun archiveCorrupt(): Boolean = runCatching {
        val corrupt = File(file.path + ".corrupt")
        if (corrupt.exists()) corrupt.delete()
        if (!file.renameTo(corrupt)) {
            file.copyTo(corrupt, overwrite = true)
            file.delete()
        }
        true
    }.getOrDefault(false)

    private fun read(f: File): Pair<List<MangaBook>, Prefs> {
        val text = f.readText(StandardCharsets.UTF_8)
        if (text.isBlank()) throw IOException("书架文件为空")
        val root = JSONObject(text)
        val arr: JSONArray = root.optJSONArray("books") ?: JSONArray()
        val books = ArrayList<MangaBook>(arr.length())
        val seen = HashSet<String>(arr.length())
        var skipped = 0
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val b = MangaBook.fromJson(o)
            if (b == null || !seen.add(b.key)) {
                skipped++
                continue
            }
            books.add(b)
        }
        if (skipped > 0) AppLog.w("书架跳过 ${skipped} 条无效/重复记录")
        return books to Prefs.fromJson(root.optJSONObject("preferences"))
    }

    // ------------------------------------------------------------------ //
    // 写
    // ------------------------------------------------------------------ //

    /**
     * 原子写入。
     *
     * 顺序：先把现有主文件（且它自己能被解析）留成 .bak，再把新内容写进临时文件、
     * fsync、最后原子替换到主文件。任一步失败都会抛 IOException，由调用方决定怎么提示 ——
     * 宁可让用户看到「保存失败」，也不能留下一个半截书架。
     */
    fun save(books: List<MangaBook>, prefs: Prefs) {
        if (!lastLoadReadable) {
            throw IOException(
                "书架上次读取失败，已拒绝写入以免覆盖现有记录；请先解决读取问题",
            )
        }
        val data = JSONObject().apply {
            put("version", 1)
            put("books", JSONArray().apply { books.forEach { put(it.toJson()) } })
            put("preferences", prefs.toJson())
        }.toString(2)

        val dir = file.parentFile
        if (dir != null && !dir.exists() && !dir.mkdirs()) {
            throw IOException("无法创建书架目录：${dir.path}")
        }

        // 旧文件能读才留备份；读不出来的那份已经由 load() 留档成 .corrupt 了，
        // 这里再覆盖一次 .bak 会把上一份好备份也毁掉。
        if (file.exists() && runCatching { read(file) }.isSuccess) {
            // 直接覆盖写备份：先删再拷会留一个「备份不存在」的窗口，
            // 此刻进程被杀就同时失去主文件和备份。
            runCatching { file.copyTo(File(file.path + ".bak"), overwrite = true) }
                .onFailure { AppLog.w("书架备份失败：" + it.message) }
        }

        val tmp = File(dir ?: File("."), file.name + ".tmp")
        try {
            FileOutputStream(tmp).use { out ->
                out.write(data.toByteArray(StandardCharsets.UTF_8))
                out.flush()
                // fsync：否则断电后可能只留下一个长度为 0 的文件
                runCatching { out.fd.sync() }
            }
            if (!tmp.renameTo(file)) {
                // 同目录内的 rename 在 Android 上几乎不会失败；真失败就退化为拷贝，
                // 并明确把异常抛出去（不静默吞掉）。
                tmp.copyTo(file, overwrite = true)
                tmp.delete()
            }
        } catch (e: IOException) {
            runCatching { tmp.delete() }
            throw e
        } catch (e: Exception) {
            runCatching { tmp.delete() }
            throw IOException("书架写入失败：${e.message}", e)
        }
        AppLog.i("书架已保存 ${books.size} 本 → ${file.name}")
    }

    companion object {

        /**
         * 按 MangaBook.key 合并：书架里已有的用户数据一律保留
         * （标题改名、收藏、阅读进度、添加时间），只刷新来自文件系统的客观信息
         * （URI 变了、页数变了、平台/来源补全了）。
         *
         * 与桌面端 MangaLibrary.add 的行为一致：重复入库不产生第二条记录。
         * 返回值顺序 = 新书记录追加在已有记录之后（界面自己按时间排序）。
         */
        fun merge(existing: List<MangaBook>, incoming: List<MangaBook>): List<MangaBook> {
            if (incoming.isEmpty()) return existing
            val byKey = LinkedHashMap<String, MangaBook>(existing.size + incoming.size)
            existing.forEach { byKey[it.key] = it }
            for (novel in incoming) {
                val old = byKey[novel.key]
                byKey[novel.key] = if (old == null) {
                    novel
                } else {
                    old.copy(
                        // 客观字段：以本次扫描到的为准（文件可能被移动/重建）
                        uri = novel.uri.ifEmpty { old.uri },
                        // 散图列表以这次的为准（只在非空时覆盖，避免用空列表冲掉记录）
                        uris = if (novel.uris.isNotEmpty()) novel.uris else old.uris,
                        folder = novel.folder.ifEmpty { old.folder },
                        platform = if (novel.platform != com.xd.vdl.core.Platform.UNKNOWN) {
                            novel.platform
                        } else {
                            old.platform
                        },
                        sourceUrl = novel.sourceUrl.ifEmpty { old.sourceUrl },
                        // 页数只在这次真的数过时才覆盖，避免用 0 冲掉已数出的页数
                        pageCount = if (novel.counted) novel.pageCount else old.pageCount,
                        counted = old.counted || novel.counted,
                        chapters = if (novel.chapters.isNotEmpty()) novel.chapters else old.chapters,
                        // 下载时间只在原本没有时补
                        addedAt = if (old.addedAt > 0) old.addedAt else novel.addedAt,
                        // 用户数据：标题、收藏、进度，一律以旧记录为准（copy 余下字段已保留）
                    )
                }
            }
            return byKey.values.toList()
        }
    }
}
