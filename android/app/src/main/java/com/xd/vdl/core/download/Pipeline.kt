package com.xd.vdl.core.download

import kotlinx.coroutines.Deferred
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope

/**
 * 滑动窗口流水线：最多 [concurrency] 个任务同时在跑，但 [consume] 严格按输入顺序调用。
 *
 * 禁漫下载要的正是这个形状 —— 图片可以并行下（原来一张一张串行，就是「安卓比 PC 慢」
 * 的根因），但 zip 是流式的，entry 必须按页序写入，不能乱。
 *
 * 抽成独立函数是为了能单测：「并发但不乱序」这条性质光靠跑真实下载验不出来。
 *
 * @param onStart 在**提交任务时**按输入顺序回调（串行执行），用来报「正在处理哪一页」；
 *  [consume] 里报的是「刚写完哪一页」，两者错开一个窗口宽度是正常的。
 * @param transform 单个元素的处理，并发执行
 * @param consume   按输入顺序消费结果
 */
internal suspend fun <T, R> pipelineOrdered(
    items: List<T>,
    concurrency: Int,
    onStart: ((index: Int, item: T) -> Unit)? = null,
    transform: suspend (index: Int, item: T) -> R,
    consume: suspend (index: Int, item: T, result: R) -> Unit,
) = coroutineScope {
    val width = concurrency.coerceAtLeast(1)
    val window = ArrayDeque<Pair<Int, Deferred<R>>>()

    suspend fun flushHead() {
        val (idx, deferred) = window.removeFirst()
        consume(idx, items[idx], deferred.await())
    }

    items.forEachIndexed { idx, item ->
        onStart?.invoke(idx, item)
        window.addLast(idx to async { transform(idx, item) })
        if (window.size >= width) flushHead()
    }
    while (window.isNotEmpty()) flushHead()
}
