package com.xd.vdl

import android.Manifest
import android.app.Activity
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.core.app.ActivityCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import com.xd.vdl.core.AppLog
import com.xd.vdl.core.ClipDetect
import com.xd.vdl.core.Platform
import com.xd.vdl.core.net.CookieStore
import com.xd.vdl.ui.AppViewModel
import com.xd.vdl.ui.HomeScreen
import com.xd.vdl.ui.LoginDialog
import com.xd.vdl.ui.SettingsScreen
import com.xd.vdl.ui.TasksScreen
import com.xd.vdl.ui.theme.VdlTheme

class MainActivity : ComponentActivity() {

    private val pendingShared = mutableStateOf<String?>(null)

    /** 剪切板嗅探：最近一次自动填入的文本，用于去重（同一条不反复填） */
    private var lastClip = ""
    private var skipStaleClipboardForShare = false
    private var appViewModel: AppViewModel? = null
    private val clipboard by lazy { getSystemService(CLIPBOARD_SERVICE) as ClipboardManager }
    private val clipboardListener = ClipboardManager.OnPrimaryClipChangedListener {
        // 分类结果更新也会回调；仅在窗口聚焦时读取，并按文本去重。
        if (hasWindowFocus()) appViewModel?.let(::sniffClipboard)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppLog.setup(java.io.File(filesDir, "logs"))
        CookieStore.init(this)
        com.xd.vdl.core.SaveSettings.init(this)
        com.xd.vdl.core.parse.JmSession.init(this)
        askNotificationPermission()
        askStoragePermission()
        handleIntent(intent)

        setContent {
            VdlTheme {
                val vm: AppViewModel = viewModel()
                appViewModel = vm
                LaunchedEffect(vm) {
                    if (hasWindowFocus()) sniffClipboard(vm)
                }

                val shared by pendingShared
                LaunchedEffect(shared) {
                    if (!shared.isNullOrBlank()) {
                        vm.acceptDetectedLink(shared!!)
                        pendingShared.value = null
                    }
                }

                AppRoot(vm, this)
            }
        }
    }

    /**
     * 回到前台时看剪切板里有没有认识的链接，有就自动填进输入框。
     *
     * 只在 Android 10+ 允许的时机（前台）读；同一条内容只处理一次，
     * 避免用户切来切去被反复覆盖已经改过的输入框。
     */
    override fun onStart() {
        super.onStart()
        clipboard.addPrimaryClipChangedListener(clipboardListener)
    }

    override fun onResume() {
        super.onResume()
        // onResume 可能早于窗口获得焦点，焦点回调会再读一次。
        if (hasWindowFocus()) appViewModel?.let(::sniffClipboard)
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) appViewModel?.let(::sniffClipboard)
    }

    override fun onStop() {
        clipboard.removePrimaryClipChangedListener(clipboardListener)
        super.onStop()
    }

    private fun sniffClipboard(vm: AppViewModel) {
        // 分享意图比旧剪贴板优先，等分享文本进入输入框再嗅探。
        if (!hasWindowFocus() || pendingShared.value != null) return
        val text = runCatching {
            clipboard.primaryClip?.takeIf { it.itemCount > 0 }
                ?.getItemAt(0)?.coerceToText(this)?.toString()
        }.getOrNull().orEmpty().trim()
        if (skipStaleClipboardForShare) {
            // 从系统分享进入时，剪贴板可能还存着另一个旧链接。
            lastClip = text
            skipStaleClipboardForShare = false
            return
        }
        if (text.isEmpty() || text == lastClip) return
        val detected = ClipDetect.detect(text) ?: return
        lastClip = text
        if (vm.link.value.trim() == detected.url) return
        vm.acceptDetectedLink(detected.url)
        vm.notify("已从剪贴板识别到${detected.platformLabel}链接，点「开始解析」即可")
        AppLog.i("剪贴板嗅探：识别到 ${detected.platformLabel} 链接")
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        if (intent?.action == Intent.ACTION_SEND) {
            val t = intent.getStringExtra(Intent.EXTRA_TEXT)
            if (!t.isNullOrBlank()) {
                skipStaleClipboardForShare = true
                val vm = appViewModel
                if (vm != null) {
                    vm.acceptDetectedLink(t)
                    if (hasWindowFocus()) sniffClipboard(vm)
                } else pendingShared.value = t
            }
        }
    }

    private fun askStoragePermission() {
        // Android 9 及以下向公共相册写入文件需要运行时存储权限。
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P &&
            checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) !=
                PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this, arrayOf(Manifest.permission.WRITE_EXTERNAL_STORAGE), 1002,
            )
        }
    }

    private fun askNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED
            if (!granted) {
                ActivityCompat.requestPermissions(
                    this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1001)
            }
        }
    }
}

@Composable
private fun AppRoot(vm: AppViewModel, activity: Activity) {
    var tab by remember { mutableStateOf(0) }
    val homeRequest by vm.homeRequest.collectAsState()
    LaunchedEffect(homeRequest) { tab = 0 }
    var loginPlatform by remember { mutableStateOf<Platform?>(null) }
    val message by vm.message.collectAsState()
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(message) {
        message?.let {
            snackbar.showSnackbar(it)
            vm.consumeMessage()
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            NavigationBar {
                NavigationBarItem(
                    selected = tab == 0,
                    onClick = { tab = 0 },
                    icon = { Icon(Icons.Filled.Home, contentDescription = null) },
                    label = { Text("首页") },
                )
                NavigationBarItem(
                    selected = tab == 1,
                    onClick = { tab = 1 },
                    icon = { Icon(Icons.Filled.Download, contentDescription = null) },
                    label = { Text("任务") },
                )
                NavigationBarItem(
                    selected = tab == 2,
                    onClick = { tab = 2 },
                    icon = { Icon(Icons.Filled.Settings, contentDescription = null) },
                    label = { Text("设置") },
                )
            }
        },
    ) { pad ->
        Box(
            Modifier
                .fillMaxSize()
                .padding(pad),
        ) {
            when (tab) {
                0 -> HomeScreen(vm, activity)
                1 -> TasksScreen(vm)
                else -> SettingsScreen(vm, onEditCookie = { loginPlatform = it })
            }
        }
    }

    loginPlatform?.let { p ->
        LoginDialog(
            platform = p,
            onSaved = { r ->
                loginPlatform = null
                vm.refreshLogin()
                vm.notify(
                    if (!r.ok) "没识别到 Cookie，检查复制的内容"
                    else if (r.authHit.isEmpty()) {
                        "已保存 ${r.count} 项，但没找到登录凭证，可能仍是未登录状态"
                    } else {
                        "${p.label} 已保存 ${r.count} 项（含 ${r.authHit.joinToString("/")}）"
                    },
                )
            },
            onClose = {
                loginPlatform = null
                vm.refreshLogin()
            },
        )
    }
}
