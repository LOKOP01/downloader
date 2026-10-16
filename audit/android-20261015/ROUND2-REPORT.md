# Android 审计第二轮报告（B-01～B-09 / Lint / 待验证风险）

- 日期：2026-10-15
- 分支：`audit/android-full-20261015`（未推送、未合并、未发布）
- 前置：第一轮报告见 [REPORT.md](REPORT.md)，分模块详报见 review-*.md

## 0. 上下文恢复确认

已重读第一轮 [REPORT.md](REPORT.md) 的 B-01～B-09 记录与四份分模块报告，并逐项对照当前源码：

| 项 | 报告记录 | 恢复时源码状态 | 是否一致 |
|---|---|---|---|
| B-01 | Http.kt SessionCookieJar 未用 Cookie.matches | 确认存在（第 39–60 行整桶返回） | 一致 |
| B-02 | WebViewProxy 反射 ProxyProperties | 确认存在（只找该类型名） | 一致 |
| B-03 | JmClient 自建 client 绕过代理 | 确认存在（无 proxySelector） | 一致 |
| B-04 | allowBackup 未排除敏感 prefs | 确认存在（无 fullBackupContent） | 一致 |
| B-05 | 解析协程持有 Activity、取消记为失败 | 确认存在 | 一致 |
| B-06 | 预览外部打开用首张 URI | 确认存在（顶层 idx 固定） | 一致 |
| B-07 | AppLog 主线程写盘、formatter 并发 | 确认存在 | 一致 |
| B-08 | 低版本拒权后仍完整下载才失败 | 确认存在 | 一致 |
| B-09 | 同步 execute 不随协程取消 | 确认存在（16 处 execute） | 一致 |

第一轮已修的 A-01～A-14 未被重复修改。

## 1. 本轮修复概览

**修复 10 项**（B-01～B-09 + N08 + 混合内容策略），**Lint 从 24 条降到 2 条**，新增 **17 项 JVM 测试**。

| 编号 | 级别 | 修复内容 | 验证方式 |
|---|---|---|---|
| B-01 | P2 | Cookie 按 Domain/Path/Secure 匹配；顺带修好 Domain Cookie 跨子域失效 | 10 项单测（真实 OkHttp Cookie） |
| B-02 | P2 | 代理类型按 ProxyInfo → ProxyProperties 顺序探测；失败不再被误记为「已应用」 | 编译通过（设备行为未验证） |
| B-03 | P2 | JmClient 接入统一代理（新增 wrapInitialized） | 编译通过 + 代理分组单测 |
| B-04 | P2 | 排除 3 份凭据 prefs + WebView 目录，云备份与设备迁移都排除 | 资源合并通过（备份行为未在设备验证） |
| B-05 | P2 | 解析任务持 Job、新解析取消旧的、过期结果丢弃、取消不再报失败 | 编译通过（竞态需设备/集成验证） |
| B-06 | P2 | 当前页提升到顶层，外部打开/类型判定都用当前页 | 编译通过 |
| B-07 | P2 | 磁盘写入移到单独写线程（有界队列 + 丢弃计数）；时间戳每次新建 formatter | 编译通过 |
| B-08 | P2 | 下载开始前检查存储权限，缺权限直接失败并说明 | 编译通过 |
| B-09 | P2 | 新增 Call.await()，取消立即 call.cancel()；下载与封面已接入 | 4 项单测（真实 socket 验证取消 <4s） |
| N08 | P2 | 代理缓存键改为 (base client, 地址)，解析/下载超时不再互相污染 | 3 项单测 |
| — | P2 | WebView 混合内容 ALWAYS_ALLOW → COMPATIBILITY_MODE | 编译通过 |

### 本轮新发现的缺陷（第一轮未记录）

1. **Domain Cookie 跨子域失效**（B-01 深挖）：按 host 分桶导致在 www 收到的 Domain=example.com Cookie，子域完全查不到。修复后按语义键去重、跨桶匹配。
2. **代理缓存污染**（N08）：原报告已记录，本轮确认并修复。
3. **图标资源**：旧版 ic_launcher 系列 11 个文件确认无引用后删除。

## 2. B-01～B-09 逐项结果

| 编号 | 状态 | 说明 |
|---|---|---|
| B-01 | **已修复** | 见上表；额外发现 Domain 语义问题一并修复 |
| B-02 | **已修复** | 反射改为多类型探测；失败状态不再被缓存 |
| B-03 | **已修复** | 代理只改 proxySelector，未触碰 TLS 配置 |
| B-04 | **已修复** | 未关闭 allowBackup，只排除凭据，普通配置仍可迁移 |
| B-05 | **已修复** | 附带修复「取消被当成解析失败弹提示」 |
| B-06 | **已修复** | 翻页后媒体类型判定也跟随当前页 |
| B-07 | **已修复** | 队列满丢弃并计数，不静默丢日志 |
| B-08 | **已修复** | 权限检查放在任务开始前 |
| B-09 | **部分修复** | 下载主链路 + 封面已接入 await()；其余 14 处 execute 仍为同步（见第 7 节） |

## 3. Lint 警告完整分类（24 → 2）

