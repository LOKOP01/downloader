package com.xd.vdl.ui

import android.content.ClipboardManager
import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.xd.vdl.BuildConfig
import com.xd.vdl.core.Platform
import com.xd.vdl.core.SaveSettings
import com.xd.vdl.core.download.JmDownloadMode
import com.xd.vdl.core.parse.JmAlbumZip
import com.xd.vdl.core.parse.JmClient
import com.xd.vdl.core.parse.JmSession
import com.xd.vdl.core.net.CookieStore
import com.xd.vdl.core.net.Http
import com.xd.vdl.ui.component.GkCard
import com.xd.vdl.ui.component.GkDimens
import com.xd.vdl.ui.component.GkPageTitle
import com.xd.vdl.ui.component.GkSectionLabel
import com.xd.vdl.ui.component.GkTextBadge
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
fun SettingsScreen(vm: AppViewModel, onEditCookie: (Platform) -> Unit) {
    val ctx = LocalContext.current
    // 保存/清除 Cookie 后自增，驱动下面重新读一次登录态
    val epoch by vm.loginEpoch.collectAsState()

    val platforms = remember { Platform.values().filter { it != Platform.UNKNOWN } }
    val status = remember(epoch) {
        platforms.associateWith { p ->
            val ck = Http.cookieFor(p)
            LoginStatus(ck, CookieStore.authKeysOf(p).filter { CookieStore.has(ck, it) })
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = GkDimens.screenPadding),
    ) {
        Spacer(Modifier.height(10.dp))
        GkPageTitle("设置")
        Spacer(Modifier.height(14.dp))

        GkSectionLabel("登录")
        GkCard {
            Text(
                "从电脑浏览器取 Cookie 填入即可，Cookie 只保存在本机，" +
                    "下次打开无需重填。高清晰度、受限内容需要对应平台的登录态。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(4.dp))

            platforms.forEachIndexed { i, p ->
                val st = status[p] ?: LoginStatus("", emptyList())
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    GkTextBadge(p.label.take(1))
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f)) {
                        Text(p.label, style = MaterialTheme.typography.bodyLarge)
                        Text(
                            if (st.loggedIn) "已登录 · ${st.hit.joinToString("/")}"
                            else if (st.hasCookie) "已填 Cookie（未见登录凭证）"
                            else "未登录",
                            style = MaterialTheme.typography.bodySmall,
                            color = if (st.loggedIn) MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    TextButton(onClick = { onEditCookie(p) }) {
                        Text(if (st.hasCookie) "修改" else "填写")
                    }
                    if (st.hasCookie) {
                        TextButton(onClick = {
                            CookieStore.clear(ctx, p)
                            vm.refreshLogin()
                            vm.notify("已清除 ${p.label} 的 Cookie")
                        }) { Text("清除") }
                    }
                }
                if (i < platforms.size - 1) HorizontalDivider()
            }
        }

        Spacer(Modifier.height(16.dp))
        GkSectionLabel("下载位置")
        SaveLocationCard(vm)

        Spacer(Modifier.height(16.dp))
        GkSectionLabel("禁漫下载")
        JmModeCard(vm)

        Spacer(Modifier.height(16.dp))
        GkSectionLabel("关于")
        GkCard {
            InfoLine("视频位置", SaveSettings.previewVideo(ctx))
            InfoLine("图片位置", SaveSettings.previewImage(ctx))
            InfoLine("压缩包位置", SaveSettings.previewArchive(ctx))
            InfoLine("支持平台", "抖音 / B站 / X / 小红书 / Instagram / 禁漫")
            InfoLine(
                "版本",
                "${BuildConfig.VERSION_NAME}（build ${BuildConfig.VERSION_CODE}）",
            )
        }

        Spacer(Modifier.height(24.dp))
    }
}

/**
 * 禁漫下载方式 + 账号登录。
 *
 * 「官方打包直链」(`/album_download_2`) 认的是**接口域自己签发的会话**，
 * 从 18comic.vip 抄来的 AVS 基本不管用（jmcomic 官方文档 issue #104 专门写过这个坑），
 * 所以这里直接做**账号登录** —— 和官方 App 同一个接口（`POST /login`），
 * 拿回 `jwttoken` 与 `s`（AVS），两个头一起带。登录后直链才会返回 `download_url`。
 */
