package com.xd.vdl.core.net

import android.content.Context
import android.content.SharedPreferences
import android.os.Handler
import android.os.Looper
import android.webkit.CookieManager
import com.xd.vdl.core.AppLog
import com.xd.vdl.core.Platform

/**
 * 各平台 Cookie 的本地存储。
 *
 * 用户在设置页从电脑浏览器把 Cookie 粘进来，这里负责解析、按平台持久化，
 * 并同步一份给 WebView 的 CookieManager（X 的解析要真 WebView 加载，那里必须能拿到）。
 *
 * 解析入口 [parse] 兼容几种常见的粘贴格式，因为用户拿到的原文形态很杂：
 *  · `a=1; b=2`                      —— DevTools 控制台 `document.cookie`、请求头
 *  · 每行一条 `a=1` / `a=1; Path=/; Secure`
 *  · DevTools → Application → Cookies 的表格（制表符分隔：名字、值、域、路径…）
 *  · 整段 `Cookie: a=1; b=2`（顺手剥掉前缀）
 * `Path` / `Domain` / `Expires` / `Secure` 这类属性名一律丢掉，只留真正的键值对。
 */
object CookieStore {

    private const val PREFS = "vdl_cookies"
    private const val PREFIX = "cookie_"

    /** 这些名字是 Cookie 的属性、不是 Cookie 本身 */
    private val ATTRS = setOf(
        "path", "domain", "expires", "max-age", "maxage", "secure", "httponly",
        "samesite", "priority", "partitioned", "size", "created", "lastaccessed",
        "hostonly", "session", "comment", "version",
    )

    /** Cookie 名字的合法字符集，不匹配的整条丢掉（防把中文说明文字当 Cookie） */
    private val NAME_RE = Regex("^[A-Za-z0-9_\\-.$]+$")

    private val COOKIE_PREFIX_RE = Regex("(?i)^\\s*cookie\\s*:\\s*")

    private var prefs: SharedPreferences? = null
    private val main = Handler(Looper.getMainLooper())

    fun init(context: Context) {
        if (prefs == null) {
            prefs = context.applicationContext
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        }
    }

    // ------------------------------------------------------------------ //
    // 解析
    // ------------------------------------------------------------------ //

    /**
     * 把粘贴进来的文本解析成规范的 `a=1; b=2`。
     * 一条都认不出来时返回空串（调用方据此提示用户）。
     */
    fun parse(raw: String): String {
        val body = raw.trim().replace(COOKIE_PREFIX_RE, "")
        if (body.isEmpty()) return ""

        val out = LinkedHashMap<String, String>()
        body.split(Regex("\\r?\\n")).forEach { line ->
            val t = line.trim()
            if (t.isEmpty()) return@forEach
            val cols = t.split('\t')
            // DevTools 的 Cookies 表格：第一列是 Cookie 名（不含 =），第二列是值。
            // 值本身常以 = 结尾（base64），所以只能靠第一列判断，不能靠整行。
            if (cols.size >= 2 && !cols[0].contains('=')) {
                put(out, cols[0], cols[1])
            } else {
                splitPairs(t).forEach { (k, v) -> put(out, k, v) }
            }
        }
        return out.entries.joinToString("; ") { "${it.key}=${it.value}" }
    }

    /** 把 `a=1; b=2; Path=/; Secure` 切成键值对，属性段丢掉 */
    private fun splitPairs(line: String): List<Pair<String, String>> {
        val res = ArrayList<Pair<String, String>>()
        line.split(';').forEach { seg ->
            val i = seg.indexOf('=')
            if (i <= 0) return@forEach
            res.add(seg.substring(0, i).trim() to seg.substring(i + 1).trim().trim('"'))
        }
        return res
    }

    private fun put(map: LinkedHashMap<String, String>, name: String, value: String) {
        val n = name.trim()
        if (n.isEmpty() || !NAME_RE.matches(n)) return
        if (ATTRS.contains(n.lowercase())) return
        map[n] = value.trim().trim('"')
    }

    /** 取出 cookie 串里的名字列表（界面用来展示到底存了什么） */
    fun names(cookie: String): List<String> = cookie.split(';').mapNotNull { seg ->
        val i = seg.indexOf('=')
        if (i > 0) seg.substring(0, i).trim().takeIf { it.isNotEmpty() } else null
    }

