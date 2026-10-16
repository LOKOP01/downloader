package com.xd.vdl.core

import java.io.File
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

/**
 * 日志落盘器：有界队列 + 单个写线程。
 *
 * 从 [AppLog] 抽出来是为了**能在 JVM 单测里直接验证**（AppLog 还依赖
 * android.util.Log，测试里会走桩实现）。
 *
 * ## 为什么要有 pending 计数
 *
 * 最初的 flush 用「队列是否为空」判断是否写完，这是**错的**：
 * 写线程 `poll` 之后队列就空了，但文件可能还没写完（甚至还没开始写）。
 * 于是 flush 立刻返回，调用方以为日志已落盘，实际上最后几条还在路上 ——
 * 进程紧接着被杀就丢了。
 *
 * 现在用 [pending]（已入队但尚未写完的条数）作为判据：
 * `submit` 先加、写完再减，`flush` 等它归零。
 *
 * ## 队列满与异常
 *
 * - 队列满：丢弃该条并累加 [droppedLines]，**绝不阻塞调用方**
 *   （日志写不进去不该拖慢下载或卡住 UI 线程）。
 * - 写失败：`runCatching` 吞掉，但 pending 仍然递减（`finally`），
 *   否则 flush 会一直等到超时。
 */
internal class LogSink(
    private val maxFileBytes: Long = 512 * 1024,
    queueCapacity: Int = 512,
) {

    private val queue = ArrayBlockingQueue<String>(queueCapacity)

    /** 已入队但尚未写完的条数；flush 的唯一判据 */
    private val pending = AtomicInteger()

    private val dropped = AtomicLong()

    @Volatile
    private var file: File? = null

    @Volatile
    private var writer: Thread? = null

    @Volatile
    private var closed = false

    private val startLock = Any()

    fun setFile(target: File?) {
        file = target
    }

    fun hasPending(): Boolean = pending.get() > 0

    fun droppedLines(): Long = dropped.get()

    /**
     * 交给写线程落盘。
     *
     * @return true 表示已入队；false 表示已关闭或队列满（被丢弃）
     */
    fun submit(line: String): Boolean {
        if (closed || file == null) return false
        ensureWriter()
        // ⚠️ 顺序很重要：**先加 pending 再入队**。
        // 反过来的话，写线程可能在 increment 之前就取走并 decrement，
        // 使 pending 变成 -1，flush 会误判为「已写完」。
        pending.incrementAndGet()
        if (!queue.offer(line)) {
            pending.decrementAndGet()
            dropped.incrementAndGet()
            return false
        }
        return true
    }

    private fun ensureWriter() {
        if (writer != null) return
        synchronized(startLock) {
            if (writer != null) return
            val thread = Thread({ drainLoop() }, "vdl-log")
            // daemon：进程退出时不阻塞（队列里剩下的日志由 flush 负责）
            thread.isDaemon = true
            thread.start()
            writer = thread
        }
    }

    private fun drainLoop() {
        while (true) {
            val line = try {
                queue.poll(1, TimeUnit.SECONDS)
            } catch (e: InterruptedException) {
                // close() 的中断：退出循环，剩余日志由 close 的 flush 处理
                Thread.currentThread().interrupt()
                break
            } ?: continue
            try {
                writeLine(line)
            } finally {
                // 无论成功失败都要减，否则 flush 永远等不到 0
                pending.decrementAndGet()
            }
        }
    }

    private fun writeLine(line: String) {
        val f = file ?: return
        runCatching {
            if (f.exists() && f.length() > maxFileBytes) {
                val backup = File(f.parentFile, f.name + ".1")
                if (backup.exists()) backup.delete()
                f.renameTo(backup)
            }
            f.appendText(line + "\n", Charsets.UTF_8)
        }
    }

    /**
     * 等待队列排空。
     *
     * @return true 表示已全部写完；false 表示超时或线程被中断（仍有未落盘的日志）
     */
    fun flush(timeoutMs: Long): Boolean {
        if (pending.get() == 0) return true
        val deadline = System.nanoTime() + timeoutMs * 1_000_000L
        while (pending.get() > 0) {
            if (System.nanoTime() >= deadline) return false
            try {
                Thread.sleep(2)
            } catch (e: InterruptedException) {
                Thread.currentThread().interrupt()
                return false
            }
        }
        return true
    }

    /**
     * 停止接收新日志，尽力把队列写完，然后让写线程退出。
     *
     * 顺序不能反：先 interrupt 会让写线程立刻退出，队列里剩下的日志就丢了。
     */
    fun close(timeoutMs: Long = 300) {
        closed = true
        flush(timeoutMs)
        writer?.interrupt()
        writer = null
        file = null
    }
}
