package com.xd.vdl.core

import android.content.Context
import android.content.SharedPreferences

/**
 * 下载位置设置。
 *
 * Android 10（Q）之后，应用往公共存储写文件必须走 MediaStore，而 MediaStore 只接受
 * **相对路径**（`RELATIVE_PATH`，如 `Movies/xxx`）—— 它决定了系统按哪种媒体类型归档，
 * 不能随便换成任意绝对目录。所以这里让用户自定义的是**末级文件夹名**：
 *
 * ```
 * 视频   →  Movies/<videoDir>      （相册「视频」分类下）
 * 图片   →  Pictures/<imageDir>    （相册「图片」分类下）
 * 压缩包 →  Download/<archiveDir>  （文件管理器「下载」下，不进相册）
 * ```
 *
 * 这样既满足「位置可自定义」，又不会牺牲图库可见性 —— 若走 SAF 选任意目录，
 * 很多机型的相册根本扫不到，反而更难找。
 *
 * ⚠️ [sanitize] 必须过滤 `/` `\` 和 `..`：目录名里混进斜杠会让 RELATIVE_PATH
 * 变成多级路径，混进 `..` 则可能越权写到别的目录。
 */
object SaveSettings {

    private const val PREFS = "vdl_save"
    private const val KEY_VIDEO = "dir_video"
    private const val KEY_IMAGE = "dir_image"
    private const val KEY_ARCHIVE = "dir_archive"

    /** 默认末级目录名（与 1.0.18 及以前的硬编码保持一致，老用户升级后位置不变） */
    const val DEFAULT_VIDEO = "视频下载器"
    const val DEFAULT_IMAGE = "视频下载器"
    const val DEFAULT_ARCHIVE = "视频下载器"

    /** 目录名长度上限，防止超长路径插入 MediaStore 失败 */
    private const val MAX_LEN = 40

    private var prefs: SharedPreferences? = null

    fun init(context: Context) {
        if (prefs == null) {
            prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        }
    }

    // ------------------------------------------------------------------ //
    // 读写
    // ------------------------------------------------------------------ //

    /** 视频目录名：相册 `Movies/` 下的一级文件夹 */
    fun videoDir(context: Context): String = read(context, KEY_VIDEO, DEFAULT_VIDEO)

    /** 图片目录名：相册 `Pictures/` 下的一级文件夹 */
    fun imageDir(context: Context): String = read(context, KEY_IMAGE, DEFAULT_IMAGE)

    /** 压缩包目录名：`Download/` 下的一级文件夹 */
    fun archiveDir(context: Context): String = read(context, KEY_ARCHIVE, DEFAULT_ARCHIVE)

    fun setVideoDir(context: Context, name: String) = write(context, KEY_VIDEO, name)

    fun setImageDir(context: Context, name: String) = write(context, KEY_IMAGE, name)

    fun setArchiveDir(context: Context, name: String) = write(context, KEY_ARCHIVE, name)

    /** 全部恢复默认 */
    fun reset(context: Context) {
        init(context)
        prefs?.edit()?.remove(KEY_VIDEO)?.remove(KEY_IMAGE)?.remove(KEY_ARCHIVE)?.apply()
        AppLog.i("下载位置已恢复默认")
    }

    // ------------------------------------------------------------------ //
    // 内部
    // ------------------------------------------------------------------ //

    private fun read(context: Context, key: String, def: String): String {
        init(context)
        val v = prefs?.getString(key, "").orEmpty()
        return if (v.isEmpty()) def else v
    }

    private fun write(context: Context, key: String, name: String) {
        init(context)
        val clean = sanitize(name).ifEmpty { return }
        prefs?.edit()?.putString(key, clean)?.apply()
        AppLog.i("下载位置更新：$key=$clean")
    }

    /**
     * 把用户输入洗成合法的**单级**目录名。
     *
     * - 掐掉首尾空白与斜杠
     * - `/` `\` `:` `*` `?` `"` `<` `>` `|` 及控制字符一律换成 `_`
     * - `..` / `.` 整体退化成默认值（调用方 `.ifEmpty{}` 兜底）
     * - 超长截断
     *
     * 返回空串表示「这个输入不可用」。
     */
    fun sanitize(raw: String): String {
        var s = raw.trim().trim('/', '\\')
        if (s.isEmpty()) return ""
        // 纯点目录（`.` / `..` / `...`）会让 RELATIVE_PATH 语义错乱，直接拒掉
        if (s.all { it == '.' }) return ""
        val sb = StringBuilder(s.length)
        s.forEach { c ->
            sb.append(
                when {
                    c == '/' || c == '\\' -> '_'
                    c == ':' || c == '*' || c == '?' || c == '"' -> '_'
                    c == '<' || c == '>' || c == '|' -> '_'
                    c.code < 0x20 || c.code == 0x7F -> '_'
                    else -> c
                },
            )
        }
        s = sb.toString().trim('.', ' ', '_').ifEmpty { return "" }
        if (s.length > MAX_LEN) s = s.substring(0, MAX_LEN).trimEnd('.', ' ')
        return s.ifEmpty { "" }
    }

    /**
     * 供界面「路径预览」用：把三类目录拼成可读文本。
     * 注意末尾没有斜杠，与 MediaStore 的实际相对路径一致。
     */
    fun previewVideo(context: Context): String = "Movies/${videoDir(context)}"

    fun previewImage(context: Context): String = "Pictures/${imageDir(context)}"

    fun previewArchive(context: Context): String = "Download/${archiveDir(context)}"
}
