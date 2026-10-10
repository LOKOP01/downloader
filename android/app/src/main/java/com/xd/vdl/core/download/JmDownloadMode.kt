package com.xd.vdl.core.download

import android.content.Context
import android.content.SharedPreferences
import com.xd.vdl.core.AppLog

/**
 * 禁漫下载方式。
 *
 * 两条路都能出同样的成品（一个以车号命名的 zip），区别只在「怎么拿到图」：
 *
 *  · [OFFICIAL_ZIP] —— 先问服务端要整本打包直链（`/album_download_2`），一次请求下完。
 *    最快，但**必须登录**（未登录接口返回 `請先登入`），而且进度只能按字节走。
 *  · [PER_IMAGE]   —— 逐张下载 + 解码后边下边写进 zip，与桌面版同一条路。
 *    进度按张数增长，未登录也能用。
 *
 * 默认「官方优先」：不可用时自动退回逐张（不会因为没登录就下不了）。
 * 想固定用逐张就在这里关掉。
 */
object JmDownloadMode {

    private const val PREFS = "vdl_jm"
    private const val KEY_OFFICIAL_FIRST = "official_zip_first"

    private var prefs: SharedPreferences? = null

    fun init(context: Context) {
        if (prefs == null) {
            prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        }
    }

    /** 是否优先尝试官方打包直链（默认开） */
    fun officialZipFirst(context: Context): Boolean {
        init(context)
        return prefs?.getBoolean(KEY_OFFICIAL_FIRST, true) ?: true
    }

    fun setOfficialZipFirst(context: Context, enabled: Boolean) {
        init(context)
        prefs?.edit()?.putBoolean(KEY_OFFICIAL_FIRST, enabled)?.apply()
        AppLog.i("禁漫下载方式：${if (enabled) "优先官方打包直链" else "始终逐张下载"}")
    }
}
