package com.xd.vdl.core.parse

import android.annotation.SuppressLint
import android.webkit.JavascriptInterface
import android.app.Activity
import android.os.Handler
import android.os.Looper
import android.webkit.CookieManager
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import com.xd.vdl.core.net.Http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import okhttp3.Request
import org.json.JSONObject
import org.json.JSONTokener
import java.io.ByteArrayInputStream
import kotlin.coroutines.resume

/** 一次页面加载的结果：[value] 为注入 JS 的返回值（已解码），[intercepted] 为命中的请求 URL */
data class ExtractResult(
    val value: String?,
    val intercepted: List<String>,
)

/**
 * 用系统 WebView（本身就是真实 Chromium）加载页面并抽取数据。
 *
 * 这是安卓端替代桌面版 Playwright 的核心：真内核 + Cookie 持久化 + 请求拦截。
 * 所有 WebView 操作都在主线程执行。
 */
class WebViewBridge(private val activity: Activity) {

    /**
     * 加载 [url]，等页面停止加载并静置 [settleMs] 毫秒后执行 [extractJs]（应返回字符串），
     * 期间收集命中 [interceptFilter] 的请求 URL。
     *
     * [finishWhen] 非空时：每拦到一个 URL 就判一次，命中即**立刻**结束等待（不再等静置）。
     * 用于「拦到视频直链就够了」的场景 —— 原来只能等满 [settleMs]，白等好几秒。
     */
    @SuppressLint("SetJavaScriptEnabled")
    suspend fun loadAndExtract(
        url: String,
        ua: String? = null,
        settleMs: Long = 2500,
        timeoutMs: Long = 25000,
        interceptFilter: (String) -> Boolean = { false },
        finishWhen: ((List<String>) -> Boolean)? = null,
        extractJs: String,
    ): ExtractResult = withContext(Dispatchers.Main) {
        val collected = mutableListOf<String>()
        val handler = Handler(Looper.getMainLooper())
        val wv = WebView(activity)

        wv.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            loadsImagesAutomatically = false     // 解析期不需要图片，省流量也减少干扰
            blockNetworkImage = true
            mediaPlaybackRequiresUserGesture = false
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            // X 使用系统 WebView 原生移动端标识，避免伪装 PC Chrome。
            if (ua != null) userAgentString = ua
            // 解析只要文档和 XHR，缓存全关：避免旧缓存让拦截迟迟不触发
            cacheMode = WebSettings.LOAD_NO_CACHE
        }
        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setAcceptThirdPartyCookies(wv, true)
        }

        try {
            val value = suspendCancellableCoroutine<String?> { cont ->
                var done = false
                var settleTask: Runnable? = null

                fun finish(v: String?) {
                    if (done) return
                    done = true
                    settleTask?.let { handler.removeCallbacks(it) }
                    if (cont.isActive) cont.resume(v)
                }

                wv.webViewClient = object : WebViewClient() {
                    override fun shouldInterceptRequest(
                        view: WebView?, request: WebResourceRequest?
                    ): WebResourceResponse? {
                        val u = request?.url?.toString()
                        if (u != null && interceptFilter(u)) {
                            collected.add(u)
                            // 拦到目标就已经够用了 —— 立刻结束，不等静置
                            if (finishWhen != null && finishWhen(collected.toList())) {
                                handler.post {
                                    view?.evaluateJavascript(extractJs) { raw ->
                                        finish(decodeJsString(raw))
                                    }
                                }
                            }
                        }
                        return null
                    }

                    override fun onPageFinished(view: WebView?, u: String?) {
                        if (view == null || done) return
                        // 页面可能有多次跳转，每次完成后重置静置计时（防抖）
                        settleTask?.let { handler.removeCallbacks(it) }
                        val task = Runnable {
                            view.evaluateJavascript(extractJs) { raw ->
                                finish(decodeJsString(raw))
                            }
                        }
                        settleTask = task
                        handler.postDelayed(task, settleMs)
                    }

                    override fun onReceivedError(
                        view: WebView?, request: WebResourceRequest?, error: WebResourceError?
                    ) {
                        if (request?.isForMainFrame == true) finish(null)
                    }
                }

                handler.postDelayed({ finish(null) }, timeoutMs)
                wv.loadUrl(url)
                cont.invokeOnCancellation { handler.removeCallbacksAndMessages(null) }
            }
            ExtractResult(value, collected.toList())
        } finally {
            runCatching {
                wv.stopLoading()
                wv.loadUrl("about:blank")
                wv.destroy()
            }
        }
    }

    /** 在 Instagram 已登录的 WebView 中调用同源媒体接口。 */
    @SuppressLint("SetJavaScriptEnabled")
    suspend fun fetchInPage(
        pageUrl: String,
        script: String,
        cookies: String,
        timeoutMs: Long = 30000,
    ): String? = withContext(Dispatchers.Main) {
        val handler = Handler(Looper.getMainLooper())
        val wv = WebView(activity)
        wv.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            loadsImagesAutomatically = false
            blockNetworkImage = true
            cacheMode = WebSettings.LOAD_NO_CACHE
            userAgentString = Http.UA_DESKTOP
        }
        val cm = CookieManager.getInstance()
        cm.setAcceptCookie(true)
        cm.setAcceptThirdPartyCookies(wv, true)
        com.xd.vdl.core.net.CookieStore.split(cookies).forEach { (name, value) ->
            cm.setCookie("https://www.instagram.com/", "$name=$value; Path=/")
        }
        cm.flush()
        try {
            suspendCancellableCoroutine<String?> { cont ->
                var done = false
                fun finish(result: String?) {
                    if (done) return
                    done = true
                    handler.removeCallbacksAndMessages(null)
                    if (cont.isActive) cont.resume(result)
                }
                val bridge = object {
                    @JavascriptInterface
                    fun deliver(value: String) { handler.post { finish(value) } }
                }
                wv.addJavascriptInterface(bridge, "vdlResult")
                wv.webViewClient = object : WebViewClient() {
                    override fun onPageFinished(view: WebView?, url: String?) {
                        if (view == null || done || url == null ||
                            !url.startsWith("https://www.instagram.com/")) return
                        handler.postDelayed({
                            if (!done) view.evaluateJavascript(script, null)
                        }, 800)
                    }
                    override fun onReceivedError(
                        view: WebView?, request: WebResourceRequest?, error: WebResourceError?
                    ) {
                        if (request?.isForMainFrame == true) finish(null)
                    }
                }
                handler.postDelayed({ finish(null) }, timeoutMs)
                wv.loadUrl(pageUrl)
                cont.invokeOnCancellation { handler.removeCallbacksAndMessages(null) }
            }
        } finally {
            wv.stopLoading()
            wv.loadUrl("about:blank")
            wv.removeJavascriptInterface("vdlResult")
            wv.destroy()
        }
    }
    /**
     * 打开页面并拦截匹配的 XHR：用 OkHttp 带 Cookie 重放请求、读 JSON。
     * 抖音分享页的 bit_rate 已经是 null，完整档位只在
     * `/aweme/v1/web/aweme/detail/` 里。拿到 [pick] 非空就立刻返回。
     */
    @SuppressLint("SetJavaScriptEnabled")
    suspend fun loadAndCaptureJson(
        pageUrl: String,
        ua: String = Http.UA_DESKTOP,
        urlMatch: (String) -> Boolean,
        pick: (JSONObject) -> JSONObject?,
        timeoutMs: Long = 12000,
        pageStateJs: String? = null,
        pageStatePick: ((String) -> JSONObject?)? = null,
    ): JSONObject? = withContext(Dispatchers.Main) {
        val handler = Handler(Looper.getMainLooper())
        val wv = WebView(activity)
        wv.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            loadsImagesAutomatically = false
            blockNetworkImage = true
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            userAgentString = ua
        }
        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setAcceptThirdPartyCookies(wv, true)
        }
        try {
            suspendCancellableCoroutine<JSONObject?> { cont ->
                var done = false
                fun finish(v: JSONObject?) {
                    if (done) return
                    done = true
                    handler.removeCallbacksAndMessages(null)
                    if (cont.isActive) cont.resume(v)
                }

                wv.webViewClient = object : WebViewClient() {
                    override fun shouldInterceptRequest(
                        view: WebView?, request: WebResourceRequest?
                    ): WebResourceResponse? {
                        val u = request?.url?.toString() ?: return null
                        if (!urlMatch(u)) return null
                        return replayAndCapture(u, request, ua, pick) { item ->
                            handler.post { finish(item) }
                        }
                    }
                }
                handler.postDelayed({
                    if (pageStateJs != null && pageStatePick != null) {
                        // 图文页可能只把作品放在内联状态里，不发 detail XHR。
                        // WebView 页面卡死时 JS 回调也可能不来，留一个收尾超时。
                        handler.postDelayed({ finish(null) }, 2000)
                        wv.evaluateJavascript(pageStateJs) { raw ->
                            val state = decodeJsString(raw)
                            finish(state?.let { runCatching { pageStatePick(it) }.getOrNull() })
                        }
                    } else finish(null)
                }, timeoutMs)
                wv.loadUrl(pageUrl)
                cont.invokeOnCancellation { handler.removeCallbacksAndMessages(null) }
            }
        } finally {
            runCatching {
                wv.stopLoading()
                wv.loadUrl("about:blank")
                wv.destroy()
            }
        }
    }

    companion object {
        /**
         * 在 WebView 拦截线程里重放请求。OkHttp 会自动解 gzip，返回给 WebView
         * 时必须去掉 Content-Encoding，否则页面解第二次会坏掉。
         */
        fun replayAndCapture(
            url: String,
            request: WebResourceRequest,
            ua: String,
            pick: (JSONObject) -> JSONObject?,
            onHit: (JSONObject) -> Unit,
        ): WebResourceResponse? {
            return try {
                val b = Request.Builder().url(url).header("User-Agent", ua)
                request.requestHeaders.forEach { (k, v) ->
                    if (!k.equals("User-Agent", true)) b.header(k, v)
                }
                val cookie = CookieManager.getInstance().getCookie(url).orEmpty()
                if (cookie.isNotEmpty() && request.requestHeaders.keys.none { it.equals("Cookie", true) }) {
                    b.header("Cookie", cookie)
                }
                Http.client.newCall(b.build()).execute().use { resp ->
                    val bytes = resp.body?.bytes() ?: ByteArray(0)
                    val text = bytes.toString(Charsets.UTF_8)
                    if (text.isNotBlank() && (text.startsWith("{") || text.startsWith("["))) {
                        runCatching { JSONObject(text) }.getOrNull()?.let { json ->
                            pick(json)?.let(onHit)
                        }
                    }
                    val mime = (resp.header("Content-Type") ?: "application/json")
                        .substringBefore(";").trim().ifEmpty { "application/json" }
                    val headers = mutableMapOf<String, String>()
                    for (name in resp.headers.names()) {
                        if (name.equals("Content-Encoding", true)) continue
                        if (name.equals("Content-Length", true)) continue
                        headers[name] = resp.header(name) ?: continue
                    }
                    WebResourceResponse(
                        mime, "utf-8", resp.code,
                        resp.message.ifBlank { "OK" },
                        headers, ByteArrayInputStream(bytes),
                    )
                }
            } catch (_: Exception) {
                null
            }
        }
        /** evaluateJavascript 对字符串返回带引号的 JSON 字面量，这里解一层 */
        fun decodeJsString(raw: String?): String? {
            if (raw == null || raw == "null") return null
            val t = raw.trim()
            if (t.startsWith("\"")) {
                return runCatching { JSONTokener(t).nextValue() as? String }.getOrNull()
            }
            return t
        }
    }
}



