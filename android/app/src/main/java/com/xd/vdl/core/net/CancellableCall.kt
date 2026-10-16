package com.xd.vdl.core.net

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.Call
import okhttp3.Callback
import okhttp3.Response
import java.io.IOException
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * 把 OkHttp 的**异步**调用接进协程的取消链（B-09）。
 *
 * ⚠️ 为什么需要它：项目里大量使用 `call.execute()`（同步阻塞）。同步调用
 * **不响应协程取消** —— 用户点了取消 / 关掉对话框，协程被 cancel 了，
 * 但底层 socket 仍会跑到超时为止（最长 60s）。表现是「取消后还在偷偷下载」
 * 和「关掉页面后线程还被占着」。
 *
 * 这里用 `enqueue` + `suspendCancellableCoroutine`：协程一取消就
 * `call.cancel()`，socket 立即关闭。
 *
 * 保持与 `execute()` 相同的语义：失败抛 [IOException]，由调用方原有的
 * try/catch 处理，不需要改动调用点的异常逻辑。
 */
suspend fun Call.await(): Response = suspendCancellableCoroutine { cont ->
    enqueue(object : Callback {
        override fun onFailure(call: Call, e: IOException) {
            if (cont.isActive) cont.resumeWithException(e)
        }

        override fun onResponse(call: Call, response: Response) {
            if (cont.isActive) {
                cont.resume(response)
            } else {
                // 协程已经取消：这个响应没人接手，必须关掉，否则连接泄漏。
                runCatching { response.close() }
            }
        }
    })
    cont.invokeOnCancellation {
        // 关键：协程取消 → 立刻掐断底层请求。
        runCatching { cancel() }
    }
}

/** 取消时抛出的异常类型，便于调用方区分「被取消」与「网络失败」。 */
class RequestCancelledException(message: String = "请求已取消") : IOException(message)

/**
 * 取消感知的等待：如果协程已被取消，直接抛 [CancellationException]，
 * 不再发起请求。用于「取消后还会继续跑」的循环里。
 */
suspend fun checkCancelled() {
    kotlinx.coroutines.currentCoroutineContext().ensureActive()
}

private fun kotlin.coroutines.CoroutineContext.ensureActive() {
    val job = this[kotlinx.coroutines.Job] ?: return
    if (!job.isActive) throw CancellationException("已取消")
}
