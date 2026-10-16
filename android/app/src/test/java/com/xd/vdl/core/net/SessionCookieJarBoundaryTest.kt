package com.xd.vdl.core.net

import okhttp3.Cookie
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * B-01 边界补充：路径前缀、协议端口、登录态保持。
 *
 * 这些都直接对应「Cookie 语义是否被正确实现」，且全部可在 JVM 里验证
 * （用的是 OkHttp 自己的 Cookie 实现，不需要 Android 环境）。
 */
class SessionCookieJarBoundaryTest {

    private val https = "https://www.example.com/".toHttpUrl()

    @Before fun clearJar() {
        SessionCookieJar.clear()
    }

    private fun cookie(
        name: String,
        value: String,
        path: String = "/",
        secure: Boolean = false,
        hostOnly: Boolean = true,
        domainHost: String = "www.example.com",
    ): Cookie = Cookie.Builder()
        .name(name).value(value)
        .apply { if (hostOnly) hostOnlyDomain(domainHost) else domain(domainHost) }
        .path(path)
        .apply { if (secure) secure() }
        .expiresAt(Long.MAX_VALUE)
        .build()

    private fun at(path: String, host: String = "www.example.com", scheme: String = "https"): HttpUrl =
        "$scheme://$host$path".toHttpUrl()

    private fun names(url: HttpUrl): List<String> =
        SessionCookieJar.loadForRequest(url).map { it.name }.sorted()

    // ---------------- 路径前缀 ----------------

    @Test fun pathPrefixMatchesButSimilarNameDoesNot() {
        SessionCookieJar.saveFromResponse(https, listOf(cookie("c", "1", path = "/account")))
        // /account 与 /account/... 都算匹配
        assertEquals(listOf("c"), names(at("/account")))
        assertEquals(listOf("c"), names(at("/account/profile")))
        // ⚠️ /accountx 只是**字符串前缀相同**，不是路径前缀，不能匹配。
        // 这是最容易写错的一条（用 startsWith 就会错）。
        assertTrue(names(at("/accountx")).isEmpty())
    }

    @Test fun rootPathCookieMatchesEverything() {
        SessionCookieJar.saveFromResponse(https, listOf(cookie("root", "1", path = "/")))
        for (p in listOf("/", "/a", "/a/b/c", "/deep/path?x=1")) {
            assertEquals(p, listOf("root"), names(at(p)))
        }
    }

    @Test fun trailingSlashPathIsDistinct() {
        SessionCookieJar.saveFromResponse(https, listOf(cookie("s", "1", path = "/api/")))
        assertEquals(listOf("s"), names(at("/api/v1")))
        // /api（无斜杠）不是 /api/ 的前缀匹配目标
        assertTrue(names(at("/api")).isEmpty())
    }

    // ---------------- 协议与端口 ----------------

    @Test fun secureCookieSurvivesSchemeSwitchOnlyOnHttps() {
        SessionCookieJar.saveFromResponse(https, listOf(cookie("sid", "v", secure = true)))
        assertEquals(listOf("sid"), names(at("/", scheme = "https")))
        assertTrue(names(at("/", scheme = "http")).isEmpty())
    }

    @Test fun plainCookieIsSentOnBothSchemes() {
        SessionCookieJar.saveFromResponse(https, listOf(cookie("p", "v")))
        assertEquals(listOf("p"), names(at("/", scheme = "https")))
        assertEquals(listOf("p"), names(at("/", scheme = "http")))
    }

    @Test fun differentPortSameHostSharesCookie() {
        // Cookie 不区分端口（RFC 6265），80/8080 都应拿到。
        SessionCookieJar.saveFromResponse(https, listOf(cookie("anyport", "v")))
        assertEquals(listOf("anyport"), names("https://www.example.com:8443/x".toHttpUrl()))
        assertEquals(listOf("anyport"), names("https://www.example.com:443/x".toHttpUrl()))
    }

    // ---------------- 登录态保持 ----------------

    @Test fun sessionCookieRoundTripsRepeatedly() {
        // 「服务端下发 → 后续请求带上」这条链路要能反复走，
        // 模拟登录后连续多次请求（原实现一旦某次清桶就会掉登录态）。
        val sid = cookie("SESSDATA", "token-1", path = "/")
        SessionCookieJar.saveFromResponse(https, listOf(sid))
        repeat(5) {
            assertEquals(listOf("SESSDATA"), names(at("/api/feed")))
        }
        // 服务端续期（同名同路径）后仍只有一条，值更新
        SessionCookieJar.saveFromResponse(https, listOf(cookie("SESSDATA", "token-2")))
        val loaded = SessionCookieJar.loadForRequest(at("/api/feed"))
        assertEquals(1, loaded.size)
        assertEquals("token-2", loaded.first().value)
    }

    @Test fun userCookieAndSessionCookieDoNotConflictByName() {
        // Http.cookieForRequest 的合并规则是「用户 Cookie 优先」，
        // 这里验证数据层面：同名时 jar 只保留一条，不会出现重复名。
        SessionCookieJar.saveFromResponse(https, listOf(cookie("ttwid", "from-server")))
        SessionCookieJar.saveFromResponse(https, listOf(cookie("ttwid", "from-server-2")))
        val list = SessionCookieJar.loadForRequest(at("/"))
        assertEquals(1, list.count { it.name == "ttwid" })
        assertEquals("from-server-2", list.first { it.name == "ttwid" }.value)
    }

    @Test fun cookiesAreScopedToTheirOwnHostOnly() {
        SessionCookieJar.saveFromResponse(
            "https://www.douyin.com/".toHttpUrl(), listOf(cookie("ttwid", "a", domainHost = "www.douyin.com"))
        )
        SessionCookieJar.saveFromResponse(
            "https://www.iesdouyin.com/".toHttpUrl(), listOf(cookie("ttwid", "b", domainHost = "www.iesdouyin.com"))
        )
        // 两个 host 各自的 hostOnly Cookie 不能互相串（原注释明确提到的场景）
        assertEquals("a", SessionCookieJar.loadForRequest("https://www.douyin.com/".toHttpUrl()).first().value)
        assertEquals("b", SessionCookieJar.loadForRequest("https://www.iesdouyin.com/".toHttpUrl()).first().value)
    }

    @Test fun mixedHostOnlyAndDomainCookieWithSameNameCoexist() {
        val hostOnly = cookie("mix", "host", hostOnly = true, domainHost = "www.example.com")
        val wide = cookie("mix", "wide", hostOnly = false, domainHost = "example.com")
        SessionCookieJar.saveFromResponse(https, listOf(hostOnly, wide))
        // 域名不同（hostOnly vs example.com）→ 两条都保留
        val onWww = SessionCookieJar.loadForRequest(at("/")).map { it.value }.sorted()
        assertEquals(listOf("host", "wide"), onWww)
        // 子域只能看到 Domain Cookie
        assertEquals(
            listOf("wide"),
            SessionCookieJar.loadForRequest(at("/", host = "sub.example.com")).map { it.value },
        )
    }
}
