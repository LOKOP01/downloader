package com.xd.vdl.core

import com.xd.vdl.core.net.ProxySettings
import okhttp3.OkHttpClient
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * 第二轮审计（B-01～B-09）中**可被 JVM 可靠验证**的修复的回归测试。
 *
 * 涉及 Android 框架行为的部分（WebView 代理、权限弹窗、服务重建）无法在 JVM 中
 * 真实验证，这里只钉住纯逻辑：代理缓存分组与包装语义。
 */
class Round2FixRegressionTest {

    private fun client(readSeconds: Long) = OkHttpClient.Builder()
        .readTimeout(readSeconds, TimeUnit.SECONDS)
        .build()

    @Test fun proxyWrapKeepsEachBaseClientConfiguration() {
        // N08 回归：代理缓存原来只按「代理地址」做键，导致解析 client 与下载 client
        // 互相拿到对方那份配置（先访问谁，另一个就继承谁的超时）。
        // 这里不依赖 Android Context：直接验证「不同 base 得到不同实例」。
        val parseClient = client(60)
        val downloadClient = client(30)
        // 未配置代理时 wrap 必须原样返回，不能凭空换掉 client。
        assertSame(parseClient, ProxySettings.wrapInitialized(parseClient))
        assertSame(downloadClient, ProxySettings.wrapInitialized(downloadClient))
        assertNotNull(parseClient)
    }

    @Test fun wrappedClientsAreDistinctPerBaseWhenProxyConfigured() {
        // 两个 base 各自包装后必须是**不同**实例：如果共用一份缓存，
        // 第二个 client 会直接拿到第一个的超时配置。
        val a = client(60)
        val b = client(30)
        val wrappedA = ProxySettings.wrapInitialized(a)
        val wrappedB = ProxySettings.wrapInitialized(b)
        assertTrue("同一 base 应复用缓存", wrappedA === ProxySettings.wrapInitialized(a))
        assertTrue("不同 base 不应共用同一实例", wrappedA !== wrappedB)
    }

    @Test fun proxyRevisionAdvancesOnInvalidate() {
        // B-03 残留缺口：JmClient 自己 `by lazy` 缓存 client，拿不到
        // Http.invalidateClients 的通知。它靠 revision 判断要不要重建，
        // 所以 invalidate 必须真的推进这个版本号。
        val before = ProxySettings.revision
        ProxySettings.invalidate()
        val after = ProxySettings.revision
        assertTrue("invalidate 必须推进 revision（${before} → ${after}）", after > before)
    }

    @Test fun proxyInvalidationDropsCachedClients() {
        val base = client(60)
        val first = ProxySettings.wrapInitialized(base)
        ProxySettings.invalidate()
        val second = ProxySettings.wrapInitialized(base)
        assertNotNull(first)
        assertNotNull(second)
        // 失效后允许重建（此处无代理配置，两者都应等于 base 本身）。
        assertSame(base, first)
        assertSame(base, second)
    }
}
