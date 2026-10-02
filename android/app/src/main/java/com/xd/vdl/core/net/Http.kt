package com.xd.vdl.core.net

import android.webkit.CookieManager
import com.xd.vdl.core.Platform
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

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

    val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .followRedirects(true)
        .followSslRedirects(true)
        .build()

    /** 下载专用：建连 10s 判死坏 CDN（B 站 PCDN），读 30s 无字节即放弃 */
    val downloadClient: OkHttpClient = client.newBuilder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    /**
     * 取某平台要用的 Cookie：优先用户在设置页粘贴保存的那份（[CookieStore]），
     * 没有再退到 WebView 的 CookieManager（旧版本留下的数据还能继续用）。
     */
    fun cookieFor(platform: Platform): String {
        if (platform == Platform.UNKNOWN) return ""
        val saved = CookieStore.get(platform)
        if (saved.isNotEmpty()) return saved
        return runCatching { CookieManager.getInstance().getCookie(platform.cookieUrl) }
            .getOrNull().orEmpty()
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
        val b = Request.Builder()
            .url(url)
            .header("User-Agent", if (mobile) UA_MOBILE else UA_DESKTOP)
            .header("Accept", "*/*")
            .header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
        val ref = platform.referer
        if (ref.isNotEmpty()) b.header("Referer", ref)
        val ck = cookieFor(platform)
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
        val b = Request.Builder()
            .url(url)
            .header("User-Agent", ua)
            .header("Accept", "*/*")
            .header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
        val ref = platform.referer
        if (ref.isNotEmpty()) b.header("Referer", ref)
        val ck = cookieFor(platform)
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
        val ck = cookieFor(platform)
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
            val req = Request.Builder()
                .url(url)
                .head()
                .header("User-Agent", ua)
            val ref = platform.referer
            if (ref.isNotEmpty()) req.header("Referer", ref)
            client.newCall(req.build()).execute().use { r ->
                if (r.isSuccessful) r.header("Content-Length")?.toLongOrNull() ?: -1L else -1L
            }
        }.getOrDefault(-1L)
}
