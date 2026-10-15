package com.xd.vdl.core.parse

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 抖音解析靠「注入 JS hook 拦 detail 接口」拿完整 bit_rate（1.0.41 起）。
 *
 * ⚠️ **1.0.42 修的 bug 就出在这条链的入口**：判据曾经用
 * \`java.util.regex.Pattern.quote()\` 生成 \`\Q…\E\` 再拼进 \`new RegExp('…')\`，
 * 而 JS 的正则不认这两个标记 —— hook 装上了却永远不命中，页面白等满超时，
 * 最后退回分享页那条单档路（现象：**只有 1 档、没有帧率、耗时 20s+**）。
 *
 * 这组用例钉住三件事：
 *  1. 生成的路径数组是**合法 JSON**（能被页面直接 \`var PATHS = …\` 吃掉）
 *  2. 里面**不能再出现正则标记**（\`\Q\` / \`\E\` / \`new RegExp\`）
 *  3. 数组里的路径对**真实接口 URL** 做包含判断必须为真
 */
class DouyinHookInjectionTest {

    /** 解析器实际传进来的判据（只认 detail 接口） */
    private val detailOnly: (String) -> Boolean = { it.contains("/aweme/v1/web/aweme/detail/") }

    private fun pathsOf(js: String): JSONArray {
        val start = js.indexOf("var PATHS = [")
        assertTrue("hookJs 里没有 PATHS 数组", start >= 0)
        val from = start + "var PATHS = ".length
        val end = js.indexOf(";", from)
        assertTrue("PATHS 数组没有以分号结束", end > from)
        return JSONArray(js.substring(from, end))
    }

    @Test
    fun `路径数组是合法 JSON 且内容正确`() {
        val arr = pathsOf(WebViewBridge.hookJs(WebViewBridge.urlMatchToJsArray(detailOnly)))
        assertEquals(1, arr.length())
        assertEquals("/aweme/v1/web/aweme/detail/", arr.getString(0))
    }

    @Test
    fun `不再出现正则标记`() {
        val js = WebViewBridge.hookJs(WebViewBridge.urlMatchToJsArray(detailOnly))
        // 这两个标记在 JS 里没有意义，出现即说明又退回了 Pattern.quote 那条路
        assertFalse("hookJs 里出现了 \\Q", js.contains("\\Q"))
        assertFalse("hookJs 里出现了 \\E", js.contains("\\E"))
        assertFalse("hookJs 不应再构造正则", js.contains("new RegExp"))
    }

    @Test
    fun `判据为空时给空数组而不是通配`() {
        val js = WebViewBridge.hookJs(WebViewBridge.urlMatchToJsArray { false })
        assertEquals(0, pathsOf(js).length())
    }

    @Test
    fun `数组里的路径能命中真实详情 URL`() {
        val arr = pathsOf(WebViewBridge.hookJs(WebViewBridge.urlMatchToJsArray(detailOnly)))
        // 抖音详情接口的真实形态（参数被省略，路径一致）
        val real = "https://www.douyin.com/aweme/v1/web/aweme/detail/?device_platform=webapp&aweme_id=7695620172719131913"
        var hit = false
        for (i in 0 until arr.length()) {
            if (real.contains(arr.getString(i))) hit = true
        }
        assertTrue("生成的路径没能命中真实 URL —— 这正是 1.0.41「只有 1 档」的原因", hit)
    }

    @Test
    fun `jsString 转义引号与反斜杠`() {
        assertEquals("\"a/b\"", WebViewBridge.jsString("a/b"))
        assertEquals("\"a\\\"b\"", WebViewBridge.jsString("a\"b"))
        assertEquals("\"a\\\\b\"", WebViewBridge.jsString("a\\b"))
    }

    /**
     * 把真实生成的 hook 脚本导出到 build 目录，交给 Node 里的 JS 引擎复核
     * （见 \`_scratch_x_probe/_v1042_hook_check.js\`）。
     *
     * **为什么要绕这一圈**：上面几个用例只断言了「生成的字符串长什么样」，
     * 而真正的风险是**这段字符串在 JS 引擎里跑起来是什么行为** ——
     * 1.0.41 的 bug 恰好就是「字符串看着对、跑起来恒不匹配」。
     * 所以这里把产物落盘，让真引擎去验。
     */
    @Test
    fun `导出 hook 脚本供 JS 引擎复核`() {
        val js = WebViewBridge.hookJs(WebViewBridge.urlMatchToJsArray(detailOnly))
        val dir = java.io.File(System.getProperty("user.dir"), "build/tmp/hook-export")
        dir.mkdirs()
        val f = java.io.File(dir, "douyin-hook.js")
        f.writeText(js, Charsets.UTF_8)
        assertTrue("导出的 hook 脚本为空", f.length() > 100)
    }

    /**
     * 24.9s 的账：两页各跑满 12s 超时。第二页只是「类型判错了」的兜底，
     * 不该和第一页平摊一样的预算 —— 否则第一页已经证明走不通，还要再等一轮。
     */
    @Test
    fun `第二页的等待预算减半`() {
        val t = DouyinParser.detailTimeouts()
        assertEquals(2, t.size)
        assertEquals(12_000L, t[0])
        assertEquals(6_000L, t[1])
        assertTrue("第二页预算必须明显小于第一页", t[1] < t[0])
    }

    /** 视频链接先试 /video/，图文先试 /note/ —— 顺序错了会白等一轮兜底页 */
    @Test
    fun `详情页顺序跟着链接类型`() {
        val video = DouyinParser.detailPages("123", note = false)
        val note = DouyinParser.detailPages("123", note = true)
        assertTrue(video[0].endsWith("/video/123"))
        assertTrue(note[0].endsWith("/note/123"))
    }

    @Test
    fun `多个判据按顺序保留`() {
        val two: (String) -> Boolean = {
            it.contains("/aweme/v1/web/aweme/detail/") || it.contains("/aweme/v1/web/aweme/iteminfo/")
        }
        val arr = pathsOf(WebViewBridge.hookJs(WebViewBridge.urlMatchToJsArray(two)))
        assertEquals(2, arr.length())
        assertEquals("/aweme/v1/web/aweme/detail/", arr.getString(0))
        assertEquals("/aweme/v1/web/aweme/iteminfo/", arr.getString(1))
    }
}
