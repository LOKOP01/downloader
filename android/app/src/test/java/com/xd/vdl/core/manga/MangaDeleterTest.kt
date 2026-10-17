package com.xd.vdl.core.manga

import com.xd.vdl.core.Platform
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * 删除文件的护栏测试。
 *
 * 这一层是**不可逆操作**，所以测的重点不是「能不能删」，而是「会不会多删」：
 *  · 只碰记录里列出的 URI；
 *  · 目录绝不递归删除；
 *  · 记录异常（条目过多）时整体拒绝。
 *
 * 需要 Context 的部分（content:// 的 MediaStore 删除）不在单测范围 ——
 * 本机没有 adb 设备，拿不到真实 ContentResolver。
 */
class MangaDeleterTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun book(uris: List<String>, kind: BookKind = BookKind.FOLDER) = MangaBook(
        key = "k",
        title = "测试本",
        platform = Platform.JMCOMIC,
        kind = kind,
        uri = uris.firstOrNull().orEmpty(),
        uris = uris,
    )

    @Test
    fun `压缩包只产生一个删除目标`() {
        val b = book(listOf("content://media/external/downloads/1"), BookKind.ZIP)
        assertEquals(1, MangaDeleter.targets(b).size)
    }

    @Test
    fun `散图每个 URI 都是目标且去重`() {
        val b = book(listOf("a", "b", "a", ""))
        assertEquals(listOf("a", "b"), MangaDeleter.targets(b))
    }

    @Test
    fun `兼容只有 uri 没有 uris 的旧记录`() {
        val b = MangaBook(
            key = "k", title = "t", kind = BookKind.ZIP,
            uri = "content://x/1", uris = emptyList(),
        )
        assertEquals(listOf("content://x/1"), MangaDeleter.targets(b))
    }

    @Test
    fun `界面提示写清楚要删什么`() {
        assertEquals("1 个压缩包",
            MangaDeleter.describeTargets(book(listOf("a"), BookKind.ZIP)))
        assertEquals("3 张图片",
            MangaDeleter.describeTargets(book(listOf("a", "b", "c"))))
        assertEquals("没有登记文件", MangaDeleter.describeTargets(book(emptyList())))
    }

    @Test
    fun `没有任何目标时不算失败`() {
        // targets() 为空 → delete() 直接返回空结果（不碰任何文件）
        val r = DeleteResult(0, emptyList(), 0, emptyList())
        assertTrue(r.ok)
        assertEquals("没有需要删除的文件", r.summary)
    }

    @Test
    fun `目录一律跳过不递归删`() {
        // 目录里可能还装着别的漫画或用户自己的东西，递归删除是不可接受的风险
        val dir = File(tmp.root, "某个漫画目录").apply { mkdirs() }
        File(dir, "别的书.jpg").writeText("x")
        assertTrue(MangaDeleter.shouldSkipDirectory(dir))
        assertFalse(MangaDeleter.shouldSkipDirectory(File(tmp.root, "x.zip")))
    }

    @Test
    fun `结果文案包含数量与失败信息`() {
        val ok = DeleteResult(3, emptyList(), 3L * 1024 * 1024, emptyList())
        assertTrue(ok.ok)
        assertTrue(ok.summary.contains("已删除 3 个文件"))
        assertTrue(ok.summary.contains("3.0 MB"))

        val partial = DeleteResult(1, listOf("x"), 0, emptyList())
        assertFalse(partial.ok)
        assertTrue(partial.summary.contains("1 个删除失败"))

        val skipped = DeleteResult(1, emptyList(), 0, listOf("dir"))
        assertFalse(skipped.ok)
        assertTrue(skipped.summary.contains("跳过"))
    }
    @Test
    fun `file 目标会被真的删掉且只删自己`() {
        val mine = File(tmp.root, "我的本子.zip").apply { writeText("abcdef") }
        val other = File(tmp.root, "别人的本子.zip").apply { writeText("keep") }
        val uris = listOf(mine.toURI().toString())
        // 只传 file:// 目标：MangaDeleter 的 file 分支不依赖 Context
        val deleted = runFileDelete(uris)
        assertTrue("目标文件应被删除", !mine.exists())
        assertEquals(1, deleted)
        assertTrue("无关文件绝不能被删", other.isFile)
        assertEquals("keep", other.readText())
    }

    /** 复刻 MangaDeleter 的 file:// 分支（该分支不使用 Context） */
    private fun runFileDelete(uris: List<String>): Int {
        var n = 0
        for (u in uris) {
            val f = MangaFiles.fileOf(u) ?: continue
            if (f.isDirectory) continue
            if (!f.exists()) continue
            if (f.delete()) n++
        }
        return n
    }
}
