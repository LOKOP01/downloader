# Android 项目全量代码审计报告（2026-10-15）

## 0. 审计范围与口径

- 对象：本仓库 `android/`（Kotlin 单模块，无 Java 源码）。
- 分支：`audit/android-full-20261015`（未推送、未合并、未发布）。
- 口径：**仅统计有代码证据的缺陷**；已修复项须有编译或测试证据才算「已验证」。
- 无关改动：工作区中 Windows 端未提交修改**未被本次审计触碰**。

## 1. 项目盘点

| 项 | 实际值 |
|---|---|
| 语言 | Kotlin（47 个生产 .kt + 12 个测试 .kt），**0 个 Java 文件** |
| 模块 | 单模块 `:app`，无 Product Flavor |
| 架构 | 单 Activity + Jetpack Compose；`core/parse` 各平台 Parser、`core/download` 下载器、`core/net` 网络与 Cookie、`ui/` 界面 |
| 入口 | `MainActivity`（Compose），`DownloadService`（前台服务保活） |
| 状态管理 | `AppViewModel`（StateFlow）+ 单例 `DownloadManager`（应用级 CoroutineScope） |
| 并发 | 协程（IO Dispatcher）+ JM 图片下载用线程池并发 |
| 构建 | AGP 8.5.2 / Kotlin 2.0.20 / Gradle Wrapper / JDK 17 |
| SDK | compileSdk 34、minSdk 24、targetSdk 34 |
| 依赖 | OkHttp 4.12、kotlinx-coroutines、Compose BOM、qfluentwidgets 无关（桌面端）、Pillow 无关（桌面端） |
| 测试 | 13 个 JVM 单测类，共 **66 项**（原 60 项 + 本次新增 6 项） |
| 权限 | INTERNET、POST_NOTIFICATIONS、WRITE_EXTERNAL_STORAGE(maxSdk 28) |
| 组件导出 | 仅 MainActivity 导出（含 ACTION_SEND）；其余未导出 |

**未覆盖**：无真机/模拟器（`adb devices` 为空，SDK 中无 emulator 与 system-images），故**未做真机运行验证**。

## 2. 验证结果（真实执行）

| 检查 | 命令 | 结果 |
|---|---|---|
| Debug 编译 | `gradlew assembleDebug` | 通过（修复前/后均通过） |
| Release 编译 | `gradlew assembleRelease` | 通过 |
| 单元测试 | `gradlew test` | 通过（66 项，0 失败） |
| Lint | `gradlew lint` | **修复前失败（2 error）→ 修复后通过** |
| 真机/模拟器 | `adb devices` | **未执行**（无设备、无模拟器镜像） |

## 3. 问题台账与修复

### 已修复（含验证方式）

| 编号 | 级别 | 问题 | 位置 | 修复 |
|---|---|---|---|---|
| A-01 | P1 | 跨域重定向未剥离 Cookie，登录凭据可随 302 送往任意目标域（OkHttp 只剥 Authorization） | `net/PreserveLoginCookieInterceptor.kt` | host 变化时显式 removeHeader("Cookie")；回归测试覆盖 |
| A-02 | P1 | 禁漫凭据（AVS/JWT）无条件附加到服务端返回的任意 URL | `parse/JmClient.kt` | 新增域名白名单 `isJmCredentialHost`；三处调用点均校验；回归测试覆盖 |
| A-03 | P1 | 禁漫 HostnameVerifier 恒 true，任意域名+任意证书均接受 | `parse/JmClient.kt` | 证书链保持放宽，主机名收窄到禁漫白名单；回归测试覆盖 |
| A-04 | P1 | 前台服务可被旧任务收尾停掉，新任务失去保活 | `core/DownloadManager.kt` | 先登记 job 再 start 服务 |
| A-05 | P1 | JM 首批 8 路全尺寸解码，预算滞后，可能 OOM | `download/JmDownloader.kt` | 首块改为 2 路探路，测出尺寸后再放宽 |
| A-06 | P1 | 封面无上限读取 + 全尺寸解码，列表易 OOM | `ui/RemoteImage.kt` | 12 MB 上限 + 采样解码（最长边 1080） |
| A-07 | P1 | 低版本同名 ZIP 覆盖已有成品 | `download/MediaStoreExporter.kt` | 改用不重名目标文件 |
| A-08 | P2 | X 混合推文保留图片导致路由成图集，视频被丢弃 | `parse/XParser.kt` | 拿到视频后清空 images；回归测试覆盖 |
| A-09 | P2 | B 站多分 P 只取第 1 P，标题无分 P 名 | `parse/BiliParser.kt` | 新增 `pickPage` 按 ?p=N 取 cid/标题/时长；回归测试覆盖 |
| A-10 | P2 | B 站 durl 多段被当成互斥档位，只下到第一段 | `parse/BiliParser.kt` + `DownloadManager.kt` | 合并为单档 + 按序拼接分段；编译通过 |
| A-11 | P2 | 合并过程不响应取消，取消后仍合并并写相册 | `download/Muxer.kt` | 改为 suspend + ensureActive；取消时删除半成品 |
| A-12 | P2 | 通知未检查权限，13+ 拒绝后可能抛异常（Lint error） | `core/DownloadService.kt` | 显式检查 POST_NOTIFICATIONS |
| A-13 | P2 | sampleFlags 类型不匹配（Lint error） | `download/Muxer.kt` | 用 SAMPLE_FLAG_MASK 收敛取值 |
| A-14 | P2 | JmClient 缩进歧义（Lint error，原代码即有） | `parse/JmClient.kt` | 消除歧义缩进 |

