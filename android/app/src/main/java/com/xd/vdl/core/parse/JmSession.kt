package com.xd.vdl.core.parse

import android.content.Context
import android.content.SharedPreferences
import com.xd.vdl.core.AppLog

/**
 * 禁漫 App 接口域的登录态。
 *
 * ## 为什么不能只靠设置页里粘的那个 AVS
 *
 * jmcomic 官方文档专门写过这个坑（issue #104）：**cookie 是区分域名的** ——
 * 从 `18comic.vip` 拿的 AVS 配到访问 `www.cdngwc.*` 的客户端上，多半「配了也没效果」。
 * 官方的移动端接口（`/album_download_2` 这类）认的是**它自己那套域名签发的会话**，
 * 而那个会话只能从 App 的 `POST /login` 拿到。
 *
 * ## 官方 App 的凭据模型（从它的 JS 里读出来的）
 *
 * ```js
 * POST <api>/login  {username, password}
 *   → code 200 时 data = { jwttoken, s, ... }
 *   → 存起来：jwttoken 用作 Authorization: Bearer，s 用作 Cookie: AVS=<s>
 * ```
 * 之后每个接口请求都同时带这两个头。所以这里也照做。
 *
 * 只存服务端发回来的 `jwt` / `avs`，**不存密码**。
 */
object JmSession {

    private const val PREFS = "vdl_jm_session"
    private const val KEY_JWT = "jwt"
    private const val KEY_AVS = "avs"
    private const val KEY_ACCOUNT = "account"

    private var prefs: SharedPreferences? = null

    fun init(context: Context) {
        if (prefs == null) {
            prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        }
    }

    val jwt: String get() = prefs?.getString(KEY_JWT, "").orEmpty()

    /** 接口域签发的 AVS（`login` 返回里的 `s`） */
    val avs: String get() = prefs?.getString(KEY_AVS, "").orEmpty()

    /** 登录时填的账号，只用于界面显示「已登录：xxx」 */
    val account: String get() = prefs?.getString(KEY_ACCOUNT, "").orEmpty()

    val loggedIn: Boolean get() = jwt.isNotEmpty() || avs.isNotEmpty()

    fun save(context: Context, jwtToken: String, avsToken: String, account: String) {
        init(context)
        prefs?.edit()
            ?.putString(KEY_JWT, jwtToken)
            ?.putString(KEY_AVS, avsToken)
            ?.putString(KEY_ACCOUNT, account)
            ?.apply()
        AppLog.i("禁漫账号登录成功：$account（jwt ${jwtToken.length} 字符 / avs ${avsToken.length} 字符）")
    }

    fun clear(context: Context) {
        init(context)
        prefs?.edit()?.clear()?.apply()
        AppLog.i("禁漫账号登录态已清除")
    }
}
