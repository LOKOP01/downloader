package com.xd.vdl.core

import android.content.Context
import com.xd.vdl.core.download.Downloader
import com.xd.vdl.core.download.JmDownloader
import com.xd.vdl.core.download.MediaStoreExporter
import com.xd.vdl.core.download.Muxer
import com.xd.vdl.core.parse.JmParser
import com.xd.vdl.core.model.Quality
import com.xd.vdl.core.model.VideoInfo
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.File
import java.io.IOException
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong

/** 下载队列的唯一入口（应用级单例，配合前台服务保活） */
object DownloadManager {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val _tasks = MutableStateFlow<List<DownloadTask>>(emptyList())
    val tasks: StateFlow<List<DownloadTask>> = _tasks.asStateFlow()

    private val jobs = ConcurrentHashMap<String, Job>()
    private val lastNotify = AtomicLong(0L)

    fun enqueue(
        context: Context, info: VideoInfo, quality: Quality,
        selectedPages: Set<Pair<String, Int>>? = null,
    ) {
        val ctx = context.applicationContext
        val id = UUID.randomUUID().toString().replace("-", "").substring(0, 12)
        val label = when {
            selectedPages != null -> "选择 ${selectedPages.size} 页"
            info.isImage -> "图集 ${info.images.size} 张"
            else -> quality.label
        }
        _tasks.update { it + DownloadTask(id, info.title, info.platform, label) }
        DownloadService.start(ctx)
        AppLog.i(
            "入队 $id ${info.platform.label}「${info.title}」| $label | " +
                "视频候选=${if (info.isImage) 0 else quality.videoCandidates.size} | " +
                "音频候选=${if (quality.needMerge) quality.audioCandidates.size else 0}",
        )
        // 先登记再启动：`scope.launch` 会立刻调度协程，而完成回调里的
        // `jobs.isEmpty()` → stop() 跑在 IO 线程上。若先 launch 后赋值，批量入队
        // 时前一个任务的收尾可能插在赋值之前，把刚起头的这个任务的前台保护撤掉。
        val job = scope.launch(start = CoroutineStart.LAZY) {
            runTask(ctx, info, quality, id, selectedPages)
        }
        jobs[id] = job
        job.invokeOnCompletion {
            jobs.remove(id)
            if (jobs.isEmpty()) DownloadService.stop(ctx)
        }
        job.start()
        refreshNotification(force = true)
    }

    fun cancel(id: String) {
        jobs[id]?.cancel()
    }

    fun clearFinished() {
        val n = _tasks.value.count { it.finished }
        _tasks.update { list -> list.filterNot { it.finished } }
        if (n > 0) AppLog.i("清空已结束的任务记录 $n 条")
    }

    // ------------------------------------------------------------------ //
    private fun update(id: String, block: (DownloadTask) -> DownloadTask) {
        _tasks.update { list -> list.map { if (it.id == id) block(it) else it } }
        refreshNotification()
    }

    private fun setStatus(id: String, status: TaskStatus, error: String = "") {
        update(id) { it.copy(status = status, error = error) }
        refreshNotification(force = true)
    }

    private suspend fun runTask(
        ctx: Context, info: VideoInfo, quality: Quality, id: String,
        selectedPages: Set<Pair<String, Int>>?,
    ) {
        setStatus(id, TaskStatus.RUNNING)
        val t0 = System.currentTimeMillis()
        val root = ctx.getExternalFilesDir(null) ?: ctx.filesDir
        val work = File(root, "downloads/$id").apply { mkdirs() }
        try {
            if (info.platform == Platform.JMCOMIC && selectedPages != null)
                runJmSelectedImages(ctx, info, id, work, selectedPages)
            else if (info.platform == Platform.JMCOMIC) runJmcomic(ctx, info, id, work)
            else if (info.isImage) runImages(ctx, info, id, work)
            else runVideo(ctx, info, quality, id, work)
            setStatus(id, TaskStatus.DONE)
            AppLog.i("下载结束 $id 状态=已完成 用时${(System.currentTimeMillis() - t0) / 1000.0}s")
        } catch (e: CancellationException) {
            setStatus(id, TaskStatus.CANCELED)
            AppLog.i("下载结束 $id 状态=已取消")
            throw e
        } catch (e: Exception) {
            setStatus(id, TaskStatus.FAILED, e.message ?: e.javaClass.simpleName)
            AppLog.e("下载结束 $id 状态=失败 错误=${e.message ?: e.javaClass.simpleName}")
        } finally {
            runCatching { work.deleteRecursively() }
        }
    }

