package com.xd.vdl.core.parse

import org.json.JSONObject

/**
 * 官方「整本打包」接口 `/album_download_2/<本子id>` 的返回。
 *
 * 字段名是从官方 App（Capacitor + React）的下载页里读出来的 —— 那一页直接渲染
 * `title` / `fileSize` / `img_url`，并把 `download_url` 挂到一个 `<a download>` 上点击，
 * 而 `status === "0"` 时弹登录。
 *
 * 未登录（服务端原话「請先登入」）时 `status` 为 `"0"` 且没有 `download_url`。
 */
internal data class JmAlbumZip(
    val loggedIn: Boolean,
    val title: String,
    val fileSize: String,
    val imageUrl: String,
    val downloadUrl: String,
    /** 服务端给的说明，未登录时就是「請先登入」——直接回显给用户，比自己猜强 */
    val msg: String,
    /** 原样的 `status` 字段（空串 = 响应里没这个字段） */
    val status: String,
    /** 解密后的原始返回，只用来写日志排错 */
    val raw: String,
) {
    /** 有登录态且有直链，才值得走官方打包 */
    val usable: Boolean get() = loggedIn && downloadUrl.isNotEmpty()

    /** 不可用时给出一句**能区分原因**的话 —— 别再一律写「未登录」 */
    val reason: String
        get() = when {
            usable -> ""
            !loggedIn -> msg.ifEmpty { "服务端说未登录（status=$status）" }
            msg.isNotEmpty() -> msg
            else -> "拿到了登录态但没给直链"
        }

    companion object {
        fun parse(body: String): JmAlbumZip {
            val json = JSONObject(body)
            val status = json.optString("status")
            return JmAlbumZip(
                // ⚠️ 只判「是不是字面量 "0"」，**不是**判「status 是否存在」。
                // 成功的返回里根本没有 status 字段（实测：只有 title / fileSize /
                // download_url / img_url），官方 App 也是这么判的（`"0" === status ? 未登录 : 已登录`）。
                // 我第一版写成 `status.isNotEmpty() && status != "0"`，于是把一次
                // 完全成功的响应误判成了「未登录」，白转两轮。
                loggedIn = status != "0",
                title = json.optString("title"),
                fileSize = json.optString("fileSize"),
                imageUrl = json.optString("img_url"),
                downloadUrl = json.optString("download_url"),
                msg = json.optString("msg"),
                status = status,
                raw = body,
            )
        }
    }
}
