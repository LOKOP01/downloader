package com.xd.vdl.core.download

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 页数对账。数字取自实测本子 350234：解析显示 50 页（两章合计），
 * 用户实际只拿到 25 页（第一章）。这种差额必须被说出来。
 */
class JmReconcileTest {

    @Test
    fun `少一半时给出具体差额`() {
        val msg = JmReconcile.describe(expected = 50, actual = 25)
        assertTrue(msg.contains("25"))
        assertTrue(msg.contains("50"))
        assertTrue(msg.contains("少 25 张"))
    }

    @Test
    fun `数量一致时不打扰用户`() {
        assertEquals("", JmReconcile.describe(50, 50))
    }

    @Test
    fun `多出几张也算正常`() {
        // 服务端补了番外页时不该报警
        assertEquals("", JmReconcile.describe(50, 52))
    }

    @Test
    fun `预期未知时不下结论`() {
        assertEquals("", JmReconcile.describe(0, 25))
        assertEquals("", JmReconcile.describe(50, 0))
    }

    @Test
    fun `isShort 与文案一致`() {
        assertTrue(JmReconcile.isShort(50, 25))
        assertFalse(JmReconcile.isShort(50, 50))
        assertFalse(JmReconcile.isShort(50, 60))
        assertFalse(JmReconcile.isShort(0, 0))
    }
}
