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
) {
    /** 有登录态且有直链，才值得走官方打包 */
    val usable: Boolean get() = loggedIn && downloadUrl.isNotEmpty()

    companion object {
        fun parse(body: String): JmAlbumZip {
            val json = JSONObject(body)
            val status = json.optString("status")
            return JmAlbumZip(
                loggedIn = status.isNotEmpty() && status != "0",
                title = json.optString("title"),
                fileSize = json.optString("fileSize"),
                imageUrl = json.optString("img_url"),
                downloadUrl = json.optString("download_url"),
                msg = json.optString("msg"),
            )
        }
    }
}
