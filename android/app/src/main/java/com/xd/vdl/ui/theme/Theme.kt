package com.xd.vdl.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

/**
 * Android 12 以下没有动态取色，用这套固定配色兜底。
 *
 * 12 及以上一律走系统动态取色（跟随壁纸），所以这里的色值只影响老机型。
 * `surfaceContainer*` 系列不在这里写死 —— 交给 Material 3 按 surface 派生，
 * 保证卡片底色与页面底色始终有层次（见 `component/GkCard.kt` 的用法）。
 */
private val LightColors = lightColorScheme(
    primary = Color(0xFF3B6FE0),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFDDE6FF),
    onPrimaryContainer = Color(0xFF0A2452),
    secondary = Color(0xFF5A6B8C),
    background = Color(0xFFF7F8FB),
    surface = Color(0xFFF7F8FB),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF8AB4FF),
    onPrimary = Color(0xFF0A1A33),
    primaryContainer = Color(0xFF23395F),
    onPrimaryContainer = Color(0xFFD6E3FF),
    secondary = Color(0xFFB7C4DE),
    background = Color(0xFF111318),
    surface = Color(0xFF111318),
)

/**
 * 应用主题。
 *
 * 默认开启动态取色（Android 12+），这是 gkd 那种「界面配色跟着系统壁纸走」的
 * 来源 —— 用户换壁纸，整套 UI 的主色、卡片底色、开关颜色都会跟着变。
 * 老系统上退回上面那套固定配色，观感不至于割裂。
 */
@Composable
fun VdlTheme(
    dark: Boolean = isSystemInDarkTheme(),
    dynamicColor: Boolean = true,
    content: @Composable () -> Unit,
) {
    val ctx = LocalContext.current
    val scheme = when {
        dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ->
            if (dark) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)

        dark -> DarkColors
        else -> LightColors
    }
    MaterialTheme(
        colorScheme = scheme,
        content = content,
    )
}
