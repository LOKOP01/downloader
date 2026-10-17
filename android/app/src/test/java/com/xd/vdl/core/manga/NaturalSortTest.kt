package com.xd.vdl.core.manga

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 自然排序测试。
 *
 * 期望值全部由桌面端 `app/core/manga.py` 的 `natural_key` 推导而来（Python 里跑一遍
 * `sorted(...)` 得到的就是这些顺序）。两端排不出同一个页序，用户就会「PC 上第 37 页、
 * 手机上另一张」，所以这里逐条钉死。
 */
class NaturalSortTest {

    private fun sorted(vararg names: String): List<String> =
        names.toList().sortedWith(NaturalSort.comparator)

    @Test
    fun `数字段按数值排序而不是字典序`() {
        assertEquals(
            listOf("1.jpg", "2.jpg", "9.jpg", "10.jpg", "100.jpg"),
            sorted("10.jpg", "2.jpg", "100.jpg", "1.jpg", "9.jpg"),
        )
    }

    @Test
    fun `带章节目录的相对路径先比章节再比页`() {
        assertEquals(
            listOf("第02章/2.jpg", "第02章/10.jpg", "第10章/1.jpg"),
            sorted("第10章/1.jpg", "第02章/10.jpg", "第02章/2.jpg"),
        )
    }

    @Test
    fun `前导零不影响数值比较但影响同值顺序`() {
        // 数值相同（1 == 01）时，短的那个在前 —— 对应桌面端关键字里的 len(p)
        assertEquals(listOf("1.jpg", "01.jpg"), sorted("01.jpg", "1.jpg"))
    }

    @Test
    fun `纯数字名排在字母名之前`() {
        // 桌面端实测（Python sorted + natural_key）：'1.jpg','2.jpg','a.jpg','b.jpg'。
        // 原因：两者第一个段都是「文本段」，"" < "a.jpg"，所以数字名反而靠前。
        assertEquals(listOf("1.jpg", "2.jpg", "a.jpg", "b.jpg"),
            sorted("2.jpg", "a.jpg", "1.jpg", "b.jpg"))
    }

    @Test
    fun `大小写不敏感但保持稳定`() {
        val out = sorted("B.jpg", "a.jpg", "c.jpg")
        assertEquals(listOf("a.jpg", "B.jpg", "c.jpg"), out)
    }

    @Test
    fun `前缀短名排在长名之前`() {
        assertEquals(listOf("1.jpg", "1.jpg.bak", "10.jpg"),
            sorted("10.jpg", "1.jpg.bak", "1.jpg"))
    }

    @Test
    fun `超长数字不会溢出`() {
        // 20 位数字远超 Int/Long 范围，必须靠「去零后长度 + 字典序」比较
        val big = "99999999999999999999999999.jpg"
        val bigger = "100000000000000000000000000.jpg"
        assertEquals(listOf(big, bigger), sorted(bigger, big))
    }

    @Test
    fun `全角数字排在 ASCII 数字之前`() {
        // 桌面端实测：'２.jpg' 在 '1.jpg' 之前。
        // 机制：Python 的 \\d 是 Unicode 感知的，全角 ２ 被切成独立段；
        // 但因为 p.isascii() 不成立，它退化成**文本段** (0, '２')，
        // 而 '1.jpg' 的首段是数字段 (1, …) —— 文本段更小，所以排前面。
        assertEquals(listOf("２.jpg", "1.jpg"), sorted("２.jpg", "1.jpg"))
    }

    @Test
    fun `空串与纯数字名可比较`() {
        assertEquals(listOf("", "0", "1"), sorted("1", "0", ""))
    }

    @Test
    fun `同名前缀的浮点数名不崩`() {
        val out = sorted("1.2.jpg", "1.10.jpg", "1.9.jpg")
        assertEquals(listOf("1.2.jpg", "1.9.jpg", "1.10.jpg"), out)
    }

    @Test
    fun `比较器可用于泛型排序`() {
        val items = listOf("c" to 1, "a" to 2, "b" to 3)
        val out = NaturalSort.sortedBy(items) { it.first }
        assertEquals(listOf("a", "b", "c"), out.map { it.first })
        assertTrue(NaturalSort.compare("a", "a") == 0)
    }
}
