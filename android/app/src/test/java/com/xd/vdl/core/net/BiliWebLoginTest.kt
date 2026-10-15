package com.xd.vdl.core.net

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class BiliWebLoginTest {
    @Test fun sessionRequiresExactNonEmptyName() {
        assertEquals("", BiliWebLogin.session("buvid3=guest; SESSDATA="))
        assertEquals("", BiliWebLogin.session("SESSDATA_backup=not-login"))
        assertEquals("abc%2Cdef==", BiliWebLogin.session("SESSDATA=abc%2Cdef==; bili_jct=csrf"))
    }
    @Test fun cookieMergeKeepsApiCredentialAndExtras() {
        val merged = BiliWebLogin.cookies(listOf("SESSDATA=api; buvid3=device", "SESSDATA=web; bili_jct=csrf", "SESSDATA="))
        assertEquals("api", BiliWebLogin.session(merged))
        assertTrue(merged.contains("buvid3=device"))
        assertTrue(merged.contains("bili_jct=csrf"))
    }
    @Test fun noSuccessForGuestOrMerelyPresentCookie() {
        assertFalse(BiliWebLogin.verified(JSONObject("{}")))
        assertFalse(BiliWebLogin.verified(JSONObject("{code:0,data:{isLogin:false}}")))
        assertFalse(BiliWebLogin.verified(JSONObject("{code:-101,data:{isLogin:true}}")))
        assertFalse(BiliWebLogin.verified(JSONObject("{data:{isLogin:true}}")))
    }
    @Test fun serverMustConfirmLoginBeforeSuccess() {
        assertTrue(BiliWebLogin.verified(JSONObject("{code:0,data:{isLogin:true}}")))
    }
    @Test fun navigationAllowsOnlyBiliHttps() {
        assertTrue(BiliWebLogin.allowed(BiliWebLogin.LOGIN_URL))
        assertTrue(BiliWebLogin.allowed("https://www.bilibili.com/"))
        for (url in listOf("http://www.bilibili.com", "https://bilibili.com.evil.test/", "https://evilbilibili.com/", "intent://open", "file:///data/test", "javascript:alert(1)"))
            assertFalse(url, BiliWebLogin.allowed(url))
    }
}
