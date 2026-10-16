package com.xd.vdl.core.net

import okhttp3.Cookie
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * B-01 回归：会话 Cookie 罐必须遵循 Cookie 自身的 Domain / Path / Secure 约束。
 *
 * 原实现「按 host 分桶后整桶返回」，等价于忽略 Path 与 Secure ——
 * 服务端下发给 /account 的会话 Cookie 会被带到同 host 的任意路径，
 * 带 Secure 的 Cookie 也会被明文 HTTP 请求带上。
 */
class SessionCookieJarTest {

    private val https = "https://www.example.com/".toHttpUrl()
    private val http = "http://www.example.com/".toHttpUrl()

    @Before fun clearJar() {
        SessionCookieJar.clear()
    }

    private fun cookie(
        name: String,
        value: String,
        url: HttpUrl = https,
        path: String = "/",
        secure: Boolean = false,
        expiresAt: Long = Long.MAX_VALUE,
        hostOnly: Boolean = true,
    ): Cookie = Cookie.Builder()
        .name(name).value(value)
        .apply { if (hostOnly) hostOnlyDomain(url.host) else domain(url.host) }
        .path(path)
        .apply { if (secure) secure() }
        .expiresAt(expiresAt)
        .build()

    private fun names(url: HttpUrl): List<String> =
        SessionCookieJar.loadForRequest(url).map { it.name }.sorted()

    @Test fun pathIsRespected() {
        SessionCookieJar.saveFromResponse(https, listOf(cookie("account", "1", path = "/account")))
        assertEquals(listOf("account"), names(https.newBuilder().encodedPath("/account").build()))
        // 同 host 的其它路径不应收到它（修复前会收到）。
        assertTrue(names(https.newBuilder().encodedPath("/other").build()).isEmpty())
        assertTrue(names(https).isEmpty())
    }

    @Test fun secureCookieIsNotSentOverPlainHttp() {
        SessionCookieJar.saveFromResponse(https, listOf(cookie("sid", "secret", secure = true)))
        assertEquals(listOf("sid"), names(https))
        // 明文 HTTP 绝不能带上 Secure Cookie（修复前会带上）。
        assertTrue(names(http).isEmpty())
    }

    @Test fun nonSecureCookieIsSentOverBoth() {
        SessionCookieJar.saveFromResponse(https, listOf(cookie("plain", "1")))
        assertEquals(listOf("plain"), names(https))
        assertEquals(listOf("plain"), names(http))
    }

    @Test fun hostOnlyCookieDoesNotLeakToSubdomain() {
        SessionCookieJar.saveFromResponse(https, listOf(cookie("hostonly", "1", hostOnly = true)))
        assertTrue(names("https://sub.example.com/".toHttpUrl()).isEmpty())
    }

    @Test fun expiredCookieIsDropped() {
        SessionCookieJar.saveFromResponse(
            https, listOf(cookie("old", "1", expiresAt = System.currentTimeMillis() - 1000))
        )
        assertTrue(names(https).isEmpty())
    }

    @Test fun sameNameDifferentPathCoexist() {
        SessionCookieJar.saveFromResponse(https, listOf(
            cookie("token", "root", path = "/"),
            cookie("token", "scoped", path = "/api"),
        ))
        // 同名不同 path 的两条都要留着；各自只在自己的路径前缀生效。
        assertEquals(listOf("root"), SessionCookieJar.loadForRequest(https).map { it.value })
        val api = SessionCookieJar.loadForRequest(
            https.newBuilder().encodedPath("/api/v1").build()
        ).map { it.value }.sorted()
        assertEquals(listOf("root", "scoped"), api)
    }

    @Test fun sameNameSamePathIsReplacedNotDuplicated() {
        SessionCookieJar.saveFromResponse(https, listOf(cookie("token", "first")))
        SessionCookieJar.saveFromResponse(https, listOf(cookie("token", "second")))
        val loaded = SessionCookieJar.loadForRequest(https)
        assertEquals(1, loaded.size)
        assertEquals("second", loaded.first().value)
    }

    @Test fun differentHostsDoNotShareCookies() {
        SessionCookieJar.saveFromResponse(https, listOf(cookie("a", "1")))
        assertFalse(SessionCookieJar.loadForRequest("https://other.example.org/".toHttpUrl()).isNotEmpty())
    }

    @Test fun domainCookieIsSentToSubdomain() {
        // 在 www 上收到的 Domain=example.com Cookie，子域必须能命中。
        // 修复前按 host 分桶，sub 查不到这个桶，Domain 语义实际失效。
        val wide = Cookie.Builder()
            .name("wide").value("1")
            .domain("example.com")
            .path("/")
            .expiresAt(Long.MAX_VALUE)
            .build()
        SessionCookieJar.saveFromResponse(https, listOf(wide))
        assertEquals(listOf("wide"),
            SessionCookieJar.loadForRequest("https://sub.example.com/".toHttpUrl()).map { it.name })
        assertEquals(listOf("wide"),
            SessionCookieJar.loadForRequest("https://www.example.com/".toHttpUrl()).map { it.name })
        // 无关域不能命中。
        assertTrue(SessionCookieJar.loadForRequest("https://example.org/".toHttpUrl()).isEmpty())
    }

    @Test fun domainCookieIsNotDuplicatedAcrossSubdomains() {
        val wide = Cookie.Builder()
            .name("wide").value("1").domain("example.com").path("/")
            .expiresAt(Long.MAX_VALUE).build()
        // 两个子域各自下发同一 Domain Cookie，取回时不能重复。
        SessionCookieJar.saveFromResponse(https, listOf(wide))
        SessionCookieJar.saveFromResponse("https://sub.example.com/".toHttpUrl(), listOf(wide))
        assertEquals(1, SessionCookieJar.loadForRequest(https).size)
    }
}