    /** 把 cookie 串拆成 名 → 值（同名取后者） */
    fun split(cookie: String): Map<String, String> {
        val out = LinkedHashMap<String, String>()
        cookie.split(';').forEach { seg ->
            val i = seg.indexOf('=')
            if (i <= 0) return@forEach
            val k = seg.substring(0, i).trim()
            if (k.isNotEmpty()) out[k] = seg.substring(i + 1).trim()
        }
        return out
    }

    /** cookie 串里是否含某个名字（按 Cookie 边界匹配，避免 sessionid 命中 sessionid_ss） */
    fun has(cookie: String, key: String): Boolean =
        Regex("(^|;\\s*)" + Regex.escape(key) + "=").containsMatchIn(cookie)

    /** 各平台的登录态关键 Cookie：命中才算真登录（只看"有没有 Cookie"会把游客 Cookie 误判） */
    fun authKeysOf(platform: Platform): List<String> = when (platform) {
        Platform.BILIBILI -> listOf("SESSDATA")
        Platform.X -> listOf("auth_token")
        Platform.INSTAGRAM -> listOf("sessionid")
        Platform.XIAOHONGSHU -> listOf("web_session")
        Platform.DOUYIN -> listOf("sessionid", "sessionid_ss", "sid_tt")
        // 禁漫的 AVS 是登录凭证；但接口大部分情况不校验，所以只作提示
        Platform.JMCOMIC -> listOf("AVS")
        Platform.UNKNOWN -> emptyList()
    }

    // ------------------------------------------------------------------ //
    // 各平台要填哪些项（与桌面版 app/view/cookie_dialog.py 的 FIELDS 同一套做法）
    // ------------------------------------------------------------------ //

    /** Cookie 输入框里的一项 */
    data class CookieField(val name: String, val hint: String, val required: Boolean)

    /**
     * 登录框逐项列出的字段。
     * `required` 只用于界面校验，与 [authKeysOf] 不是一回事：X 的 ct0 必填，
     * 但它不是判断「是否登录」的凭证（有 auth_token 就算登录了）。
     */
    fun fieldsOf(platform: Platform): List<CookieField> = when (platform) {
        Platform.DOUYIN -> listOf(
            CookieField("sessionid", "抖音网页版登录后的 sessionid（最重要）", true),
            CookieField("sessionid_ss", "备用登录态，与 sessionid 同批下发", false),
            CookieField("sid_tt", "备用登录态", false),
            CookieField("ttwid", "设备标识，提高接口通过率", false),
        )

        Platform.BILIBILI -> listOf(
            CookieField("SESSDATA", "bilibili.com 登录后的 SESSDATA（登录态核心）", true),
            CookieField("bili_jct", "CSRF 令牌", false),
            CookieField("DedeUserID", "用户 ID", false),
        )

        Platform.X -> listOf(
            CookieField("auth_token", "x.com 登录后的 auth_token", true),
            CookieField("ct0", "x.com 登录后的 ct0（CSRF 令牌）", true),
        )

        Platform.INSTAGRAM -> listOf(
            CookieField("sessionid", "instagram.com 登录后的 sessionid", true),
            CookieField("csrftoken", "instagram.com 的 CSRF 令牌", true),
            CookieField("ds_user_id", "用户 ID", false),
        )

        Platform.XIAOHONGSHU -> listOf(
            CookieField("web_session", "xiaohongshu.com 登录后的 web_session", true),
            CookieField("a1", "设备标识，有助于加载笔记", false),
            CookieField("webId", "设备 ID", false),
        )

        Platform.JMCOMIC -> listOf(
            CookieField("AVS", "18comic 登录后的 AVS（看排行榜/收藏等需登录内容才用得上）", false),
        )

        Platform.UNKNOWN -> emptyList()
    }

    // ------------------------------------------------------------------ //
    // 读写
    // ------------------------------------------------------------------ //

    fun get(platform: Platform): String {
        if (platform == Platform.UNKNOWN) return ""
        return prefs?.getString(PREFIX + platform.name, "").orEmpty()
    }

