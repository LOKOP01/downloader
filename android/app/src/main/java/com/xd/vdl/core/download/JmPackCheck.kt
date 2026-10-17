package com.xd.vdl.core.download

/**
 * 「官方整本打包直链」的完整性判定。
 *
 * 背景（实测数据，本子 350234）：
 *
 *     /album?id=350234        → total_photos = 50，series 有 2 章
 *     /chapter?id=350234      → 25 张（第 1 章）
 *     /chapter?id=350235      → 25 张（第 2 章）
 *
 * 也就是说多章本子的 `total_photos` 是**各章页数之和**，而 `/album_download_2` 的打包
 * 结果并不保证覆盖全部章节 —— 它按本子 id（在多章本子里恰好等于**首章** id）打包时，
 * 拿到的可能只有第一章。此时若直接采信这个包，用户就会看到「解析 50 页、下载 25 页」，
 * 且**没有任何报错**。
 *
 * 所以：官方包必须先和「逐章页数之和」对一遍，对不上就丢弃、回退逐张下载。
 * 这个判定抽成纯函数，是为了能用真实数字（25 / 50）写单测。
 */
internal object JmPackCheck {

    /**
     * 官方包是否覆盖了全部章节。
     *
     * @param actual   官方包里实际的图片条目数
     * @param expected 逐章取图得到的页数之和（拉目录失败时为 0，表示无从校验）
     * @return true 表示可以采信；false 表示必须回退逐张下载
     *
     * 判定刻意**偏保守**：
     *  · actual <= 0 —— 空包/错误页，绝不可用；
     *  · expected > 0 且 actual < expected —— 少页，不可用（正是本 bug）；
     *  · expected == 0 —— 目录没拉成，无从校验，只能接受（后续逐张路径本来也会失败）；
     *  · actual > expected —— 包比预期更全（服务端合并了番外等），接受。
     */
    fun coversAllChapters(actual: Int, expected: Int): Boolean {
        if (actual <= 0) return false
        if (expected <= 0) return true
        return actual >= expected
    }

    /** 给日志/提示用的一句话，说明为什么丢弃官方包 */
    fun shortfallReason(actual: Int, expected: Int): String =
        "官方打包只有 $actual 张，少于逐章合计的 $expected 张（多章本子按首章打包时会这样），已改为逐张下载"
}
