package com.xd.vdl.core.net

import okhttp3.Interceptor
import okhttp3.Response

/** BridgeInterceptor 会用非空 CookieJar 重写 Cookie 头。网络层在其之后合并登录态。
 *
 * ⚠️ 跨域重定向必须**主动剥掉** Cookie：OkHttp 的重定向只移除 Authorization，
 * 并不移除 Cookie（CookieJar 为空时 BridgeInterceptor 原样保留已有头），
 * 所以「只比较 host 然后 proceed」等于把登录凭据原样送给重定向目标域。
 */
internal class PreserveLoginCookieInterceptor : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val original = chain.call().request()
        val outgoing = chain.request()
        val explicit = original.header("Cookie").orEmpty()
        if (original.url.host != outgoing.url.host) {
            // 跨域重定向：显式清空，而不是依赖 OkHttp 自动剥离（它只剥 Authorization）。
            return chain.proceed(outgoing.newBuilder().removeHeader("Cookie").build())
        }
        if (explicit.isEmpty()) return chain.proceed(outgoing)
        val merged = merge(outgoing.header("Cookie").orEmpty(), explicit)
        return chain.proceed(outgoing.newBuilder().header("Cookie", merged).build())
    }

    companion object {
        internal fun merge(session: String, explicit: String): String {
            val values = linkedMapOf<String, String>()
            for (header in listOf(session, explicit)) {
                header.split(';').forEach { part ->
                    val i = part.indexOf('=')
                    if (i > 0) values[part.substring(0, i).trim()] = part.substring(i + 1).trim()
                }
            }
            return values.entries.joinToString("; ") { it.key + "=" + it.value }
        }
    }
}
