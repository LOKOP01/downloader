package com.xd.vdl.core.net

import android.webkit.CookieManager
import com.xd.vdl.core.Platform
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit

/**
 * 进程内会话 Cookie 罐。
 *
 * **为什么必须有它（1.0.30 修）**：抖音对「冷会话」（一个 Cookie 都没有的首次请求）
 * 固定只回**空壳页** —— HTML 里 `_ROUTER_DATA` 在，但只有 1399 字节的
 * `ua/webId/itemId`，**没有任何视频地址**，而且不给任何错误码。
 * 实测（同一 IP、同一 UA、同一作品）：
 *
 * | 请求 | `_ROUTER_DATA` 长度 | 含作品数据 |
 * |---|---|---|
 * | 无 Cookie | 1399 / 1339 | 否 |
 * | 带 `ttwid`（第 1 次响应下发） | 8527 | 是 |
 *
 * 也就是说 `ttwid` 是服务端在**第一次响应里**下发的，会话内带上它之后请求才有效。
 * OkHttp 默认 `CookieJar.NO_COOKIES`（我们没设），所以原来每一次
 * `Http.getText` 都是独立冷会话 → 分享页/iteminfo/detail 三路永远拿不到数据。
 * 这里把响应里的 Cookie 留下来，下次请求自动带上。
 */
private object SessionCookieJar : CookieJar {
    private val store = ConcurrentHashMap<String, MutableList<Cookie>>()

    override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
        if (cookies.isEmpty()) return
        val key = url.host
        val list = store.getOrPut(key) { mutableListOf() }
        synchronized(list) {
            cookies.forEach { c ->
                list.removeAll { it.name == c.name && it.path == c.path }
                list.add(c)
            }
            // 会话 Cookie 会过期，条目无限增长只是浪费内存；留一个上限即可
            while (list.size > 40) list.removeAt(0)
        }
    }

    override fun loadForRequest(url: HttpUrl): List<Cookie> {
        val list = store[url.host] ?: return emptyList()
        val now = System.currentTimeMillis()
        return synchronized(list) {
            list.removeAll { it.expiresAt < now }
            list.toList()
        }
    }

    /** 解析失败重来时清掉（换作品 / 换账号场景） */
    fun clear() = store.clear()
}

/** 统一的网络出口：UA / Referer / Cookie 都在这里补，避免到处手写。 */
object Http {

    const val UA_DESKTOP =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    const val UA_MOBILE =
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"

    /** 抖音移动端接口要求的 iPhone UA（桌面版实测有效） */
    const val UA_IPHONE =
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 " +
            "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"

    /**
     * 基础 client（无代理）。**对外一律用 [client] / [downloadClient]**，
     * 那两个才会套上用户配置的代理。
     */
    private val baseClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .followRedirects(true)
        .followSslRedirects(true)
        .cookieJar(SessionCookieJar)
        // 必须在 BridgeInterceptor 之后恢复登录态：非空 jar 会覆盖手动 Cookie 头。
        .addNetworkInterceptor(PreserveLoginCookieInterceptor())
        .build()

    /**
     * 应用上下文。代理设置要读 SharedPreferences，必须有 Context；
     * 用 `ApplicationProvider` 不行（多进程/单元测试下会炸），
     * 所以在 [com.xd.vdl.core.net.Net.init] 里显式注入。
     */
    @Volatile
    private var appContext: android.content.Context? = null

    fun attachContext(ctx: android.content.Context) {
        appContext = ctx.applicationContext
        ProxySettings.init(ctx)
        // WebView 用 Chromium 自己的网络栈，不读 OkHttp 的代理 —— 必须单独设
        WebViewProxy.apply(ctx)
        // 换 client（代理配置可能变了）
        cachedClient = null
        cachedDownload = null
    }

    private fun invalidateClients() {
        cachedClient = null
        cachedDownload = null
    }

    @Volatile
    private var cachedClient: OkHttpClient? = null

    @Volatile
    private var cachedDownload: OkHttpClient? = null

    /** 带代理的解析/接口用client */
    val client: OkHttpClient
        get() = cachedClient ?: synchronized(this) {
            cachedClient ?: applyProxy(baseClient).also { cachedClient = it }
        }

