package com.xd.vdl.core.manga

import com.xd.vdl.core.Platform
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * 书架单例的行为测试（不需要 Android Context）。
 *
 * 只覆盖不碰 MediaStore 的路径：载入、合并入库、阅读进度、移除（不删缓存时）。
 * 需要 Context 的扫描与缓存清理不在单测范围（本机无 adb 设备，见交付报告）。
 */
class MangaLibraryIntakeTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun book(title: String, uri: String, key: String = title) = MangaBook(
        key = key,
        title = title,
        platform = Platform.JMCOMIC,
        kind = BookKind.ZIP,
        uri = uri,
        uris = listOf(uri),
        addedAt = 1L,
    )

    @Test
    fun `入库立即落盘且重启后仍在`() = runBlocking {
        val file = File(tmp.root, "manga_library.json")
        MangaLibrary.initFile(file)
        MangaLibrary.awaitLoaded()
        MangaLibrary.add(book("甲", "content://a/1"))
        // 等串行写队列跑完
        MangaLibrary.awaitLoaded()
        var tries = 0
        while (MangaLibrary.snapshot().isEmpty() && tries++ < 50) {
            kotlinx.coroutines.delay(20)
        }
        assertEquals(1, MangaLibrary.snapshot().size)
        assertTrue("书架文件必须已落盘", file.isFile)

        // 「重启」：换一个实例读同一个文件
        val onDisk = MangaStore(file).load()
        assertEquals(1, onDisk.books.size)
        assertEquals("甲", onDisk.books[0].title)
    }

    @Test
    fun `重复入库不产生第二条记录`() = runBlocking {
        MangaLibrary.initFile(File(tmp.root, "a.json"))
        MangaLibrary.awaitLoaded()
        val b = book("甲", "content://a/1")
        MangaLibrary.add(b)
        MangaLibrary.add(b)
        var tries = 0
        while (MangaLibrary.snapshot().size < 1 && tries++ < 50) kotlinx.coroutines.delay(20)
        kotlinx.coroutines.delay(120)
        assertEquals(1, MangaLibrary.snapshot().size)
    }

    @Test
    fun `阅读进度写入后重新加载仍然在`() = runBlocking {
        val file = File(tmp.root, "b.json")
        MangaLibrary.initFile(file)
        MangaLibrary.awaitLoaded()
        MangaLibrary.add(book("甲", "content://a/1"))
        var tries = 0
        while (MangaLibrary.snapshot().isEmpty() && tries++ < 50) kotlinx.coroutines.delay(20)
        MangaLibrary.markRead("甲", 3, listOf("p0", "p1", "p2", "p3", "p4"))
        kotlinx.coroutines.delay(150)
        assertEquals(3, MangaLibrary.find("甲")!!.pageIndex)
        val onDisk = MangaStore(file).load().books.single()
        assertEquals(3, onDisk.pageIndex)
        assertEquals("p3", onDisk.lastPage)
        assertEquals(5, onDisk.pageCount)
    }

    @Test
    fun `空页列表不写进度`() = runBlocking {
        MangaLibrary.initFile(File(tmp.root, "c.json"))
        MangaLibrary.awaitLoaded()
        MangaLibrary.add(book("甲", "content://a/1"))
        kotlinx.coroutines.delay(100)
        MangaLibrary.markRead("甲", 9, emptyList())
        kotlinx.coroutines.delay(120)
        assertEquals(0, MangaLibrary.find("甲")!!.pageIndex)
        assertEquals(0, MangaLibrary.find("甲")!!.pageCount)
    }

    @Test
    fun `移除只删索引不删文件`() = runBlocking {
        val victim = File(tmp.root, "book.zip").apply { writeText("数据") }
        MangaLibrary.initFile(File(tmp.root, "d.json"))
        MangaLibrary.awaitLoaded()
        MangaLibrary.add(book("甲", victim.toURI().toString()))
        var tries = 0
        while (MangaLibrary.snapshot().isEmpty() && tries++ < 50) kotlinx.coroutines.delay(20)
        MangaLibrary.remove(null, "甲")
        kotlinx.coroutines.delay(150)
        assertTrue(MangaLibrary.snapshot().isEmpty())
        assertNull(MangaLibrary.find("甲"))
        assertTrue("绝不能删除用户文件", victim.isFile)
        assertEquals("数据", victim.readText())
    }

    @Test
    fun `初始化新书架文件会清掉上一份快照`() = runBlocking {
        MangaLibrary.initFile(File(tmp.root, "e1.json"))
        MangaLibrary.awaitLoaded()
        MangaLibrary.add(book("甲", "content://a/1"))
        var tries = 0
        while (MangaLibrary.snapshot().isEmpty() && tries++ < 50) kotlinx.coroutines.delay(20)
        // 换到另一个（空）书架文件：不能还显示上一份的内容
        MangaLibrary.initFile(File(tmp.root, "e2.json"))
        MangaLibrary.awaitLoaded()
        assertTrue(MangaLibrary.snapshot().isEmpty())
    }
}
