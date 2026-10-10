package com.xd.vdl.core.parse

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * `/login` 响应体的拆解。
 *
 * 这里钉住的是一条踩过的坑：**成功时 `data` 是加密过的内层 JSON 字符串**，
 * 不是对象。第一版直接当对象读，于是拿着一个完全正常的成功响应报出
 * 「登录返回里没有 data」。
 *
 * 字段名与失败文案都来自实测 / 官方 App 的 JS：
 * `data = {jwttoken, s, ...}`，失败时 `errorMsg` 是「無效的用戶名和/或密碼！」。
 */
class JmLoginResultTest {

    /** 假解密器：单测里 android.util.Base64 是桩，不能走真解密 */
    private val fakeDecrypt: (String, Long) -> String = { _, _ ->
        """{"jwttoken":"eyJhbGciOi.abc.def","s":"7f3a91c2d4","uid":"12345","username":"someone"}"""
    }

    @Test
    fun encryptedStringDataIsDecrypted() {
        // 成功响应：data 是 base64 密文（真实形态）
        val body = """{"code":200,"data":"Q2F0R29lcy5BQkNERUZHaGk="}"""
        val r = JmClient.unwrapLoginBody(200, body, 1234L, fakeDecrypt)
        assertTrue(r.message, r.ok)
        assertEquals("eyJhbGciOi.abc.def", r.jwt)
        assertEquals("7f3a91c2d4", r.avs)
    }

    @Test
    fun plainObjectDataStillWorks() {
        // 万一服务端直接给对象
        val body = """{"code":200,"data":{"jwttoken":"jwt-x","s":"avs-y"}}"""
        val r = JmClient.unwrapLoginBody(200, body, 1L, fakeDecrypt)
        assertTrue(r.message, r.ok)
        assertEquals("jwt-x", r.jwt)
        assertEquals("avs-y", r.avs)
    }

    @Test
    fun plainJsonStringDataStillWorks() {
        val body = """{"code":200,"data":"{\"jwttoken\":\"j\",\"s\":\"s\"}"}"""
        val r = JmClient.unwrapLoginBody(200, body, 1L, fakeDecrypt)
        assertTrue(r.message, r.ok)
    }

    @Test
    fun arrayDataIsReportedPrecisely() {
        // data 是空数组 —— 这是第一版报「没有 data」的那种形态
        val body = """{"code":200,"data":[]}"""
        val r = JmClient.unwrapLoginBody(200, body, 1L, fakeDecrypt)
        assertFalse(r.ok)
        assertTrue(r.message, r.message.contains("形态不认识"))
        assertTrue(r.message, r.message.contains("JSONArray"))
    }

    @Test
    fun wrongPasswordReportsServerMessage() {
        val body = """{"code":401,"data":[],"errorMsg":"無效的用戶名和/或密碼！"}"""
        val r = JmClient.unwrapLoginBody(401, body, 1L, fakeDecrypt)
        assertFalse(r.ok)
        assertEquals("無效的用戶名和/或密碼！", r.message)
    }

    @Test
    fun emptyFieldsReportsServerMessage() {
        val body = """{"code":401,"data":[],"errorMsg":"用戶名和密碼字段不能留空！"}"""
        val r = JmClient.unwrapLoginBody(401, body, 1L, fakeDecrypt)
        assertFalse(r.ok)
        assertEquals("用戶名和密碼字段不能留空！", r.message)
    }

    @Test
    fun decryptedButNoCredentialsIsFailure() {
        val r = JmClient.unwrapLoginBody(
            200, """{"code":200,"data":"x"}""", 1L,
        ) { _, _ -> """{"uid":"1"}""" }
        assertFalse(r.ok)
        assertTrue(r.message, r.message.contains("没有凭据"))
    }

    @Test
    fun htmlBodyDoesNotCrash() {
        val r = JmClient.unwrapLoginBody(200, "<html>blocked</html>", 1L, fakeDecrypt)
        assertFalse(r.ok)
        assertTrue(r.message, r.message.contains("不是 JSON"))
    }
}
