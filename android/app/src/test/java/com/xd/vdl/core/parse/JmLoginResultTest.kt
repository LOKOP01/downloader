package com.xd.vdl.core.parse

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * `/login` 返回体的解析。
 *
 * 字段名是从官方 App 的 JS 里读出来的：`code === 200` 时 `data = {jwttoken, s, ...}`，
 * 之后 `jwttoken` 用作 `Authorization: Bearer`、`s` 用作 `Cookie: AVS=`。
 * 失败分支的文案是实测的原文（「無效的用戶名和/或密碼！」）。
 */
class JmLoginResultTest {

    @Test
    fun successCarriesBothCredentials() {
        val body = """{"code":200,"data":{"jwttoken":"eyJhbGciOi.abc.def","s":"7f3a91c2d4","uid":"12345","username":"someone"}}"""
        val r = JmClient.LoginResult.parse(200, body)
        assertTrue(r.ok)
        assertEquals("eyJhbGciOi.abc.def", r.jwt)
        assertEquals("7f3a91c2d4", r.avs)
    }

    @Test
    fun wrongPasswordReportsServerMessage() {
        val body = """{"code":401,"data":[],"errorMsg":"無效的用戶名和/或密碼！"}"""
        val r = JmClient.LoginResult.parse(401, body)
        assertFalse(r.ok)
        assertEquals("無效的用戶名和/或密碼！", r.message)
    }

    @Test
    fun emptyFieldsReportsServerMessage() {
        val body = """{"code":401,"data":[],"errorMsg":"用戶名和密碼字段不能留空！"}"""
        val r = JmClient.LoginResult.parse(401, body)
        assertFalse(r.ok)
        assertEquals("用戶名和密碼字段不能留空！", r.message)
    }

    @Test
    fun code200ButNoCredentialsIsFailure() {
        val r = JmClient.LoginResult.parse(200, """{"code":200,"data":{"uid":"1"}}""")
        assertFalse(r.ok)
        assertTrue(r.message.contains("凭据"))
    }

    @Test
    fun htmlBodyDoesNotCrash() {
        val r = JmClient.LoginResult.parse(200, "<html>blocked</html>")
        assertFalse(r.ok)
        assertTrue(r.message.contains("不是 JSON"))
    }
}