    /** 下载专用：建连 10s 判死坏 CDN（B 站 PCDN），读 30s 无字节即放弃 */
    val downloadClient: OkHttpClient
        get() = cachedDownload ?: synchronized(this) {
            cachedDownload ?: applyProxy(
                baseClient.newBuilder()
                    .connectTimeout(10, TimeUnit.SECONDS)
                    .readTimeout(30, TimeUnit.SECONDS)
                    .writeTimeout(30, TimeUnit.SECONDS)
                    .build()
            ).also { cachedDownload = it }
        }

    private fun applyProxy(base: OkHttpClient): OkHttpClient {
        val ctx = appContext ?: return base
        return ProxySettings.wrap(ctx, base)
    }

    /** 设置页改完代理后调用，让后续请求用新client */
    fun onProxyChanged(ctx: android.content.Context? = null) {
        ProxySettings.invalidate()
        invalidateClients()
        appContext?.let { WebViewProxy.apply(it) }
    }

    /**
     * 取某平台要用的 Cookie：用户在设置页粘的那份（[CookieStore]）优先，
     * 没有再退到 WebView 的 CookieManager（旧版本留下的数据还能继续用）。
     */
    fun cookieFor(platform: Platform): String {
        if (platform == Platform.UNKNOWN) return ""
        val saved = CookieStore.get(platform)
        if (saved.isNotEmpty()) return saved
        return runCatching { CookieManager.getInstance().getCookie(platform.cookieUrl) }
            .getOrNull().orEmpty()
    }

    /**
     * 实际发请求时用的 Cookie：把 [SessionCookieJar] 里**属于该请求 host** 的
     * 会话 Cookie（ttwid 之类）并到用户 Cookie 后面。
     *
     * **必须显式并**，不能指望 CookieJar 自动生效：`request()` / `call()` 都是
     * 手动 `header("Cookie", …)`。注意 OkHttp 在 CookieJar 非空时会覆盖整个 Cookie
     * 头，因此这里只是构建阶段的合并；真正发送前由 PreserveLoginCookieInterceptor
     * 在网络层补回用户凭据（1.0.43 修复），同时保留会话 Cookie。
     *
     * 按 host 而不是按平台合并：抖音的 `ttwid` 是下发给 `*.iesdouyin.com` 的，
     * 与 `www.douyin.com` 上那个同名 Cookie 不是同一个。
     */
    private fun cookieForRequest(platform: Platform, url: HttpUrl): String {
        val base = cookieFor(platform)
        val jar = SessionCookieJar.loadForRequest(url)
        if (jar.isEmpty()) return base
        val merged = LinkedHashMap<String, String>()
        CookieStore.split(base).forEach { (k, v) -> merged[k] = v }
        jar.forEach { if (!merged.containsKey(it.name)) merged[it.name] = it.value }
        return merged.entries.joinToString("; ") { "${it.key}=${it.value}" }
    }

    /** 判断是否已登录：命中平台的登录态关键 Cookie 才算是真登录 */
    fun isLoggedIn(platform: Platform): Boolean {
        val ck = cookieFor(platform)
        if (ck.isEmpty()) return false
        val keys = CookieStore.authKeysOf(platform)
        if (keys.isEmpty()) return true
        return keys.any { CookieStore.has(ck, it) }
    }

    fun request(url: String, platform: Platform, mobile: Boolean = false): Request {
        val target = url.toHttpUrlOrNull() ?: throw IOException("非法地址：$url")
        val b = Request.Builder()
            .url(target)
            .header("User-Agent", if (mobile) UA_MOBILE else UA_DESKTOP)
            .header("Accept", "*/*")
            .header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
        val ref = platform.referer
        if (ref.isNotEmpty()) b.header("Referer", ref)
        val ck = cookieForRequest(platform, target)
        if (ck.isNotEmpty()) b.header("Cookie", ck)
        return b.build()
    }

    /** 发一个 GET 并返回响应体字符串（用于公开接口，如 B站） */
    fun getText(url: String, platform: Platform, mobile: Boolean = false): String {
        client.newCall(request(url, platform, mobile)).execute().use { resp ->
            if (!resp.isSuccessful) {
                throw java.io.IOException("HTTP ${resp.code}")
            }
            return resp.body?.string().orEmpty()
        }
    }

    fun getJson(url: String, platform: Platform, mobile: Boolean = false): JSONObject =
        JSONObject(getText(url, platform, mobile))

