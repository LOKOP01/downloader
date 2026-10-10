package com.xd.vdl.ui

import android.app.Activity
import android.content.ClipboardManager
import android.content.Context
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.ContentPaste
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.xd.vdl.BuildConfig
import com.xd.vdl.core.Platform
import com.xd.vdl.core.model.fmtSize
import com.xd.vdl.ui.component.GkCard
import com.xd.vdl.ui.component.GkDimens
import com.xd.vdl.ui.component.GkIconBadge
import com.xd.vdl.ui.component.GkPageTitle

@Composable
fun HomeScreen(vm: AppViewModel, activity: Activity) {
    val context = LocalContext.current
    val link by vm.link.collectAsState()
    val parsing by vm.parsing.collectAsState()
    val info by vm.info.collectAsState()
    val qIndex by vm.qualityIndex.collectAsState()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = GkDimens.screenPadding),
    ) {
        Spacer(Modifier.height(10.dp))
        GkPageTitle("视频解析")
        Text(
            "支持 抖音 / B站 / X / Instagram / 小红书 / 禁漫 链接",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 6.dp, top = 2.dp),
        )
        Text(
            "v${BuildConfig.VERSION_NAME}（build ${BuildConfig.VERSION_CODE}）",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 6.dp, top = 2.dp),
        )
        Spacer(Modifier.height(16.dp))

        GkCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                GkIconBadge(Icons.Filled.Link)
                Spacer(Modifier.width(14.dp))
                Column(Modifier.weight(1f)) {
                    Text(
                        "分享链接",
                        style = MaterialTheme.typography.bodyLarge,
                        fontWeight = FontWeight.Medium,
                    )
                    Text(
                        "粘贴后解析，选清晰度再下载",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Spacer(Modifier.height(14.dp))

            OutlinedTextField(
                value = link,
                onValueChange = vm::setLink,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(GkDimens.rowCorner),
                placeholder = { Text("粘贴分享链接，如 xhslink.cn / instagram.com/reel") },
                maxLines = 3,
                trailingIcon = {
                    IconButton(onClick = {
                        val cm = context.getSystemService(Context.CLIPBOARD_SERVICE)
                            as ClipboardManager
                        val t = cm.primaryClip?.takeIf { it.itemCount > 0 }
                            ?.getItemAt(0)?.coerceToText(context)?.toString().orEmpty()
                        if (t.isNotEmpty()) vm.setLink(t)
                    }) {
                        Icon(Icons.Filled.ContentPaste, contentDescription = "粘贴")
                    }
                },
            )
            Spacer(Modifier.height(12.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                Button(
                    onClick = { vm.parse(activity) },
                    enabled = !parsing,
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(GkDimens.rowCorner),
                ) {
                    Icon(
                        Icons.Filled.Search,
                        contentDescription = null,
                        modifier = Modifier.size(18.dp),
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(if (parsing) "解析中…" else "开始解析")
                }
                if (info != null || link.isNotBlank()) {
                    Spacer(Modifier.width(8.dp))
                    OutlinedButton(
                        onClick = { vm.clearAll() },
                        // 解析中禁用：此时结果已置空、清空后若解析结果才回来，
                        // 会出现「有结果但输入框是空的」矛盾状态
                        enabled = !parsing,
                        shape = RoundedCornerShape(GkDimens.rowCorner),
                    ) { Text("清空") }
                }
            }

            if (parsing) {
                Spacer(Modifier.height(12.dp))
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            }
        }

        info?.let { v ->
            Spacer(Modifier.height(GkDimens.cardGap))
            ResultCard(vm, activity, qIndex)
        }

        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun ResultCard(vm: AppViewModel, activity: Activity, qIndex: Int) {
    val info by vm.info.collectAsState()
    val v = info ?: return
    var expanded by remember { mutableStateOf(false) }
    var showPreview by remember(v) { mutableStateOf(false) }

    GkCard {
        Row {
            RemoteImage(
                url = v.coverUrl,
                modifier = Modifier
                    .size(width = 108.dp, height = 144.dp)
                    .clip(RoundedCornerShape(14.dp)),
            )
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    v.title,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(6.dp))
                if (v.author.isNotEmpty()) {
                    Text(
                        "@${v.author}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Spacer(Modifier.height(4.dp))
                Text(
                    buildString {
                        append(v.platform.label)
                        if (v.durationText.isNotEmpty()) append(" · ${v.durationText}")
                        if (v.platform == Platform.JMCOMIC) {
                            if (v.jmChapterCount > 1) append(" · ${v.jmChapterCount} 章")
                            if (v.jmPageCount > 0) append(" · ${v.jmPageCount} 页")
                        } else if (v.isImage) {
                            append(" · ${v.images.size} 张图片")
                        }
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                // 禁漫的标签单独一行展示（对齐桌面版把 tags 放 music_title 的做法）
                if (v.jmTags.isNotEmpty()) {
                    Spacer(Modifier.height(4.dp))
                    Text(
                        v.jmTags.joinToString(" / "),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }

        Spacer(Modifier.height(14.dp))

        if (v.platform == Platform.JMCOMIC) {
            val n = v.jmPageCount
            Text(
                buildString {
                    append("将下载整本")
                    if (v.jmChapterCount > 1) append(" ${v.jmChapterCount} 章")
                    if (n > 0) append("，共约 $n 页")
                    append("，打包成 ")
                    append(if (v.jmId.isNotEmpty()) "${v.jmId}.zip" else "zip")
                    append(" 存到下载目录")
                },
                style = MaterialTheme.typography.bodyMedium,
            )
        } else if (!v.isImage) {
            Text("清晰度", style = MaterialTheme.typography.labelLarge)
            Spacer(Modifier.height(6.dp))
            val items = v.qualities
            val current = items.getOrNull(qIndex.coerceIn(0, (items.size - 1).coerceAtLeast(0)))
            Box {
                OutlinedButton(
                    onClick = { if (items.size > 1) expanded = true },
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(GkDimens.rowCorner),
                ) {
                    Text(
                        current?.let {
                            buildString {
                                append(it.label)
                                val s = fmtSize(it.sizeBytes)
                                if (s.isNotEmpty()) append(" · $s")
                            }
                        } ?: "无可用清晰度",
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                    if (items.size > 1) {
                        Icon(Icons.Filled.ArrowDropDown, contentDescription = null)
                    }
                }
                DropdownMenu(
                    expanded = expanded,
                    onDismissRequest = { expanded = false },
                ) {
                    items.forEachIndexed { i, q ->
                        DropdownMenuItem(
                            text = {
                                Text(
                                    buildString {
                                        append(q.label)
                                        val s = fmtSize(q.sizeBytes)
                                        if (s.isNotEmpty()) append(" · $s")
                                        if (q.needMerge) append(" · 需合并")
                                    },
                                    maxLines = 2,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            },
                            onClick = {
                                vm.setQualityIndex(i)
                                expanded = false
                            },
                        )
                    }
                }
            }
        } else {
            Text(
                "将下载 ${v.images.size} 张图片并保存到相册",
                style = MaterialTheme.typography.bodyMedium,
            )
        }

        Spacer(Modifier.height(14.dp))
        OutlinedButton(
            onClick = { showPreview = true },
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(GkDimens.rowCorner),
        ) { Text(if (v.isImage) "预览图片并选择下载" else "下载前预览视频") }
        Spacer(Modifier.height(8.dp))
        Button(
            onClick = { vm.download(activity) },
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(GkDimens.rowCorner),
        ) {
            Icon(
                Icons.Filled.Download,
                contentDescription = null,
                modifier = Modifier.size(18.dp),
            )
            Spacer(Modifier.width(6.dp))
            Text(
                when {
                    v.platform == Platform.JMCOMIC -> "下载整本"
                    v.isImage -> "下载全部图片"
                    else -> "下载视频"
                },
            )
        }
    }

    if (showPreview) {
        BeforeDownloadDialog(v, qIndex, vm, activity, onClose = { showPreview = false })
    }
}
