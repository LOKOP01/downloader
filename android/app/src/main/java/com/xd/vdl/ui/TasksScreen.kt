package com.xd.vdl.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.PlayCircle
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.xd.vdl.core.AppLog
import com.xd.vdl.core.DownloadManager
import com.xd.vdl.core.DownloadTask
import com.xd.vdl.core.Platform
import com.xd.vdl.core.TaskStatus
import com.xd.vdl.core.model.fmtSize

@Composable
fun TasksScreen(vm: AppViewModel) {
    val tasks by vm.tasks.collectAsState()
    var preview by remember { mutableStateOf<DownloadTask?>(null) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "下载任务",
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.weight(1f))
            if (tasks.any { it.finished }) {
                TextButton(onClick = { DownloadManager.clearFinished() }) {
                    Text("清空已完成")
                }
            }
        }
        Spacer(Modifier.height(12.dp))

        LazyColumn(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (tasks.isEmpty()) {
                item {
                    Text(
                        "暂无任务，去首页粘贴链接下载吧",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                items(tasks, key = { it.id }) { t ->
                    TaskRow(t, onPreview = { preview = it })
                }
            }
        }

        Spacer(Modifier.height(12.dp))
        LogPanel()
    }

    preview?.let { t ->
        PreviewDialog(
            title = t.title,
            uris = t.previewUris,
            startIndex = 0,
            onClose = { preview = null },
        )
    }
}

@Composable
private fun LogPanel() {
    val lines by AppLog.lines.collectAsState()
    val listState = rememberLazyListState()
    // 只有原本就贴着底部才继续跟随新日志 —— 下载中日志几秒一条，无条件
    // scrollToItem 会把正在向上翻看历史的人一直拽回底部（桌面端
    // task_interface.py 里就是这条「在底部才跟随」的规则）。
    // 首次铺开时还没有任何已布局项，按「在底部」处理，进来即看到最新一行。
    LaunchedEffect(lines.size) {
        if (lines.isEmpty()) return@LaunchedEffect
        val laid = listState.layoutInfo.visibleItemsInfo
        if (laid.isEmpty() || laid.last().index >= lines.size - 2) {
            listState.scrollToItem(lines.lastIndex)
        }
    }
    Card(
        modifier = Modifier.fillMaxWidth(),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "运行日志",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.weight(1f))
                TextButton(onClick = { AppLog.clear() }) { Text("清空") }
            }
            LazyColumn(
                state = listState,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(180.dp),
            ) {
                if (lines.isEmpty()) {
                    item {
                        Text(
                            "暂无日志",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                } else {
                    items(lines.size) { i ->
                        val line = lines[i]
                        val color = when {
                            line.contains(" E  ") || "失败" in line -> MaterialTheme.colorScheme.error
                            line.contains(" W  ") -> Color(0xFFD97706)
                            else -> MaterialTheme.colorScheme.onSurface
                        }
                        Text(
                            line,
                            fontFamily = FontFamily.Monospace,
                            fontSize = 11.sp,
                            color = color,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun TaskRow(t: DownloadTask, onPreview: (DownloadTask) -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = t.canPreview) { onPreview(t) },
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    t.title,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (t.canPreview) {
                    IconButton(onClick = { onPreview(t) }) {
                        Icon(
                            Icons.Filled.PlayCircle,
                            contentDescription = "预览",
                            modifier = Modifier.size(20.dp),
                            tint = MaterialTheme.colorScheme.primary,
                        )
                    }
                }
                if (t.status == TaskStatus.RUNNING || t.status == TaskStatus.PENDING) {
                    IconButton(onClick = { DownloadManager.cancel(t.id) }) {
                        Icon(
                            Icons.Filled.Close,
                            contentDescription = "取消",
                            modifier = Modifier.size(18.dp),
                        )
                    }
                }
            }
            Spacer(Modifier.height(4.dp))
            Text(
                "${t.platform.label} · ${t.qualityLabel}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(8.dp))

            when (t.status) {
                TaskStatus.DONE -> Text(
                    buildString {
                        append("已完成")
                        append(when {
                            t.platform == Platform.JMCOMIC -> " · 已保存到下载目录"
                            t.savedUris.size > 1 -> " · 已保存到相册（${t.savedUris.size} 张）"
                            else -> " · 已保存到相册"
                        })
                        if (t.note.isNotEmpty()) append("（${t.note}）")
                        if (t.canPreview) append(" · 点卡片预览")
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.primary,
                )

                TaskStatus.FAILED -> Text(
                    "失败：${t.error}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )

                TaskStatus.CANCELED -> Text(
                    "已取消",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )

                else -> {
                    val indeterminate = t.status != TaskStatus.RUNNING || t.total <= 0
                    if (indeterminate) {
                        LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                    } else {
                        LinearProgressIndicator(
                            progress = { t.percent / 100f },
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                    Spacer(Modifier.height(4.dp))
                    val sizeText = if (t.total > 0) {
                        "${fmtSize(t.downloaded)} / ${fmtSize(t.total)}"
                    } else {
                        fmtSize(t.downloaded)
                    }
                    Text(
                        "${t.status.label} · $sizeText" +
                            if (t.status == TaskStatus.RUNNING) " · ${t.percent}%" else "",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}
