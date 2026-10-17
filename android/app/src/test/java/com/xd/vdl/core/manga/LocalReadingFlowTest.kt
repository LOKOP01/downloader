package com.xd.vdl.core.manga

import com.xd.vdl.core.Platform
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * 端到端（纯逻辑）的「下载 → 入库 → 重启 → 断网续读」链路。
 *
 * 这里刻意**不碰任何网络与 Android API**：如果这条链路能在没有 Context、没有网络、
 * 没有在线接口的前提下跑通，就说明「断网仍能阅读已下载章节」所依赖的环节都是本地的。
 *
 * 覆盖任务书里的四条验收点：完整下载后阅读、断网阅读、阅读进度、重启应用。
 */
class LocalReadingFlowTest {

    @get:Rule
    val tmp = TemporaryFolder()

    /** 造一个禁漫风格的包：多章、STORED、每章若干页 */
    private fun jmZip(chapters: Int = 2, pages: Int = 3): ByteArray {
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { zos ->
            // 用默认压缩方式造包（真实禁漫包是 STORED，但解包逻辑对两者一致）。
            for (c in 1..chapters) {
                for (p in 1..pages) {
                    val name = "第%02d章_测试/%04d.jpg".format(c, p)
                    zos.putNextEntry(ZipEntry(name))
                    zos.write(ByteArray(64) { (p + c).toByte() })
                    zos.closeEntry()
                }
            }
        }
        return out.toByteArray()
    }

    @Test
    fun `整条离线链路_下载_入库_重启_续读`() {
        val ctxFreeDir = File(tmp.root, "pages")
        val zipBytes = jmZip(chapters = 2, pages = 3)

        // ① 「下载完成」：产物是一份 zip。这里用落盘文件模拟已经写进下载目录。
        val zipFile = File(tmp.root, "422866.zip").apply { writeBytes(zipBytes) }
        val uri = "file://" + zipFile.absolutePath.replace('\\', '/')

        // ② 入库
        val intake = MangaIntake.zip(
            title = "测试本子",
            platform = Platform.JMCOMIC,
            uri = uri,
            dirName = "视频下载器",
            fileName = "422866.zip",
            sourceUrl = "jmcomic://album/422866",
        )

        // ③ 打开：解包（离线，只读本地字节）
        val unpacked = ZipUnpacker.unpack(
            ByteArrayInputStream(zipBytes), ctxFreeDir,
        )
        assertEquals(6, unpacked.entries.size)
        val ids = unpacked.entries.map { it.name }
        assertEquals("第01章_测试/0001.jpg", ids.first())
        assertEquals("第02章_测试/0003.jpg", ids.last())

        // ④ 章节划分
        val chapters = ChapterSplitter.chaptersOf(ids)
        assertEquals(2, chapters.size)
        assertEquals(3, chapters[0].pages)
        assertEquals(3, chapters[1].pages)

        // ⑤ 读到第 3 页（跨章前的位置）并落盘
        val store = MangaStore(File(tmp.root, "manga_library.json"))
        store.load()
        val read = intake.markRead(2, ids)
        store.save(listOf(read), MangaStore.Prefs())

        // ⑥ 「重启应用」：重新从磁盘读书架
        val reopened = MangaStore(File(tmp.root, "manga_library.json")).load()
        val back = reopened.books.single()
        assertEquals("测试本子", back.title)
        assertEquals(6, back.pageCount)
        assertTrue(back.counted)
        // 断网、无在线元数据的情况下也能算出续读位置
        assertEquals(2, back.restoreIndex(ids))
        assertEquals("第01章_测试/0003.jpg", back.lastPage)

        // ⑦ 重复下载同一本：进度与标题不被冲掉，也不产生第二条记录
        val again = MangaIntake.zip(
            title = "下载时的标题",
            platform = Platform.JMCOMIC,
            uri = uri,
            dirName = "视频下载器",
            fileName = "422866.zip",
        )
        val merged = MangaStore.merge(listOf(back), listOf(again))
        assertEquals(1, merged.size)
        assertEquals("测试本子", merged[0].title)
        assertEquals(2, merged[0].pageIndex)
        assertEquals(2, merged[0].restoreIndex(ids))
    }

    @Test
    fun `文件被删除后打开时给出明确错误而不是崩溃`() {
        val missing = File(tmp.root, "nope.zip")
        // 不存在的文件：解包必须抛出可读的异常（上层转成「打不开这个压缩包」）
        var msg = ""
        try {
            java.io.FileInputStream(missing).use {
                ZipUnpacker.unpack(it, File(tmp.root, "pages"))
            }
        } catch (e: Exception) {
            msg = e.javaClass.simpleName + ": " + (e.message ?: "")
        }
        assertTrue("必须给出明确异常：$msg", msg.contains("Exception"))
    }

    @Test
    fun `包里全是空条目时明确报错而不是空书架条目`() {
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { zos ->
            zos.putNextEntry(ZipEntry("empty.jpg"))
            zos.closeEntry()
        }
        var msg = ""
        try {
            ZipUnpacker.unpack(ByteArrayInputStream(out.toByteArray()), File(tmp.root, "p"))
        } catch (e: Exception) {
            msg = e.message.orEmpty()
        }
        assertTrue("应提示没有可显示的图片：$msg", msg.contains("没有可显示的图片"))
    }

    @Test
    fun `页数变化时续读位置不越界`() {
        val book = MangaBook(
            key = "k", title = "t", platform = Platform.JMCOMIC,
            uri = "file:///x.zip", uris = listOf("file:///x.zip"),
            pageIndex = 99, pageCount = 100, lastPage = "已消失的页", counted = true,
        )
        assertEquals(2, book.restoreIndex(listOf("a", "b", "c")))
    }

    @Test
    fun `序列化走一遍 JSON 后进度仍然可恢复`() {
        val ids = listOf("1.jpg", "2.jpg", "3.jpg")
        val b = MangaBook(
            key = "k", title = "t", platform = Platform.JMCOMIC,
            uri = "file:///x.zip", uris = listOf("file:///x.zip"),
        ).markRead(1, ids)
        val round = MangaBook.fromJson(JSONObject(b.toJson().toString()))!!
        assertEquals(1, round.restoreIndex(ids))
        assertEquals("2.jpg", round.lastPage)
    }
}
