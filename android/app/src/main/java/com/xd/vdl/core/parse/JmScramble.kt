package com.xd.vdl.core.parse

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Rect
import java.security.MessageDigest

/**
 * 禁漫原图「解码」（切片重排）。
 *
 * 禁漫把每张图按**横向切块**后**逆序**存进文件，直接看是花屏。
 * 还原就是把第 i 块搬到正确位置：
 *
 * ```
 * over = h % num
 * for i in 0 until num:
 *     move = h / num          // 每块高度
 *     y_src = h - move*(i+1) - over
 *     y_dst = move*i
 *     if i == 0: move += over
 *     else:      y_dst += over
 *     把 [0, y_src, w, y_src+move] 贴到 [0, y_dst, w, y_dst+move]
 * ```
 *
 * 与 Python `jmcomic.jm_toolkit.JmImageTool.decode_and_save` 逐行等价。
 */
object JmScramble {

    private const val SCRAMBLE_220980 = 220980
    private const val SCRAMBLE_268850 = 268850
    private const val SCRAMBLE_421926 = 421926

    /**
     * 算出要切成几块。返回 0 表示这张图没被切片、直接保存即可。
     *
     * 规则（与 jmcomic 的 `JmImageTool.get_num` 一致）：
     *  · aid < scramble_id            → 0（老图没切）
     *  · aid < 268850                 → 10
     *  · aid < 421926                 → x = 10
     *  · 否则                          → x = 8
     *  · 后两种：num = (md5(`$aid$basename`)[-1] % x) * 2 + 2，basename 不含扩展名
     */
    fun segmentationNum(scrambleId: String, aid: String, filename: String): Int {
        val sid = scrambleId.toIntOrNull() ?: return 0
        val a = aid.toIntOrNull() ?: return 0
        if (a < sid) return 0
        if (a < SCRAMBLE_268850) return 10

        val x = if (a < SCRAMBLE_421926) 10 else 8
        // JM 的 get_num_by_url 使用不带扩展名的文件名。把 .webp/.jpg 一起散列
        // 会让大部分页面的切片数错误，恰好撞对的几页才会正常显示。
        val basename = filename.substringAfterLast('/').substringBeforeLast('.', filename)
        val d = MessageDigest.getInstance("MD5")
            .digest("$a$basename".toByteArray(Charsets.UTF_8))
        // 取 md5 十六进制串的最后一个字符的码点（与 Python ord(hexdigest()[-1]) 一致）
        val lastHexChar = "%02x".format(d[d.size - 1]).last()
        var num = lastHexChar.code
        num %= x
        return num * 2 + 2
    }

    /**
     * 按 [num] 还原切片顺序。num == 0 或图太小则原样返回。
     *
     * 返回新 Bitmap；调用方负责回收旧图。
     */
    fun decode(src: Bitmap, num: Int): Bitmap {
        if (num <= 0) return src
        val w = src.width
        val h = src.height
        if (w <= 0 || h <= 0 || h < num) return src

        val out = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(out)
        val over = h % num
        for (i in 0 until num) {
            var move = h / num
            val ySrc = h - move * (i + 1) - over
            var yDst = move * i
            if (i == 0) {
                move += over
            } else {
                yDst += over
            }
            // 边界防御：切块高度为 0 或越界就跳过（脏数据不该崩）
            if (move <= 0) continue
            val sTop = ySrc.coerceAtLeast(0)
            val sBottom = (ySrc + move).coerceAtMost(h)
            if (sBottom <= sTop) continue
            val dTop = yDst.coerceAtLeast(0)
            val dBottom = (yDst + (sBottom - sTop)).coerceAtMost(h)
            if (dBottom <= dTop) continue
            canvas.drawBitmap(
                src,
                Rect(0, sTop, w, sBottom),
                Rect(0, dTop, w, dBottom),
                null,
            )
        }
        return out
    }
}
