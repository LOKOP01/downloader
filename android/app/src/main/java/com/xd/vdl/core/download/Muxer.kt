package com.xd.vdl.core.download

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import kotlinx.coroutines.ensureActive
import kotlin.coroutines.coroutineContext
import java.io.File
import java.io.IOException
import java.nio.ByteBuffer

/**
 * 把分离的视频轨与音频轨合并成一个 mp4（DASH 用）。
 * 直接用系统 MediaExtractor + MediaMuxer，不依赖 ffmpeg。
 */
object Muxer {

    suspend fun merge(videoFile: File, audioFile: File, outFile: File) {
        if (outFile.exists()) outFile.delete()

        val videoEx = MediaExtractor()
        val audioEx = MediaExtractor()
        var muxer: MediaMuxer? = null
        try {
            videoEx.setDataSource(videoFile.absolutePath)
            var videoFormat: MediaFormat? = null
            for (i in 0 until videoEx.trackCount) {
                val f = videoEx.getTrackFormat(i)
                val mime = f.getString(MediaFormat.KEY_MIME).orEmpty()
                if (mime.startsWith("video/")) {
                    videoEx.selectTrack(i)
                    videoFormat = f
                    break
                }
            }
            if (videoFormat == null) throw IOException("视频轨缺失，无法合并")

            audioEx.setDataSource(audioFile.absolutePath)
            var audioFormat: MediaFormat? = null
            for (i in 0 until audioEx.trackCount) {
                val f = audioEx.getTrackFormat(i)
                val mime = f.getString(MediaFormat.KEY_MIME).orEmpty()
                if (mime.startsWith("audio/")) {
                    audioEx.selectTrack(i)
                    audioFormat = f
                    break
                }
            }

            muxer = MediaMuxer(outFile.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
            val vTrack = muxer.addTrack(videoFormat)
            val aTrack = if (audioFormat != null) muxer.addTrack(audioFormat) else -1

            muxer.start()
            copyTrack(videoEx, muxer, vTrack)
            if (aTrack >= 0) copyTrack(audioEx, muxer, aTrack)
            muxer.stop()
        } finally {
            runCatching { muxer?.release() }
            runCatching { videoEx.release() }
            runCatching { audioEx.release() }
        }
    }

    private suspend fun copyTrack(ex: MediaExtractor, muxer: MediaMuxer, trackIndex: Int) {
        val buf = ByteBuffer.allocate(1 shl 20)
        val info = MediaCodec.BufferInfo()
        while (true) {
            val size = ex.readSampleData(buf, 0)
            if (size < 0) break
            info.offset = 0
            info.size = size
            info.presentationTimeUs = ex.sampleTime
            // ⚠️ 这里必须做**语义映射**，不能直接把 ex.sampleFlags 赋给 info.flags：
            //  · MediaMuxer 关心的是「这一帧是不是关键帧」，对应 BUFFER_FLAG_KEY_FRAME；
            //  · MediaExtractor.SAMPLE_FLAG_SYNC 的数值虽然也是 1，但两者语义不同
            //    （Lint 会报 WrongConstant）；
            //  · 而 SAMPLE_FLAG_PARTIAL_FRAME 是 API 26 才有的常量，minSdk 24 直接引用
            //    会被内联成 API 24 不认识的取值（Lint 报 InlinedApi）。
            // 只保留关键帧标志，行为对 H.264/AAC（B 站 DASH 的实际格式）完全一致。
            info.flags = if (ex.sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC != 0) {
                MediaCodec.BUFFER_FLAG_KEY_FRAME
            } else {
                0
            }
            muxer.writeSampleData(trackIndex, buf, info)
            ex.advance()
            // 取消必须能打断长视频合并：原来是无条件同步循环，用户点取消后
            // 仍会把整段合并完并写进相册，看起来像「取消无效」。
            coroutineContext.ensureActive()
        }
    }
}
