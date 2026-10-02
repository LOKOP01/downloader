package com.xd.vdl.core.model

import com.xd.vdl.core.Platform

/**
 * 一个可选清晰度。
 * [audioUrl] 非空表示音视频分离（DASH），下载后需要合并。
 * [urlBackups] / [audioBackups] 是同一档的备用 CDN（B 站 playurl 的 backupUrl）。
 */
data class Quality(
    val label: String,
    val url: String,
    val audioUrl: String = "",
    val sizeBytes: Long = 0L,
    val urlBackups: List<String> = emptyList(),
    val audioBackups: List<String> = emptyList(),
) {
    val needMerge: Boolean get() = audioUrl.isNotEmpty()

    val videoCandidates: List<String>
        get() = (listOf(url) + urlBackups).filter { it.isNotEmpty() }.distinct()

    val audioCandidates: List<String>
        get() = (listOf(audioUrl) + audioBackups).filter { it.isNotEmpty() }.distinct()
}

/** 解析结果 */
data class VideoInfo(
    val platform: Platform,
    val title: String,
    val author: String = "",
    val coverUrl: String = "",
    val durationMs: Long = 0L,
    val qualities: List<Quality> = emptyList(),
    val images: List<String> = emptyList(),
    // ---- 禁漫专用（其他平台保持默认值） ----
    /** "album"（整本，多章）/ "photo"（单章） */
    val jmKind: String = "",
    val jmId: String = "",
    /** 章节列表：章节 id → 章节名。整本下载要逐章拉图 */
    val jmChapters: List<Pair<String, String>> = emptyList(),
    /** 图片切片数所需；单章解析时才拿得到 */
    val jmScrambleId: String = "",
    val jmTags: List<String> = emptyList(),
    val jmPageCount: Int = 0,
    val jmChapterCount: Int = 0,
    val jmViews: Int = 0,
    val jmLikes: Int = 0,
    val jmComments: Int = 0,
) {
    val isImage: Boolean get() = platform == Platform.JMCOMIC || images.isNotEmpty()

    /** 禁漫：整本（有多个章节） */
    val isJmAlbum: Boolean get() = platform == Platform.JMCOMIC && jmKind == "album"

    val durationText: String
        get() {
            if (durationMs <= 0) return ""
            val total = durationMs / 1000
            val m = total / 60
            val s = total % 60
            return "%d:%02d".format(m, s)
        }
}

fun fmtSize(bytes: Long): String {
    if (bytes <= 0) return ""
    val kb = bytes / 1024.0
    if (kb < 1024) return "%.0f KB".format(kb)
    val mb = kb / 1024.0
    if (mb < 1024) return "%.1f MB".format(mb)
    return "%.2f GB".format(mb / 1024.0)
}
