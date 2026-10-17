package com.xd.vdl.core.download

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 官方打包完整性判定。
 *
 * 用例里的数字不是编的，是实测本子 350234 的真实返回值：
 *
 *     /album?id=350234     → total_photos = 50，series = 2 章
 *     /chapter?id=350234   → 25 张
 *     /chapter?id=350235   → 25 张
 *
 * 用户报的现象「显示 50 页、下载只有 25 页」正是 [coversAllChapters] 缺失时会发生的事。
 */
class JmPackCheckTest {

    @Test
    fun `多章本子只打包了首章时必须拒绝`() {
        // 真实场景：350234 两章共 50 张，官方包只给了第一章的 25 张
        assertFalse(JmPackCheck.coversAllChapters(actual = 25, expected = 50))
    }

    @Test
    fun `覆盖全部章节时接受`() {
        assertTrue(JmPackCheck.coversAllChapters(actual = 50, expected = 50))
    }

    @Test
    fun `比预期更全时接受`() {
        // 服务端把番外/附页一并打包时，张数会多于逐章合计
        assertTrue(JmPackCheck.coversAllChapters(actual = 52, expected = 50))
    }

    @Test
    fun `空包永远拒绝`() {
        assertFalse(JmPackCheck.coversAllChapters(actual = 0, expected = 50))
        assertFalse(JmPackCheck.coversAllChapters(actual = 0, expected = 0))
        assertFalse(JmPackCheck.coversAllChapters(actual = -1, expected = 0))
    }

    @Test
    fun `目录没拉成时无从校验，不阻断下载`() {
        // expected = 0 表示逐章目录一条都没拿到：此时没有依据说官方包不全，
        // 采信它比直接失败更合理（后续逐张路径本身也走不通）
        assertTrue(JmPackCheck.coversAllChapters(actual = 30, expected = 0))
    }

    @Test
    fun `单章本子不受影响`() {
        // 单章短篇：official 包与逐章页数一致，正常放行
        assertTrue(JmPackCheck.coversAllChapters(actual = 1, expected = 1))
        assertTrue(JmPackCheck.coversAllChapters(actual = 25, expected = 25))
    }

    @Test
    fun `少一张也拒绝`() {
        assertFalse(JmPackCheck.coversAllChapters(actual = 49, expected = 50))
    }

    @Test
    fun `原因文案带上具体张数便于排错`() {
        val msg = JmPackCheck.shortfallReason(25, 50)
        assertTrue(msg.contains("25"))
        assertTrue(msg.contains("50"))
        assertTrue(msg.contains("逐张"))
    }
}
