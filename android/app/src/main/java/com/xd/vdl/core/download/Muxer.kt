package com.xd.vdl.core.download

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import java.io.File
import java.io.IOException
import java.nio.ByteBuffer

/**
 * 把分离的视频轨与音频轨合并成一个 mp4（DASH 用）。
 * 直接用系统 MediaExtractor + MediaMuxer，不依赖 ffmpeg。
 */
object Muxer {

    fun merge(videoFile: File, audioFile: File, outFile: File) {
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

    private fun copyTrack(ex: MediaExtractor, muxer: MediaMuxer, trackIndex: Int) {
        val buf = ByteBuffer.allocate(1 shl 20)
        val info = MediaCodec.BufferInfo()
        while (true) {
            val size = ex.readSampleData(buf, 0)
            if (size < 0) break
            info.offset = 0
            info.size = size
            info.presentationTimeUs = ex.sampleTime
            info.flags = ex.sampleFlags
            muxer.writeSampleData(trackIndex, buf, info)
            ex.advance()
        }
    }
}
