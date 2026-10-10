package com.xd.vdl.ui.component

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * 界面尺寸常量。集中在一处，避免各页面各写各的圆角与间距。
 *
 * 数值参考 gkd 那套 Material 3 观感：大圆角 flat 卡片、宽松留白、
 * 卡片之间靠底色差分层而不是靠阴影。
 */
object GkDimens {
    /** 页面左右留白 */
    val screenPadding = 16.dp

    /** 卡片之间的竖直间距 */
    val cardGap = 10.dp

    /** 卡片内部留白 */
    val cardPadding = 16.dp

    /** 大卡片圆角 */
    val cardCorner = 20.dp

    /** 小控件（输入框、行内卡片）圆角 */
    val rowCorner = 16.dp

    /** 圆形图标底座的直径 */
    val badge = 40.dp
}

/**
 * 圆角 flat 卡片 —— gkd 每屏内容的容器。
 *
 * 刻意不用 `Card`：`Card` 的层次靠 elevation 阴影，深色下几乎看不见，
 * 浅色下也显得「浮」。这里改用 `surfaceContainerLow` 填充色与页面底色拉开
 * 层次，全局观感更平、更整齐（切换壁纸/深色模式时也不会跑偏）。
 */
@Composable
fun GkCard(
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
    contentPadding: Dp = GkDimens.cardPadding,
    content: @Composable ColumnScope.() -> Unit,
) {
    val shape = RoundedCornerShape(GkDimens.cardCorner)
    val color = MaterialTheme.colorScheme.surfaceContainerLow
    val base = modifier.fillMaxWidth()

    if (onClick != null) {
        Surface(
            onClick = onClick,
            shape = shape,
            color = color,
            modifier = base,
        ) {
            Column(Modifier.padding(contentPadding), content = content)
        }
    } else {
        Surface(shape = shape, color = color, modifier = base) {
            Column(Modifier.padding(contentPadding), content = content)
        }
    }
}

/**
 * 圆形浅色底图标 —— 卡片左侧那个视觉锚点。
 *
 * 单独一个彩色图标会很突兀，垫一层 `primaryContainer` 圆底后既能压住视线，
 * 又能在深色/动态取色下自动跟着主色走。
 */
@Composable
fun GkIconBadge(
    icon: ImageVector,
    modifier: Modifier = Modifier,
    size: Dp = GkDimens.badge,
    container: Color = MaterialTheme.colorScheme.primaryContainer,
    contentColor: Color = MaterialTheme.colorScheme.onPrimaryContainer,
) {
    Box(
        modifier = modifier
            .size(size)
            .clip(CircleShape)
            .background(container),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            icon,
            contentDescription = null,
            tint = contentColor,
            modifier = Modifier.size(size * 0.52f),
        )
    }
}

/**
 * 圆形浅色底 + 单字 —— 没有合适矢量图标时（如平台名）的替代方案。
 * 视觉规格与 [GkIconBadge] 保持一致，两者可以并排出现。
 */
@Composable
fun GkTextBadge(
    text: String,
    modifier: Modifier = Modifier,
    size: Dp = 36.dp,
    container: Color = MaterialTheme.colorScheme.primaryContainer,
    contentColor: Color = MaterialTheme.colorScheme.onPrimaryContainer,
) {
    Box(
        modifier = modifier
            .size(size)
            .clip(CircleShape)
            .background(container),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text,
            style = MaterialTheme.typography.labelLarge,
            fontWeight = FontWeight.SemiBold,
            color = contentColor,
            maxLines = 1,
        )
    }
}

/**
 * 页面大标题。gkd 的页面顶部都是「左对齐大标题 + 内容」，没有 AppBar 底板，
 * 标题与内容同底色、靠字号和字重区分层级。
 */
@Composable
fun GkPageTitle(
    text: String,
    modifier: Modifier = Modifier,
) {
    Text(
        text,
        style = MaterialTheme.typography.headlineMedium,
        fontWeight = FontWeight.Bold,
        modifier = modifier.padding(start = 4.dp, top = 4.dp, bottom = 2.dp),
    )
}

/** 设置页的分组小标题（常规 / 登录 / 下载位置 …），用主色弱化呈现 */
@Composable
fun GkSectionLabel(
    text: String,
    modifier: Modifier = Modifier,
) {
    Text(
        text,
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.primary,
        modifier = modifier.padding(start = 6.dp, top = 4.dp, bottom = 4.dp),
    )
}