    /** 跟随重定向拿到最终 URL（v.douyin.com / b23.tv 短链） */
    fun resolveFinalUrl(url: String, platform: Platform, mobile: Boolean = true): String {
        return runCatching {
            client.newCall(request(url, platform, mobile)).execute().use { resp ->
                resp.request.url.toString()
            }
        }.getOrDefault(url)
    }

    // ---- 指定 UA 的重载（抖音接口要用 iPhone UA） ----

    fun request(url: String, platform: Platform, ua: String): Request {
        val target = url.toHttpUrlOrNull() ?: throw IOException("非法地址：$url")
        val b = Request.Builder()
            .url(target)
            .header("User-Agent", ua)
            .header("Accept", "*/*")
            .header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
        val ref = platform.referer
        if (ref.isNotEmpty()) b.header("Referer", ref)
        val ck = cookieForRequest(platform, target)
        if (ck.isNotEmpty()) b.header("Cookie", ck)
        return b.build()
    }

    fun getText(url: String, platform: Platform, ua: String): String {
        client.newCall(request(url, platform, ua)).execute().use { resp ->
            if (!resp.isSuccessful) throw java.io.IOException("HTTP ${resp.code}")
            return resp.body?.string().orEmpty()
        }
    }

    fun getJson(url: String, platform: Platform, ua: String): JSONObject =
        JSONObject(getText(url, platform, ua))

    fun resolveFinalUrl(url: String, platform: Platform, ua: String): String =
        runCatching {
            client.newCall(request(url, platform, ua)).execute().use { resp ->
                resp.request.url.toString()
            }
        }.getOrDefault(url)

    // ---- 需要自定义头 / 查询参数、并且要拿到状态码的请求 ----

    /**
     * 带状态码的响应。X 的 GraphQL 要靠 400/404 判断 queryId 是否过期，
     * 所以这里不抛异常，把判断交给调用方。
     */
    data class Resp(val code: Int, val body: String) {
        val ok: Boolean get() = code in 200..299
    }

    /**
     * 通用请求。`extraHeaders` 用来补 X 的 `authorization` / `x-csrf-token`，
     * `params` 拼查询串，`post` 时发一个空 JSON 体（guest/activate.json 要 POST）。
     * Cookie 与 Referer 仍按平台自动补。
     */
    fun call(
        url: String,
        platform: Platform,
        ua: String = UA_DESKTOP,
        extraHeaders: Map<String, String> = emptyMap(),
        params: Map<String, String> = emptyMap(),
        post: Boolean = false,
    ): Resp {
        val base = url.toHttpUrlOrNull() ?: throw IOException("非法地址：$url")
        val target = base.newBuilder().apply {
            params.forEach { (k, v) -> addQueryParameter(k, v) }
        }.build()

        val b = Request.Builder()
            .url(target)
            .header("User-Agent", ua)
            .header("Accept", "*/*")
            .header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
        val ref = platform.referer
        if (ref.isNotEmpty()) b.header("Referer", ref)
        val ck = cookieForRequest(platform, target)
        if (ck.isNotEmpty()) b.header("Cookie", ck)
        extraHeaders.forEach { (k, v) -> if (v.isNotEmpty()) b.header(k, v) }

        val req = if (post) {
            b.post(ByteArray(0).toRequestBody("application/json".toMediaTypeOrNull())).build()
        } else {
            b.get().build()
        }
        client.newCall(req).execute().use { r ->
            return Resp(r.code, r.body?.string().orEmpty())
        }
    }

    /** HEAD 取 Content-Length（X 用它把"码率×时长"的估算换成精确字节）；拿不到返回 -1 */
    fun contentLength(url: String, platform: Platform, ua: String = UA_DESKTOP): Long =
        runCatching {
            val target = url.toHttpUrlOrNull() ?: return -1L
            val req = Request.Builder()
                .url(target)
                .head()
                .header("User-Agent", ua)
            val ref = platform.referer
            if (ref.isNotEmpty()) req.header("Referer", ref)
            val ck = cookieForRequest(platform, target)
            if (ck.isNotEmpty()) req.header("Cookie", ck)
            client.newCall(req.build()).execute().use { r ->
                if (r.isSuccessful) r.header("Content-Length")?.toLongOrNull() ?: -1L else -1L
            }
        }.getOrDefault(-1L)
}
