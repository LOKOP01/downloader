package com.xd.vdl.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * 异步日志落盘器回归测试（B-07 深化）。
 *
 * 重点验证原实现的两个缺陷：
 *  1. `flush()` 用「队列是否为空」判断，写线程已 poll 但尚未落盘时会**提前返回**；
 *  2. 写线程出错会让 pending 永远不清零，flush 只能等到超时。
 */
class LogSinkTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun readAll(f: File): List<String> =
        if (f.exists()) f.readLines().filter { it.isNotEmpty() } else emptyList()

    @Test fun flushWaitsUntilLinesAreActuallyOnDisk() {
        val file = File(tmp.root, "app.log")
        val sink = LogSink()
        sink.setFile(file)
        val total = 200
        repeat(total) { sink.submit("line-$it") }
        // 关键断言：flush 返回 true 之后，文件里必须已经有全部内容。
        // 原实现只等队列变空，文件往往还差最后几条。
        assertTrue("flush 应报告已落盘", sink.flush(5_000))
        assertEquals(total, readAll(file).size)
        sink.close()
    }

    @Test fun everySubmittedLineEndsUpOnDisk() {
        val file = File(tmp.root, "app.log")
        val sink = LogSink()
        sink.setFile(file)
        val lines = (1..50).map { "m-$it" }
        lines.forEach { sink.submit(it) }
        sink.flush(5_000)
        assertEquals(lines, readAll(file))
        sink.close()
    }

    @Test fun flushReturnsTrueImmediatelyWhenNothingPending() {
        val sink = LogSink()
        sink.setFile(File(tmp.root, "app.log"))
        assertFalse(sink.hasPending())
        val started = System.nanoTime()
        assertTrue(sink.flush(5_000))
        val elapsedMs = (System.nanoTime() - started) / 1_000_000
        // 无积压时不该有可观察的等待（给宽松上限，避免 CI 抖动误报）
        assertTrue("空队列 flush 耗时 ${elapsedMs}ms，不应等待", elapsedMs < 100)
        sink.close()
    }

    @Test fun writeFailureStillClearsPendingSoFlushCannotHang() {
        val target = File(tmp.root, "app.log")
        // 直接把目标路径做成**目录**：appendText 必然失败（FileNotFound/IOException）。
        target.mkdirs()
        val sink = LogSink()
        sink.setFile(target)
        repeat(20) { sink.submit("boom-$it") }
        // 写失败也必须把 pending 递减，否则这里会一直等到超时返回 false。
        assertTrue("写失败后 flush 不应挂住", sink.flush(3_000))
        assertFalse(sink.hasPending())
        sink.close()
    }

    @Test fun flushTimesOutAndReportsFailureWhenWriterCannotKeepUp() {
        val file = File(tmp.root, "app.log")
        val sink = LogSink()
        sink.setFile(file)
        // 大量写入 + 极短超时：允许返回 false（这正是「没写完」的诚实上报）
        repeat(500) { sink.submit("x".repeat(200) + "-$it") }
        val drained = sink.flush(1)
        if (!drained) {
            assertTrue("超时返回 false 时必然仍有积压", sink.hasPending())
        }
        // 收尾：给足时间确保线程退出，避免测试间互相干扰
        sink.flush(5_000)
        sink.close()
    }

    @Test fun concurrentSubmittersAllGetWritten() {
        val file = File(tmp.root, "app.log")
        val sink = LogSink()
        sink.setFile(file)
        val threads = 8
        val perThread = 40
        val start = CountDownLatch(1)
        val failures = AtomicInteger()
        val workers = (0 until threads).map { t ->
            Thread {
                start.await()
                repeat(perThread) { i ->
                    if (!sink.submit("t$t-$i")) failures.incrementAndGet()
                }
            }.apply { isDaemon = true; start() }
        }
        start.countDown()
        workers.forEach { it.join(10_000) }
        assertTrue("并发提交不应丢行（丢 $failures 条）", sink.flush(5_000))
        assertEquals(0, failures.get())
        assertEquals(threads * perThread, readAll(file).size)
        sink.close()
    }

    @Test fun closeDrainsPendingBeforeStoppingWriter() {
        val file = File(tmp.root, "app.log")
        val sink = LogSink()
        sink.setFile(file)
        repeat(120) { sink.submit("last-$it") }
        // close 的正确顺序是「先排空再中断」：反过来的话这里会少于 120 行。
        sink.close(5_000)
        assertEquals(120, readAll(file).size)
    }

    @Test fun submitAfterCloseIsRejectedInsteadOfSilentlyQueued() {
        val file = File(tmp.root, "app.log")
        val sink = LogSink()
        sink.setFile(file)
        sink.submit("before")
        sink.close(5_000)
        assertFalse("关闭后不应再接受日志", sink.submit("after"))
        assertEquals(listOf("before"), readAll(file))
    }

    @Test fun submitWithoutFileIsIgnored() {
        val sink = LogSink()
        // 未 setFile（AppLog.setup 之前）时不能启动线程，也不能计数
        assertFalse(sink.submit("nowhere"))
        assertFalse(sink.hasPending())
        sink.close()
    }

    @Test fun rotationKeepsFileBoundedAndPreservesBackup() {
        val file = File(tmp.root, "app.log")
        val sink = LogSink(maxFileBytes = 2 * 1024, queueCapacity = 256)
        sink.setFile(file)
        repeat(200) { sink.submit("y".repeat(100) + "-$it") }
        assertTrue(sink.flush(5_000))
        sink.close()
        val backup = File(tmp.root, "app.log.1")
        assertTrue("超过上限应产生轮转备份", backup.exists())
        assertTrue("轮转后主文件不应无限增长", file.length() <= 4 * 1024)
    }
}
