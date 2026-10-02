package com.xd.vdl.core.parse

import com.xd.vdl.core.AppLog
import com.xd.vdl.core.net.Http
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.IOException
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.spec.SecretKeySpec
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

/**
 * 禁漫移动端 API 客户端（移植自 Python `jmcomic` 库 2.7.7 的 `JmApiClient`）。
 *
 * 为什么要自己实现：PC 端用 `jmcomic` 这个库，安卓没有等价的 Kotlin 库，
 * 所以把它的协议照搬过来。核心就三件事：
 *
 *  1. **请求签名**：每个请求头要带 `token` = md5(时间戳 + 密钥)、
 *     `tokenparam` = `时间戳,版本号`。
 *  2. **响应解密**：接口返回 `{"code":200,"data":"<base64>"}`，
 *     data 是 **AES-ECB**（密钥 = md5(时间戳 + 密钥) 的 utf8 字节）解出来的 JSON。
 *  3. **图片切片**：原图是按横条切块后**乱序**拼进 jpg 的，必须按算法重排
 *     （见 [JmScramble]），否则下下来是花屏。
 *
 * 注意 `/chapter_view_template` 这个接口用的是**另一个密钥**（`18comicAPPContent`），
 * 用主密钥会 403 —— 这是 jmcomic 源码里特别标注的坑。
 */
object JmClient {

    /** 主密钥（移动端 API 签名 + 响应解密） */
    private const val SECRET = "185Hcomic3PAPP7R"

    /** `/chapter_view_template` 专用密钥 */
    private const val SECRET_SCRAMBLE = "18comicAPPContent"

    private const val APP_VERSION = "2.1.7"

    /** 移动端 UA（禁漫要求必须像 App 请求） */
    private const val UA_APP =
        "Mozilla/5.0 (Linux; Android 9; V1938CT Build/PQ3A.190705.11211812; wv) " +
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 " +
            "Chrome/91.0.4472.114 Safari/537.36"

    /** API 备用域名；第一个通了就缓存下来，后续直接用 */
    private val DOMAINS = listOf(
        "www.cdnhjk.net",
        "www.cdngwc.cc",
        "www.cdngwc.net",
        "www.cdngwc.club",
    )

    @Volatile
    private var liveDomain = ""

    /**
     * 禁漫接口是自签名/非常规证书，官方 App 与 Python 版 jmcomic（`verify=False`）
     * 都不校验。这里同样放行，否则真机上大概率连不上。
     *
     * 只在禁漫自己的 client 上放宽，不影响其它平台（各平台有自己的 client）。
     */
    private val x509: X509TrustManager = object : X509TrustManager {
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    private val hostnameVerifier = HostnameVerifier { _, _ -> true }

    private val client: OkHttpClient by lazy {
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(null, arrayOf<TrustManager>(x509), java.security.SecureRandom())
        OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .followRedirects(true)
            .sslSocketFactory(ctx.socketFactory, x509)
            .hostnameVerifier(hostnameVerifier)
            .build()
    }

    // ------------------------------------------------------------------ //
    // 加解密
    // ------------------------------------------------------------------ //

    private fun md5Hex(s: String): String {
        val d = MessageDigest.getInstance("MD5").digest(s.toByteArray(Charsets.UTF_8))
        return d.joinToString("") { "%02x".format(it) }
    }

    /** 响应体解密：base64 → AES-ECB → 去 padding → utf8 */
    internal fun decrypt(b64: String, ts: Long, secret: String = SECRET): String {
        val raw = android.util.Base64.decode(b64, android.util.Base64.DEFAULT)
        val key = md5Hex("$ts$secret").toByteArray(Charsets.UTF_8)
        val cipher = Cipher.getInstance("AES/ECB/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"))
        val out = cipher.doFinal(raw)
        // PKCS#5/7 padding：末字节就是填充长度
        val pad = if (out.isEmpty()) 0 else out[out.size - 1].toInt() and 0xFF
        val len = if (pad in 1..16 && pad <= out.size) out.size - pad else out.size
        return String(out, 0, len, Charsets.UTF_8)
    }

    /**
     * 签名头。
     *
     * ⚠️ **绝对不要手动加 `Accept-Encoding`**：OkHttp 只在「由它自己添加」这个头时才做
     * 透明解压；一旦我们手写 `gzip, deflate`，它就把**原始 gzip 字节**原样交回来，
     * `JSONObject(body)` 随即抛
     * `JSONException: Value ?? of type java.lang.String cannot be converted to JSONObject`
     * （那几个问号就是 gzip 魔数 `\x1f\x8b\x08\x00`）。
     * 之前踩过：现象像是「接口连不上 / 代理没开」，实际是解压没做。
     */
    private fun signHeaders(secret: String = SECRET): Pair<Map<String, String>, Long> {
        val ts = System.currentTimeMillis() / 1000
        return mapOf(
            "user-agent" to UA_APP,
            "token" to md5Hex("$ts$secret"),
            "tokenparam" to "$ts,$APP_VERSION",
        ) to ts
    }

    // ------------------------------------------------------------------ //
    // 请求
    // ------------------------------------------------------------------ //