@Composable
private fun JmModeCard(vm: AppViewModel) {
    val ctx = LocalContext.current
    var on by remember { mutableStateOf(JmDownloadMode.officialZipFirst(ctx)) }
    // 登录态刷新用
    var epoch by remember { mutableStateOf(0) }
    var showLogin by remember { mutableStateOf(false) }
    var showTest by remember { mutableStateOf(false) }
    val loggedIn = remember(epoch) { JmSession.loggedIn }
    val account = remember(epoch) { JmSession.account }

    GkCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("优先用官方打包直链", style = MaterialTheme.typography.bodyLarge)
                Text(
                    if (on) {
                        "登录后一次请求下完，最快；不可用会自动退回逐张下载"
                    } else {
                        "始终逐张下载并打包（进度按张数，未登录也能用）"
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.width(12.dp))
            Switch(
                checked = on,
                onCheckedChange = {
                    on = it
                    JmDownloadMode.setOfficialZipFirst(ctx, it)
                    vm.notify(if (it) "禁漫：优先官方打包直链" else "禁漫：始终逐张下载")
                },
            )
        }

        HorizontalDivider(Modifier.padding(vertical = 12.dp))

        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    if (loggedIn) "已登录：$account" else "未登录禁漫账号",
                    style = MaterialTheme.typography.bodyLarge,
                )
                Text(
                    if (loggedIn) {
                        "直链下载要用这个登录态；它和设置里手填的 Cookie 不是一回事"
                    } else {
                        "官方打包直链必须用它自己的登录态；手填的网页 Cookie 多半不认"
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = if (loggedIn) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            TextButton(onClick = { showLogin = true }) {
                Text(if (loggedIn) "重新登录" else "账号登录")
            }
            if (loggedIn) {
                TextButton(onClick = {
                    JmSession.clear(ctx)
                    epoch++
                    vm.notify("已退出禁漫账号")
                }) { Text("退出") }
            }
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.End,
        ) {
            TextButton(onClick = { showTest = true }) { Text("测试打包直链") }
        }
    }

    if (showLogin) {
        JmLoginDialog(
            onDone = { epoch++; showLogin = false },
            onClose = { showLogin = false },
        )
    }
    if (showTest) {
        JmZipTestDialog(onClose = { showTest = false })
    }
}

/** 账号登录：只用账号密码，不碰 Cookie（和官方 App 同一个 /login 接口） */
@Composable
private fun JmLoginDialog(onDone: () -> Unit, onClose: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var user by remember { mutableStateOf("") }
    var pass by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf("") }

    Dialog(
        onDismissRequest = onClose,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Card(Modifier.fillMaxSize()) {
            Column(Modifier.fillMaxSize().padding(20.dp)) {
                Text("登录禁漫", style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(6.dp))
                Text(
                    "用禁漫账号密码走官方 App 的登录接口，只把服务端返回的登录凭据" +
                        "（jwt / AVS）存在本机，**不会保存密码**。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(14.dp))

                OutlinedTextField(
                    value = user,
                    onValueChange = { user = it; msg = "" },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("账号") },
                    singleLine = true,
                )
                Spacer(Modifier.height(8.dp))
                var showPass by remember { mutableStateOf(false) }
                OutlinedTextField(
                    value = pass,
                    onValueChange = { pass = it; msg = "" },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("密码") },
                    singleLine = true,
                    // 密码默认不显示明文；要看一眼时点右上角的「显示」
                    visualTransformation = if (showPass) VisualTransformation.None
                    else PasswordVisualTransformation(),
                    trailingIcon = {
                        TextButton(onClick = { showPass = !showPass }) {
                            Text(if (showPass) "隐藏" else "显示", style = MaterialTheme.typography.labelSmall)
                        }
                    },
                )
                if (msg.isNotEmpty()) {
                    Spacer(Modifier.height(10.dp))
                    Text(msg, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error)
                }

                Spacer(Modifier.weight(1f))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = onClose, enabled = !busy) { Text("取消") }
                    Spacer(Modifier.width(8.dp))
                    Button(
                        onClick = {
                            if (user.isBlank() || pass.isEmpty()) {
                                msg = "账号和密码都要填"
                                return@Button
                            }
                            busy = true
                            msg = ""
                            scope.launch {
                                val r = withContext(Dispatchers.IO) {
                                    JmClient.login(user.trim(), pass)
                                }
                                busy = false
                                if (r.ok) {
                                    JmSession.save(ctx, r.jwt, r.avs, user.trim())
                                    onDone()
                                } else {
                                    msg = r.message
                                }
                            }
                        },
                        enabled = !busy,
                    ) { Text(if (busy) "登录中…" else "登录") }
                }
            }
        }
    }
}