    private suspend fun runVideo(
        ctx: Context, info: VideoInfo, quality: Quality, id: String, work: File,
    ) {
        val out = File(work, FileName.build(info.title, "mp4"))
        if (quality.needMerge) {
            val vf = File(work, "v.m4s")
            val af = File(work, "a.m4s")
            // 两条轨各自按自己的 Content-Length 如实上报（视频轨 0→100%，音频轨
            // 再按「视频 + 音频」的合计重算基准）。原来把视频轨写成 d/2、t*2，
            // 整个视频下完进度条只到 25%，到音频轨才猛跳到 90%+ —— 用户盯着
            // 25% 不动会以为卡住了。代价是两轨交界处进度会回退一点（音频通常
            // 只占 5~10%），这比「卡在 25%」诚实得多。
            Downloader.downloadFirst(
                quality.videoCandidates, info.platform, vf,
                onProgress = { d, t ->
                    update(id) { it.copy(downloaded = d, total = if (t > 0) t else 0) }
                },
                onTry = { i, n, u ->
                    AppLog.i("视频 $id 地址 $i/$n ${Downloader.hostOf(u)}")
                },
                onFail = { i, n, u, err ->
                    AppLog.w("视频 $id 地址 $i/$n 失败 ${Downloader.hostOf(u)}：$err")
                },
            )
            val vLen = vf.length()
            Downloader.downloadFirst(
                quality.audioCandidates, info.platform, af,
                onProgress = { d, t ->
                    update(id) {
                        it.copy(downloaded = vLen + d, total = vLen + if (t > 0) t else 0)
                    }
                },
                onTry = { i, n, u ->
                    AppLog.i("音频 $id 地址 $i/$n ${Downloader.hostOf(u)}")
                },
                onFail = { i, n, u, err ->
                    AppLog.w("音频 $id 地址 $i/$n 失败 ${Downloader.hostOf(u)}：$err")
                },
            )
            AppLog.i("DASH 两轨完成 $id：视频 ${vf.length()} + 音频 ${af.length()}，开始合并")
            setStatus(id, TaskStatus.MERGING)
            Muxer.merge(vf, af, out)
            vf.delete()
            af.delete()
        } else {
            Downloader.downloadFirst(
                quality.videoCandidates, info.platform, out,
                onProgress = { d, t ->
                    update(id) { it.copy(downloaded = d, total = t) }
                },
                onTry = { i, n, u ->
                    AppLog.i("下载 $id 地址 $i/$n ${Downloader.hostOf(u)}")
                },
                onFail = { i, n, u, err ->
                    AppLog.w("下载 $id 地址 $i/$n 失败 ${Downloader.hostOf(u)}：$err")
                },
            )
        }
        if (!out.exists() || out.length() == 0L) throw IllegalStateException("下载文件为空")
        setStatus(id, TaskStatus.EXPORTING)
        val uri = MediaStoreExporter.exportVideo(ctx, out)
        if (uri == null) throw IOException("视频写入相册失败")
        update(id) { it.copy(savedUri = uri.toString()) }
    }

    /** 禁漫：逐章拉图 → 切片解码 → 压 JPEG → 存相册 */
    private suspend fun runJmcomic(ctx: Context, info: VideoInfo, id: String, work: File) {
        JmDownloader.download(
            ctx = ctx,
            info = info,
            onProgress = { done, total ->
                update(id) {
                    it.copy(downloaded = done.toLong(), total = total.toLong())
                }
            },
            onChapter = { text -> update(id) { it.copy(note = text) } },
            onZip = { zip, count ->
                // 以车号命名：422866.zip
                val named = File(work, "${FileName.sanitize(info.jmId, 32)}.zip")
                if (!zip.renameTo(named)) {
                    zip.copyTo(named, overwrite = true)
                    zip.delete()
                }
                val uri = MediaStoreExporter.exportDownload(ctx, named, "application/zip")
                if (uri == null) throw IOException("压缩包写入下载目录失败")
                // 记下 URI，卡片才可点（否则 canPreview 恒 false，界面像没反应）
                update(id) {
                    it.copy(
                        savedUri = uri.toString(),
                        note = "已保存 $count 张 → ${named.name}",
                    )
                }
            },
        )
    }

