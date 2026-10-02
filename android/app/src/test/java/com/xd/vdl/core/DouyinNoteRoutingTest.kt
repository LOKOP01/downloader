package com.xd.vdl.core

import com.xd.vdl.core.parse.DouyinParser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DouyinNoteRoutingTest {
    @Test fun sharedNoteAndSlidesLinksOpenNotePageFirst() {
        for (url in listOf(
            "https://www.douyin.com/note/7689694274094914490",
            "https://www.iesdouyin.com/share/note/7689694274094914490/",
            "https://www.douyin.com/slides/7689694274094914490",
        )) {
            assertTrue(DouyinParser.isNoteLink(url))
        }
        assertEquals("https://www.douyin.com/note/7689694274094914490",
            DouyinParser.detailPages("7689694274094914490", true).first())
        assertEquals("https://www.douyin.com/video/7689694274094914490",
            DouyinParser.detailPages("7689694274094914490", true).last())
    }

    @Test fun videoLinksStillOpenVideoPageFirst() {
        assertFalse(DouyinParser.isNoteLink("https://www.douyin.com/video/7689694274094914490"))
        assertEquals("https://www.douyin.com/video/7689694274094914490",
            DouyinParser.detailPages("7689694274094914490", false).first())
    }
}
