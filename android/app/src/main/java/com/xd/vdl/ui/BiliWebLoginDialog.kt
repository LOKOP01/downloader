package com.xd.vdl.ui

import android.annotation.SuppressLint
import android.webkit.CookieManager
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.xd.vdl.core.AppLog
import com.xd.vdl.core.Platform
import com.xd.vdl.core.net.BiliWebLogin
import com.xd.vdl.core.net.CookieStore
import com.xd.vdl.core.net.Http
import kotlinx.coroutines.*
import okhttp3.CookieJar
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume

@SuppressLint("SetJavaScriptEnabled")
@Composable
internal fun BiliWebLoginDialog(onDone: () -> Unit, onClose: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var message by remember { mutableStateOf("请在B站官方页面完成登录，成功后自动验证并保存。") }
    var busy by remember { mutableStateOf(false) }
    var checkedSession by remember { mutableStateOf("") }
    val web = remember {
        WebView(ctx).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.setSupportMultipleWindows(false)
            CookieManager.getInstance().setAcceptCookie(true)
            CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean =
                    request != null && !BiliWebLogin.allowed(request.url.toString())
                override fun onReceivedError(view: WebView?, request: WebResourceRequest?, error: WebResourceError?) {
                    if (request?.isForMainFrame == true) message = "登录页面加载失败，请检查网络后点刷新。"
                }
            }
            loadUrl(BiliWebLogin.LOGIN_URL)
        }
    }
    DisposableEffect(web) {
        onDispose { web.stopLoading(); web.webViewClient = WebViewClient(); web.destroy() }
    }
    fun check(force: Boolean) {
        if (busy) return
        val cm = CookieManager.getInstance()
        val cookie = BiliWebLogin.cookies(BiliWebLogin.COOKIE_URLS.map { cm.getCookie(it).orEmpty() })
        val session = BiliWebLogin.session(cookie)
        if (session.isEmpty()) {
            if (force) message = "尚未获得登录凭据，请先完成页面上的登录。"
            return
        }
        if (!force && session == checkedSession) return
        checkedSession = session
        busy = true
        message = "正在向B站确认登录是否有效…"
        scope.launch {
            try {
                val root = withContext(Dispatchers.IO) {
                    // 验证的是新窗口拿到的凭据，不使用CookieStore中的旧Cookie。
                    val client = Http.client.newBuilder().cookieJar(CookieJar.NO_COOKIES)
                        .callTimeout(15, TimeUnit.SECONDS).build()
                    val request = Request.Builder().url(BiliWebLogin.NAV_URL)
                        .header("User-Agent", Http.UA_DESKTOP)
                        .header("Referer", "https://www.bilibili.com/")
                        .header("Cookie", cookie).build()
                    client.newCall(request).execute().use { response ->
                        if (!response.isSuccessful) throw java.io.IOException("HTTP " + response.code)
                        JSONObject(response.body?.string().orEmpty())
                    }
                }
                ensureActive()
                if (BiliWebLogin.verified(root)) {
                    cm.flush()
                    val saved = CookieStore.save(ctx, Platform.BILIBILI, cookie)
                    check(saved.ok) { "登录凭据保存失败" }
                    AppLog.i("B站网页登录成功：服务端已确认登录，Cookie已保存（不记录凭据内容）")
                    onDone()
                } else {
                    message = "B站尚未确认登录有效，请完成验证码/安全验证后再点完成登录。"
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                message = "登录验证失败，请检查网络后重试（" + e.javaClass.simpleName + "）。"
                AppLog.w("B站网页登录验证失败：" + e.javaClass.simpleName)
            } finally { busy = false }
        }
    }
    LaunchedEffect(web) {
        while (isActive) { delay(1500); check(false) }
    }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(modifier = Modifier.fillMaxWidth().fillMaxHeight(0.94f), shape = MaterialTheme.shapes.large) {
            Column(Modifier.padding(12.dp)) {
                Text("B站网页登录", style = MaterialTheme.typography.titleLarge)
                Text(message, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))
                TextButton(enabled = !busy, onClick = {
                    busy = true
                    scope.launch {
                        try {
                            // 只删除B站登录Cookie，不能调用removeAllCookies影响其他平台。
                            val cm = CookieManager.getInstance()
                            val keys = listOf("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid")
                            for (url in BiliWebLogin.COOKIE_URLS) for (key in keys) {
                                for (domain in listOf("", "; Domain=.bilibili.com")) {
                                    suspendCancellableCoroutine<Unit> { continuation ->
                                        cm.setCookie(url, key + "=; Max-Age=0; Path=/" + domain) {
                                            if (continuation.isActive) continuation.resume(Unit)
                                        }
                                    }
                                }
                            }
                            cm.flush()
                            checkedSession = ""
                            message = "窗口中的旧登录已清除，请重新登录；成功验证后才替换已保存的凭据。"
                            web.loadUrl(BiliWebLogin.LOGIN_URL)
                        } finally { busy = false }
                    }
                }) { Text("重新登录 / 换账号") }
                AndroidView(factory = { web }, modifier = Modifier.fillMaxWidth().weight(1f))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    TextButton(onClick = onClose) { Text("取消") }
                    TextButton(onClick = { if (web.canGoBack()) web.goBack() else web.reload() }, enabled = !busy) { Text("返回/刷新") }
                    Button(onClick = { check(true) }, enabled = !busy) { Text(if (busy) "验证中…" else "完成登录") }
                }
            }
        }
    }
}