    /** 只选几页时作为单张图片保存到相册，不强制打包成整本 ZIP。 */
    private suspend fun runJmSelectedImages(
        ctx: Context, info: VideoInfo, id: String, work: File,
        selectedPages: Set<Pair<String, Int>>,
    ) {
        val saved = mutableListOf<String>()
        val chapters = info.jmChapters.ifEmpty { listOf(info.jmId to info.title) }
        for ((cid, _) in chapters) {
            val numbers = selectedPages.filter { it.first == cid }.map { it.second }.sorted()
            if (numbers.isEmpty()) continue
            val filenames = JmParser.fetchChapterImages(cid)
            for (page in numbers) {
                val filename = filenames.getOrNull(page - 1)
                    ?: throw IOException("第 $page 页不存在")
                update(id) { it.copy(note = "第 ${saved.size + 1}/${selectedPages.size} 张") }
                val bytes = JmDownloader.previewPage(info, cid, filename, page)
                val file = File(work,
                    "${FileName.sanitize(info.jmId, 32)}-${FileName.sanitize(cid, 32)}-%04d.jpg".format(page))
                file.writeBytes(bytes)
                val uri = MediaStoreExporter.exportImage(ctx, file, "image/jpeg")
                    ?: throw IOException("第 $page 页写入相册失败")
                saved.add(uri.toString())
                update(id) {
                    it.copy(savedUris = saved.toList(), downloaded = saved.size.toLong(),
                        total = selectedPages.size.toLong())
                }
            }
        }
        if (saved.size != selectedPages.size) throw IOException("有选择的图片未找到")
        update(id) { it.copy(note = "已保存 ${saved.size} 张") }
    }

    private suspend fun runImages(ctx: Context, info: VideoInfo, id: String, work: File) {
        val n = info.images.size
        if (n == 0) throw IOException("图片地址为空")
        val saved = mutableListOf<String>()
        info.images.forEachIndexed { i, url ->
            update(id) { it.copy(note = "第 ${i + 1}/$n 张") }
            val ext = if (url.contains("webp")) "webp" else "jpg"
            val f = File(work, "%02d.%s".format(i + 1, ext))
            Downloader.download(url, info.platform, f) { d, t ->
                val cur = if (t > 0) (d * 100 / t).toInt() else 0
                update(id) {
                    it.copy(downloaded = (i * 100 + cur).toLong(), total = n * 100L)
                }
            }
            val uri = MediaStoreExporter.exportImage(
                ctx, f, if (ext == "webp") "image/webp" else "image/jpeg",
            ) ?: throw IOException("第 ${i + 1}/$n 张图片写入相册失败")
            saved.add(uri.toString())
            // 每存一张就同步一次，下完前也能预览已拿到的部分
            update(id) { it.copy(savedUris = saved.toList()) }
        }
        // 全部 URI 交给任务，预览时可横滑翻页
        update(id) { it.copy(savedUris = saved, note = "已保存 ${saved.size} 张") }
    }

    private fun refreshNotification(force: Boolean = false) {
        val now = System.currentTimeMillis()
        if (!force && now - lastNotify.get() < 700) return
        lastNotify.set(now)
        val list = _tasks.value
        val active = list.filter {
            it.status == TaskStatus.RUNNING || it.status == TaskStatus.MERGING ||
                it.status == TaskStatus.EXPORTING
        }
        val pending = list.count { it.status == TaskStatus.PENDING }
        if (active.isEmpty() && pending == 0) return
        val cur = active.lastOrNull()
        val text = buildString {
            append("下载中 ${active.size}")
            if (pending > 0) append(" · 等待 $pending")
            if (cur != null && cur.status == TaskStatus.RUNNING) append(" · ${cur.percent}%")
        }
        DownloadService.update(text, cur?.percent)
    }
}
