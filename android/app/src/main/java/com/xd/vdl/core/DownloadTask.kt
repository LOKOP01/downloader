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
}
