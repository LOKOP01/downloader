package com.xd.vdl.core

import android.util.Log
import com.xd.vdl.BuildConfig
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 运行日志：内存环形缓冲给「任务」页显示，同时写 filesDir/logs/app.log。
 * 任意线程可写；界面用 [lines] 收集。
 *
 * ⚠️ **磁盘写入在后台线程**（B-07 修）：原来 [add] 直接在调用线程上
 * `appendText` + 可能的重命名轮转。日志调用点有 60 多处，其中不少在 UI 回调里，
 * 等于主线程同步做文件 IO；日志量大时就是掉帧来源。
 * 现在只把行交给 [LogSink]，由单个写线程落盘。
 *
 * ⚠️ 时间戳在**调用线程**上格式化：SimpleDateFormat 不是线程安全的，
 * 原来多个线程共用同一个实例（注释还写着「任意线程可写」）。
 * 这里改为每次新建实例 —— 日志频率不高，这点分配可以接受。
 */
object AppLog {
    private const val TAG = "vdl"
    private const val MAX = 400
    private val lock = Any()
    private val buf = ArrayDeque<String>(MAX)
    private val _lines = MutableStateFlow<List<String>>(emptyList())
    val lines: StateFlow<List<String>> = _lines.asStateFlow()

    /** 实际落盘由它负责；抽出去是为了能在 JVM 单测里直接验证（见 LogSinkTest） */
    private val sink = LogSink()

    fun setup(dir: File) {
        dir.mkdirs()
        sink.setFile(File(dir, "app.log"))
        i("启动 v${BuildConfig.VERSION_NAME}（build ${BuildConfig.VERSION_CODE}）")
    }

    fun i(msg: String) = add("I", msg)
    fun w(msg: String) = add("W", msg)
    fun e(msg: String) = add("E", msg)

    fun clear() {
        synchronized(lock) {
            buf.clear()
            _lines.value = emptyList()
        }
    }

    private fun add(level: String, msg: String) {
        // 每次新建：SimpleDateFormat 非线程安全，共享实例在多线程下会产出错乱时间。
        val stamp = SimpleDateFormat("HH:mm:ss", Locale.CHINA).format(Date())
        val line = "$stamp $level  $msg"
        when (level) {
            "W" -> Log.w(TAG, msg)
            "E" -> Log.e(TAG, msg)
            else -> Log.i(TAG, msg)
        }
        synchronized(lock) {
            if (buf.size >= MAX) buf.removeFirst()
            buf.addLast(line)
            _lines.value = buf.toList()
        }
        sink.submit(line)
    }

    /** 已丢弃的日志条数（0 表示没有丢过）。 */
    fun droppedLines(): Long = sink.droppedLines()

    /** 是否还有日志没落盘；调用方据此决定要不要等，避免无谓阻塞。 */
    fun hasPending(): Boolean = sink.hasPending()

    /**
     * 等待队列中的日志**真正写入磁盘**。
     *
     * @return true = 已全部落盘；false = 超时（仍有未落盘日志）
     */
    fun flush(timeoutMs: Long = 300): Boolean = sink.flush(timeoutMs)

    /** 停止接收并尽力写完（收尾用）。 */
    fun close(timeoutMs: Long = 300) = sink.close(timeoutMs)
}
