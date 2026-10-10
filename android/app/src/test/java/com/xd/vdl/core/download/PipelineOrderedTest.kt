package com.xd.vdl.core.download

import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 滑动窗口流水线的两条性质：**顺序不乱** + **并发有上限**。
 *
 * 这两条正是禁漫下载从串行改成并发后必须守住的：顺序乱了 zip 里页码就错，
 * 并发没上限则会把手机内存打爆。真实下载没法单测，所以逻辑本身单测。
 */
class PipelineOrderedTest {

    @Test
    fun resultsArriveInInputOrder() = runBlocking {
        val got = mutableListOf<Int>()
        // 故意让靠前的元素慢、靠后的元素快，乱序的话结果一定看得出来
        pipelineOrdered(
            items = (1..60).toList(),
            concurrency = 4,
            transform = { idx, value ->
                delay(if (idx < 30) 6L else 1L)
                value
            },
            consume = { _, _, result -> got += result },
        )
        assertEquals((1..60).toList(), got)
    }

    @Test
    fun concurrencyIsBoundedByWidth() = runBlocking {
        var live = 0
        var peak = 0
        val lock = Mutex()
        pipelineOrdered(
            items = (1..40).toList(),
            concurrency = 3,
            transform = { _, value ->
                lock.withLock {
                    live++
                    if (live > peak) peak = live
                }
                delay(4)
                lock.withLock { live-- }
                value
            },
            consume = { _, _, _ -> },
        )
        assertTrue("峰值并发 $peak 不该超过 3", peak <= 3)
        assertTrue("峰值并发 $peak 说明根本没并行起来", peak >= 2)
    }

    @Test
    fun onStartRunsSeriallyInInputOrder() = runBlocking {
        val started = mutableListOf<Int>()
        pipelineOrdered(
            items = listOf("a", "b", "c", "d", "e"),
            concurrency = 2,
            onStart = { _, item -> started += item.length + item[0].code },
            transform = { _, item -> item },
            consume = { _, _, _ -> },
        )
        assertEquals(5, started.size)
    }

    @Test
    fun everyItemIsConsumed() = runBlocking {
        var count = 0
        pipelineOrdered(
            items = (1..7).toList(),
            concurrency = 3,
            transform = { _, value -> value },
            consume = { _, _, _ -> count++ },
        )
        assertEquals(7, count)
    }

    @Test
    fun singleWidthStillWorks() = runBlocking {
        val got = mutableListOf<Int>()
        pipelineOrdered(
            items = (1..5).toList(),
            concurrency = 1,
            transform = { _, value -> value },
            consume = { _, _, result -> got += result },
        )
        assertEquals((1..5).toList(), got)
    }

    /**
     * 这组数字就是「安卓比 PC 慢」的量化说明：每张图假设 40ms 的等待，
     * 串行是 n×40ms，4 路并发约等于 n/4×40ms。真实下载里等待来自网络，
     * 与这里的 delay 同一个形状，所以提速比例可以直接类比。
     */
    @Test
    fun pipelineOverlapsWaits() = runBlocking {
        val n = 40
        val perItemMs = 40L

        val serialStart = System.currentTimeMillis()
        pipelineOrdered(
            items = (1..n).toList(),
            concurrency = 1,
            transform = { _, value -> delay(perItemMs); value },
            consume = { _, _, _ -> },
        )
        val serialMs = System.currentTimeMillis() - serialStart

        val parallelStart = System.currentTimeMillis()
        pipelineOrdered(
            items = (1..n).toList(),
            concurrency = 4,
            transform = { _, value -> delay(perItemMs); value },
            consume = { _, _, _ -> },
        )
        val parallelMs = System.currentTimeMillis() - parallelStart

        println("串行 ${serialMs}ms → 4 路并发 ${parallelMs}ms（${n} 张 × ${perItemMs}ms）")
        assertTrue("并发没有提速：串行 ${serialMs}ms / 并发 ${parallelMs}ms", parallelMs * 2 < serialMs)
    }
}
