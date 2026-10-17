package com.xd.vdl.core.parse

import com.xd.vdl.core.Platform
import com.xd.vdl.core.model.VideoInfo

/**
 * 禁漫页数文案。
 *
 * `/album` 的 `total_photos` 是**各章页数之和**（实测本子 350234：两章各 25 张，
 * total_photos = 50）。多章本子只写「50 页」容易被理解成单章 50 页，
 * 于是下载得到的 25 页/章看起来就像「少了一半」。这里把「合计」说出来。
 */
object JmPageCountText {

    /** 例如：`2 章 · 共 50 页` / `共 25 页` / 空串（信息不足时不编造） */
    fun describe(info: VideoInfo): String {
        if (info.platform != Platform.JMCOMIC) return ""
        if (info.jmPageCount <= 0) return ""
        return if (info.jmChapterCount > 1) {
            info.jmChapterCount.toString() + " 章 · 共 " + info.jmPageCount + " 页"
        } else {
            "共 " + info.jmPageCount + " 页"
        }
    }
}