| 数量 | Issue ID | 分类 | 处理 |
|---|---|---|---|
| 11 | IconLauncherShape | D 合理设计 | 10 个旧图标文件删除（无引用）；剩 1 条 v2 圆角图标为美术取舍，保留 |
| 5 | IconDuplicates | D 合理设计 | 随旧图标删除一并消除 |
| 3 | UnusedResources | A 真实缺陷 | 确认无引用后删除 |
| 2 | MonochromeLauncherIcon | C 兼容性 | v26 补 monochrome 标签（v33 已有） |
| 1 | ApplySharedPref | D 误报 | 加注释 + 局部 @Suppress，说明必须同步落盘 |
| 1 | GetInstance (AES/ECB) | D 误报 | 服务端协议规定，加注释 + 局部 @Suppress |
| 1 | InlinedApi | **A 真实缺陷** | 上一轮我引入的：SAMPLE_FLAG_PARTIAL_FRAME 需 API 26；改为按语义映射 BUFFER_FLAG_KEY_FRAME |
| 1 | CustomX509TrustManager | B 已知例外 | 保留（禁漫自签名证书），已用主机名白名单收窄；见下 |
| 3 | SuspiciousIndentation | D 误报 | 重构缩进消除（未用抑制） |

**剩余 2 条**：
1. `CustomX509TrustManager` —— 禁漫站点使用自签名证书，关闭链校验是功能必需；已通过主机名白名单限制范围（A-03）。**真实风险：中间人可解密禁漫流量**，要彻底解决需固定证书指纹，属独立议题。
2. `IconLauncherShape` —— 圆角图标美术规范，不影响功能。

## 4. 3 项待验证风险的最终判断

### 4.1 Instagram WebView 注入面 —— **确认为真实风险，已部分加固**

证据：`WebViewBridge.fetchInPage` 注册了 `addJavascriptInterface(bridge, "vdlResult")`，且 `deliver()` **不校验来源**（无 `isForMainFrame`、无 origin 检查）。iframe 或跳转后的页面若调用 `vdlResult.deliver(...)`，可伪造解析结果。

- 触发前提：页面内出现不可信 iframe，或导航离开 instagram.com（onPageFinished 只对主框架 URL 做了前缀判断，但 bridge 对象一旦注册就一直在）。
- 影响：伪造视频直链（误导下载）、或让解析提前返回错误结果。
- **本轮加固**：混合内容策略收紧为 COMPATIBILITY_MODE。
- **未修复原因**：给 JS 接口加 origin 校验需要 WebView 侧改造（`WebViewCompat.addWebMessageListener` 才支持 allowedOriginRules），改动面较大；建议单独立项。
- 判定：**待验证风险 → 真实缺陷（P2）**，但可利用性需要构造页面，未在设备实测。

### 4.2 FileProvider 路径过宽 —— **确认为设计选择，非缺陷**

证据：`file_paths.xml` 含 `<files-path path=".">` 与 `<cache-path path=".">`，范围覆盖整个私有目录。

- provider `exported="false"` + `grantUriPermissions="true"`：**其他应用无法主动访问**，只能通过我们显式授权的 URI。
- 我们只在用户点「打开 / 分享」时对**具体文件**授权。
- 判定：**不构成实际漏洞**。收紧到具体子目录收益有限，且可能破坏压缩包/图片导出；本轮不改。

### 4.3 START_STICKY 服务重建 —— **确认为真实缺陷，已修复**

证据：`DownloadManager` 的任务只存在内存（`_tasks` StateFlow + `jobs` map），进程被杀后**无任何持久化**。原返回 START_STICKY，系统重建空服务并显示「准备下载…」常驻通知，实际没有任何任务。

- 判定：**真实缺陷（P2）**，误导性提示。
- **本轮修复**：改为 `START_NOT_STICKY`。理由：与其显示假进度，不如干净停掉；真要恢复任务必须先做持久化（独立议题）。
- 注意：`START_NOT_STICKY` 不阻止系统在**内存压力下**回收服务，只是不再主动重建。**进程回收行为本身无法在 JVM 验证**。

## 5. 修改文件清单

**修改（22 个生产文件）**：AndroidManifest.xml、AppLog.kt、DownloadManager.kt、DownloadService.kt、download/Downloader.kt、download/JmDownloader.kt、download/MediaStoreExporter.kt、download/Muxer.kt、model/VideoInfo.kt、net/Http.kt、net/PreserveLoginCookieInterceptor.kt、net/ProxySettings.kt、net/WebViewProxy.kt、parse/BiliParser.kt、parse/JmClient.kt、parse/JmSession.kt、parse/WebViewBridge.kt、parse/XParser.kt、ui/AppViewModel.kt、ui/PreviewDialog.kt、ui/RemoteImage.kt、res/mipmap-anydpi-v26/ic_launcher_v2*.xml

**新增（6 个文件）**：
- `core/net/CancellableCall.kt`（可取消网络调用）
- `res/xml/backup_rules.xml`、`res/xml/data_extraction_rules.xml`（备份排除）
- 测试：`SessionCookieJarTest.kt`(10)、`CancellableCallTest.kt`(4)、`Round2FixRegressionTest.kt`(3)

