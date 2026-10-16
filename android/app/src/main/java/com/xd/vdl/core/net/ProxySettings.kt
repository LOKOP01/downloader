package com.xd.vdl.core.net

import android.content.Context
import android.content.SharedPreferences
import okhttp3.Authenticator
import okhttp3.Credentials
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.Route
import java.net.InetSocketAddress
import java.net.Proxy
import java.net.ProxySelector
import java.net.URI

/**
 * 网络代理设置（1.0.30 新增）。
 *
 * **为什么必须有**：X（`api.x.com` / `cdn.syndication.twimg.com` / `x.com`）
 * 在境内直连基本不可达，表现为三条链路（GraphQL / syndication / WebView）
 * 一起挂掉，日志里是三行 `connection closed` / `页面没发出视频请求`。
 * 桌面版靠读 Windows 系统代理解决；安卓端此前**完全没有代理入口**，
 * 于是「X 解析不出来」在境内是必然结果，而不是接口变更。
 *
 * 填 `http://127.0.0.1:7897` 这类地址即可（Clash / v2rayN / Shadowsocks 的
 * 本地混合端口）。留空 = 直连。
 *
 * **不读系统代理**：Android 的 `ProxySelector.getDefault()` 在多数国产 ROM 上
 * 返回 `DIRECT`，读了等于没读；真开了全局代理的机器，其系统 DNS/路由本来就能用，
 * 不需要在这里重复配置。用户显式填地址反而是最可预期、可排查的做法。
 */
object ProxySettings {

    private const val PREFS = "vdl_net"
    private const val KEY_PROXY = "proxy_url"

    private var prefs: SharedPreferences? = null

    /**
     * 已包装过的 client 缓存。
     *
     * ⚠️ 键必须含 **base client 身份**（N08）：原来只按代理地址缓存，
     * 于是「解析用 client（20s/60s）」与「下载用 client（10s/30s）」会互相拿到
     * 对方那份配置 —— 先访问哪一个，另一个就继承了它的超时。
     * 现在按 base 分组、组内再按地址缓存。
     */
    private val wrapped = java.util.Collections.synchronizedMap(
        java.util.WeakHashMap<OkHttpClient, MutableMap<String, OkHttpClient>>()
    )

    /**
     * 代理配置版本号。每次 [invalidate] 递增。
     *
     * 供**自己缓存 OkHttpClient 的模块**（如 JmClient 的 `by lazy`）判断
     * 是否需要重建：它们拿不到 [Http.invalidateClients] 的通知，
     * 只看缓存的话，用户改完代理后禁漫仍会一直用旧配置（B-03 的残留缺口）。
     */
    @Volatile
    var revision: Int = 0
        private set

    fun init(context: Context) {
        if (prefs == null) {
            prefs = context.applicationContext
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        }
    }

    /** 用户填的原始字符串（设置页回显用） */
    fun raw(context: Context): String {
        init(context)
        return prefs?.getString(KEY_PROXY, "").orEmpty()
    }

    fun set(context: Context, value: String) {
        init(context)
        prefs?.edit()?.putString(KEY_PROXY, value.trim())?.apply()
        invalidate()
    }

    fun invalidate() {
        synchronized(wrapped) { wrapped.clear() }
        revision++
    }

    /**
     * 规范化用户填的地址：补协议头、拆出账号密码。
     * 返回 null =「不用代理」或「填得不对」。带账号密码时 URL 里的 userinfo
     * 必须剥掉 —— 否则 okhttp 会把 `user:pass@` 当主机名的一部分去解析。
     */
    private fun parse(value: String): Pair<HttpUrl, Pair<String, String>?>? {
        if (value.isEmpty()) return null
        val withScheme = if (value.contains("://")) value else "http://$value"
        val url = withScheme.toHttpUrlOrNull() ?: return null
        val auth = if (url.username.isNotEmpty()) url.username to url.password else null
        val clean = if (auth != null) url.newBuilder().username("").password("").build() else url
        return clean to auth
    }

    /** 填了但格式不对（界面据此提示；此时按直连走） */
    fun invalid(context: Context): Boolean {
        val v = raw(context)
        return v.isNotEmpty() && parse(v) == null
    }

    /**
     * 给 [base] 套上代理，返回**新** client（不改传入对象）。
     * 没配 / 配错时原样返回 —— 配错时按直连走，总比整个应用发不出请求好。
     */
    fun wrap(context: Context, base: OkHttpClient): OkHttpClient {
        init(context)
        return wrapWith(raw(context), base)
    }

    /**
     * 用**已初始化**的配置包装任意 client（不需要 Context）。
     *
     * 供 [com.xd.vdl.core.parse.JmClient] 这类自己持有 OkHttpClient 的模块使用：
     * 它们此前完全绕过了应用代理设置（B-03），用户填了代理也走直连。
     */
    fun wrapInitialized(base: OkHttpClient): OkHttpClient =
        wrapWith(prefs?.getString(KEY_PROXY, "").orEmpty(), base)

    private fun wrapWith(value: String, base: OkHttpClient): OkHttpClient {
        if (value.isEmpty()) return base

        synchronized(wrapped) {
            wrapped[base]?.get(value)?.let { return it }
        }

        val parsed = parse(value) ?: return base
        val (url, auth) = parsed
        val port = if (url.port == 0) defaultPort(url.scheme) else url.port

        val b = base.newBuilder()
            // 常量代理选择器：这个应用所有请求都走同一个本地代理。
            // 用 createUnresolved：走代理时主机名由代理端解析，
            // 本地做一次 DNS 只会平白多一次失败可能。
            .proxySelector(FixedProxySelector(InetSocketAddress.createUnresolved(url.host, port)))
        if (auth != null) {
            b.proxyAuthenticator(object : Authenticator {
                override fun authenticate(route: Route?, response: Response): Request? {
                    // 只有 407 才补凭证；带上还失败就别再试了，否则死循环
                    if (response.code != 407) return null
                    if (response.request.header("Proxy-Authorization") != null) return null
                    return response.request.newBuilder()
                        .header(
                            "Proxy-Authorization",
                            Credentials.basic(auth.first, auth.second),
                        )
                        .build()
                }
            })
        }
        val built = b.build()
        synchronized(wrapped) {
            wrapped.getOrPut(base) { mutableMapOf() }[value] = built
        }
        return built
    }

    private fun defaultPort(scheme: String): Int = if (scheme == "https") 443 else 80
}
/**
 * 固定代理选择器：这个应用所有请求都走同一个本地代理。
 *
 * 不用 `ProxySelector.of(...)`：它在部分 OkHttp/Android 组合下会被系统的
 * 默认选择器覆盖（应用 VPN 分流规则），而我们要的是「用户填的地址优先」。
 */
private class FixedProxySelector(
    private val addr: InetSocketAddress,
) : ProxySelector() {
    private val proxy = Proxy(Proxy.Type.HTTP, addr)

    override fun select(uri: URI): List<Proxy> = listOf(proxy)

    override fun connectFailed(
        uri: URI?, sa: java.net.SocketAddress?, ioe: java.io.IOException?,
    ) {
        // 单一代理，失败就失败，不做备用切换
    }
}
