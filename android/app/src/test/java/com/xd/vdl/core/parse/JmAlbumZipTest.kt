package com.xd.vdl.core.parse

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * `/album_download_2` 的返回体解析。
 *
 * 字段名是从官方 App 的下载页里读出来的（`title` / `fileSize` / `img_url` /
 * `download_url`），原始返回也从真机上抓到过。最要紧的一条：
 *
 * **成功的返回里根本没有 `status` 字段** —— 只有 `status == "0"` 才代表未登录，
 * 「字段不存在」必须当成已登录。这里踩过一次坑：写成「status 非空且不为 0」，
 * 于是把一次完全成功的响应（84.6 MB 的本子，download_url 都在）误判成未登录。
 */
class JmAlbumZipTest {

    /** 真机上抓到的成功返回（截短了一点，结构原样） */
    private val realSuccess = """
        {"title":"[CheerOtter] 清純智識會夢到電子肉棒嗎？(Patreon) [AI Generated]",
         "fileSize":"84.6 MB",
         "download_url":"https://dl2025.cdnhjk.net/download_zip?md5=-3gjvc5o1_e7oWrQfJy0vQ&expires=1791642805&aid=1480165&uid=21058188",
         "img_url":"https://cdn-msp2.jmapiproxy1.cc/media/albums/1480165.jpg?v=1791453143"}
    """.trimIndent()

    @Test
    fun realSuccessBodyWithNoStatusFieldIsUsable() {
        val z = JmAlbumZip.parse(realSuccess)
        // 关键：status 缺省 = 已登录
        assertTrue("status 缺省不该判成未登录", z.loggedIn)
        assertTrue(z.reason, z.usable)
        assertEquals("84.6 MB", z.fileSize)
        assertEquals("", z.status)
        assertTrue(z.downloadUrl.startsWith("https://dl2025.cdnhjk.net/download_zip?"))
        assertEquals("", z.reason)
    }

    @Test
    fun notLoggedInIsNotUsable() {
        val z = JmAlbumZip.parse("""{"status":"0","msg":"請先登入"}""")
        assertFalse(z.loggedIn)
        assertFalse(z.usable)
        assertEquals("請先登入", z.reason)
    }

    @Test
    fun statusZeroWithoutMessageStillExplains() {
        val z = JmAlbumZip.parse("""{"status":"0","msg":""}""")
        assertFalse(z.usable)
        assertEquals("服务端说未登录（status=0）", z.reason)
    }

    @Test
    fun loggedInButNoLinkIsNotUsable() {
        val z = JmAlbumZip.parse("""{"status":"1","title":"x.zip","download_url":""}""")
        assertTrue(z.loggedIn)
        assertFalse(z.usable)
        assertEquals("拿到了登录态但没给直链", z.reason)
    }

    @Test
    fun emptyObjectReportsNoLink() {
        // 没有 download_url 就是不可用；但不能再报「未登录」误导人
        val z = JmAlbumZip.parse("{}")
        assertTrue(z.loggedIn)
        assertFalse(z.usable)
        assertEquals("拿到了登录态但没给直链", z.reason)
    }

    @Test
    fun keepsRawBodyForLogging() {
        assertEquals(realSuccess, JmAlbumZip.parse(realSuccess).raw)
    }
}
