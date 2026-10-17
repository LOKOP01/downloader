package com.xd.vdl.core

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 「什么算下载成功、可以进书架」的边界。
 *
 * 任务书明确要求：不得把**任务已创建、部分图片已下载、任务异常结束**误判成整章成功。
 * 这些判定集中在 [DownloadTask.readableAsManga]，所以在这里逐状态钉死。
 */
class DownloadTaskMangaTest {

    private fun task(
        status: TaskStatus,
        platform: Platform = Platform.JMCOMIC,
        uris: List<String> = emptyList(),
        saved: String = "",
    ) = DownloadTask(
        id = "t1",
        title = "测试",
        platform = platform,
        qualityLabel = "整本",
        status = status,
        savedUri = saved,
        savedUris = uris,
    )

    @Test
    fun `只有已完成的任务才可进书架`() {
        assertTrue(task(TaskStatus.DONE, saved = "content://a/1").readableAsManga)
        for (s in listOf(
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.MERGING,
            TaskStatus.EXPORTING,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
        )) {
            assertFalse("$s 不该进书架", task(s, saved = "content://a/1").readableAsManga)
        }
    }

    @Test
    fun `禁漫任务完成后可进书架`() {
        assertTrue(task(TaskStatus.DONE, uris = emptyList(), saved = "content://a/1").readableAsManga)
    }

    @Test
    fun `非禁漫的纯视频任务不显示书架入口`() {
        val t = task(TaskStatus.DONE, platform = Platform.BILIBILI, saved = "content://video/1")
        assertFalse("视频不该出现在书架", t.readableAsManga)
        assertTrue("但视频仍应可预览", t.canPreview)
    }

    @Test
    fun `图集任务完成后可进书架`() {
        val t = task(
            TaskStatus.DONE, platform = Platform.INSTAGRAM,
            uris = listOf("content://i/1", "content://i/2"),
        )
        assertTrue(t.readableAsManga)
    }

    @Test
    fun `失败的图集任务不进书架`() {
        val t = task(
            TaskStatus.FAILED, platform = Platform.INSTAGRAM,
            uris = listOf("content://i/1", "content://i/2"),
        )
        // 关键：哪怕已经存下来一部分图（savedUris 非空），失败也不进书架
        assertFalse(t.readableAsManga)
    }

    @Test
    fun `取消的禁漫任务不进书架`() {
        assertFalse(task(TaskStatus.CANCELED, saved = "content://a/1").readableAsManga)
    }

    @Test
    fun `完成任务没有产物时也没有入口`() {
        assertFalse(task(TaskStatus.DONE).readableAsManga)
    }
}