/**
 * 诊断：原样打一次官方打包接口，把服务端回复摆出来。
 *
 * 「登录了还是说未登录」这类问题，光看界面提示没法定位 —— 得看服务端原话
 * （`status` / `msg`）以及这次请求到底带了什么头。
 */
@Composable
private fun JmZipTestDialog(onClose: () -> Unit) {
    val scope = rememberCoroutineScope()
    var id by remember { mutableStateOf("422866") }
    var busy by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf("") }

    Dialog(
        onDismissRequest = onClose,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Card(Modifier.fillMaxSize()) {
            Column(Modifier.fillMaxSize().padding(20.dp)) {
                Text("测试打包直链", style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(6.dp))
                Text(
                    "填一个本子号，看服务端原样返回什么。" +
                        "`status:0` = 未登录；有 download_url 就说明能用。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = id,
                    onValueChange = { id = it },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("本子号") },
                    singleLine = true,
                )
                Spacer(Modifier.height(10.dp))
                Text(
                    JmClient.cookieDebug(),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (result.isNotEmpty()) {
                    Spacer(Modifier.height(10.dp))
                    Text(result, style = MaterialTheme.typography.bodySmall)
                }
                Spacer(Modifier.weight(1f))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = onClose, enabled = !busy) { Text("关闭") }
                    Spacer(Modifier.width(8.dp))
                    Button(
                        onClick = {
                            busy = true
                            result = ""
                            scope.launch {
                                result = withContext(Dispatchers.IO) {
                                    val raw = JmClient.albumDownloadRaw(id.trim())
                                    // 既给「人话」也给原始返回：`status` 是 0 和「没这个字段」
                                    // 是完全不同的两种情况，只看结论会误判
                                    val parsed = runCatching { JmAlbumZip.parse(raw) }.getOrNull()
                                    if (parsed == null) {
                                        "原始返回：\n$raw"
                                    } else {
                                        buildString {
                                            append("status = ${parsed.status.ifEmpty { "(无)" }}")
                                            append("   msg = ${parsed.msg.ifEmpty { "(无)" }}\n")
                                            append("title = ${parsed.title.ifEmpty { "(无)" }}\n")
                                            append("fileSize = ${parsed.fileSize.ifEmpty { "(无)" }}\n")
                                            append("download_url = ${parsed.downloadUrl.ifEmpty { "(无)" }}\n")
                                            append("\n原始返回：\n$raw")
                                        }
                                    }
                                }
                                busy = false
                            }
                        },
                        enabled = !busy && id.isNotBlank(),
                    ) { Text(if (busy) "请求中…" else "测试") }
                }
            }
        }
    }
}

/**
 * 下载位置设置卡。
 *
 * 只让用户改**末级文件夹名**，前缀（Movies / Pictures / Download）由系统定：
 * Android 10+ 往公共目录写文件必须走 MediaStore，而 MediaStore 只认相对路径，
 * 前缀决定了系统按哪种媒体类型归档 —— 换掉它图库就扫不到，反而更难找。
 *
 * 目录名一律经 [SaveSettings.sanitize] 过滤：混进 `/` 会拼出多级路径，
 * 混进 `..` 可能越权写别处。保存时立刻回显过滤后的结果，让用户看得见。
 */
@Composable
private fun SaveLocationCard(vm: AppViewModel) {
    val ctx = LocalContext.current
    // 保存后自增，触发重新读一遍（三个输入框的初值跟着走）
    var epoch by remember { mutableStateOf(0) }

    GkCard {
        Text(
            "只改末级文件夹名即可，前面的大类目录（视频 / 图片 / 下载）由系统决定。" +
                "视频与图片会归入相册对应分类，压缩包放在下载目录。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(12.dp))

        DirField(
            key = "video",
            epoch = epoch,
            label = "视频",
            prefix = "Movies /",
            initial = SaveSettings.videoDir(ctx),
            onSave = { SaveSettings.setVideoDir(ctx, it); vm.notify("视频位置已更新") },
            onChanged = { epoch += 1 },
        )
        Spacer(Modifier.height(10.dp))
        DirField(
            key = "image",
            epoch = epoch,
            label = "图片",
            prefix = "Pictures /",
            initial = SaveSettings.imageDir(ctx),
            onSave = { SaveSettings.setImageDir(ctx, it); vm.notify("图片位置已更新") },
            onChanged = { epoch += 1 },
        )
        Spacer(Modifier.height(10.dp))
        DirField(
            key = "archive",
            epoch = epoch,
            label = "压缩包",
            prefix = "Download /",
            initial = SaveSettings.archiveDir(ctx),
            onSave = { SaveSettings.setArchiveDir(ctx, it); vm.notify("压缩包位置已更新") },
            onChanged = { epoch += 1 },
        )

        Spacer(Modifier.height(12.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "已下载的文件不会移动，改动只对之后的新任务生效。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = {
                SaveSettings.reset(ctx)
                epoch += 1
                vm.notify("已恢复默认位置")
            }) { Text("恢复默认") }
        }
    }
}