### 记录但未修复（有证据、影响较低或改动面大）

| 编号 | 级别 | 问题 | 位置 | 未修原因 |
|---|---|---|---|---|
| B-01 | P2 | SessionCookieJar 未用 Cookie.matches，Secure/Path 语义失效 | `net/Http.kt` | 需改动会话语义，风险高于收益，建议单独立项 |
| B-02 | P2 | WebView 代理反射用 ProxyProperties，与 Android Q 的 ProxyInfo 签名不符 | `net/WebViewProxy.kt` | 需真机验证，本机无设备 |
| B-03 | P2 | JM 独立 OkHttpClient 绕过应用代理设置 | `parse/JmClient.kt` | 与 TLS 放宽耦合，需一并设计 |
| B-04 | P2 | allowBackup 未排除敏感 prefs | `AndroidManifest.xml` | 会影响用户备份行为，需产品决策 |
| B-05 | P2 | 解析协程跨重建持有 Activity、取消被记为解析失败 | `ui/AppViewModel.kt` | 需配合 UI 状态机调整 |
| B-06 | P2 | 图片翻页后「外部打开」仍用首张 URI | `ui/PreviewDialog.kt` | 需把当前页回传，涉及 Pager 状态 |
| B-07 | P2 | AppLog 在主线程同步写盘、SimpleDateFormat 并发访问 | `core/AppLog.kt` | 改动面涉及全局日志路径 |
| B-08 | P2 | 老版本拒绝存储权限后仍完整下载再导出失败 | `MainActivity.kt` | 需真机验证权限流程 |
| B-09 | P2 | 同步 execute 未绑定 Call.cancel，取消不中断网络 | 多处 | 需逐个改造为可取消调用 |

### 待验证风险（不足以确认缺陷）

- Instagram WebView 脚本注入面（需恶意页面/跳转条件，未实测）。
- FileProvider 路径范围偏宽（未构造可利用场景）。
- 下载服务 START_STICKY 重建后不恢复任务（未在真机复现）。

## 4. 文件修改清单

**修改（11 个生产文件）**：`core/DownloadManager.kt`、`core/DownloadService.kt`、`core/download/JmDownloader.kt`、`core/download/MediaStoreExporter.kt`、`core/download/Muxer.kt`、`core/model/VideoInfo.kt`、`core/net/PreserveLoginCookieInterceptor.kt`、`core/parse/BiliParser.kt`、`core/parse/JmClient.kt`、`core/parse/XParser.kt`、`ui/RemoteImage.kt`

合计 264 行新增、38 行删除。

**新增（1 个测试文件）**：`app/src/test/java/com/xd/vdl/core/AuditFixRegressionTest.kt`（6 项）

**删除**：无

**未变更**：依赖、权限、数据库结构、网络协议、持久化格式、`minSdk/targetSdk` 均未改动。

## 5. 剩余风险

1. **无真机验证**：所有修复只经过编译与 JVM 单测，未在设备上运行；JM 下载、前台服务保活、相册导出等行为需真机确认。
2. **B-01～B-09 未修**：均为有证据的真实问题，但改动面或验证成本超出本次安全范围。
3. **Lint 仍有 warning**（25 项，非阻断），未逐条处理。
4. 审计报告由 4 个子代理分模块产出（`review-download.md`/`review-network.md`/`review-parsers.md`/`review-ui.md`），本文件为汇总去重结果。
