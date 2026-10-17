package com.xd.vdl.core.parse

import com.xd.vdl.core.Platform
import com.xd.vdl.core.model.VideoInfo
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * 首页页数文案。
 *
 * 用户报「显示 50 页、下载只有 25 页」时，50 是 `/album` 的 `total_photos`，
 * 它等于**各章页数之和**（实测本子 350234：两章各 25 张）。多章本子只写「50 页」
 * 会让人以为 50 页都在同一章里，所以文案必须点明是合计。
 */
class JmPageCountTextTest {

    private fun info(pages: Int, chapters: Int) = VideoInfo(
        platform = Platform.JMCOMIC,
        title = "t",
        jmId = "350234",
        jmKind = "album",
        jmPageCount = pages,
        jmChapterCount = chapters,
    )

    @Test
    fun `多章时页数标注为合计`() {
        assertEquals("2 章 · 共 50 页", JmPageCountText.describe(info(50, 2)))
    }

    @Test
    fun `单章时只说页数`() {
        assertEquals("共 25 页", JmPageCountText.describe(info(25, 1)))
    }

    @Test
    fun `页数未知时不编造`() {
        assertEquals("", JmPageCountText.describe(info(0, 2)))
    }

    @Test
    fun `非禁漫返回空串`() {
        val v = VideoInfo(platform = Platform.BILIBILI, title = "t", jmPageCount = 10, jmChapterCount = 2)
        assertEquals("", JmPageCountText.describe(v))
    }
}