/**
 * 一行目录名编辑：前缀只读、末级可改，回车、失焦或点「保存」都会提交。
 *
 * 保存后靠 `onChanged()` 自增 epoch、由 `key(epoch)` 重建输入框，把
 * [SaveSettings.sanitize] 过滤后的结果回显出来（避免显示未过滤的原值）。
 */
@Composable
private fun DirField(
    key: String,
    epoch: Int,
    label: String,
    prefix: String,
    initial: String,
    onSave: (String) -> Unit,
    onChanged: () -> Unit,
) {
    var text by remember(key, epoch) { mutableStateOf(initial) }
    var bad by remember(key, epoch) { mutableStateOf(false) }
    // 用于「失焦即提交」：只在 true→false 那一次提交，首次获得焦点不提交
    var focused by remember(key, epoch) { mutableStateOf(false) }
    val focusManager = LocalFocusManager.current

    // 提交：过滤后为空说明用户把名字清空了（或输入的全是非法字符），拒绝并回退
    fun commit() {
        val clean = SaveSettings.sanitize(text)
        if (clean.isEmpty()) {
            bad = true
            return
        }
        bad = false
        if (clean != text) text = clean
        if (clean != initial) {
            onSave(clean)
            onChanged()
        }
    }

    Column(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                prefix,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.width(6.dp))
            OutlinedTextField(
                value = text,
                onValueChange = { text = it; bad = false },
                modifier = Modifier
                    .weight(1f)
                    // 失焦即提交 —— 注释里一直写着这个语义，但之前只有下面那个
                    // 「保存」按钮会调 commit()，靠回车/切走以为保存了的编辑会被
                    // 同卡片的下一次保存（epoch 变化 → 重新以 initial 播种）悄悄回滚。
                    .onFocusChanged { state ->
                        if (focused && !state.isFocused) commit()
                        focused = state.isFocused
                    },
                label = { Text(label) },
                isError = bad,
                singleLine = true,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = {
                    commit()
                    focusManager.clearFocus()
                }),
                supportingText = if (bad) {
                    { Text("不能为空，也不能只有符号") }
                } else null,
            )
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.End,
        ) {
            TextButton(onClick = { commit() }) { Text("保存") }
        }
    }
}

/** 一个平台的登录态快照：有没有 Cookie、命中了哪些登录凭证 */
private data class LoginStatus(val cookie: String, val hit: List<String>) {
    val hasCookie: Boolean get() = cookie.isNotEmpty()
    val loggedIn: Boolean get() = hit.isNotEmpty()
}

@Composable
private fun InfoLine(k: String, v: String) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 5.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(
            k,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(v, style = MaterialTheme.typography.bodyMedium)
    }
}

/**
 * Cookie 登录弹窗：按平台逐项填写（与桌面版 `app/view/cookie_dialog.py` 同一套做法）。
 *
 * 每一项一个独立输入框，已保存的值会预填 —— 只改其中一项不会动到别的。
 * 懒得逐项填时可以点「从剪贴板填充」：把整串 Cookie 复制好，会按名字自动分到各字段；
 * 认不出的项（抖音的 tt_scid、odin_tt 之类）也保留下来一并保存，提高接口通过率。
 */
