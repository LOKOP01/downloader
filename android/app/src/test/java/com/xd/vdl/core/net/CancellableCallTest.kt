package com.xd.vdl.core.net

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.net.ServerSocket
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread

/**
 * B-09 回归：网络调用必须能被协程取消**真正打断**。
 *
 * 原实现用 `call.execute()`（同步阻塞）—— 协程取消了，底层 socket 仍会
 * 一直跑到超时（最长 60s）。这里用本地 ServerSocket 造一个「只接受连接、
 * 不回响应」的服务端，验证取消能立刻让请求结束。
 *
 * 全部走环回地址，不访问任何第三方服务。
 */
class CancellableCallTest {

    /** 起一个接受连接但从不响应的服务端，返回 (port, 是否已收到连接, 关闭函数) */
    private fun stalledServer(): Triple<Int, AtomicBoolean, () -> Unit> {
        val server = ServerSocket(0)
        val accepted = AtomicBoolean(false)
        val worker = thread(isDaemon = true) {
            runCatching {
                server.accept().use {
                    accepted.set(true)
                    // 连接建立后故意不写任何响应头，让客户端一直等。
                    Thread.sleep(10_000)
                }
            }
        }
        return Triple(server.localPort, accepted) {
            runCatching { server.close() }
            worker.interrupt()
        }
    }

    @Test fun cancellationInterruptsInFlightRequest() = runBlocking {
        val (port, accepted, close) = stalledServer()
        val client = OkHttpClient.Builder().build()
        val call = client.newCall(Request.Builder().url("http://127.0.0.1:$port/").build())
        try {
            val started = System.currentTimeMillis()
            val job = async(Dispatchers.IO) { runCatching { call.await() } }
            // 等连接真正建立，确保取消发生在「请求进行中」而不是之前。
            val connected = withTimeoutOrNull(5_000) {
                while (!accepted.get()) delay(10)
                true
            }
            assertTrue("请求未建立连接", connected == true)

            job.cancel()
            val finished = withTimeoutOrNull(5_000) { job.join(); true }
            val elapsed = System.currentTimeMillis() - started

            assertTrue("取消失败：请求没有被立刻打断（耗时 ${elapsed}ms）", finished == true)
            assertTrue("取消后仍耗时 ${elapsed}ms，看起来仍在等待超时", elapsed < 4_000)
        } finally {
            close()
            client.dispatcher.executorService.shutdown()
        }
    }

    @Test fun cancelledCoroutineDoesNotLeakTheCall() = runBlocking {
        val (port, _, close) = stalledServer()
        val client = OkHttpClient.Builder().build()
        val call = client.newCall(Request.Builder().url("http://127.0.0.1:$port/").build())
        try {
            val job = async(Dispatchers.IO) { runCatching { call.await() } }
            delay(200)
            job.cancel()
            withTimeoutOrNull(5_000) { job.join() }
            // 取消后 OkHttp 应把该 Call 标记为已取消。
            assertTrue("取消后 call 未被标记为 cancelled", call.isCanceled())
        } finally {
            close()
            client.dispatcher.executorService.shutdown()
        }
    }

    @Test fun failureIsReportedAsIOExceptionLikeExecute() = runBlocking {
        // 连一个没人监听的端口：await 必须像 execute() 一样抛 IOException，
        // 这样调用点原有的 try/catch 不需要改。
        val dead = ServerSocket(0).use { it.localPort }
        val client = OkHttpClient.Builder().build()
        val call = client.newCall(Request.Builder().url("http://127.0.0.1:$dead/").build())
        try {
            val error = runCatching { call.await() }.exceptionOrNull()
            assertTrue("期望 IOException，实际 ${error?.javaClass?.name}", error is IOException)
        } finally {
            client.dispatcher.executorService.shutdown()
        }
    }

    @Test fun cancellationThrowsCancellationExceptionNotIoFailure() = runBlocking {
        val (port, _, close) = stalledServer()
        val client = OkHttpClient.Builder().build()
        val call = client.newCall(Request.Builder().url("http://127.0.0.1:$port/").build())
        try {
            val job = async(Dispatchers.IO) {
                // 不再用 runCatching 包住，观察取消到底以什么形式抛出。
                call.await()
            }
            delay(200)
            job.cancel()
            val outcome = withTimeoutOrNull(5_000) { runCatching { job.await() } }
            assertTrue("任务未在取消后结束", outcome != null)
            assertTrue(
                "取消应表现为 CancellationException，而不是被当成网络失败",
                outcome!!.exceptionOrNull() is CancellationException,
            )
        } finally {
            close()
            client.dispatcher.executorService.shutdown()
        }
    }
}
