package com.xd.vdl.core.manga

import com.xd.vdl.core.Platform
import org.junit.Assert.assertEquals
import org.junit.Test

class MangaSorterTest {

    private fun b(title: String, read: Long = 0, added: Long = 0, idx: Int = 0, pages: Int = 0) =
        MangaBook(
            key = title,
            title = title,
            platform = Platform.JMCOMIC,
            uri = "content://x/$title",
            uris = listOf("content://x/$title"),
            lastRead = if (read > 0) MangaTime.of(read) else "",
            addedAt = added,
            pageIndex = idx,
            pageCount = pages,
            counted = pages > 0,
        )

    @Test
    fun `最近阅读把没读过的排后面`() {
        val list = listOf(b("旧", read = 1000), b("没读过"), b("新", read = 2000))
        assertEquals(listOf("新", "旧", "没读过"),
            MangaSorter.sort(list, MangaSort.RECENT).map { it.title })
    }

    @Test
    fun `加入时间按从新到旧`() {
        val list = listOf(b("先", added = 1), b("后", added = 2))
        assertEquals(listOf("后", "先"), MangaSorter.sort(list, MangaSort.ADDED).map { it.title })
    }

    @Test
    fun `标题按自然序`() {
        val list = listOf(b("第10话"), b("第2话"))
        assertEquals(listOf("第2话", "第10话"),
            MangaSorter.sort(list, MangaSort.TITLE).map { it.title })
    }

    @Test
    fun `进度排序把快读完的放前面且未读在最后`() {
        val list = listOf(
            b("一半", idx = 5, pages = 10),
            b("快完", idx = 9, pages = 10),
            b("未读", pages = 0),
        )
        assertEquals(listOf("快完", "一半", "未读"),
            MangaSorter.sort(list, MangaSort.PROGRESS).map { it.title })
    }

    @Test
    fun `同时刻入库的顺序稳定不会乱跳`() {
        val list = listOf(b("b", added = 5), b("a", added = 5), b("c", added = 5))
        // 同一时间戳时用标题自然序兜底，顺序确定
        assertEquals(listOf("a", "b", "c"),
            MangaSorter.sort(list, MangaSort.ADDED).map { it.title })
    }

    @Test
    fun `搜索大小写不敏感且能命中书名`() {
        val list = listOf(b("Comic A"), b("本子 B"))
        assertEquals(1, MangaSorter.filter(list, "comic").size)
        assertEquals(1, MangaSorter.filter(list, "本子").size)
        assertEquals(2, MangaSorter.filter(list, "  ").size)
        assertEquals(0, MangaSorter.filter(list, "不存在").size)
    }

    @Test
    fun `排序不修改原列表`() {
        val list = listOf(b("b", read = 1), b("a", read = 2))
        val before = list.map { it.title }
        MangaSorter.sort(list, MangaSort.RECENT)
        assertEquals(before, list.map { it.title })
    }
}
