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
import android.os.SystemClock
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.xd.vdl.core.AppLog
import com.xd.vdl.core.net.Http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import okhttp3.Request
import org.json.JSONObject
import org.json.JSONTokener
import java.io.ByteArrayInputStream
import java.util.concurrent.CopyOnWriteArrayList
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
        // shouldInterceptRequest 跑在 WebView 自己的线程上，而主线程会读快照
        // （finishWhen 判定 + 返回值）—— 普通 ArrayList 并发 add/toList 可能
        // 让快照丢掉刚拦到的那条，表现成「页面明明发了视频请求却没拦到」。
        // CopyOnWriteArrayList 的读快照天然一致。
        val collected = CopyOnWriteArrayList<String>()
        val handler = Handler(Looper.getMainLooper())
        val wv = WebView(activity)

        wv.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            loadsImagesAutomatically = false     // 解析期不需要图片，省流量也减少干扰
            blockNetworkImage = true
            mediaPlaybackRequiresUserGesture = false
            // ⚠️ 收紧为「兼容模式」而不是 ALWAYS_ALLOW：
            // 解析页面（抖音/X）的媒体请求本来就走 shouldInterceptRequest 拦下来，
            // 不需要放行 HTTPS 页面里的明文子资源；而 ALWAYS_ALLOW 会让页面
            // 在明文连接上加载任意资源，扩大中间人可篡改的面。
            // COMPATIBILITY_MODE 仍允许混合内容但按块处理，实测不影响这些站点的解析。
            mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
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
                        // 已经拿到结果（或超时）后 WebView 还在收尾，继续往
                        // collected 里塞没有意义，只会和主线程读快照重叠。
                        if (done) return null
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

    /**
     * 打开页面并拦截匹配的 XHR：**由 WebView 自己发请求**，用注入的 JS hook 住
     * `fetch` / `XMLHttpRequest`，把命中的响应体存进页面里的全局数组，再读出来。
     *
     * ⚠️ **为什么不能用 OkHttp 重放**（1.0.41 修）：抖音 `/aweme/v1/web/aweme/detail/`
     * 走 Argus 风控，**只看 TLS 指纹与浏览器上下文**。OkHttp 重放会被
     * `Blocked by ArgusSecurityPlugin Uifid Not Found` 403 —— 即使 UA、Cookie、Referer 全对齐。
     * 所以必须让真实 Chromium（WebView）自己发这个请求，才有完整指纹。
     *
     * 走pageStatePick 兜底：页面若根本没发 detail XHR（如图文），仍能从内联状态里取。
     */
    @SuppressLint("SetJavaScriptEnabled")
    suspend fun loadAndCaptureInPage(
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
        // 命中判定放在 JS 侧，避免把响应体往 Kotlin 搬（可能上百 KB）
        val matchPaths = urlMatchToJsArray(urlMatch)
        // 页面上一共攒了几条响应体（诊断 + 早退判定用），以及已经喂过 pick 的条数
        var seen = 0
        var picked = 0
        try {
            suspendCancellableCoroutine<JSONObject?> { cont ->
                var done = false
                fun finish(v: JSONObject?) {
                    if (done) return
                    done = true
                    handler.removeCallbacksAndMessages(null)
                    // 真机排查用：把「hook 一条都没拦到」与「拦到了但 pick 不认」
                    // 分开 —— 前者是注入时机/正则的问题，后者是数据字段的问题
                    if (v == null) {
                        AppLog.i("WebView 详情无数据：$pageUrl（拦到响应体 $seen 条）")
                    }
                    if (cont.isActive) cont.resume(v)
                }
                /** 取页面里攒下的响应体，逐个喂给 pick，第一个命中即收；
                 *  顺带把「页面上目前有几条」记进 [seen]，供早退判定与日志用 */
                fun drain() {
                    // 只取「还没喂过的那些」：详情响应体动辄几十上百 KB，
                    // 每 400ms 把整个数组重新跨进程搬一遍、再全量解析一遍，
                    // 既费主线程也费页面自己的 JS 线程
                    wv.evaluateJavascript(drainJs(picked)) { raw ->
                        if (done) return@evaluateJavascript
                        val arr = decodeJsString(raw) ?: return@evaluateJavascript
                        runCatching {
                            val payload = JSONObject(arr)
                            val n = payload.optInt("n")
                            // 页面被导航顶掉时数组会重建，长度变小 → 从头再读
                            if (n < picked) {
                                picked = 0
                            }
                            seen = n
                            val items = payload.optJSONArray("i") ?: return@runCatching
                            for (i in 0 until items.length()) {
                                val item = items.optString(i)
                                if (item.isBlank()) {
                                    continue
                                }
                                val obj = runCatching { JSONObject(item) }.getOrNull() ?: continue
                                val hit = runCatching { pick(obj) }.getOrNull()
                                if (hit != null) { picked = n; finish(hit); return@runCatching }
                            }
                            // 这批都没认出来；它们不会再变，别重复喂
                            picked = n
                        }
                    }
                }

                /**
                 * 早退：**等页面加载完成之后再给几秒宽限**，而不是按「打开后固定 N 秒」判死。
                 *
                 * 依据是真机日志里的两段十二秒：抖音作品页从打开到 onPageFinished 是
                 * 个长过程（1.0.41 的日志里，两页各自都跑满了 12s 的 timeout，
                 * 说明「打开后固定几秒」这种判据根本卡不到点上）。所以判据改成
                 * 「onPageFinished 之后又过了 [GRACE_AFTER_LOAD_MS] 还没拦到任何响应体」
                 * → 这一页基本没戏，趁早收工，把时间让给下一页 / 兜底路径。
                 *
                 * ⚠️ 数值关系必须自洽（1.0.42 踩过）：宽限的锚点若晚于 timeoutMs，
                 * 早退永远轮不到触发，等于白写。所以 [GRACE_FALLBACK_MS] 必须
                 * 明显小于调用方给的 timeoutMs。
                 */
                var gracePosted = false
                fun armGrace() {
                    if (gracePosted || done) return
                    gracePosted = true
                    handler.postDelayed({ if (!done && seen == 0) finish(null) }, GRACE_AFTER_LOAD_MS)
                }

                // 关键：页面任何脚本执行前就要把 hook 装上 —— 晚一步站点自己的
                // fetch 已经跑完，拦不到了。evaluateJavascript 在 onPageStarted 里
                // 并不保证一定早于文档脚本（它只是「尽快」），所以另外注册一个
                // 文档开始脚本（API 24+ 的 TRIGGER_UNSPECIFIED 即文档开始），
                // 两个都装：hook 自身有幂等守卫，装两次无副作用。
                if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
                    runCatching {
                        WebViewCompat.addDocumentStartJavaScript(wv, hookJs(matchPaths), setOf("*"))
                    }
                }
                wv.webViewClient = object : WebViewClient() {
                    override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
                        view?.evaluateJavascript(hookJs(matchPaths), null)
                    }

                    override fun onPageFinished(view: WebView?, url: String?) {
                        // 页面收尾后再冲几次；随后靠轮询兜住「XHR还在路上」。
                        // 这里的时机是**相对页面加载完成**，不是绝对时间。
                        for (delay in CATCH_UP_MS) handler.postDelayed({ drain() }, delay)
                        // 页面说它加载完了 → 从这一刻开始计早退宽限
                        handler.postDelayed({ armGrace() }, GRACE_AFTER_LOAD_MS)
                    }
                }
                handler.postDelayed({ armGrace() }, GRACE_FALLBACK_MS)
                // 内联状态兜底：图文作品页往往**根本不发** detail XHR，数据只
                // 在页面自己的 SSR 状态里（RENDER_DATA / _ROUTER_DATA / __INITIAL_STATE__）。
                // 不挂在 onPageFinished 上 —— 那个回调可能迟迟不来，而状态早在
                // 首屏解析时就有了，按固定时刻试几枪更快也更稳。
                if (pageStateJs != null && pageStatePick != null) {
                    for (delay in PAGE_STATE_AT_MS) handler.postDelayed({
                        if (done) return@postDelayed
                        wv.evaluateJavascript(pageStateJs) { r ->
                            if (done) return@evaluateJavascript
                            val state = decodeJsString(r) ?: return@evaluateJavascript
                            val hit = runCatching { pageStatePick(state) }.getOrNull()
                            if (hit != null) finish(hit)
                        }
                    }, delay)
                }
                // 轮询读到结束为止：详情响应可能比 onPageFinished 早、也可能晚，
                // 只有一直读才能「一到就收」。drain 内部按 [picked] 增量取，
                // 命中即 finish（finish 会清掉本 handler 的所有回调）。
                val poll = object : Runnable {
                    override fun run() {
                        if (done) return
                        drain()
                        handler.postDelayed(this, POLL_MS)
                    }
                }
                handler.postDelayed(poll, POLL_MS)
                handler.postDelayed({ finish(null) }, timeoutMs)
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
         * 轮询间隔：每 [POLL_MS] 毫秒去页面里读一次新到的响应体。
         *
         * 为什么是轮询而不是「请求完成回调」：注入的 hook 在**页面自己的** JS 里
         * 执行，它把响应体塞进 `__vdlCap` 时没法通知 Kotlin 侧 —— 只能主动读。
         * 400ms 是「够快」与「别把主线程的 evaluateJavascript 堆起来」之间的折中；
         * 读到的都是增量（见 [drainJs]），不会随响应体变大而变贵。
         */
        private const val POLL_MS = 400L

        /**
         * 页面加载完成后的补枪时刻（毫秒，相对 onPageFinished）。
         * 详情 XHR 常在 onPageFinished 前后落地，这里连补几枪把缝隙盖住。
         */
        private val CATCH_UP_MS = longArrayOf(600, 1600, 2800)

        /**
         * 内联状态（pageStateJs）的取样时刻，相对打开页面：
         * 太早拿不到（SSR 状态还没解析完），太晚则白等；先密后疏各试一枪。
         */
        private val PAGE_STATE_AT_MS = longArrayOf(1200, 3000, 6000)

        /**
         * 早退宽限：**从页面加载完成（onPageFinished）算起**，不是从打开算起。
         *
         * 为什么锚在 onPageFinished 上：抖音作品页的加载是个长过程，详情 XHR
         * 什么时候发出取决于页面自己的节奏。锚在「页面说它加载完了」之后，
         * 才谈得上「又等了这么久还没有，那就是没有」。
         */
        private const val GRACE_AFTER_LOAD_MS = 3000L

        /**
         * 早退兜底起点：onPageFinished 因故没来（页面报错 / 卡在长轮询）时，
         * 从打开页面算起到这里开始计宽限，免得永远不早退。
         *
         * ⚠️ 必须明显小于调用方的 timeoutMs（抖音给 12s），否则早退永远轮不到
         * 触发 —— 那样这个机制就是白写的（1.0.42 第一版就踩了：9000+4000 > 12000）。
         */
        private const val GRACE_FALLBACK_MS = 6000L

        /**
         * 在 WebView 拦截线程里重放请求。OkHttp 会自动解 gzip，返回给 WebView
         * 时必须去掉 Content-Encoding，否则页面解第二次会坏掉。
         *
         * ⚠️ **抖音不能用这条**（见 [loadAndCaptureInPage] 的说明）：Argus 认 TLS 指纹，
         * OkHttp 重放必 403。此方法保留给不校验指纹的站点（Iwara 等）。
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

        /** 抖音详情接口的候选路径；[urlMatchToJsArray] 逐个探测调用方认哪几条 */
        private val DOUYIN_URL_PATHS = listOf(
            "/aweme/v1/web/aweme/detail/",
            "/aweme/v1/web/aweme/iteminfo/",
            "/web/api/v2/aweme/iteminfo/",
        )

        /**
         * 把 Kotlin 的 URL 匹配函数转成**页面里用的路径数组字面量**。
         * 抖音只用「包含某个路径」这一种判据，单独实现而不做通用序列化。
         *
         * ⚠️ **不要退回正则**（1.0.42 修）：原来的实现用
         * `java.util.regex.Pattern.quote()` 生成 `\Q…\E` 再拼进 `new RegExp('…')`，
         * 但 **JS 的正则不认 `\Q`/`\E`** —— `\Q` 被当成「转义字母 Q」，
         * 整条正则退化成「URL 里必须有个 Q」，对任何真实 URL 都恒不匹配。
         *
         * 它还很隐蔽地**叠了两层转义**：Kotlin 侧把 `\Q` 塞进单引号 JS 字符串，
         * JS 解析时又吃掉一个反斜杠。结果 hook 装上了、页面也确实发了 detail 请求，
         * 但 `__vdlCap` 永远是空数组 —— 表现为**每次都等满超时**，最后退回分享页
         * 那条单档路（现象就是「只有 1 档、没有帧率、耗时 20s+」）。
         *
         * 现在改成纯字符串包含判断（见 [hookJs]），从根上没有转义问题。
         */
        internal fun urlMatchToJsArray(match: (String) -> Boolean): String {
            val paths = DOUYIN_URL_PATHS.filter { match(it) }
            return paths.joinToString(",", "[", "]") { jsString(it) }
        }

        /** Kotlin 字符串 → JS 字符串字面量（含引号），够用且不依赖任何转义库 */
        internal fun jsString(s: String): String = buildString(s.length + 2) {
            append('"')
            for (c in s) when (c) {
                '"' -> append("\\\"")
                '\\' -> append("\\\\")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\u2028' -> append("\\u2028")
                '\u2029' -> append("\\u2029")
                else -> append(c)
            }
            append('"')
        }

        /**
         * 注入的 hook：包住 fetch 与 XHR，把 URL 命中 [pathsJs] 里任一子串的响应体
         * 存进 `__vdlCap`。
         *
         * 用 `indexOf` 做包含判断而不是正则：判据本来就是「URL 含某个路径」，
         * 走字符串比较既不涉及正则转义（1.0.42 那个 bug 的根因），也更快。
         */
        internal fun hookJs(pathsJs: String): String = """
            (function(){
              if (window.__vdlHooked) return; window.__vdlHooked = 1;
              var PATHS = $pathsJs;
              window.__vdlCap = [];
              function hit(url){
                if (!url) return false;
                var s = String(url);
                for (var i = 0; i < PATHS.length; i++) {
                  if (s.indexOf(PATHS[i]) >= 0) return true;
                }
                return false;
              }
              function keep(url, txt){
                if (!txt) return;
                try { if (hit(url)) window.__vdlCap.push(txt); } catch(e){}
              }
              var of = window.fetch;
              if (of) {
                window.fetch = function(input, init){
                  var p = of.apply(this, arguments);
                  try {
                    var u = (typeof input === 'string') ? input : (input && input.url);
                    p.then(function(r){ try { r.clone().text().then(function(t){ keep(u, t); }); } catch(e){} });
                  } catch(e){}
                  return p;
                };
              }
              var xo = XMLHttpRequest.prototype.open;
              var xs = XMLHttpRequest.prototype.send;
              XMLHttpRequest.prototype.open = function(m, u){ this.__u = u; return xo.apply(this, arguments); };
              XMLHttpRequest.prototype.send = function(){
                try {
                  this.addEventListener('load', function(){
                    try { keep(this.__u, this.responseText); } catch(e){}
                  });
                } catch(e){}
                return xs.apply(this, arguments);
              };
            })();
        """.trimIndent()

        /**
         * 读出「页面里第 [from] 条起」的响应体。返回 `{"n":总数,"items":[...]}`。
         *
         * 用下标而不是整份拷回来：详情响应体动辄几十上百 KB，而轮询是几百毫秒
         * 一次 —— 每次把整个数组搬到 Kotlin 侧、再全量 JSON 解析，白烧主线程。
         * 返回 `{n: 总数, i: 第 from 条起的新增项}`；页面被导航顶掉时数组会重建
         * （长度变小），调用方据此把下标归零重读。
         */
        private fun drainJs(from: Int): String =
            "(function(){var a=window.__vdlCap||[];return JSON.stringify({n:a.length,i:a.slice($from)});})()"
    }
}



