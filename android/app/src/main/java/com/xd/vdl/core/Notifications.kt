package com.xd.vdl.core

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import com.xd.vdl.MainActivity

object Notifications {
    const val CHANNEL = "vdl_download"
    const val ID = 1001

    fun ensureChannel(ctx: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(CHANNEL, "下载", NotificationManager.IMPORTANCE_LOW)
            ctx.getSystemService(NotificationManager::class.java)
                .createNotificationChannel(ch)
        }
    }

    fun build(ctx: Context, text: String, percent: Int? = null): Notification {
        val open = PendingIntent.getActivity(
            ctx, 0, Intent(ctx, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val b = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle("视频下载器")
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(open)
        if (percent != null) b.setProgress(100, percent, false)
        return b.build()
    }
}
