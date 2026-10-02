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
 */
object AppLog {
    private const val TAG = "vdl"
    private const val MAX = 400
    private const val MAX_FILE = 512 * 1024
    private val lock = Any()
    private val buf = ArrayDeque<String>(MAX)
    private val _lines = MutableStateFlow<List<String>>(emptyList())
    val lines: StateFlow<List<String>> = _lines.asStateFlow()
    private val fmt = SimpleDateFormat("HH:mm:ss", Locale.CHINA)
    @Volatile private var file: File? = null

    fun setup(dir: File) {
        dir.mkdirs()
        file = File(dir, "app.log")
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
        val line = "${fmt.format(Date())} $level  $msg"
        when (level) {
            "W" -> Log.w(TAG, msg)
            "E" -> Log.e(TAG, msg)
            else -> Log.i(TAG, msg)
        }
        synchronized(lock) {
            if (buf.size >= MAX) buf.removeFirst()
            buf.addLast(line)
            _lines.value = buf.toList()
            appendFile(line)
        }
    }

    private fun appendFile(line: String) {
        val f = file ?: return
        runCatching {
            if (f.exists() && f.length() > MAX_FILE) {
                val bak = File(f.parentFile, "app.log.1")
                if (bak.exists()) bak.delete()
                f.renameTo(bak)
            }
            f.appendText(line + "\n", Charsets.UTF_8)
        }
    }
}
