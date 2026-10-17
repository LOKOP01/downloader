package com.xd.vdl.core

/** 下载任务状态 */
enum class TaskStatus(val label: String) {
    PENDING("等待中"),
    RUNNING("下载中"),
    MERGING("合并中"),
    EXPORTING("保存中"),
    DONE("已完成"),
    FAILED("失败"),
    CANCELED("已取消"),
}

data class DownloadTask(
    val id: String,
    val title: String,
    val platform: Platform,
    val qualityLabel: String,
    val status: TaskStatus = TaskStatus.PENDING,
    val downloaded: Long = 0L,
    val total: Long = 0L,
    val error: String = "",
    val savedUri: String = "",
    /** 图集/禁漫解压前的散图 URI 列表（用于预览翻页）；视频/压缩包为空 */
    val savedUris: List<String> = emptyList(),
    val note: String = "",          // 附加说明（如「第 3/9 张」）
) {
    val percent: Int
        get() = if (total <= 0) 0 else ((downloaded * 100 / total).toInt()).coerceIn(0, 100)

    val finished: Boolean
        get() = status == TaskStatus.DONE || status == TaskStatus.FAILED ||
            status == TaskStatus.CANCELED

    /** 预览用得上的所有 URI（图集取列表，单个取 savedUri） */
    val previewUris: List<String>
        get() = savedUris.ifEmpty { listOfNotNull(savedUri.takeIf { it.isNotEmpty() }) }

    /** 已完成且有东西可预览 */
    val canPreview: Boolean
        get() = status == TaskStatus.DONE && previewUris.isNotEmpty()

    /**
     * 这个产物是否属于「可以进书架离线阅读」的漫画。
     *
     * 判定必须**保守**，两个条件缺一不可：
     *  1. 下载真正完成（[TaskStatus.DONE]）—— 失败、取消、下载中一律不算，
     *     否则书架上会出现点开就报错的假记录；
     *  2. **确实拿到了产物**（[previewUris] 非空）—— 只看平台是不够的：
     *     禁漫任务也可能在导出失败时走到完成分支，此时指向书架的按钮会把人送进
     *     一个空书架。
     *
     * 非禁漫平台还要求产物是多张图片（[savedUris] 非空）：视频下载完没必要进书架。
     */
    val readableAsManga: Boolean
        get() = status == TaskStatus.DONE &&
            previewUris.isNotEmpty() &&
            (platform == Platform.JMCOMIC || savedUris.isNotEmpty())
}
