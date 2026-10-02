package com.xd.vdl.core

import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/**
 * 轻量前台服务：只负责让进程在下载期间不被系统回收，
 * 实际下载逻辑在 [DownloadManager]（应用级协程作用域）里跑。
 */
class DownloadService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        Notifications.ensureChannel(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        runCatching {
            startForeground(Notifications.ID, Notifications.build(this, "准备下载…"))
        }
        return START_STICKY
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    fun setText(text: String, percent: Int?) {
        runCatching {
            NotificationManagerCompat.from(this)
                .notify(Notifications.ID, Notifications.build(this, text, percent))
        }
    }

    companion object {
        @Volatile
        private var instance: DownloadService? = null

        fun start(ctx: Context) {
            runCatching {
                ContextCompat.startForegroundService(
                    ctx, Intent(ctx, DownloadService::class.java))
            }
        }

        fun stop(ctx: Context) {
            runCatching { ctx.stopService(Intent(ctx, DownloadService::class.java)) }
        }

        fun update(text: String, percent: Int?) {
            instance?.setText(text, percent)
        }
    }
}
