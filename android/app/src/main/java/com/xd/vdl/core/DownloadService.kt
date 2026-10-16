package com.xd.vdl.core

import android.Manifest
import android.app.Service
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.content.Intent
import android.os.IBinder
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

import com.xd.vdl.core.parse.JmSession

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
        // 初始化一次，别让后台下载读到空的禁漫登录态。
        // （虽然已改为 START_NOT_STICKY，服务仍可能先于 Activity 被显式启动）
        JmSession.init(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        runCatching {
            startForeground(Notifications.ID, Notifications.build(this, "准备下载…"))
        }
        // ⚠️ 用 START_NOT_STICKY（D07 修复）：
        // 进程被系统回收后，DownloadManager 里的协程任务**全部丢失**，
        // 没有任何持久化可恢复。此时若返回 START_STICKY，系统会重建一个
        // 空服务、拉起一条「准备下载…」的常驻通知，用户看到「还在下载」，
        // 实际上一个任务都没有 —— 误导性比直接停掉更糟。
        // 需要恢复下载时，应由用户重新入队（后续若要真恢复，得先把任务持久化）。
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    fun setText(text: String, percent: Int?) {
        runCatching {
            // Android 13+ 用户可拒绝通知权限；未授权时 notify 会抛 SecurityException，
            // 这里显式跳过（下载本身不受影响），也让 Lint 的 MissingPermission 有据可依。
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
                PackageManager.PERMISSION_GRANTED
            ) {
                return
            }
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