    /**
     * 打一个 API 请求并解密 data。域名逐个试，通了记住。
     * 返回解密后的 [JSONObject]；全部域名都失败抛 [ParseException]。
     */
    internal fun apiGet(
        path: String,
        params: Map<String, String> = emptyMap(),
        secret: String = SECRET,
        decryptResponse: Boolean = true,
    ): Pair<String, Long> {
        val (headers, ts) = signHeaders(secret)
        val order = if (liveDomain.isNotEmpty()) {
            listOf(liveDomain) + DOMAINS.filter { it != liveDomain }
        } else DOMAINS

        var lastErr = "无可用域名"
        for (dom in order) {
            val q = params.entries.joinToString("&") { (k, v) -> "$k=${urlEnc(v)}" }
            val url = "https://$dom$path" + if (q.isEmpty()) "" else "?$q"
            try {
                val b = Request.Builder().url(url)
                headers.forEach { (k, v) -> b.header(k, v) }
                val r = client.newCall(b.build()).execute()
                val body = r.use { it.body?.string().orEmpty() }
                if (!r.isSuccessful) {
                    lastErr = "HTTP ${r.code}"
                    throw IOException(lastErr)
                }
                if (!decryptResponse) {
                    liveDomain = dom
                    return body to ts
                }
                // 非 JSON 一律当「这个域名坏了」跳过（实测 www.cdngwc.club 会回
                // HTTP 200 + HTML 的 "Could not connect to mysql!"），不要当成代理问题
                val json = try {
                    JSONObject(body)
                } catch (je: org.json.JSONException) {
                    lastErr = "返回的不是 JSON（${body.take(40).replace(Regex("[\\p{Cntrl}]"), "")}）"
                    throw IOException(lastErr, je)
                }
                val code = json.optInt("code")
                if (code != 200) {
                    lastErr = "接口返回 code=$code"
                    throw IOException(lastErr)
                }
                val data = json.optString("data")
                if (data.isEmpty()) {
                    lastErr = "接口无 data（可能本子不存在）"
                    throw IOException(lastErr)
                }
                liveDomain = dom
                return decrypt(data, ts, secret) to ts
            } catch (e: Exception) {
                if (e is ParseException) throw e
                lastErr = "${e.javaClass.simpleName}: ${e.message}"
                AppLog.w("禁漫域名 $dom 失败：$lastErr")
            }
        }
        // 分两类报错，别再一律写「需要代理」——上次就是这句话把「解压没做」误导成「代理没开」
        val connLike = CONN_MARKERS.any { lastErr.contains(it, ignoreCase = true) }
        throw ParseException(
            if (connLike) {
                "禁漫接口连不上（$lastErr）。禁漫需要代理才能访问，请确认代理已开启"
            } else {
                "禁漫接口响应异常（$lastErr）"
            },
        )
    }

    /** 真正的连接类错误特征；只有命中这些才提示「需要代理」 */
    private val CONN_MARKERS = listOf(
        "UnknownHost", "ConnectException", "SocketTimeout", "timeout",
        "SSLHandshake", "SSLException", "StreamReset", "ConnectionShutdown",
        "ProtocolException", "EOFException", "Connection reset",
    )

    private fun urlEnc(s: String): String =
        java.net.URLEncoder.encode(s, "UTF-8")

    /** 取 scramble_id：这个接口返回 HTML，用另一个密钥，且**不解密** */
    internal fun fetchScrambleId(photoId: String): String {
        val body = apiGet(
            "/chapter_view_template",
            params = mapOf(
                "id" to photoId,
                "mode" to "vertical",
                "page" to "0",
                "app_img_shunt" to "1",
                "express" to "off",
                "v" to (System.currentTimeMillis() / 1000).toString(),
            ),
            secret = SECRET_SCRAMBLE,
            decryptResponse = false,
        ).first
        val m = Regex("var scramble_id = (\\d+);").find(body)
        if (m == null) {
            AppLog.w("禁漫未匹配到 scramble_id，回退默认值 220980")
            return "220980"
        }
        return m.groupValues[1]
    }

    // ------------------------------------------------------------------ //
    // 图片
    // ------------------------------------------------------------------ //

    /** 移动端图片 CDN 域名 */
    private val IMG_DOMAINS = listOf(
        "cdn-msp.jmapiproxy1.cc",
        "cdn-msp.jmapiproxy2.cc",
        "cdn-msp2.jmapiproxy2.cc",
        "cdn-msp3.jmapiproxy2.cc",
        "cdn-msp.jmapinodeudzn.net",
        "cdn-msp3.jmapinodeudzn.net",
    )

    /** 使用 /chapter 返回的原始文件名，保证请求的页面与切片散列使用的文件名一致。 */
    internal fun imageUrl(photoId: String, filename: String, domain: String): String =
        "https://$domain/media/photos/$photoId/$filename"

    internal fun imageDomains(): List<String> = IMG_DOMAINS

    /** 图片 CDN 的域名特征，供界面层判断「这张图要不要走禁漫 client」 */
    internal fun isJmImageHost(url: String): Boolean =
        IMG_DOMAINS.any { url.contains(it, ignoreCase = true) } ||
            url.contains("jmapiproxy", ignoreCase = true) ||
            url.contains("jmapinode", ignoreCase = true)

    /** 给界面层用的图片字节抓取（封面等），自带禁漫 UA / Referer / 宽松 SSL */
    internal fun fetchImageBytes(url: String): ByteArray? = runCatching {
        fetchImage(url)
    }.getOrNull()

    /**
     * 下载一张原图（**已解码**）。禁漫图片按横条切块乱序存放，直接保存会花屏，
     * 所以要取回字节后交给 [JmScramble] 重排。
     */
    internal fun fetchImage(url: String): ByteArray {
        val b = Request.Builder().url(url).header("User-Agent", UA_APP)
        b.header("Accept", "image/avif,image/webp,image/apng,image/*,*/*;q=0.8")
        b.header("X-Requested-With", "com.JMComic3.app")
        b.header("Referer", "https://${DOMAINS.first()}/")
        val r = client.newCall(b.build()).execute()
        val bytes = r.use { resp ->
            if (!resp.isSuccessful) throw IOException("HTTP ${resp.code}")
            resp.body?.bytes() ?: throw IOException("图片响应为空")
        }
        return bytes
    }
}
