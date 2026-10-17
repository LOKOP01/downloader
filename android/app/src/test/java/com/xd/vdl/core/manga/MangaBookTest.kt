package com.xd.vdl.core.manga

import com.xd.vdl.core.Platform
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MangaBookTest {

    private fun book(vararg pages: String) = MangaBook(
        key = "zip|视频下载器|422866.zip",
        title = "测试本子",
        platform = Platform.JMCOMIC,
        kind = BookKind.ZIP,
        uri = "content://media/external/downloads/1",
        uris = listOf("content://media/external/downloads/1"),
        sourceUrl = "jmcomic://album/422866",
    )

    @Test
    fun `没有阅读记录时从第一页开始`() {
        assertEquals(0, book().restoreIndex(listOf("a", "b", "c")))
    }

    @Test
    fun `按页标识恢复到上次位置`() {
        val b = book().copy(lastPage = "c", pageIndex = 2)
        assertEquals(2, b.restoreIndex(listOf("a", "b", "c")))
    }

    @Test
    fun `页标识消失时退回下标并夹紧`() {
        // 上次读到第 5 页，但现在只剩 2 页 —— 必须回到最后一页，不能越界
        val b = book().copy(lastPage = "被删掉的页", pageIndex = 5)
        assertEquals(1, b.restoreIndex(listOf("a", "b")))
    }

    @Test
    fun `空页列表不崩`() {
        assertEquals(0, book().copy(pageIndex = 3).restoreIndex(emptyList()))
        assertEquals(0, book().markRead(2, emptyList()).pageIndex)
    }

    @Test
    fun `markRead 记录页标识与时间`() {
        val b = book().markRead(1, listOf("a", "b", "c"))
        assertEquals(1, b.pageIndex)
        assertEquals(3, b.pageCount)
        assertEquals("b", b.lastPage)
        assertTrue(b.lastRead.isNotEmpty())
        assertTrue(b.counted)
    }

    @Test
    fun `markRead 越界自动夹紧`() {
        val b = book().markRead(99, listOf("a", "b"))
        assertEquals(1, b.pageIndex)
        assertEquals("b", b.lastPage)
    }

    @Test
    fun `进度文案区分未读与读完`() {
        assertEquals("未读", book().progressText)
        assertEquals("读至 2/10 页",
            book().copy(pageIndex = 1, pageCount = 10, counted = true).progressText)
        assertEquals("已读完 · 10 页",
            book().copy(pageIndex = 9, pageCount = 10, counted = true).progressText)
    }

    @Test
    fun `序列化往返不丢字段`() {
        val b = book().copy(
            pageIndex = 3,
            pageCount = 12,
            lastPage = "第01章/0004.jpg",
            favorite = true,
            counted = true,
            chapters = listOf(MangaChapter("第01章", 6), MangaChapter("第02章", 6)),
            addedAt = 1700000000000L,
        )
        val back = MangaBook.fromJson(JSONObject(b.toJson().toString()))
        assertNotNull(back)
        assertEquals(b, back)
    }

    @Test
    fun `缺 key 或既无 uri 又无 folder 的记录被丢弃`() {
        assertNull(MangaBook.fromJson(JSONObject("{}")))
        assertNull(MangaBook.fromJson(JSONObject("""{"key":"k"}""")))
    }

    @Test
    fun `坏字段只丢该字段不影响整条记录`() {
        val o = JSONObject(
            """{"key":"k","uri":"content://x/1","page_index":"7","platform":"NOT_A_PLATFORM"}""",
        )
        val b = MangaBook.fromJson(o)
        assertNotNull(b)
        assertEquals(7, b!!.pageIndex)
        assertEquals(Platform.UNKNOWN, b.platform)
        assertFalse(b.counted)
    }

    @Test
    fun `散图记录的 uris 缺失时回退成单 uri`() {
        val o = JSONObject("""{"key":"k","uri":"content://x/1"}""")
        val b = MangaBook.fromJson(o)!!
        assertEquals(listOf("content://x/1"), b.uris)
    }

    @Test
    fun `稳定标识对同一本书恒定且不依赖任务 id`() {
        val a = MangaKeys.zip("视频下载器", "422866.zip")
        val b = MangaKeys.zip("视频下载器", "422866.zip")
        assertEquals(a, b)
        assertTrue(a != MangaKeys.zip("视频下载器", "422866(1).zip"))
    }

    @Test
    fun `散图标识在无业务 id 时用标题哈希`() {
        val a = MangaKeys.images(Platform.JMCOMIC, "", "标题甲")
        val b = MangaKeys.images(Platform.JMCOMIC, "", "标题甲")
        val c = MangaKeys.images(Platform.JMCOMIC, "", "标题乙")
        assertEquals(a, b)
        assertTrue(a != c)
    }

    @Test
    fun `文件夹标识忽略大小写与尾斜杠`() {
        assertEquals(MangaKeys.folder("/a/b/"), MangaKeys.folder("/A/B"))
    }
}
