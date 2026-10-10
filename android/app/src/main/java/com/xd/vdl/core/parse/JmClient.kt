package com.xd.vdl.core.parse

import com.xd.vdl.core.AppLog
import com.xd.vdl.core.Platform
import com.xd.vdl.core.net.Http
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
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
                applyAuth(b)
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

    // ------------------------------------------------------------------ //
    // Cookie / 登录态
    // ------------------------------------------------------------------ //

    /**
     * 请求要带的 Cookie。
     *
     * 优先用**接口域登录**拿到的 AVS（那才是 `/album_download_2` 这类接口认的），
     * 没有才退回设置页里手填的那份 —— 手填的多半来自 `18comic.vip`，
     * 官方文档明确说过跨域名的 AVS 会「配了也没效果」（issue #104）。
     */
    private fun cookieHeader(): String {
        val fromLogin = JmSession.avs
        if (fromLogin.isNotEmpty()) return "AVS=$fromLogin"
        return runCatching { Http.cookieFor(Platform.JMCOMIC) }.getOrNull().orEmpty()
    }

    /** 头部值只能是可见 ASCII，从浏览器粘过来的常常混进别的字符 */
    private fun sanitize(v: String): String = v.filter { it.code in 0x20..0x7E }

    /**
     * 官方 App 就是这么发的：`Token` / `Tokenparam` 之外，再带
     * `Authorization: Bearer <jwttoken>` 与 `Cookie: AVS=<s>`，两个都来自 `/login`。
     */
    private fun applyAuth(b: Request.Builder) {
        val ck = sanitize(cookieHeader())
        if (ck.isNotEmpty()) b.header("Cookie", ck)
        val jwt = sanitize(JmSession.jwt)
        if (jwt.isNotEmpty()) b.header("Authorization", "Bearer $jwt")
    }

    // ------------------------------------------------------------------ //
    // 登录
    // ------------------------------------------------------------------ //

    /** `/login` 的返回：`s` 就是接口域的 AVS，`jwttoken` 是会员接口用的 JWT */
    internal data class LoginResult(
        val ok: Boolean,
        val jwt: String,
        val avs: String,
        val message: String,
    ) {
        companion object {
            /** 内层 JSON（已解密）→ LoginResult */
            fun fromData(innerJson: String): LoginResult {
                val data = runCatching { JSONObject(innerJson) }.getOrNull()
                    ?: return LoginResult(false, "", "", "登录返回的 data 不是 JSON：${innerJson.take(120)}")
                val jwt = data.optString("jwttoken")
                // AVS 就是 data.s
                val avs = data.optString("s")
                return if (jwt.isEmpty() && avs.isEmpty()) {
                    LoginResult(false, "", "", "登录返回里没有凭据（jwttoken/s 都为空）：${innerJson.take(120)}")
                } else {
                    LoginResult(true, jwt, avs, "")
                }
            }
        }
    }

    /**
     * 依次试两把密钥解密（与官方 App 的响应处理一模一样：它也是把两把密钥轮着试一遍，
     * 谁能解出合法 JSON 就用谁）。主密钥之外还有一把 `/chapter_view_template` 专用的。
     *
     * 解不出来返回空串。
     */
    internal fun decryptAny(b64: String, ts: Long): String {
        for (secret in listOf(SECRET, SECRET_SCRAMBLE)) {
            val text = runCatching { decrypt(b64, ts, secret) }.getOrNull() ?: continue
            if (runCatching { JSONObject(text) }.isSuccess) return text
        }
        return ""
    }

    /**
     * 拆 `/login` 的响应体。
     *
     * ⚠️ 这里踩过一次：**成功时 `data` 是加密过的内层 JSON 字符串**，不是对象 ——
     * 和其它接口同一套约定（AES-ECB，key = md5(时间戳 + 密钥)）。失败的响应才是
     * 明文（`code != 200`、`data` 是空数组、说明在 `errorMsg` 里）。
     * 第一版直接 `optJSONObject("data")`，拿到 null 就报「登录返回里没有 data」，
     * 把「要解密」误判成了「服务端没给数据」。
     *
     * 同时也兼容服务端万一直接给对象 / 给明文 JSON 的情况。
     *
     * [decryptor] 只为单测能注入 —— `android.util.Base64` 在 JVM 单测里是桩。
     */
    internal fun unwrapLoginBody(
        code: Int,
        body: String,
        ts: Long,
        decryptor: ((String, Long) -> String)? = null,
    ): LoginResult {
        val json = runCatching { JSONObject(body) }.getOrNull()
            ?: return LoginResult(false, "", "", "登录返回的不是 JSON：${body.take(120)}")
        if (code != 200) {
            val msg = json.optString("errorMsg").ifEmpty { "登录失败（HTTP $code）" }
            return LoginResult(false, "", "", msg)
        }
        val dec = decryptor ?: { b: String, t: Long -> decryptAny(b, t) }
        val raw = json.opt("data")
        val inner = when (raw) {
            is JSONObject -> raw.toString()
            is String -> if (raw.trimStart().startsWith("{")) raw else dec(raw, ts)
            else -> ""
        }
        if (inner.isEmpty()) {
            val shape = raw?.javaClass?.simpleName ?: "null"
            return LoginResult(false, "", "", "登录返回的 data 形态不认识（$shape）：${body.take(120)}")
        }
        return LoginResult.fromData(inner)
    }

    /**
     * 用禁漫账号登录接口域，拿到 `/album_download_2` 认的那套凭据。
     *
     * 字段名实测确认：`username` + `password`（用 `email` 会被回
     * 「用戶名和密碼字段不能留空！」）。只用账号密码，不碰 Cookie。
     */
    internal fun login(username: String, password: String): LoginResult {
        val (headers, ts) = signHeaders()
        var last = "无可用域名"
        for (dom in DOMAINS) {
            try {
                val form = okhttp3.FormBody.Builder()
                    .add("username", username)
                    .add("password", password)
                    .build()
                val b = Request.Builder()
                    .url("https://$dom/login")
                    .post(form)
                headers.forEach { (k, v) -> b.header(k, v) }
                val r = client.newCall(b.build()).execute()
                val body = r.use { it.body?.string().orEmpty() }
                val res = unwrapLoginBody(r.code, body, ts)
                if (res.ok) {
                    liveDomain = dom
                    return res
                }
                // 401 = 账号密码不对，换域名也没意义，直接把服务端的话带回去
                if (r.code == 401) return res
                last = res.message
            } catch (e: Exception) {  // noqa: BLE001
                last = "${e.javaClass.simpleName}: ${e.message}"
            }
        }
        return LoginResult(false, "", "", "登录请求失败：$last")
    }

    // ------------------------------------------------------------------ //
    // 官方「整本打包」直链
    // ------------------------------------------------------------------ //

    /**
     * 问服务端要「整本打包」的直链（返回结构见 [JmAlbumZip]）。
     *
     * 接口名出自官方 App（Capacitor + React）的 main.js：
     * `API_ALBUM_DOWNLOAD: "album_download_2"`，调用形如
     * `GET <api>/album_download_2/<album_id>`，返回 `{status, title, fileSize, img_url, download_url}`。
     *
     * 两个实测结论：
     *  1. 未登录时返回 `{"status":"0","msg":"請先登入"}` —— **接口本身是通的**，只是要登录态。
     *  2. 官方 App 页面上那个「算数验证码」是**前端本地生成**的（problem/answer 都在 JS 里
     *     当场算出来再比对），服务端既不签发也不校验，所以这里完全不用处理它。
     *
     * 失败（网络/未登录）返回 null，调用方回退逐张下载。
     */
    internal fun albumDownload(albumId: String): JmAlbumZip? = runCatching {
        // 官方 App 的 fetchGet 会给每个 GET 补一个 lang，这里也照做（保持与它完全一致）
        JmAlbumZip.parse(
            apiGet("/album_download_2/$albumId", params = mapOf("lang" to "TW")).first,
        )
    }.onFailure {
        AppLog.w("禁漫打包接口不可用：${it.javaClass.simpleName}: ${it.message}")
    }.getOrNull()

    // ------------------------------------------------------------------ //

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

    /**
     * 流式把 [url] 写到 [dest]，边写边回报 (已下载, 总长)。总长未知时为 0。
     *
     * 与 [fetchImage] 的区别是**不把整个响应读进内存**：整本打包的压缩包动辄几十 MB，
     * 而且需要进度，所以走流。
     *
     * [onProgress] 里可以抛 [kotlinx.coroutines.CancellationException] 来中止 ——
     * 阻塞读写里没法直接查协程状态，由调用方在回调里 `ensureActive()`。
     */
    internal fun downloadTo(url: String, dest: File, onProgress: (Long, Long) -> Unit): Long {
        return try {
            fetchTo(url, dest, onProgress, withReferer = true)
        } catch (e: IOException) {
            // 下载域（`dl*.cdnhjk.net`）跟图片 CDN 不一定是一套规则。官方 App 是浏览器式
            // 直接下载、本来就不带 Referer；万一带 Referer 被拒就原样再来一次。
            if (!e.message.orEmpty().startsWith("HTTP 403")) throw e
            AppLog.w("禁漫打包直链带 Referer 被拒（403），去掉 Referer 重试")
            fetchTo(url, dest, onProgress, withReferer = false)
        }
    }

    private fun fetchTo(
        url: String,
        dest: File,
        onProgress: (Long, Long) -> Unit,
        withReferer: Boolean,
    ): Long {
        val b = Request.Builder().url(url).header("User-Agent", UA_APP)
        b.header("Accept", "*/*")
        b.header("X-Requested-With", "com.JMComic3.app")
        if (withReferer) b.header("Referer", "https://${DOMAINS.first()}/")
        applyAuth(b)
        client.newCall(b.build()).execute().use { resp ->
            if (!resp.isSuccessful) throw IOException("HTTP ${resp.code}")
            val body = resp.body ?: throw IOException("响应为空")
            val total = body.contentLength()
            body.byteStream().use { ins ->
                dest.outputStream().buffered().use { out ->
                    val buf = ByteArray(64 * 1024)
                    var done = 0L
                    while (true) {
                        val n = ins.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        done += n
                        onProgress(done, if (total > 0) total else 0L)
                    }
                    return done
                }
            }
        }
    }

    /**
     * 诊断用：**原样**打一次 `/album_download_2/<id>`，把服务端回复的原文交出来。
     * 设置页的「测试打包直链」用它 —— 解析成功与否都无所谓，要看的是服务端原话。
     */
    internal fun albumDownloadRaw(albumId: String): String = runCatching {
        apiGet("/album_download_2/$albumId", params = mapOf("lang" to "TW")).first
    }.getOrElse { "请求失败：${it.javaClass.simpleName}: ${it.message}" }

    /**
     * 诊断用：当前请求会带上什么凭据（只回键名与长度，不回显值）。
     *
     * 「填了 AVS 还是说未登录」这类问题，第一步就得确认凭据到底有没有带上、
     * 来自哪儿（账号登录 vs 设置页手填）、有没有 Authorization ——
     * 光看界面上的「已登录」标记看不出来。
     */
    internal fun cookieDebug(): String {
        val ck = sanitize(cookieHeader())
        val hasJwt = sanitize(JmSession.jwt).isNotEmpty()
        val auth = if (hasJwt) "带 Authorization: Bearer" else "没有 Authorization"
        if (ck.isEmpty()) return "本次请求没带 Cookie，$auth"
        // 来源很关键：账号登录拿的是接口域的 AVS，手填的多半来自网页，后者不认
        val src = if (JmSession.avs.isNotEmpty()) "账号登录" else "设置页手填"
        val names = ck.split(';').mapNotNull { seg ->
            val i = seg.indexOf('=')
            if (i > 0) seg.substring(0, i).trim().takeIf { it.isNotEmpty() } else null
        }
        return "本次请求带了 Cookie（来源：$src）：${names.joinToString("/")}（共 ${ck.length} 字符），$auth"
    }

    /** 当前生效的接口域名，用于补全可能是相对路径的 `download_url` */
    internal fun apiBase(): String = "https://" + liveDomain.ifEmpty { DOMAINS.first() }

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
        applyAuth(b)
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