@Composable
fun LoginDialog(
    platform: Platform,
    onSaved: (CookieStore.SaveResult) -> Unit,
    onClose: () -> Unit,
) {
    val ctx = LocalContext.current
    val fields = remember(platform) { CookieStore.fieldsOf(platform) }
    val fieldNames = remember(fields) { fields.map { it.name }.toSet() }

    // 已保存的值拆成两半：字段表里的预填进输入框，其余留着一起保存
    val saved = remember(platform) { CookieStore.split(CookieStore.get(platform)) }
    val values = remember(platform) {
        mutableStateMapOf<String, String>().apply {
            fields.forEach { f -> put(f.name, saved[f.name].orEmpty()) }
        }
    }
    var extra by remember(platform) { mutableStateOf(saved.filterKeys { it !in fieldNames }) }
    var tip by remember(platform) { mutableStateOf("") }
    var error by remember(platform) { mutableStateOf("") }

    Dialog(
        onDismissRequest = onClose,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Card(Modifier.fillMaxSize()) {
            Column(
                Modifier
                    .fillMaxSize()
                    .padding(20.dp),
            ) {
                Text(
                    "登录 ${platform.label}",
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold,
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    "在电脑浏览器登录 ${platform.label} 后，F12 → Application（应用程序）→ Cookies，" +
                        "对着列表把下面各项逐个填进来。只需要填必填项。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    "登录凭证多是 HttpOnly，控制台里的 document.cookie 取不到，" +
                        "得在上面那个面板里找。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(10.dp))

                OutlinedButton(onClick = {
                    val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                    // 和 MainActivity.sniffClipboard 一样地防一手：剪贴板为空时
                    // getItemAt(0) 会越界，某些 ROM 在非聚焦窗口读剪贴板会抛
                    // SecurityException。读不到就当空串，走下面的提示分支。
                    val raw = runCatching {
                        cm.primaryClip?.takeIf { it.itemCount > 0 }
                            ?.getItemAt(0)?.text?.toString()
                    }.getOrNull().orEmpty()
                    val parsed = CookieStore.parse(raw)
                    if (parsed.isEmpty()) {
                        error = "剪贴板里没识别到 Cookie，先复制好整串再点"
                        tip = ""
                        return@OutlinedButton
                    }
                    val kv = CookieStore.split(parsed)
                    fields.forEach { f -> kv[f.name]?.let { values[f.name] = it } }
                    extra = kv.filterKeys { it !in fieldNames }
                    error = ""
                    val n = fields.count { !values[it.name].isNullOrEmpty() }
                    tip = "已填入 $n 个字段" +
                        (if (extra.isNotEmpty()) "，另有 ${extra.size} 项其他 Cookie 会一并保存" else "") +
                        "，核对后保存"
                }) { Text("从剪贴板填充") }

                if (tip.isNotEmpty()) {
                    Spacer(Modifier.height(6.dp))
                    Text(
                        tip,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
                if (error.isNotEmpty()) {
                    Spacer(Modifier.height(6.dp))
                    Text(
                        error,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }

                Spacer(Modifier.height(10.dp))
                Column(
                    Modifier
                        .weight(1f)
                        .verticalScroll(rememberScrollState()),
                ) {
                    fields.forEach { f ->
                        OutlinedTextField(
                            value = values[f.name].orEmpty(),
                            onValueChange = {
                                values[f.name] = it
                                error = ""
                                tip = ""
                            },
                            modifier = Modifier.fillMaxWidth(),
                            label = {
                                Text(f.name + if (f.required) "（必填）" else "（可选）")
                            },
                            supportingText = { Text(f.hint) },
                            singleLine = true,
                        )
                        Spacer(Modifier.height(8.dp))
                    }
                    if (extra.isNotEmpty()) {
                        Text(
                            "另有 ${extra.size} 项其他 Cookie 会一并保留：" +
                                extra.keys.take(4).joinToString("/") +
                                if (extra.size > 4) " 等" else "",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }

                Spacer(Modifier.height(12.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.End,
                ) {
                    TextButton(onClick = onClose) { Text("取消") }
                    Spacer(Modifier.width(8.dp))
                    Button(onClick = {
                        val missing = fields.filter {
                            it.required && values[it.name].orEmpty().isBlank()
                        }
                        if (missing.isNotEmpty()) {
                            error = "还差必填项：" + missing.joinToString("、") { it.name }
                            tip = ""
                            return@Button
                        }
                        val r = CookieStore.saveFields(ctx, platform, values.toMap(), extra)
                        if (!r.ok) {
                            error = "没有可保存的内容"
                            return@Button
                        }
                        onSaved(r)
                    }) { Text("保存") }
                }
            }
        }
    }
}
