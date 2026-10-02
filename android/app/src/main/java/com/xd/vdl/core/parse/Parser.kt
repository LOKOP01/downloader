package com.xd.vdl.core.parse

import android.app.Activity
import com.xd.vdl.core.Platform
import com.xd.vdl.core.model.VideoInfo

class ParseException(message: String, cause: Throwable? = null) : Exception(message, cause)

interface Parser {
    suspend fun parse(activity: Activity, url: String): VideoInfo
}

object ParserRouter {
    /**
     * 从用户输入（可能是整段分享文案）里抠出链接，并选出对应解析器。
     * 返回 清理后的 URL 与解析器。
     */
    fun resolve(input: String): Pair<String, Parser> {
        val text = input.trim()
        val urls = Platform.extractUrls(text)

        // 分享文案可能先放跳转链接、后放真正的内容地址。
        for (url in urls) {
            val p = Platform.of(url)
            if (p != Platform.UNKNOWN) return url to of(p)
            if (JmParser.parseCode(url).second.isNotEmpty()) return url to JmParser
        }
        val url = urls.firstOrNull()

        // 没链接（或链接不认识）：只有整段像禁漫车号才认，否则报错
        if (JmParser.parseCode(text).second.isNotEmpty()) return text to JmParser
        if (url == null) throw ParseException("没找到链接，请把完整的分享文案粘贴进来")
        throw ParseException("暂不支持这个链接（当前支持 抖音 / B站 / X / Instagram / 小红书 / 禁漫）")
    }

    fun forUrl(url: String): Parser {
        val p = Platform.of(url)
        if (p == Platform.UNKNOWN && JmParser.parseCode(url).second.isNotEmpty()) return JmParser
        return of(p)
    }

    private fun of(p: Platform): Parser = when (p) {
        Platform.BILIBILI -> BiliParser
        Platform.DOUYIN -> DouyinParser
        Platform.X -> XParser
        Platform.INSTAGRAM -> InstagramParser
        Platform.XIAOHONGSHU -> XiaohongshuParser
        Platform.JMCOMIC -> JmParser
        Platform.UNKNOWN -> throw ParseException(
            "暂不支持这个链接（当前支持 抖音 / B站 / X / Instagram / 小红书 / 禁漫）")
    }
}

