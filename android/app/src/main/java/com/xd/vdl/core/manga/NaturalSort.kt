package com.xd.vdl.core.manga

import java.util.Locale

/**
 * 文件名自然排序。
 *
 * 与桌面端 `app/core/manga.py` 的 `natural_key` **逐条等价**：两端对同一批文件
 * 必须排出一致的页序，否则用户在 PC 上读到第 37 页、在手机上打开却是另一张。
 *
 * 规则（照搬桌面端，含几个容易漏掉的细节）：
 *  · 把名字切成「数字段 / 非数字段」交替的序列，**数字段两侧的空串保留**
 *    （Python `re.split(r"(\d+)", s)` 的语义；丢了空串会让 `1a` 与 `a1` 的顺序反过来）；
 *  · 数字段按 (数值, 原串长度) 比较 —— 于是 `2.jpg` < `10.jpg`、`1.jpg` < `01.jpg`；
 *    数值本身用「去掉前导零后的长度 + 字典序」比较，避免超长文件名把整数撑溢出；
 *  · 非数字段按 casefold（大小写不敏感）比较，且逐 **Unicode 码点**比（Python 是码点序，
 *    Kotlin 的 String.compareTo 是 UTF-16 码元序，emoji 这类增补平面字符上两者不同）；
 *  · 数字段整体排在非数字段之后（桌面端关键字里的 `(1, …)` vs `(0, …)`）；
 *  · 一方是另一方的前缀时，段数少的在前（对应 Python tuple 的长度比较）。
 *
 * ⚠️ 只把 **ASCII** 数字当数字（桌面端写了 `p.isascii()`）：全角 `２` 是普通字符。
 */
object NaturalSort {

    private sealed interface Part
    private class Text(val value: String) : Part
    private class Num(val digits: String) : Part

    /**
     * 等价于 Python 的 `re.split(r"(\d+)", name)`（保留数字段与其两侧的空串）。
     *
     * ⚠️ 两个细节都是拿 Python 实测对出来的（`cleanup-report/_manga_sort_probe.py` 逐条比对）：
     *  1. Python 对 str 的 `\d` 是 **Unicode 感知**的：全角 `２` 也会被切成独立段，
     *     所以切分必须用 `Character.isDigit`，不能用 `c in '0'..'9'`；
     *  2. 但桌面端只用 `p.isascii()` 的数字走数值比较，全角数字算**普通文本段**。
     *     合起来的效果是 `２.jpg` 排在 `1.jpg` 之前（文本段 < 数字段），与 PC 端一致。
     *     若图省事一律按 ASCII 数字切，两端页序就会不同。
     */
    private fun split(name: String): List<Part> {
        val out = ArrayList<Part>(8)
        val buf = StringBuilder()
        var i = 0
        while (i < name.length) {
            val c = name[i]
            if (Character.isDigit(c)) {
                // 数字段两侧各切一刀：先把累积的非数字段（可能是空串）交出去
                out.add(Text(buf.toString()))
                buf.setLength(0)
                val start = i
                while (i < name.length && Character.isDigit(name[i])) i++
                val run = name.substring(start, i)
                // 只有**纯 ASCII 数字**才按数值比较；全角数字等退化为普通文本段
                out.add(if (run.all { it in '0'..'9' }) Num(run) else Text(run))
            } else {
                buf.append(c)
                i++
            }
        }
        out.add(Text(buf.toString()))
        return out
    }

    /** 码点序比较（与 Python 的字符串比较一致） */
    private fun codePoints(a: String, b: String): Int {
        var i = 0
        var j = 0
        while (i < a.length && j < b.length) {
            val ca = a.codePointAt(i)
            val cb = b.codePointAt(j)
            if (ca != cb) return if (ca < cb) -1 else 1
            i += Character.charCount(ca)
            j += Character.charCount(cb)
        }
        return (a.length - i).compareTo(b.length - j)
    }

    /** 数值比较：先比去前导零后的长度，再比字典序（等价于比 int 值，且不会溢出） */
    private fun compareNum(a: Num, b: Num): Int {
        val sa = a.digits.trimStart('0').ifEmpty { "0" }
        val sb = b.digits.trimStart('0').ifEmpty { "0" }
        if (sa.length != sb.length) return sa.length - sb.length
        val c = sa.compareTo(sb)
        if (c != 0) return c
        // 数值相等（例如 "1" 与 "01"）：原串短者在前，对应桌面端关键字里的 len(p)
        return a.digits.length - b.digits.length
    }

    private fun comparePart(a: Part, b: Part): Int = when {
        a is Num && b is Num -> compareNum(a, b)
        a is Text && b is Text -> codePoints(
            a.value.lowercase(Locale.ROOT), b.value.lowercase(Locale.ROOT))
        // 数字段整体比非数字段大（桌面端 (1, …) > (0, …) 的效果）
        a is Num -> 1
        else -> -1
    }

    /** 单个名字的比较器；可直接交给 `sortedWith` */
    val comparator: Comparator<String> = Comparator { a, b -> compare(a, b) }

    fun compare(a: String, b: String): Int {
        val pa = split(a)
        val pb = split(b)
        val n = minOf(pa.size, pb.size)
        for (i in 0 until n) {
            val c = comparePart(pa[i], pb[i])
            if (c != 0) return c
        }
        return pa.size - pb.size
    }

    /** 按 [key] 取出的名字自然排序（稳定：相等时保持输入顺序） */
    fun <T> sortedBy(items: List<T>, key: (T) -> String): List<T> =
        items.sortedWith { x, y -> compare(key(x), key(y)) }
}
