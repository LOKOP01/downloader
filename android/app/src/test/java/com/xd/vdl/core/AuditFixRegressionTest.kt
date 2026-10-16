package com.xd.vdl.core

import com.xd.vdl.core.parse.BiliParser
import com.xd.vdl.core.parse.JmClient
import com.xd.vdl.core.parse.XParser
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 2026-10-15 全量审计中**已确认缺陷**的回归测试。
 *
 * 每条都对应一处真实修改；断言写的是「修好之后必须成立的行为」，
 * 而不是复述实现细节。
 */
class AuditFixRegressionTest {

    // ---------------- 禁漫：凭据只发给自有域名 ----------------

    @Test fun jmCredentialsAreRestrictedToOwnDomains() {
        // 服务端返回的 download_url / 图片 CDN 若指向外域，绝不能带上 AVS/JWT。
        for (url in listOf(
            "https://www.cdnhjk.net/media/photos/1/2.webp",
            "https://cdn-msp.jmapiproxy1.cc/media/photos/1/2.webp",
            "https://www.cdngwc.cc/album_download/1",
        )) {
            assertTrue(url, JmClient.isJmCredentialHost(url))
        }
        for (url in listOf(
            "https://evil.example.com/steal",
            "https://cdnhjk.net.evil.example.com/steal",
            "http://127.0.0.1:8080/collect",
            "not a url",
        )) {
            assertFalse(url, JmClient.isJmCredentialHost(url))
        }
    }

    // ---------------- B 站：分 P 选择 ----------------

    private fun viewJson(): JSONObject = JSONObject(
        """
        {"title":"合集","cid":111,"duration":600,
         "pages":[
           {"cid":111,"page":1,"part":"第一话","duration":200},
           {"cid":222,"page":2,"part":"第二话","duration":210},
           {"cid":333,"page":3,"part":"第三话","duration":190}]}
        """.trimIndent()
    )

    @Test fun biliMultiPartUsesRequestedPage() {
        val data = viewJson()
        // 修复前：无论链接写第几 P，都只会拿到顶层 cid=111（第 1 P）。
        assertEquals(222L, BiliParser.pickPage("https://www.bilibili.com/video/BV1xx411c7mD?p=2", data).cid)
        assertEquals(333L, BiliParser.pickPage("https://www.bilibili.com/video/BV1xx411c7mD?p=3", data).cid)
        // 标题要带分 P，否则多个 P 下载后同名互相覆盖。
        assertTrue(BiliParser.pickPage("https://www.bilibili.com/video/BV1xx411c7mD?p=2", data)
            .title.contains("第二话"))
        // 分 P 时长按该 P 计算，不再用整部合集的总时长。
        assertEquals(210L, BiliParser.pickPage("https://www.bilibili.com/video/BV1xx411c7mD?p=2", data).durationSec)
    }

    @Test fun biliPageIndexFallsBackToFirst() {
        for (url in listOf(
            "https://www.bilibili.com/video/BV1xx411c7mD",
            "https://www.bilibili.com/video/BV1xx411c7mD?p=0",
            "https://www.bilibili.com/video/BV1xx411c7mD?p=abc",
        )) {
            assertEquals(url, 1, BiliParser.pageIndex(url))
        }
        assertEquals(7, BiliParser.pageIndex("https://www.bilibili.com/video/BV1?p=7&t=3"))
    }

    @Test fun biliWithoutPagesKeepsLegacyBehaviour() {
        val data = JSONObject("""{"title":"单P","cid":999,"duration":42}""")
        val picked = BiliParser.pickPage("https://www.bilibili.com/video/BV1xx411c7mD", data)
        assertEquals(999L, picked.cid)
        assertEquals("单P", picked.title)
        assertEquals(42L, picked.durationSec)
    }

    // ---------------- X：混合推文不能丢视频 ----------------

    @Test fun xMixedMediaPrefersVideoOverImages() {
        // 推文里既有图片又有视频：修复前 images 保留，VideoInfo.isImage 判定为图集，
        // 于是走图片下载分支，视频被整个丢掉。
        val status = JSONObject(
            """
            {"full_text":"混合媒体",
             "extended_entities":{"media":[
               {"type":"photo","media_url_https":"https://pbs.twimg.com/media/a.jpg"},
               {"type":"video","media_url_https":"https://pbs.twimg.com/media/b.jpg",
                "video_info":{"duration_millis":5000,"variants":[
                  {"content_type":"video/mp4","bitrate":832000,"url":"https://video.twimg.com/v/320.mp4"},
                  {"content_type":"video/mp4","bitrate":2176000,"url":"https://video.twimg.com/v/720.mp4"}]}}]}}
            """.trimIndent()
        )
        val info = XParser.buildInfo(status, null)
        assertFalse("有视频时不能判成图集", info.isImage)
        assertTrue(info.images.isEmpty())
        // 档位按码率降序保留（用户可选画质），这里只断言最高档与不丢视频。
        assertEquals("https://video.twimg.com/v/720.mp4", info.qualities.first().url)
        assertEquals(2, info.qualities.size)
    }

    @Test fun xPhotoOnlyTweetStillBecomesImage() {
        val status = JSONObject(
            """
            {"full_text":"纯图片",
             "extended_entities":{"media":[
               {"type":"photo","media_url_https":"https://pbs.twimg.com/media/a.jpg"},
               {"type":"photo","media_url_https":"https://pbs.twimg.com/media/b.jpg"}]}}
            """.trimIndent()
        )
        val info = XParser.buildInfo(status, null)
        assertTrue(info.isImage)
        assertEquals(2, info.images.size)
    }
}
