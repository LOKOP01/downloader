package com.xd.vdl.ui

import android.app.Activity
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.xd.vdl.core.AppLog
import com.xd.vdl.core.DownloadManager
import com.xd.vdl.core.Platform
import com.xd.vdl.core.model.Quality
import com.xd.vdl.core.model.VideoInfo
import com.xd.vdl.core.model.dropdownQualities
import com.xd.vdl.core.parse.ParserRouter
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class AppViewModel : ViewModel() {

    private val _link = MutableStateFlow("")
    val link: StateFlow<String> = _link.asStateFlow()

    private val _homeRequest = MutableStateFlow(0)
    val homeRequest: StateFlow<Int> = _homeRequest.asStateFlow()

    /** 自增即请求切到「书架」页（任务页「去书架」按钮用） */
    private val _libraryRequest = MutableStateFlow(0)
    val libraryRequest: StateFlow<Int> = _libraryRequest.asStateFlow()

    fun openLibrary() {
        _libraryRequest.value += 1
    }

    fun acceptDetectedLink(text: String) {
        _link.value = text
        _info.value = null
        _homeRequest.value += 1
    }

    private val _parsing = MutableStateFlow(false)
    val parsing: StateFlow<Boolean> = _parsing.asStateFlow()

    private val _info = MutableStateFlow<VideoInfo?>(null)
    val info: StateFlow<VideoInfo?> = _info.asStateFlow()

    private val _qualityIndex = MutableStateFlow(0)
    val qualityIndex: StateFlow<Int> = _qualityIndex.asStateFlow()

    private val _message = MutableStateFlow<String?>(null)
    val message: StateFlow<String?> = _message.asStateFlow()

    val tasks = DownloadManager.tasks

    // 保存/清除 Cookie 后自增，让设置页重新读一次登录态
    private val _loginEpoch = MutableStateFlow(0)
    val loginEpoch: StateFlow<Int> = _loginEpoch.asStateFlow()

    fun refreshLogin() {
        _loginEpoch.value += 1
    }

    /** 推一条提示给全局 Snackbar */
    fun notify(msg: String) {
        _message.value = msg
    }

    fun setLink(v: String) {
        _link.value = v
    }

    fun setQualityIndex(i: Int) {
        _qualityIndex.value = i
    }

    fun consumeMessage() {
        _message.value = null
    }

    /**
     * 清空首页：解析结果、清晰度选择、输入框里的链接一起清掉。
     *
     * 以前只清 `_info`，链接还留在输入框里，用户点「清空」后会以为没生效
     * （清完还得手动删一次链接）。这里一次清干净。
     */
    fun clearAll() {
        _info.value = null
        _qualityIndex.value = 0
        _link.value = ""
    }

    /**
     * 正在跑的解析任务。
     *
     * 原来没有句柄：用户换链接后重新解析，旧任务仍在跑，完成后会把**旧结果**
     * 覆盖到新页面上（输入是新的、结果是上一个链接的）。这里持有 Job，
     * 新解析先取消旧的，并配合 [parseToken] 丢弃过期结果。
     */
    private var parseJob: Job? = null

    /** 解析请求版本号：只有最新一次的结果才允许写回界面。 */
    private var parseToken = 0

    fun parse(activity: Activity) {
        val text = _link.value.trim()
        if (text.isEmpty()) {
            _message.value = "请先粘贴分享链接"
            return
        }
        if (_parsing.value) return
        _parsing.value = true
        _info.value = null
        val token = ++parseToken
        parseJob?.cancel()
        parseJob = viewModelScope.launch {
            try {
                // 用户多半粘的是整段分享文案，先抠出链接再路由
                val (url, parser) = ParserRouter.resolve(text)
                val t0 = System.currentTimeMillis()
                val result = parser.parse(activity, url)
                // 期间用户又发起了新解析：这次的结果已经过期，直接丢弃，
                // 否则旧链接的结果会盖住新页面。
                if (token != parseToken) return@launch
                if (!result.isImage && result.qualities.isEmpty()) {
                    _message.value = "没有解析到可下载的地址"
                    AppLog.w("解析失败：没有可下载地址 | $url")
                } else {
                    // 清晰度列表：有 60fps 就不显示 30fps；其余只留前三高的分辨率
                    // （与桌面版同一套规则，档位下标在界面和下载里都是筛过之后的）
                    val shown = dropdownQualities(result.qualities)
                    _info.value = result.copy(qualities = shown)
                    _qualityIndex.value = 0
                    // 原始档数一并打出来：只写「N 档」时，分不清是解析真只拿到 1 档，
                    // 还是拿到了很多档被界面筛掉了 —— 这两种情况的排查方向完全相反
                    AppLog.i(
                        "解析成功 ${result.platform.label}「${result.title}」用时 " +
                            "${(System.currentTimeMillis() - t0) / 1000.0}s | " +
                            "画质 ${shown.size} 档（原始 ${result.qualities.size} 档）",
                    )
                }
            } catch (e: CancellationException) {
                // 取消是「用户换了链接 / 页面关了」，不是解析失败。
                // 原来被下面的 Exception 分支吃掉，界面会弹一句莫名其妙的失败提示。
                throw e
            } catch (e: Exception) {
                if (token == parseToken) {
                    _message.value = e.message ?: "解析失败"
                    AppLog.e("解析失败：${e.message ?: e.javaClass.simpleName}")
                }
            } finally {
                // 只有最新那次才允许把 parsing 复位，否则旧任务收尾会提前放开按钮。
                if (token == parseToken) _parsing.value = false
            }
        }
    }

    /** 解析后的图片选择；JM 页使用章节 ID 与原始页码以免下载错页。 */
    fun downloadSelected(
        activity: Activity, imageIndices: Set<Int> = emptySet(),
        jmPages: Set<Pair<String, Int>> = emptySet(),
    ) {
        val info = _info.value ?: return
        if (info.platform == Platform.JMCOMIC) {
            if (jmPages.isEmpty()) return
            DownloadManager.enqueue(activity, info, Quality("选择 ${jmPages.size} 页", ""), jmPages)
        } else {
            val images = info.images.filterIndexed { i, _ -> i in imageIndices }
            if (images.isEmpty()) return
            DownloadManager.enqueue(activity, info.copy(images = images), Quality("选择 ${images.size} 张", ""))
        }
        _message.value = "已加入下载队列，可在「任务」查看进度"
    }

    fun download(activity: Activity) {
        val info = _info.value ?: return
        val q: Quality? = if (info.isImage) {
            Quality(label = if (info.platform == Platform.JMCOMIC) "整本" else "图集", url = "")
        } else {
            val idx = _qualityIndex.value.coerceIn(0, (info.qualities.size - 1).coerceAtLeast(0))
            info.qualities.getOrNull(idx)
        }
        if (q == null) {
            _message.value = "没有可下载的清晰度"
            return
        }
        DownloadManager.enqueue(activity, info, q)
        _message.value = "已加入下载队列，可在「任务」查看进度"
    }
}
