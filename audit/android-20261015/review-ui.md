# Android UI / Activity / 日志只读审计

审计日期：2026-10-15。按委托的工作区与分支上下文审查当前磁盘内容；未运行任何 git 命令，未改动源码、Windows 未提交修改或构建产物。唯一写入本报告。以下均为静态代码结论及建议复现步骤，**未执行 Gradle/JVM 测试，未做模拟器或真机验证**。为遵守唯一可写路径，未启动可能生成缓存/产物的测试命令。

## 分级

P1：可由外部媒体触发严重内存耗尽等高影响问题。P2：常规操作可触发的状态错误、生命周期/取消/权限或主线程阻塞问题。P3：低影响可观测性问题。无已确认 P0。编号 UI-xx 为本分报告局部编号。

## 发现

### UI-01 · P1 · 远程封面无尺寸采样、响应体无上限，超大图片可耗尽进程内存

- 证据：[RemoteImage.kt:34–49](../../android/app/src/main/java/com/xd/vdl/ui/RemoteImage.kt#L34-L49) 对非 JM 远程响应执行 body.bytes()，随后直接 decodeByteArray，无 inJustDecodeBounds/inSampleSize；JM 分支同样把返回字节直接解码。[HomeScreen.kt:175–181](../../android/app/src/main/java/com/xd/vdl/ui/HomeScreen.kt#L175-L181) 将解析得到的 coverUrl 交给此组件，虽然显示只有 108×144dp，实际仍解全尺寸。
- 触发步骤：解析一个封面为极高分辨率/超大响应的内容；结果卡加载封面即触发。不需要用户打开大图。像 20000×20000 ARGB 图片的像素预算约 1.6GB，且响应字节与 bitmap 同时存活；实际结果取决于设备堆大小。
- 调用链：HomeScreen → ResultCard → RemoteImage → execute/body.bytes → BitmapFactory.decodeByteArray。
- 最小修复：限制响应体读取大小；先读取图片尺寸并按实际容器采样，拒绝异常像素数，或使用有尺寸约束/内存预算的图片加载器。不要把 runCatching 吞掉内存错误当成修复。
- 回归：正常/损坏/超大压缩体/超大像素封面、JM 与非 JM 封面分别验证；确保不会按原始像素尺寸分配、超限给明确占位错误。下载前图片也需同类预算：[BeforeDownloadDialog.kt:196–208](../../android/app/src/main/java/com/xd/vdl/ui/BeforeDownloadDialog.kt#L196-L208) 虽采样到 1800px，但先无上限地读取整份 bytes，仍可被大响应体耗尽内存。

### UI-02 · P2 · 解析期间更换输入/接收新分享，旧请求会回填新页面

- 证据：[AppViewModel.kt:26–29](../../android/app/src/main/java/com/xd/vdl/ui/AppViewModel.kt#L26-L29) 接收链接只改 link/info；[59–60](../../android/app/src/main/java/com/xd/vdl/ui/AppViewModel.kt#L59-L60) 手动编辑只改 link；[83–105](../../android/app/src/main/java/com/xd/vdl/ui/AppViewModel.kt#L83-L105) 捕获旧 text 后启动任务，完成时无 requestId/link 校验地更新 info。[HomeScreen.kt:105–118](../../android/app/src/main/java/com/xd/vdl/ui/HomeScreen.kt#L105-L118) 输入与粘贴按钮在 parsing 时仍可操作，只有解析和清空按钮被禁用。
- 触发步骤：输入 A 开始慢解析；未完成时输入/粘贴 B，或从外部分享 B；等 A 返回。输入框显示 B，结果和下载对象却是 A。解析按钮因旧请求仍忙也无法立即解析 B。
- 调用链：输入框 setLink / MainActivity.handleIntent→acceptDetectedLink → 旧 parse 协程完成 → _info=result → ResultCard → download 使用旧结果。
- 最小修复：维护 parse Job 和递增 generation；更换输入/分享/清空时取消旧请求并失效其 generation；回填前校验 generation。取消须向底层网络传播。若暂不支持取消，至少禁止所有输入替换且明确说明，但外部分享仍应排队或使旧结果失效。
- 回归：可控 fake parser 挂起 A，接受 B，释放 A，断言 B 不会得到 A 的 info；手动输入、粘贴、剪贴板自动探测、ACTION_SEND 各走一次；快速重复解析应只有一个有效请求。

### UI-03 · P2 · 分享 Intent 未消费且 onNewIntent 未更新 Activity.intent，重建重放陈旧分享

- 证据：[MainActivity.kt:61–72](../../android/app/src/main/java/com/xd/vdl/MainActivity.kt#L61-L72) 不区分 savedInstanceState，每次 onCreate 都 handleIntent(intent)；[144–158](../../android/app/src/main/java/com/xd/vdl/MainActivity.kt#L144-L158) onNewIntent 只 handleIntent，没有 setIntent；[82–86](../../android/app/src/main/java/com/xd/vdl/MainActivity.kt#L82-L86) 清掉的只是内存 pendingShared 而非 Intent。
- 触发步骤：从分享 A 冷启动，编辑成 B 并完成解析，然后触发 Activity 真正重建；原始分享 A 会再次 acceptDetectedLink，覆盖 B 并清空解析结果。另一场景是 singleTask 接收新分享 B，重建时 Activity.intent 仍可能是最初的 A/启动 Intent，无法表达最新分享。
- 调用链：系统重建 → onCreate → handleIntent(getIntent()) → pendingShared → acceptDetectedLink → link 被重写、info 清空。
- 最小修复：onNewIntent 中 setIntent(intent)，以 SavedStateHandle/已消费 token 持久化消费状态，onCreate 恢复与新 Intent 消费分离；不能仅依赖 pendingShared=null。保存当前输入等轻量 UI 状态。
- 回归：ActivityScenario.recreate 覆盖冷分享 A→编辑 B、分享 A→新分享 B→recreate、普通启动→分享 B→recreate；进程重建另加恢复测试。注意 [AndroidManifest.xml:25](../../android/app/src/main/AndroidManifest.xml#L25) 声明接管 orientation/screenSize/uiMode，因此普通旋转未必重建，不能将“旋转未复现”当作排除证据。

### UI-04 · P2 · VM 解析协程持有发起 Activity，重建后仍用旧 Activity；取消被作为失败处理

- 证据：[AppViewModel.kt:83–97](../../android/app/src/main/java/com/xd/vdl/ui/AppViewModel.kt#L83-L97) 将 Activity 捕获到 viewModelScope 协程并传给 parser；[115–119](../../android/app/src/main/java/com/xd/vdl/ui/AppViewModel.kt#L115-L119) catch Exception 未先重抛 CancellationException。Activity 真正重建时 VM 可保留而旧 Activity 已销毁。[Parser.kt:9–10](../../android/app/src/main/java/com/xd/vdl/core/parse/Parser.kt#L9-L10) 接口把 Activity 纳入所有解析请求；[WebViewBridge.kt:42–67](../../android/app/src/main/java/com/xd/vdl/core/parse/WebViewBridge.kt#L42-L67) 实际用其创建 WebView。
- 触发步骤：发起较慢解析，在该请求完成前真正重建 Activity；旧协程依然持有旧 Activity，若稍后进入 WebView 兜底则在旧 Activity 上创建 WebView。结束页面/清 VM 引发取消时，上层会记录“解析失败”而非正常取消。
- 调用链：HomeScreen→vm.parse(oldActivity)→viewModelScope→parser.parse→WebViewBridge(oldActivity)。这是**任务存续期旧 Activity 保留/失效上下文风险，不是已证实的无限期泄漏**。
- 最小修复：网络解析从 VM 移除 Activity 参数；需要 WebView 的 UI 生命周期工作交给当前 Activity/生命周期 owner，销毁时取消并重新请求；catch CancellationException 必须重抛。只换 applicationContext 不能直接替代所有 UI WebView 生命周期设计。
- 回归：挂起解析时 recreate，确认旧 Activity 可回收且后续不创建旧 Activity WebView；正常取消不产生失败提示/失败日志；解析失败仍正常释放 parsing。
- 已排除误报：下载入口 [DownloadManager.kt:42](../../android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L42) 转为 applicationContext；全局网络上下文 [Http.kt:105–106](../../android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L105-L106) 也如此。不能据 UI 传入 Activity 就称下载单例永久泄漏。WebViewBridge 已有 finally 销毁，不应忽略 [146–150](../../android/app/src/main/java/com/xd/vdl/core/parse/WebViewBridge.kt#L146-L150) 的既有清理。

### UI-05 · P2 · 图片页翻页后“用其它应用打开”仍打开首张

- 证据：[PreviewDialog.kt:102–107](../../android/app/src/main/java/com/xd/vdl/ui/PreviewDialog.kt#L102-L107) idx 只来自 startIndex；[142–143](../../android/app/src/main/java/com/xd/vdl/ui/PreviewDialog.kt#L142-L143) 顶栏打开 uris[idx]；[334–352](../../android/app/src/main/java/com/xd/vdl/ui/PreviewDialog.kt#L334-L352) Pager 内部维护 currentPage，但没有向 PreviewDialog 回传。任务页传 startIndex=0，见 [TasksScreen.kt:102–107](../../android/app/src/main/java/com/xd/vdl/ui/TasksScreen.kt#L102-L107)。
- 触发步骤：完成两张以上图集下载，打开预览，滑到第 2 张，点顶栏外部打开；Intent 的 URI 仍为第 1 张。这里是 ACTION_VIEW 外部打开，并非实现了 ACTION_SEND 分享。
- 调用链：TasksScreen→PreviewDialog(idx=0)→ImagePager→ZoomPager(currentPage=1)；顶栏→openExternally(uris[0])。
- 最小修复：提升当前页状态/回传 onPageChanged，外部打开使用当前显示页 URI；压缩包若允许打开当前解压页，需另走 FileProvider，不能直接暴露 file URI。
- 回归：图集 1/2/末页各点外部打开并截获 Intent 验证 URI；非零 startIndex、视频、ZIP 原文件打开分别测试。

### UI-06 · P2 · 远程预览关闭/快速换图只取消协程，底层阻塞 HTTP 仍继续

- 证据：[RemoteImage.kt:32–49](../../android/app/src/main/java/com/xd/vdl/ui/RemoteImage.kt#L32-L49)、[BeforeDownloadDialog.kt:194–213](../../android/app/src/main/java/com/xd/vdl/ui/BeforeDownloadDialog.kt#L194-L213) 在 withContext(IO) 内用 Call.execute()/body.bytes()，没有保存 Call 并在取消时 call.cancel，也没有可取消 await。JM 分支也调用同步 fetch/preview。withContext 的离开可防止取消后正常回填，但不能自动中断 execute。
- 触发步骤：慢速图片源下快速上一张/下一张、反复打开关闭图片预览、滚动网格淘汰项目；已离开画面的 HTTP 仍占连接、流量和 IO 工作线程，直到超时/响应完毕，响应体仍可能分配内存。
- 调用链：LaunchedEffect(url) → IO execute/body.bytes → Compose 移除或 key 变化 → Job.cancel（无 Call.cancel）。
- 最小修复：统一提供 suspendCancellableCoroutine/OkHttp enqueue 的可取消请求，取消时 call.cancel，关闭 response；为图片加载设置并发限制和有界缓存，重抛取消而非 runCatching 吞掉。
- 回归：MockWebServer 延迟头/延迟 body，取消加载后断言 call 被取消、无后续完整下载、并发不持续增长；重复快速切换最终只显示当前图。

### UI-07 · P2 · 日志在主线程同步写文件，并与后台日志竞争同一锁

- 证据：[AppLog.kt:28–31](../../android/app/src/main/java/com/xd/vdl/core/AppLog.kt#L28-L31) setup 直接 mkdirs+写启动日志；[45–68](../../android/app/src/main/java/com/xd/vdl/core/AppLog.kt#L45-L68) add 在同步锁内执行 exists/length/delete/rename/appendText。[MainActivity.kt:63](../../android/app/src/main/java/com/xd/vdl/MainActivity.kt#L63) onCreate 主线程调用 setup；[AppViewModel.kt:92–117](../../android/app/src/main/java/com/xd/vdl/ui/AppViewModel.kt#L92-L117) Main 上的解析完成/异常写日志；[SaveSettings.kt:84–88](../../android/app/src/main/java/com/xd/vdl/core/SaveSettings.kt#L84-L88) 设置页提交也同步写日志。
- 触发步骤：冷启动、解析结束、保存目录时遇慢存储或后台正在写/轮转日志，主线程发生磁盘 IO 或等待持锁 IO。代码足以确认阻塞风险，未量测实际 ANR。
- 调用链：UI事件/启动→AppLog.i/e→add→synchronized(lock)→appendFile。
- 最小修复：内存缓冲的短锁与磁盘写入分离；单个 IO writer/有界 Channel 串行落盘及轮转，启动 mkdirs 也移出 UI。明确满队列策略，不用无限制 launch。
- 回归：注入慢 FileSink，UI 调用应快速返回；并发写入保持顺序；512KB 轮转、写失败、退出 flush 测试；StrictMode 验证 UI 无磁盘写。

### UI-08 · P2 · 共享 SimpleDateFormat 在锁外使用，不符合日志“任意线程可写”承诺

- 证据：[AppLog.kt:25](../../android/app/src/main/java/com/xd/vdl/core/AppLog.kt#L25) 单个 SimpleDateFormat；[45–52](../../android/app/src/main/java/com/xd/vdl/core/AppLog.kt#L45-L52) fmt.format(Date()) 在 synchronized(lock) 前。[DownloadManager.kt:31](../../android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L31) 多任务 IO scope 与 UI 都可写 AppLog。
- 触发步骤：多个并发下载与 UI 操作同时产生日志；共享 formatter 的内部可变状态发生竞态，时间戳可能错误。未声称已观察到异常堆栈。
- 调用链：多个线程→AppLog.add→同一 fmt.format（未同步）→之后才锁 buf。
- 最小修复：在同一锁内格式化，或改用线程安全 DateTimeFormatter（核对 minSdk/desugaring），最好由单 writer 同时负责格式化。
- 回归：大量并行 i/w/e，注入固定 Clock 或检查时间戳格式、条数与缓冲上限；同时间隔的可重复性断言。

### UI-09 · P2 · Android 9 及以下拒绝存储权限后，仍允许完整下载到导出阶段才失败

- 证据：[MainActivity.kt:163–172](../../android/app/src/main/java/com/xd/vdl/MainActivity.kt#L163-L172) 仅启动时 requestPermissions，没有结果处理/下载前门禁；[AppViewModel.kt:125–154](../../android/app/src/main/java/com/xd/vdl/ui/AppViewModel.kt#L125-L154) 直接 enqueue。[MediaStoreExporter.kt:85–100](../../android/app/src/main/java/com/xd/vdl/core/download/MediaStoreExporter.kt#L85-L100) 老版本 ZIP 直接写公共下载目录；图片/视频老版本仍走需权限的外部 MediaStore。
- 触发步骤：API 28 安装后拒绝 WRITE_EXTERNAL_STORAGE，随后点击大视频/整本下载。下载仍执行，导出时才失败；无用户可见的权限补救入口。API 29+ 不应增加此不必要权限要求。
- 调用链：拒绝权限→vm.download→DownloadManager.runTask→下载→MediaStoreExporter→公共写入失败→FAILED。
- 最小修复：API≤28 入队前校验并按用户操作请求；拒绝时明确说明且不启动网络下载，永久拒绝提供系统设置入口；批准后继续原操作。
- 回归：API 28 首次拒绝/永久拒绝/批准后重试、API29+无需旧存储权限；通知拒绝与存储拒绝分开测试，不能错误阻止 API33+ 前台服务任务（通知权限不是前台服务启动许可）。

### UI-10 · P3 · 日志缓冲达到 400 行后，自动跟随停止

- 证据：[AppLog.kt:19/52–55](../../android/app/src/main/java/com/xd/vdl/core/AppLog.kt#L19-L55) 满 400 行后移除首条再追加，lines.size 恒 400；[TasksScreen.kt:148–152](../../android/app/src/main/java/com/xd/vdl/ui/TasksScreen.kt#L148-L152) LaunchedEffect 仅以 lines.size 为 key，因此后续新日志不再触发滚动。
- 触发步骤：日志面板在底部，持续产生日志直到超过 400 行；后续内容更新但跟随逻辑不再执行。若布局正好维持尾项可见，表象可被弱化，代码中的跟随 effect 仍确定失效。
- 最小修复：使用日志序号/最后一条版本作为 key，并独立保存“更新前是否在底部”；不要无条件随新日志拉回底部。
- 回归：399→400→401→450，底部持续跟随；向上查看历史时不被拉回。

## 完整覆盖清单

| 完整阅读/允许的例外 | 行数/范围 | 核查重点与结论 |
|---|---:|---|
| [MainActivity.kt](../../android/app/src/main/java/com/xd/vdl/MainActivity.kt) | 全 262 行 | 启动初始化、剪贴板监听 start/stop 对称；聚焦与空剪贴板防护；分享优先标记；Intent/恢复问题 UI-03；权限 UI-09；UI IO UI-07 |
| [AppViewModel.kt](../../android/app/src/main/java/com/xd/vdl/ui/AppViewModel.kt) | 全 156 行 | Activity 协程引用与取消 UI-04；输入竞态 UI-02；下载清晰度下标钳制；所选图片映射；未做 SavedStateHandle 持久化 |
| [HomeScreen.kt](../../android/app/src/main/java/com/xd/vdl/ui/HomeScreen.kt) | 全 335 行 | 输入/粘贴/清空/解析/结果清晰度/预览/下载；清空在 parsing 中禁用是已有保护，但输入未保护；封面 UI-01 |
| [TasksScreen.kt](../../android/app/src/main/java/com/xd/vdl/ui/TasksScreen.kt) | 全 342 行 | 列表稳定 id、取消按钮、DONE 预览、清空结束任务、日志复制/清空/跟随 UI-10；MERGING/EXPORTING 不显示取消，需产品明确是否允许取消 |
| [SettingsScreen.kt](../../android/app/src/main/java/com/xd/vdl/ui/SettingsScreen.kt) | 全 849 行 | Cookie 必填/预填/额外项保留；网页登录调用关系；JM 登录密码默认遮罩、不保存密码说明；登录/测试 IO 分发与 busy；目录失焦/IME/保存及过滤；代理保存立即失效缓存；见下方边界 |
| [PreviewDialog.kt](../../android/app/src/main/java/com/xd/vdl/ui/PreviewDialog.kt) | 全 625 行 | 类型检测 IO；本地 decodeSampled 2560 上限；VideoView onRelease.stopPlayback；手势/翻页；ZIP 页数500/总展开240MB/循环 ensureActive、非原始文件名落盘；外部打开错页 UI-05 |
| [BeforeDownloadDialog.kt](../../android/app/src/main/java/com/xd/vdl/ui/BeforeDownloadDialog.kt) | 全 248 行 | 全章节目录、网格/放大图、按原章节id与页码下载；视频预览头；1800采样；请求取消/响应体预算 UI-01/UI-06；关闭 VideoView 有 stopPlayback |
| [RemoteImage.kt](../../android/app/src/main/java/com/xd/vdl/ui/RemoteImage.kt) | 全 64 行 | IO分发已做；无采样/无响应上限/无可取消HTTP UI-01/UI-06 |
| [GkUi.kt](../../android/app/src/main/java/com/xd/vdl/ui/component/GkUi.kt) | 全 177 行 | 无网络/磁盘/跨生命周期资源；尺寸、卡片和徽标组合，未发现本范围确定缺陷 |
| [Theme.kt](../../android/app/src/main/java/com/xd/vdl/ui/theme/Theme.kt) | 全 66 行 | API31 动态颜色门禁、低版本固定配色；未发现本范围确定缺陷 |
| [BiliWebLoginDialog.kt](../../android/app/src/main/java/com/xd/vdl/ui/BiliWebLoginDialog.kt) | 调用关系；只读1–85行确认接口 | 经委托允许由他人专项审计，不声称已完整审此文件。设置页 onDone刷新登录态；onClose关闭；内部安全/凭据持久化结论交专项负责人 |
| [AppLog.kt](../../android/app/src/main/java/com/xd/vdl/core/AppLog.kt) | 全 71 行 | 并发formatter UI-08；同步文件IO UI-07；400行缓冲；512KB轮转；clear仅清内存不清历史文件，UI无明确“清磁盘日志”承诺，不单独定为缺陷 |
| [ZoomStateTest.kt](../../android/app/src/test/java/com/xd/vdl/ui/ZoomStateTest.kt) | 全 83 行 | 6个数值测试：范围、未放大平移、放大钳制、双击、reset、缩回1倍；不覆盖真实手势仲裁/Pager/生命周期 |
| [ShareLinkRoutingTest.kt](../../android/app/src/test/java/com/xd/vdl/core/ShareLinkRoutingTest.kt) | 全 34 行 | 小红书短链、token保留、不相关host过滤；不覆盖Activity分享消费 |
| [DouyinNoteRoutingTest.kt](../../android/app/src/test/java/com/xd/vdl/core/DouyinNoteRoutingTest.kt) | 全 29 行 | 图文/视频路由优先级；不是UI或恢复测试 |

### 关联调用链补读与边界

- 全读 [AndroidManifest.xml](../../android/app/src/main/AndroidManifest.xml)、[DownloadManager.kt](../../android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt)、[DownloadTask.kt](../../android/app/src/main/java/com/xd/vdl/core/DownloadTask.kt)、[SaveSettings.kt](../../android/app/src/main/java/com/xd/vdl/core/SaveSettings.kt)、[ProxySettings.kt](../../android/app/src/main/java/com/xd/vdl/core/net/ProxySettings.kt)、[MediaStoreExporter.kt](../../android/app/src/main/java/com/xd/vdl/core/download/MediaStoreExporter.kt)、[Parser.kt](../../android/app/src/main/java/com/xd/vdl/core/parse/Parser.kt)、[MediaKind.kt](../../android/app/src/main/java/com/xd/vdl/core/MediaKind.kt)，用于核实UI入口后的行为；不代替网络/下载专项审计。
- 按需读 [JmClient.kt:330–444](../../android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L330-L444)、[495–544](../../android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L495-L544)、[Http.kt:95–194](../../android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L95-L194)、[WebViewBridge.kt:42–161](../../android/app/src/main/java/com/xd/vdl/core/parse/WebViewBridge.kt#L42-L161)，以及 X/抖音解析入口，核实线程、取消与上下文。JM login/albumDownloadRaw 已将常见网络异常转结果，未把UI缺少局部catch直接误报为“断网必崩”。诊断 cookieDebug 只输出键名/长度，不输出值。
- 页面状态多数使用 remember；切换页面会移除其 Composition，选图/预览/未保存代理输入可丢失，进程重启也无VM SavedStateHandle恢复。已保存 Cookie/目录/代理有 Preferences 持久化；下载任务 StateFlow 只在内存，进程恢复由下载专项评审，不声称支持断点恢复。
- 快速点击：parse 在设置 parsing 后 launch，能防同一时刻重复解析；JM 登录/诊断有 busy 禁用按钮，但请求中仍可编辑文本；主下载按钮无去重，每次 enqueue 产生独立随机 id 与独立下载。是否允许重复下载属产品语义，应加双击回归，不把合法重复操作一律视作缺陷。任务取消委托 DownloadManager.cancel，媒体导出/合并可取消性由下载专项确认。
- 设置格式：目录名经过 sanitize，拒绝空串/纯点目录、清除分隔符/控制字符、限制40字符；代理 invalid 仅在保存后的 epoch 更新，不实时标错，非法值保存后明确提示按直连；含用户名密码的代理原值会直接显示在当前地址标签，属于可改进的凭据遮罩风险，未作为高优先级漏洞。保存目录的失焦提交已有实现，不重复报告旧缺陷。
- 预览 ZIP 使用序号文件名而非 entry.name，避免 Zip Slip，且限制页数/展开字节。非图片 entry 的 closeEntry 仍可能耗时处理大流，建议专项恶意归档测试；不声称总240MB限制约束了所有非图片解压工作。缓存删除异步作用域与解压任务存在并行清理窗口，未无证据宣称一定残留。
- 外部打开使用 ACTION_VIEW+FLAG_GRANT_READ_URI_PERMISSION，错误有 Toast；其 getType 在点击主线程执行，仍有一次 provider IPC，可并入 UI IO 改进。应用未提供独立 ACTION_SEND 媒体分享按钮，不能把“分享导出已测试”作为结论。
- 测试发现：现有 UI JVM 测试仅 ZoomStateTest；无 androidTest Kotlin 文件。未找到针对 AppViewModel、Activity恢复/权限、AppLog并发/IO、远程图片取消/内存、外部打开当前页的测试。现有测试代码阅读不代表测试已通过。

## 建议回归顺序

1. 先用 fake parser/MockWebServer 固定复现 UI-01/02/06，避免外部网站波动污染结论。
2. 以 ActivityScenario 和 Compose UI 测试覆盖分享重建、当前页外部打开、输入与解析竞争；用 Intent 截获而非肉眼判断目标URI。
3. 注入日志 Clock/FileSink 做并发与慢存储测试，覆盖400行后跟随。
4. 再做 API28/29/33 权限与导出设备矩阵、巨大图片/损坏 ZIP/播放器离开页面释放测试；实测前明确标注为待验证，不以代码阅读冒充真机验证。
