package com.xd.vdl.core.download

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 「按实测页大小反推并发路数」。
 *
 * 并发上限是**内存**约束：每路峰值 ≈ 两张全尺寸位图（原图 + 重排副本）+ 固定开销。
 * 实测（本子 1480165）单页 768×1344 ≈ 3.8 MB，8 路约 66 MB；但禁漫有不少高清扫图
 * （1600×2400 → 15 MB/页），那种本子 8 路要 ~250 MB，低端机会被系统杀。
 * 所以按 96 MB 的位图预算反推，超了就自动收窄。
 */
class JmConcurrencyTest {

    // 收 Int / Double 两种字面量，省得每处都写小数点
    private fun mb(n: Number) = (n.toDouble() * 1024 * 1024).toLong()

    @Test
    fun noMeasurementYetUsesMax() {
        assertEquals(8, JmDownloader.concurrencyFor(0))
    }

    @Test
    fun typicalPageKeepsEight() {
        // 实测常见页：3.8 MB/页 → 每路 ~8.6 MB → 96/8.6 ≈ 11 → 封顶 8
        assertEquals(8, JmDownloader.concurrencyFor(mb(3.8)))
        assertEquals(8, JmDownloader.concurrencyFor(mb(4.0)))
    }

    @Test
    fun highResScanNarrows() {
        // 1600×2400 ARGB_8888 = 15.3 MB/页 → 每路 ~31.6 MB → 96/31.6 ≈ 3
        assertEquals(3, JmDownloader.concurrencyFor(mb(15.0)))
    }

    @Test
    fun hugePageFloorsAtTwo() {
        // 再大也得留 2 路，否则退化成串行、白等网络
        assertEquals(2, JmDownloader.concurrencyFor(mb(30.0)))
        assertEquals(2, JmDownloader.concurrencyFor(mb(200.0)))
    }

    @Test
    fun neverExceedsCapAndNeverBelowFloor() {
        // ⚠️ 步进必须**严格递增**：早先写成 `b = (b * 1.5).toLong()`，从 1 起步
        // 永远是 1（1 * 1.5 = 1.5 → toLong = 1），直接死循环把 Gradle 挂死 10 分钟。
        var b = 1L
        while (b < mb(400)) {
            val n = JmDownloader.concurrencyFor(b)
            assertTrue("并发 $n 越界（$b 字节/页）", n in 2..8)
            b *= 2
        }
    }

    @Test
    fun monotonicNonIncreasing() {
        // 页越大，路数只能不变或更少
        var last = JmDownloader.concurrencyFor(1)
        var b = 1L
        while (b < mb(60)) {
            val n = JmDownloader.concurrencyFor(b)
            assertTrue("页变大反而开更多路：$b 字节 → $n（上一档 $last）", n <= last)
            last = n
            b *= 2
        }
    }
}
