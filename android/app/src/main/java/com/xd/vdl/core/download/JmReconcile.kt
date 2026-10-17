package com.xd.vdl.core.download

import com.xd.vdl.core.model.VideoInfo

/**
 * 下载完成后的「页数对账」。
 *
 * 用户看到首页写着「共 50 页」，下完却只有 25 页 —— 这种**静默少页**比直接报错更糟：
 * 用户要到翻到最后一页才发现，而且无从判断是资源本来如此还是应用出错。
 *
 * 这里把「解析时显示的页数」与「实际写进压缩包的张数」比一次，不一致就给出具体数字。
 * 注意它只负责**说明**，不负责中止 —— 文件已经落盘，强行回滚反而更糟。
 */
internal object JmReconcile {

    /**
     * 生成给界面显示的说明；一致时返回空串（不打扰用户）。
     *
     * @param expected 解析阶段的预期页数（`total_photos`，多章时为各章合计）
     * @param actual   实际写入压缩包的图片张数
     */
    fun describe(expected: Int, actual: Int): String {
        if (expected <= 0 || actual <= 0) return ""
        if (actual >= expected) return ""
        return "实际只下到 " + actual + " 张，比预期的 " + expected + " 张少 " +
            (expected - actual) + " 张（可能有章节被限制或未取到）"
    }

    /** 是否发生了少页（供调用方决定要不要写警告日志） */
    fun isShort(expected: Int, actual: Int): Boolean =
        expected > 0 && actual > 0 && actual < expected
}
