package com.xd.vdl.core.download

import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * 官方打包直链拿回来的文件要验一下再采信。
 *
 * 直链不可用时服务端可能回一段 HTML（200 + text/html），直接当成品存下去，
 * 用户拿到的就是个打不开的「zip」。这里把「不是压缩包」「是包但没图」两种情况钉住。
 */
class JmOfficialZipCheckTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun zipOf(vararg names: String): File {
        val f = tmp.newFile("t.zip")
        ZipOutputStream(f.outputStream()).use { z ->
            names.forEach { n ->
                z.putNextEntry(ZipEntry(n))
                z.write(ByteArray(4))
                z.closeEntry()
            }
        }
        return f
    }

    @Test
    fun countsOnlyImageEntries() {
        val f = zipOf(
            "第01章/0001.jpg", "第01章/0002.webp", "第01章/0003.PNG",
            "readme.txt", "info.json",
        )
        assertEquals(3, JmDownloader.countImageEntries(f))
    }

    @Test
    fun htmlErrorPageCountsAsZero() {
        val f = tmp.newFile("err.zip")
        f.writeText("<html><body>404 not found</body></html>")
        assertEquals(0, JmDownloader.countImageEntries(f))
    }

    @Test
    fun emptyZipCountsAsZero() {
        assertEquals(0, JmDownloader.countImageEntries(zipOf()))
    }

    @Test
    fun directoryEntriesAreIgnored() {
        val f = zipOf("第01章/", "第01章/0001.jpg")
        assertEquals(1, JmDownloader.countImageEntries(f))
    }

    @Test
    fun missingFileDoesNotThrow() {
        assertEquals(0, JmDownloader.countImageEntries(File(tmp.root, "nope.zip")))
    }
}
