package com.xd.vdl.core.parse

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 官方打包接口返回体的解析。
 *
 * 字段名是从官方 App 的下载页里抠出来的（`title` / `fileSize` / `img_url` /
 * `download_url` / `status`），这里把「未登录」「登录但没直链」「正常」三种形态钉住。
 * 本机没法用真实登录态验证，所以至少保证解析不会把「未登录」当成可用。
 */
class JmAlbumZipTest {

    @Test
    fun notLoggedInIsNotUsable() {
        val z = JmAlbumZip.parse("""{"status":"0","msg":"請先登入"}""")
        assertFalse(z.loggedIn)
        assertFalse(z.usable)
        assertEquals("", z.downloadUrl)
    }

    @Test
    fun loggedInWithLinkIsUsable() {
        val body = """
            {"status":"1","title":"[作者] 标题.zip","fileSize":"86.4 MB",
             "img_url":"https://cdn.example/cover.jpg",
             "download_url":"https://cdn.example/media/albums/422866.zip"}
        """.trimIndent()
        val z = JmAlbumZip.parse(body)
        assertTrue(z.loggedIn)
        assertTrue(z.usable)
        assertEquals("[作者] 标题.zip", z.title)
        assertEquals("86.4 MB", z.fileSize)
        assertEquals("https://cdn.example/cover.jpg", z.imageUrl)
        assertEquals("https://cdn.example/media/albums/422866.zip", z.downloadUrl)
    }

    @Test
    fun loggedInButNoLinkIsNotUsable() {
        // 有些状态码配的是空直链，这种也不能当可用
        val z = JmAlbumZip.parse("""{"status":"1","title":"x.zip","download_url":""}""")
        assertTrue(z.loggedIn)
        assertFalse(z.usable)
    }

    @Test
    fun missingFieldsDoNotCrash() {
        val z = JmAlbumZip.parse("{}")
        assertFalse(z.loggedIn)
        assertFalse(z.usable)
        assertEquals("", z.title)
    }

    // ---- reason：必须能区分「status 是 0」和「压根没有 status 字段」----
    // 只写一句「未登录」会把这两种完全不同的情况混成一种，没法继续排查。

    @Test
    fun reasonPrefersServerMessage() {
        val z = JmAlbumZip.parse("""{"status":"0","msg":"請先登入"}""")
        assertEquals("請先登入", z.reason)
    }

    @Test
    fun reasonReportsStatusWhenNoMessage() {
        val z = JmAlbumZip.parse("""{"status":"0","msg":""}""")
        assertEquals("服务端说未登录（status=0）", z.reason)
    }

    @Test
    fun reasonDistinguishesMissingStatus() {
        val z = JmAlbumZip.parse("""{"code":200,"data":{"foo":1}}""")
        assertEquals("响应里没有 status 字段", z.reason)
    }

    @Test
    fun reasonReportsLoggedInButNoLink() {
        val z = JmAlbumZip.parse("""{"status":"1","title":"x.zip"}""")
        assertTrue(z.loggedIn)
        assertFalse(z.usable)
        assertEquals("拿到登录态但没给直链", z.reason)
    }

    @Test
    fun reasonIsEmptyWhenUsable() {
        val z = JmAlbumZip.parse(
            """{"status":"1","download_url":"https://x/y.zip","title":"t"}""",
        )
        assertTrue(z.usable)
        assertEquals("", z.reason)
    }

    @Test
    fun keepsRawBodyForLogging() {
        val body = """{"status":"0","msg":"請先登入"}"""
        assertEquals(body, JmAlbumZip.parse(body).raw)
    }
}
