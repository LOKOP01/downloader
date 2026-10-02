package com.xd.vdl.core

import com.xd.vdl.core.parse.ParserRouter
import com.xd.vdl.core.parse.XiaohongshuParser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Test

class ShareLinkRoutingTest {
    @Test fun xiaohongshuShortLinksReachParserFromClipboardAndPaste() {
        for (host in listOf("xhslink.cn", "www.xhslink.cn", "xhslink.com")) {
            val url = "https://$host/a/AbC1dEf"
            val share = "分享笔记：$url 复制打开小红书"
            assertEquals(Platform.XIAOHONGSHU, Platform.of(url))
            assertEquals(url, ClipDetect.detect(share)?.url)
            val (resolved, parser) = ParserRouter.resolve(share)
            assertEquals(url, resolved)
            assertSame(XiaohongshuParser, parser)
        }
    }

    @Test fun xiaohongshuLongLinkKeepsAccessToken() {
        val url = "https://www.xiaohongshu.com/explore/690469fb000000000701506b" +
            "?xsec_token=ABC%2Bdef%3D&xsec_source=pc_share"
        assertEquals(url, ClipDetect.detect("浏览这条笔记 $url")?.url)
        assertEquals(url, ParserRouter.resolve(url).first)
    }

    @Test fun unrelatedHostsAreNotSniffed() {
        assertNull(ClipDetect.detect("https://xhslink.cn.evil.example/a/abc"))
        assertNull(ClipDetect.detect("https://unrelated.example/post/123"))
    }
}