    /** 保存结果：解析出的 cookie 串 / 条数 / 命中的登录凭证 */
    data class SaveResult(val cookie: String, val count: Int, val authHit: List<String>) {
        val ok: Boolean get() = cookie.isNotEmpty()
    }

    fun save(context: Context, platform: Platform, raw: String): SaveResult {
        init(context)
        val cookie = parse(raw)
        if (cookie.isEmpty()) return SaveResult("", 0, emptyList())

        val names = names(cookie)
        prefs?.edit()?.putString(PREFIX + platform.name, cookie)?.apply()

        val hit = authKeysOf(platform).filter { has(cookie, it) }
        syncToWebView(platform, cookie)
        AppLog.i(
            "保存 ${platform.label} Cookie：${names.size} 项" +
                if (hit.isEmpty()) "（未发现登录凭证 ${authKeysOf(platform).joinToString("/")}）"
                else "，含 ${hit.joinToString("/")}",
        )
        return SaveResult(cookie, names.size, hit)
    }

    /**
     * 拼接「逐项填写」的结果。
     *
     * 界面上的字段排在前面（用户主要核对这几项），额外项跟在后面 ——
     * extra 是没在字段表里、但之前粘贴保留下来的 Cookie（抖音的 tt_scid、odin_tt 之类），
     * 一起带上能提高接口通过率。字段被清空时，同名额外项也要一并去掉，否则删不掉。
     */
    fun compose(fields: Map<String, String>, extra: Map<String, String>): String {
        val cleared = fields.filterValues { it.isEmpty() }.keys
        val out = LinkedHashMap<String, String>()
        fields.forEach { (k, v) -> if (v.isNotEmpty()) out[k] = v }
        extra.forEach { (k, v) ->
            if (v.isNotEmpty() && k !in cleared && k !in out) out[k] = v
        }
        return out.entries.joinToString("; ") { "${it.key}=${it.value}" }
    }

    /** 保存逐项填写的结果（界面用） */
    fun saveFields(
        context: Context,
        platform: Platform,
        values: Map<String, String>,
        extra: Map<String, String>,
    ): SaveResult = save(context, platform, compose(values, extra))

    fun clear(context: Context, platform: Platform) {
        init(context)
        prefs?.edit()?.remove(PREFIX + platform.name)?.apply()
        dropWebViewCookies(platform)
        AppLog.i("清除 ${platform.label} Cookie")
    }

    // ------------------------------------------------------------------ //
    // 与 WebView 的 CookieManager 同步
    // ------------------------------------------------------------------ //

    /**
     * 同步给 CookieManager：X 的解析走真 WebView，那里只认 CookieManager。
     * 抖音额外写一份 iesdouyin.com —— 解析走的是这个域，和 douyin.com 不互通。
     * 放主线程执行，CookieManager 首次调用会初始化 Chromium，别堵住调用方。
     */
    private fun syncToWebView(platform: Platform, cookie: String) {
        main.post {
            runCatching {
                val cm = CookieManager.getInstance()
                cm.setAcceptCookie(true)
                split(cookie).forEach { (name, value) ->
                    cm.setCookie(platform.cookieUrl, "$name=$value")
                }
                if (platform == Platform.DOUYIN) {
                    split(cookie).forEach { (name, value) ->
                        cm.setCookie("https://www.iesdouyin.com", "$name=$value")
                    }
                }
                cm.flush()
            }.onFailure { AppLog.w("同步 Cookie 到 WebView 失败：${it.message}") }
        }
    }

    /**
     * CookieManager 没有"按域删除"的接口，只能清空后把其它平台的写回去，
     * 否则清一个平台会把全部登录态一起带走。
     */
    private fun dropWebViewCookies(platform: Platform) {
        main.post {
            runCatching {
                val cm = CookieManager.getInstance()
                cm.removeAllCookies(null)
                Platform.values()
                    .filter { it != platform && it != Platform.UNKNOWN }
                    .forEach { p ->
                        val c = get(p)
                        split(c).forEach { (name, value) ->
                            cm.setCookie(p.cookieUrl, "$name=$value")
                        }
                    }
                cm.flush()
            }.onFailure { AppLog.w("清除 WebView Cookie 失败：${it.message}") }
        }
    }
}



