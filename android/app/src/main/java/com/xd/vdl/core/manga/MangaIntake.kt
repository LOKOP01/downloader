package com.xd.vdl.core.manga

import com.xd.vdl.core.Platform

/**
 * 「下载完成 → 书架记录」的转换。
 *
 * 单独抽出来是因为这里出错的代价最高：key 算错会导致同一本书反复入库，
 * 或者两个不同的本子互相覆盖；页序字段写错会让断点续读错位。
 * 纯函数（无 Android 依赖），因此可以逐条单测。
 *
 * ⚠️ 调用方必须**只在确认下载成功之后**调用 —— 部分下载、失败、取消都不该产生记录，
 * 否则书架上会出现点开就报错的假条目。
 */
object MangaIntake {

    /**
     * 压缩包产物（禁漫本子、官方整本打包）。
     *
     * @param dirName  实际落盘目录名（`Download/<dirName>/`）。**不要传死默认值**，
     *                 用户可能在设置里改过目录名；[MangaFiles.meta] 能查到真实值。
     * @param fileName 落盘文件名（禁漫是车号，如 `422866.zip`）
     * @param chapters 章节目录结构（可能为空：页数要解包才知道）
     */
    fun zip(
        title: String,
        platform: Platform,
        uri: String,
        dirName: String,
        fileName: String,
        sourceUrl: String = "",
        chapters: List<MangaChapter> = emptyList(),
        now: Long = System.currentTimeMillis(),
    ): MangaBook = MangaBook(
        key = MangaKeys.zip(dirName, fileName),
        title = title.ifBlank { fileName.substringBeforeLast('.') },
        platform = platform,
        kind = BookKind.ZIP,
        uri = uri,
        uris = listOf(uri),
        chapters = chapters,
        sourceUrl = sourceUrl,
        addedAt = now,
    )

    /**
     * 散图产物（图集下载保存到相册的若干张）。
     *
     * 页序**严格等于** [uris] 的顺序 —— 那是解析结果给出的顺序，不能重排。
     *
     * 只有一张图（或没有图）时返回 null，不入库：单张图不是「一本漫画」，
     * 收进书架只会让列表变噪声（用户想看单张图，相册就是最好的入口）。
     */
    fun images(
        title: String,
        platform: Platform,
        uris: List<String>,
        businessId: String = "",
        sourceUrl: String = "",
        now: Long = System.currentTimeMillis(),
    ): MangaBook? {
        val pages = uris.filter { it.isNotEmpty() }
        if (pages.size < 2) return null
        return MangaBook(
            key = MangaKeys.images(platform, businessId, title),
            title = title.ifBlank { "未命名图集" },
            platform = platform,
            kind = BookKind.FOLDER,
            uri = pages.first(),
            uris = pages,
            sourceUrl = sourceUrl,
            addedAt = now,
        )
    }

    /** 禁漫回源地址（只用于展示与「重新下载」，不参与离线阅读） */
    fun jmSourceUrl(jmId: String, jmKind: String): String {
        if (jmId.isEmpty()) return ""
        return "jmcomic://" + (if (jmKind == "album") "album" else "photo") + "/" + jmId
    }
}
