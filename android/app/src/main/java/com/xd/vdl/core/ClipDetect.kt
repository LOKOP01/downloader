package com.xd.vdl.core

import com.xd.vdl.core.parse.JmParser

/** 剪切板嗅探的判定结果 */
data class ClipHit(val url: String, val platformLabel: String, val platform: Platform)

/**
 * 判断剪切板里的文本「值不值得自动填进输入框」。
 *
 * 原则：宁可漏、不要错。剪切板里大概率是用户随手复制的无关文字，
 * 只有能明确识别出受支持平台链接（或禁漫车号）时才认。
 */
object ClipDetect {

    /**
     * 命中返回 [ClipHit]，否则 null。
     *
     * 覆盖三种形态：
     *  1. 带域名链接（抖音/B站/X/禁漫任一）
     *  2. 禁漫网页链接（`/album/123`、`/photo/123`）
     *  3. 纯车号（`JM123456` 或 2~10 位纯数字 —— 最后这项要求整段就是数字，
     *     避免把聊天记录里的门牌号/年份当车号）
     */
    fun detect(text: String): ClipHit? {
        val raw = text.trim()
        if (raw.isEmpty() || raw.length > 4000) return null

        // 1) 有 URL：看是不是受支持的平台
        val urls = Platform.extractUrls(raw)
        for (url in urls) {
            val p = Platform.of(url)
            if (p != Platform.UNKNOWN) return ClipHit(url, p.label, p)
            // 未知域名但路径像禁漫（/album/ /photo/），也认
            if (JmParser.looksLikeCode(url)) return ClipHit(url, Platform.JMCOMIC.label, Platform.JMCOMIC)
        }
        if (urls.isNotEmpty()) return null

        // 2) 没 URL：只有「整段就是个车号」才认，避免误吞普通文字
        if (JmParser.looksLikeCode(raw)) {
            return ClipHit(raw, Platform.JMCOMIC.label, Platform.JMCOMIC)
        }
        return null
    }
}
