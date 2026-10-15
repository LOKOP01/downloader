package com.xd.vdl.core.net

import org.json.JSONObject
import java.net.URI

/** 登录窗口只访问B站HTTPS页面；凭据从CookieManager读取（含HttpOnly），不用JS桥。 */
internal object BiliWebLogin {
    const val LOGIN_URL = "https://passport.bilibili.com/login"
    const val NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
    val COOKIE_URLS = listOf("https://api.bilibili.com/", "https://www.bilibili.com/", "https://passport.bilibili.com/")

    fun allowed(url: String): Boolean = runCatching {
        val uri = URI(url)
        val host = uri.host?.lowercase().orEmpty()
        uri.scheme.equals("https", true) && (host == "bilibili.com" || host.endsWith(".bilibili.com"))
    }.getOrDefault(false)

    fun cookies(headers: List<String>): String {
        val out = linkedMapOf<String, String>()
        headers.forEach { header -> header.split(';').forEach { part ->
            val i = part.indexOf('=')
            if (i > 0) {
                val key = part.substring(0, i).trim()
                val value = part.substring(i + 1).trim()
                // 优先api域凭据；空值不能盖掉已拿到的非空值。
                if (value.isNotEmpty() && key !in out) out[key] = value
            }
        } }
        return out.entries.joinToString("; ") { it.key + "=" + it.value }
    }

    fun session(cookie: String): String = cookie.split(';').firstOrNull {
        it.substringBefore('=').trim() == "SESSDATA"
    }?.substringAfter('=', "")?.trim().orEmpty()

    fun verified(root: JSONObject): Boolean =
        root.has("code") && root.optInt("code", -1) == 0 &&
            root.optJSONObject("data")?.optBoolean("isLogin", false) == true
}
