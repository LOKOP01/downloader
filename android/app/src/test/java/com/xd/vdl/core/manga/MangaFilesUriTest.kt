package com.xd.vdl.core.manga

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * file:// URI 解析。
 *
 * 这是「点删除没反应」的根源之一：`File.toURI()` 会把中文编码成 `%E4%B8%AD`，
 * 若不解码就会去操作一个名字字面量是百分号串的文件（于是既删不掉、也判不出存在）。
 * 本项目下载目录中文名很常见（默认就叫「视频下载器」），必须覆盖。
 */
class MangaFilesUriTest {

    @Test
    fun `普通 file URI 可还原`() {
        assertEquals(
            File("/storage/emulated/0/Download/a.zip"),
            MangaFiles.fileOf("file:///storage/emulated/0/Download/a.zip"),
        )
    }

    @Test
    fun `中文路径的百分号编码必须解码`() {
        // 直接给 Android 上真实会出现的编码形态（不依赖 File.toURI：
        // Windows 上的 toURI 不编码中文，会把测试变成平台相关的假阳性）
        val uri = "file:///storage/emulated/0/Download/%E8%A7%86%E9%A2%91%E4%B8%8B%E8%BD%BD%E5%99%A8/" +
            "%E6%88%91%E7%9A%84%E6%9C%AC%E5%AD%90.zip"
        assertEquals(
            File("/storage/emulated/0/Download/视频下载器/我的本子.zip"),
            MangaFiles.fileOf(uri),
        )
    }

    @Test
    fun `空格路径也能还原`() {
        assertEquals(
            File("/tmp/my book/1.jpg"),
            MangaFiles.fileOf("file:///tmp/my%20book/1.jpg"),
        )
    }

    @Test
    fun `未编码的中文路径也照常工作`() {
        // 有些调用方直接拼 File.absolutePath，中间没有百分号编码
        assertEquals(
            File("/storage/emulated/0/Download/视频下载器/a.zip"),
            MangaFiles.fileOf("file:///storage/emulated/0/Download/视频下载器/a.zip"),
        )
    }

    @Test
    fun `localhost 前缀被忽略`() {
        assertEquals(
            File("/data/x.zip"),
            MangaFiles.fileOf("file://localhost/data/x.zip"),
        )
    }

    @Test
    fun `非 file URI 返回 null`() {
        assertNull(MangaFiles.fileOf("content://media/external/downloads/1"))
        assertNull(MangaFiles.fileOf(""))
        assertNull(MangaFiles.fileOf("file://"))
    }

    @Test
    fun `类型判定`() {
        assertTrue(MangaFiles.isFile("file:///a"))
        assertTrue(MangaFiles.isContent("content://a/1"))
        assertEquals(false, MangaFiles.isFile("content://a/1"))
    }
}
