package com.xd.vdl.core.net

import android.content.Context
import android.os.Build
import android.util.Log
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * 让**系统 WebView** 也走用户配置的代理。
 *
 * **为什么必须单独做**：X 的第三条兜底链路是「用真 WebView 加载 x.com 页面、
 * 拦截 `video.twimg.com` 的 mp4 请求」。而 WebView 用的是 Chromium 自己的网络栈，
 * **完全不读 OkHttp 的代理设置** —— 只改 [Http] 的话，前两条 HTTP 链路通了，
 * 第三条仍然连不上，日志里还是那句「页面没发出视频请求」。
 *
 * 做法：把代理写进 [android.net.ConnectivityManager] 的全局 `ProxyProperties`。
 * 这是 Chromium 在 Android 上读代理的唯一入口。
 *
 * **全程反射、不引用任何 android.net 类型**：`ProxyProperties` 与
 * `getGlobalProxy` 都是 @hide / API 30+，直接引用在本项目的
 * compileSdk=34 下也解析不出来（Android SDK 把它们从 publicapi 里剔了）。
 * 反射既能编过，也保留了「新系统加字段」时的兼容性。
 *
 * ⚠️ 已知限制：设全局代理属性需要 `ACCESS_NETWORK_STATE`（本应用已声明）；
 * 若设备已开 VPN 而本应用不在其分流的分应用列表里，本设置不生效 ——
 * 那是系统层行为，本应用改不了。
 */
object WebViewProxy {

    private const val TAG = "VdlProxy"

    /** 已应用的地址；重复设置同一值时跳过（setGlobalProxy 会重连所有网络栈） */
    private var applied: String? = null

    private data class HostPort(val host: String, val port: Int)

    fun apply(context: Context) {
        val value = ProxySettings.raw(context)
        val target = normalize(value)
        if (target?.let { "${it.host}:${it.port}" } == applied) return
        applied = target?.let { "${it.host}:${it.port}" }

        runCatching { applyInner(context, target) }
            .onFailure {
                // 设不了不是致命错：HTTP 链路仍可用，只是 WebView 兜底那条会不通
                Log.w(TAG, "设置 WebView 代理失败：${it.message}")
            }
    }

    private fun applyInner(context: Context, target: HostPort?) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            // Android 9 及更早：ProxyProperties 不存在，只能靠 cm.defaultProxy，
            // 而那个 setter 在新系统上已被移除。这里不处理 —— minSdk 24 里
            // Android 9 及以下占比极低，且此时 X 的 HTTP 两条链路仍可用。
            Log.i(TAG, "Android ${Build.VERSION.SDK_INT} 不支持全局代理属性，跳过 WebView 代理")
            return
        }
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE)
            ?: return
        val clazz = Class.forName("android.net.ConnectivityManager")

        val propsCls = Class.forName("android.net.ProxyProperties")
        val ctor = propsCls.getDeclaredConstructor(
            String::class.java, Int::class.javaPrimitiveType,
        )

        // 传 null = 恢复直连（Kotlin 里显式写成 Any? 再传给反射方法）
        val props: Any? = target?.let { ctor.newInstance(it.host, it.port) }

        val setter = clazz.getDeclaredMethod("setGlobalProxy", propsCls)
        setter.isAccessible = true
        setter.invoke(cm, props)
        Log.i(TAG, "WebView 全局代理已设为 ${target?.let { "${it.host}:${it.port}" } ?: "直连"}")
    }

    /** 与 [ProxySettings] 同样的解析口径：空串 / 非法 → null（= 不设代理） */
    private fun normalize(value: String): HostPort? {
        if (value.isBlank()) return null
        val withScheme = if (value.contains("://")) value else "http://$value"
        val url = withScheme.toHttpUrlOrNull() ?: return null
        val port = if (url.port == 0) (if (url.scheme == "https") 443 else 80) else url.port
        return HostPort(url.host, port)
    }
}