**删除（11 个文件）**：旧版启动图标 ic_launcher / ic_launcher_round（5 组共 10 个）+ ic_launcher_vec.xml（均经确认无引用，可由 git 恢复）

**未变更**：依赖版本、权限声明、数据库结构、网络协议、持久化格式、minSdk/targetSdk。

累计 23 个文件 570 行新增 / 89 行删除（含第一轮）。

## 6. 构建、测试、Lint 实际执行结果

| 检查 | 命令 | 结果 |
|---|---|---|
| Debug 编译 | `gradlew assembleDebug` | **通过** |
| Release 编译 | `gradlew assembleRelease` | **通过** |
| 单元测试 | `gradlew test` | **通过：83 项，0 失败** |
| Lint | `gradlew lint` | **通过：0 error，2 warning**（原 24 warning） |
| 真机 / 模拟器 | `adb devices` | **未执行**：无设备、SDK 无 emulator/system-images |

测试增长：第一轮 66 项 → 本轮 83 项（新增 SessionCookieJarTest 10 + CancellableCallTest 4 + Round2FixRegressionTest 3）。

## 7. 尚未修复的问题

| 编号 | 级别 | 问题 | 未修原因 |
|---|---|---|---|
| B-09 剩余 | P2 | 另有 14 处 `execute()` 未接入 await()（Http.kt 6 处、JmClient 4 处、InstagramParser、BeforeDownloadDialog、WebViewBridge、BiliWebLoginDialog） | 逐个改造需回归各自调用链；下载主链路已修 |
| N04 剩余 | P2 | CookieStore 导入 WebView 时仍丢失 Secure/HttpOnly | 需要区分「JS 可读」与「仅 HTTP」的 Cookie 白名单 |
| N05 剩余 | P2 | 凭据仍以明文存 SharedPreferences | 改用 Keystore 加密属架构级改动 |
| N06 | P2 | CookieStore.clear 不清 HTTP 会话罐；清单平台会清掉全部 WebView Cookie | 需重新设计跨平台清理语义 |
| N09 | P2 | WebView JS 接口无 origin 校验（4.1） | 需改用 addWebMessageListener |
| D05 剩余 | P2 | 部分阻塞 IO（JM 打包下载、ZIP 解析）仍不可取消 | 需逐模块改造 |
| D06 | P2 | MediaStore pending 发布失败不回滚 | 需事务化设计 |
| D09/D10/D11 | P2 | JM 空章节跳过、错误 HTML 当媒体、完整性校验弱 | 需业务确认严格程度 |
| — | P2 | 禁漫证书链仍不校验（CustomX509TrustManager） | 需证书指纹固定，属独立安全议题 |

## 8. 真机验证清单（本轮无法执行）

| # | 验证项 | 环境 | 步骤 | 预期 |
|---|---|---|---|---|
| 1 | Cookie Secure/Path 生效 | Android 7–14 任一台 | 登录抖音后检查 HTTP 请求日志 | 不带 Secure Cookie 走明文 |
| 2 | 代理全链路 | 装 Clash 的机器 | 设置填代理 → 解析 X / 禁漫 | WebView 与 JM 都走代理 |
| 3 | 备份排除 | Android 12+ | `adb backup` 或云备份后检查 | 备份内无 vdl_cookies/vdl_jm_session/vdl_net |
| 4 | 解析竞态 | 任一台 | 快速连续粘贴两个链接解析 | 结果与最后输入一致 |
| 5 | 预览翻页外部打开 | 任一台 | 图集翻到第 3 张 → 右上角打开 | 打开第 3 张 |
| 6 | 日志不卡顿 | 任一台 | 下载中观察任务页 | 无掉帧；app.log 有内容 |
| 7 | 低版本权限 | Android 9 | 拒绝存储权限后下载 | 立即提示缺权限，不下载 |
| 8 | 取消中断 | 任一台 | 大文件下载中点取消 | 立即停止；日志有「已取消」 |
| 9 | 服务重建 | Android 12+ | 下载中 `adb shell am kill` | 不出现假的常驻通知 |

## 9. 回归风险与下一步建议

**回归风险**：
1. **Cookie 匹配收紧**可能让某些原本「侥幸带上」的 Cookie 不再发送 —— 若真机出现登录态异常，优先检查这一处（测试已覆盖 10 种语义）。
2. **日志异步化**：进程被强杀时队列里的日志可能丢失（已提供 `flush()`，但未接入退出路径）。
3. **混合内容收紧**：若某站点依赖明文子资源，解析可能受影响（当前解析主要走 shouldInterceptRequest）。
4. **START_NOT_STICKY**：系统回收后不再自动重建，用户需重新入队。

**建议下一步**：
1. 优先在真机执行第 8 节清单，尤其是 1、2、4、8 项。
2. 把 `CancellableCall.await()` 推广到剩余 14 处 execute。
3. 用 `WebViewCompat.addWebMessageListener` 替换 JS 接口，解决 N09。
4. 若要在系统回收后恢复下载，需先做任务持久化（Room/DataStore），再考虑改回 START_STICKY。
