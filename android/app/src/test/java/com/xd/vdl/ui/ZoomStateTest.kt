package com.xd.vdl.ui

import androidx.compose.ui.geometry.Offset
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 预览缩放的钳制逻辑。
 *
 * 手势本身（单指滑动放给 pager、放大后才接管）没法在 JVM 单测里跑，但真正容易写错的
 * 是这里的数值边界：缩放的上下限、平移不许把图拖出可视区、缩回 1 倍要清掉残留平移。
 * 本机没有 adb 设备，这几条就靠单测兜底。
 */
class ZoomStateTest {

    private val viewW = 400f
    private val viewH = 800f

    @Test
    fun zoomIsClampedToRange() {
        val z = ZoomState()
        z.applyZoom(100f, viewW, viewH)
        assertEquals(MAX_SCALE, z.scale, 0.001f)

        z.applyZoom(0.0001f, viewW, viewH)
        assertEquals(MIN_SCALE, z.scale, 0.001f)
    }

    @Test
    fun panIsIgnoredWhenNotZoomed() {
        val z = ZoomState()
        z.panBy(50f, 50f, viewW, viewH)
        assertEquals(0f, z.offset.x, 0.001f)
        assertEquals(0f, z.offset.y, 0.001f)
        assertFalse(z.zoomed)
    }

    @Test
    fun panIsClampedWhenZoomed() {
        val z = ZoomState()
        z.applyZoom(2f, viewW, viewH)
        z.panBy(10_000f, -10_000f, viewW, viewH)
        // 2 倍时每边最多多出 (scale-1)/2 个视口宽/高
        assertEquals(viewW * (2f - 1f) / 2f, z.offset.x, 0.001f)
        assertEquals(-(viewH * (2f - 1f) / 2f), z.offset.y, 0.001f)
    }

    @Test
    fun doubleTapTogglesZoom() {
        val z = ZoomState()
        z.toggleDoubleTap()
        assertEquals(DOUBLE_TAP_SCALE, z.scale, 0.001f)
        assertTrue(z.zoomed)

        z.toggleDoubleTap()
        assertEquals(1f, z.scale, 0.001f)
        assertFalse(z.zoomed)
    }

    @Test
    fun resetClearsScaleAndOffset() {
        val z = ZoomState()
        z.applyZoom(3f, viewW, viewH)
        z.panBy(10f, 20f, viewW, viewH)
        z.reset()
        assertEquals(1f, z.scale, 0.001f)
        assertEquals(Offset.Zero, z.offset)
    }

    @Test
    fun zoomingBackOutDropsLeftoverPan() {
        val z = ZoomState()
        z.applyZoom(3f, viewW, viewH)
        z.panBy(100f, 100f, viewW, viewH)
        assertTrue(z.offset != Offset.Zero)

        z.applyZoom(1f / 3f, viewW, viewH)
        assertEquals(1f, z.scale, 0.001f)
        assertEquals(Offset.Zero, z.offset)
    }
}
