package com.xd.vdl.core.manga

import com.xd.vdl.core.Platform
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * 书架持久化测试。覆盖的都是「会真实毁掉用户数据」的路径：
 * 原子写入、备份、损坏留档、读失败禁止保存、重复入库合并。
 */
class MangaStoreTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun store(name: String = "manga_library.json") = MangaStore(File(tmp.root, name))

    private fun book(key: String, title: String = "书$key", page: Int = 0) = MangaBook(
        key = key,
        title = title,
        platform = Platform.JMCOMIC,
        kind = BookKind.ZIP,
        uri = "content://media/external/downloads/$key",
        uris = listOf("content://media/external/downloads/$key"),
        pageIndex = page,
        pageCount = 10,
        counted = true,
        addedAt = 1700000000000L,
    )

    @Test
    fun `文件不存在时视为空书架且可写`() {
        val r = store().load()
        assertTrue(r.books.isEmpty())
        assertTrue(r.readable)
    }

    @Test
    fun `保存后能原样读回`() {
        val s = store()
        s.load()
        s.save(listOf(book("a"), book("b", page = 3)), MangaStore.Prefs(rtl = true))
        val r = store().load()
        assertEquals(2, r.books.size)
        assertEquals(3, r.books[1].pageIndex)
        assertTrue(r.prefs.rtl)
    }

    @Test
    fun `第二次保存会留下可用的备份`() {
        val s = store()
        s.load()
        s.save(listOf(book("a")), MangaStore.Prefs())
        s.save(listOf(book("a"), book("b")), MangaStore.Prefs())
        assertTrue(File(tmp.root, "manga_library.json.bak").isFile)
        assertEquals(1, MangaStore(File(tmp.root, "manga_library.json.bak")).load().books.size)
    }

    @Test
    fun `主文件损坏时从备份恢复并留档`() {
        val s = store()
        s.load()
        s.save(listOf(book("a")), MangaStore.Prefs())
        s.save(listOf(book("a"), book("b")), MangaStore.Prefs())
        // 主文件被写坏（模拟断电 / 外部篡改）
        File(tmp.root, "manga_library.json").writeText("{ 这不是 JSON")
        val r = store().load()
        assertTrue("应从备份恢复", r.readable)
        assertEquals(1, r.books.size)
        assertTrue(r.warning.contains("备份"))
        assertTrue(File(tmp.root, "manga_library.json.corrupt").isFile)
    }

    @Test
    fun `主文件与备份都损坏时禁止保存`() {
        File(tmp.root, "manga_library.json").writeText("坏")
        File(tmp.root, "manga_library.json.bak").writeText("也坏")
        val s = store()
        val r = s.load()
        assertFalse("两份都读不出来时必须禁止保存", r.readable)
        assertTrue(r.warning.isNotEmpty())
        // 最后一道闸：即使调用方忽略了 readable，持久化层自己也要拦住
        var threw = false
        try {
            s.save(listOf(book("a")), MangaStore.Prefs())
        } catch (e: Exception) {
            threw = true
        }
        assertTrue("读失败后保存必须抛错", threw)
        // 两个原始文件都必须原样保留，绝不能因为「读不出来」就把它们覆盖掉
        assertEquals("也坏", File(tmp.root, "manga_library.json.bak").readText())
        assertTrue(File(tmp.root, "manga_library.json.corrupt").isFile)
        assertEquals("坏", File(tmp.root, "manga_library.json.corrupt").readText())
    }

    @Test
    fun `坏的单条记录被跳过其余仍可读`() {
        val good = book("a")
        val json = """{"version":1,"books":[
            {"key":"bad"},
            ${JSON_OBJ(good)},
            {"key":"dup","uri":"content://x/1"},
            {"key":"dup","uri":"content://x/2"}
        ]}"""
        File(tmp.root, "manga_library.json").writeText(json)
        val r = store().load()
        assertTrue(r.readable)
        assertEquals("重复 key 只保留一条", 2, r.books.size)
        assertEquals("a", r.books[0].key)
    }

    @Test
    fun `空白文件按损坏处理`() {
        File(tmp.root, "manga_library.json").writeText("   ")
        val r = store().load()
        assertFalse(r.readable)
    }

    // ---------------------------------------------------------------- //
    // 合并
    // ---------------------------------------------------------------- //

    @Test
    fun `重复入库不产生第二条记录`() {
        val first = MangaStore.merge(emptyList(), listOf(book("a")))
        val second = MangaStore.merge(first, listOf(book("a")))
        assertEquals(1, second.size)
    }

    @Test
    fun `重新入库保留用户的标题进度与收藏`() {
        val old = book("a", title = "我改过的名字", page = 5).copy(
            favorite = true,
            lastRead = "2026-10-01T10:00:00",
        )
        val fresh = book("a", title = "下载时的标题", page = 0).copy(uri = "content://new/9")
        val merged = MangaStore.merge(listOf(old), listOf(fresh)).single()
        assertEquals("我改过的名字", merged.title)
        assertEquals(5, merged.pageIndex)
        assertTrue(merged.favorite)
        assertEquals("2026-10-01T10:00:00", merged.lastRead)
        assertEquals("URI 应更新为新扫描到的", "content://new/9", merged.uri)
    }

    @Test
    fun `未数过的页数不会冲掉已数出的页数`() {
        val old = book("a").copy(pageCount = 42, counted = true)
        val fresh = book("a").copy(pageCount = 0, counted = false)
        assertEquals(42, MangaStore.merge(listOf(old), listOf(fresh)).single().pageCount)
    }

    @Test
    fun `新增记录追加在已有记录之后`() {
        val merged = MangaStore.merge(listOf(book("a")), listOf(book("b"), book("c")))
        assertEquals(listOf("a", "b", "c"), merged.map { it.key })
    }

    @Test
    fun `平台未知时不覆盖已知平台`() {
        val old = book("a").copy(platform = Platform.JMCOMIC)
        val fresh = book("a").copy(platform = Platform.UNKNOWN)
        assertEquals(Platform.JMCOMIC, MangaStore.merge(listOf(old), listOf(fresh)).single().platform)
    }

    private fun JSON_OBJ(b: MangaBook): String = b.toJson().toString()
}
