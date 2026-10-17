package com.xd.vdl.core.manga

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * 解包测试。这一层是本地阅读最容易出安全/顺序问题的地方，必须覆盖：
 * zip slip、页序、非图片条目、0 字节条目、上限截断、空包。
 */
class ZipUnpackerTest {

    @get:Rule
    val tmp = TemporaryFolder()

    /** 造一个 zip：按传入顺序写入条目（顺序即页序） */
    private fun zip(vararg entries: Pair<String, ByteArray>): ByteArray {
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { zos ->
            entries.forEach { (name, data) ->
                zos.putNextEntry(ZipEntry(name))
                zos.write(data)
                zos.closeEntry()
            }
        }
        return out.toByteArray()
    }

    private fun bytes(n: Int) = ByteArray(n) { it.toByte() }

    private fun unpack(data: ByteArray, dir: File = File(tmp.root, "pages")) =
        ZipUnpacker.unpack(ByteArrayInputStream(data), dir)

    @Test
    fun `页序严格等于包内顺序`() {
        // 故意给一个「字典序会和包内顺序不同」的名字组合
        val r = unpack(
            zip(
                "0002.jpg" to bytes(4),
                "0001.jpg" to bytes(8),
                "0010.jpg" to bytes(16),
            ),
        )
        assertEquals(listOf("0002.jpg", "0001.jpg", "0010.jpg"), r.entries.map { it.name })
        assertEquals(listOf(4L, 8L, 16L), r.entries.map { it.size })
    }

    @Test
    fun `落盘文件名是序号绝不使用条目名`() {
        val dir = File(tmp.root, "pages")
        unpack(zip("../../evil.jpg" to bytes(3)), dir)
        // 缓存目录里只应有序号文件，且父目录里没有 evil.jpg
        assertEquals(listOf("00000.bin"), dir.list()!!.toList())
        assertFalse(File(tmp.root.parentFile, "evil.jpg").exists())
        assertFalse(File(tmp.root, "evil.jpg").exists())
    }

    @Test
    fun `非图片条目被跳过并计数`() {
        val r = unpack(
            zip(
                "readme.txt" to bytes(5),
                "0001.jpg" to bytes(6),
                "meta/info.json" to bytes(7),
            ),
        )
        assertEquals(1, r.entries.size)
        assertTrue(r.note.contains("跳过 2"))
    }

    @Test
    fun `目录条目不算页也不计入跳过`() {
        val r = unpack(
            zip(
                "第01章/" to bytes(0),
                "第01章/0001.jpg" to bytes(9),
            ),
        )
        assertEquals(1, r.entries.size)
        assertEquals("", r.note)
    }

    @Test
    fun `0 字节图片条目不算页`() {
        val r = unpack(zip("a.jpg" to ByteArray(0), "b.jpg" to bytes(4)))
        assertEquals(listOf("b.jpg"), r.entries.map { it.name })
    }

    @Test
    fun `全是大写扩展名也认`() {
        val r = unpack(zip("A.JPG" to bytes(3), "b.PNG" to bytes(3)))
        assertEquals(2, r.entries.size)
    }

    @Test
    fun `没有图片时明确报错`() {
        var msg = ""
        try {
            unpack(zip("a.txt" to bytes(3)))
        } catch (e: IOException) {
            msg = e.message.orEmpty()
        }
        assertTrue("应提示包内没有图片：$msg", msg.contains("没有可显示的图片"))
    }

    @Test
    fun `损坏的 zip 会抛错而不是静默产出空结果`() {
        var threw = false
        try {
            unpack(ByteArray(64) { 0x7F })
        } catch (e: Exception) {
            threw = true
        }
        assertTrue(threw)
    }

    @Test
    fun `超过页数上限时明确告知而不是静默截断`() {
        val r = ZipUnpacker.unpack(
            ByteArrayInputStream(zip("a.jpg" to bytes(3), "b.jpg" to bytes(3), "c.jpg" to bytes(3))),
            File(tmp.root, "pages"),
            maxPages = 2,
        )
        assertEquals(2, r.entries.size)
        assertTrue("必须说明被截断：${r.note}", r.note.contains("过大"))
    }

    @Test
    fun `超过字节上限时停止并告知`() {
        // 上限设在「第一页刚好放得下、第二页放不下」的位置
        val r = ZipUnpacker.unpack(
            ByteArrayInputStream(zip("a.jpg" to bytes(100), "b.jpg" to bytes(100))),
            File(tmp.root, "pages"),
            maxBytes = 150,
        )
        assertEquals("第一页应完整写出", listOf("a.jpg"), r.entries.map { it.name })
        assertEquals(100L, r.totalBytes)
        assertTrue("必须说明被截断：${r.note}", r.note.contains("过大"))
    }

    @Test
    fun `进度回调单调递增且不超过提示总大小`() {
        val seen = mutableListOf<Pair<Long, Long>>()
        ZipUnpacker.unpack(
            ByteArrayInputStream(zip("a.jpg" to bytes(10), "b.jpg" to bytes(10))),
            File(tmp.root, "pages"),
            totalHint = 1000L,
            onProgress = { d, t -> seen.add(d to t) },
        )
        assertEquals(2, seen.size)
        assertEquals(listOf(10L, 20L), seen.map { it.first })
        assertTrue(seen.all { it.second == 1000L })
    }

    @Test
    fun `章节按第一级目录划分`() {
        val ch = ChapterSplitter.chaptersOf(
            listOf("第01章/1.jpg", "第01章/2.jpg", "第02章/1.jpg"),
        )
        assertEquals(listOf("第01章" to 2, "第02章" to 1), ch.map { it.name to it.pages })
    }

    @Test
    fun `平铺的包只有一章`() {
        val ch = ChapterSplitter.chaptersOf(listOf("1.jpg", "2.jpg"))
        assertEquals(1, ch.size)
        assertEquals("正文", ch[0].name)
        assertEquals(2, ch[0].pages)
    }

    @Test
    fun `空列表没有章节`() {
        assertTrue(ChapterSplitter.chaptersOf(emptyList()).isEmpty())
    }
}
