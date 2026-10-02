package com.xd.vdl.core

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 文件名：`年月日_时分秒_标题`，非法字符换成下划线 */
object FileName {
    private val ILLEGAL = Regex("[\\\\/:*?\"<>|\r\n]+")

    fun sanitize(s: String, max: Int = 60): String =
        ILLEGAL.replace(s, "_").trim().take(max).ifEmpty { "video" }

    fun timestamp(): String =
        SimpleDateFormat("yyyyMMdd_HHmmss", Locale.CHINA).format(Date())

    fun build(title: String, ext: String): String = "${timestamp()}_${sanitize(title)}.$ext"
}
