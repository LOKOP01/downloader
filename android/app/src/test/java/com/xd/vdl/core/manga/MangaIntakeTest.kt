package com.xd.vdl.core.manga

import com.xd.vdl.core.Platform
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 入库转换测试。
 *
 * 这里覆盖的是任务书里点名的几条验收点：
 *  · 「不得将下载任务已创建、部分图片已下载或任务异常结束误判为整章下载成功」
 *    → [MangaIntake] 只负责转换，**是否入库由调用方用 TaskStatus.DONE 把关**（见
 *      `DownloadTask.readableAsManga` 的测试）；这里验证转换本身不产生歧义；
 *  · 「同一本书重复下载不产生重复记录」→ key 的稳定性；
 *  · 「不同漫画之间不发生文件冲突」→ 不同文件/目录算出不同 key。
 */
class MangaIntakeTest {

    @Test
    fun `压缩包入库带上目录与文件名构成的稳定 key`() {
        val b = MangaIntake.zip(
            title = "某本子",
            platform = Platform.JMCOMIC,
            uri = "content://media/external/downloads/7",
            dirName = "视频下载器",
            fileName = "422866.zip",
            sourceUrl = "jmcomic://album/422866",
            now = 111L,
        )
        assertEquals(MangaKeys.zip("视频下载器", "422866.zip"), b.key)
        assertEquals(BookKind.ZIP, b.kind)
        assertEquals(listOf("content://media/external/downloads/7"), b.uris)
        assertEquals(111L, b.addedAt)
        assertEquals("jmcomic://album/422866", b.sourceUrl)
    }

    @Test
    fun `重复下载同一本产生的 key 相同`() {
        fun make() = MangaIntake.zip("t", Platform.JMCOMIC, "content://a/1", "d", "422866.zip")
        assertEquals(make().key, make().key)
    }

    @Test
    fun `MediaStore 去重后的第二个包不会与第一个串号`() {
        // 下载目录里已存在 422866.zip 时，MediaStore 会另存为 422866(1).zip
        val one = MangaIntake.zip("t", Platform.JMCOMIC, "content://a/1", "d", "422866.zip")
        val two = MangaIntake.zip("t", Platform.JMCOMIC, "content://a/2", "d", "422866(1).zip")
        assertTrue(one.key != two.key)
    }

    @Test
    fun `不同目录下的同名包不会互相冲突`() {
        val a = MangaIntake.zip("t", Platform.JMCOMIC, "content://a/1", "目录甲", "1.zip")
        val b = MangaIntake.zip("t", Platform.JMCOMIC, "content://a/2", "目录乙", "1.zip")
        assertTrue(a.key != b.key)
    }

    @Test
    fun `标题为空时回退成文件名`() {
        val b = MangaIntake.zip("", Platform.JMCOMIC, "content://a/1", "d", "422866.zip")
        assertEquals("422866", b.title)
    }

    @Test
    fun `多章信息写进记录`() {
        val b = MangaIntake.zip(
            "t", Platform.JMCOMIC, "content://a/1", "d", "1.zip",
            chapters = listOf(MangaChapter("第01话", 0), MangaChapter("第02话", 0)),
        )
        assertEquals(2, b.chapters.size)
        // 页数还没解包，必须是 0 而不是编造一个数字
        assertEquals(0, b.chapters[0].pages)
        assertEquals(0, b.pageCount)
    }

    @Test
    fun `散图入库保持原始页序`() {
        val uris = listOf("content://a/3", "content://a/1", "content://a/2")
        val b = MangaIntake.images("图集", Platform.XIAOHONGSHU, uris)!!
        assertEquals(uris, b.uris)
        assertEquals(BookKind.FOLDER, b.kind)
        assertEquals(uris.first(), b.uri)
    }

    @Test
    fun `只有一张图或没有图时不入库`() {
        assertNull(MangaIntake.images("单张", Platform.X, listOf("content://a/1")))
        assertNull(MangaIntake.images("空", Platform.X, emptyList()))
        assertNull(MangaIntake.images("空串", Platform.X, listOf("", "")))
        assertNotNull(MangaIntake.images("两张", Platform.X, listOf("content://a/1", "content://a/2")))
    }

    @Test
    fun `散图 key 在同一平台同一标题下稳定且跨平台不同`() {
        val a = MangaIntake.images("同名图集", Platform.INSTAGRAM, listOf("a", "b"))!!
        val b = MangaIntake.images("同名图集", Platform.INSTAGRAM, listOf("x", "y"))!!
        val c = MangaIntake.images("同名图集", Platform.X, listOf("a", "b"))!!
        assertEquals(a.key, b.key)
        assertTrue(a.key != c.key)
    }

    @Test
    fun `回源地址按类型生成`() {
        assertEquals("jmcomic://album/123", MangaIntake.jmSourceUrl("123", "album"))
        assertEquals("jmcomic://photo/123", MangaIntake.jmSourceUrl("123", "photo"))
        assertEquals("jmcomic://photo/123", MangaIntake.jmSourceUrl("123", ""))
        assertEquals("", MangaIntake.jmSourceUrl("", "album"))
    }
}
