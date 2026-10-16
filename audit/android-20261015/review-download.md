# Android 下载子系统只读审计

审计日期：2026-10-15（Asia/Shanghai）。工作区由 Get-Location 确认为 F:/18752/Documents/douyin download。分支 audit/android-full-20261015 是委托方提供的上下文；遵守禁止 git 操作的约束，未独立验证分支或未提交修改。除本报告外没有写文件，没有构建、执行测试、真实第三方下载、权限或数据变更。以下结论来自当前工作树，不等同于某个提交的结论。

## 方法和结论边界

- E1：完整读取源码后可直接确认控制流、缺少检查或确定交错；不是已执行复现。
- E2：代码缺陷/资源边界可确认，但实际损害依赖 Android 版本、Provider 行为、媒体样本、时序或堆大小，需隔离测试确认。
- P0：灾难性/广泛不可恢复破坏；P1：数据覆盖、进程崩溃或核心后台保障失效；P2：可触发功能/完整性/取消错误；P3：防御与可维护性问题。本次无足够证据定为 P0；不表示不存在 P0。
- 结论：**3 项 P1、12 项 P2、1 项 P3**。优先处理服务生命周期原子性、低版本覆盖写、JM 图片内存限制，再处理取消与导出事务。

## 调用链

[AppViewModel.kt:124–154](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/ui/AppViewModel.kt#L124-L154>) → [DownloadManager.kt:38–68](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L38-L68>)：添加内存任务、启动前台服务、创建 IO Job。[DownloadManager.kt:92–117](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L92-L117>) 按视频/图集/JM 分流并发布最终状态，finally 删除工作目录。

- 视频：[DownloadManager.kt:120–182](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L120-L182>) → [Downloader.kt:25–98](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/Downloader.kt#L25-L98>) → 可选 [Muxer.kt:17–76](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/Muxer.kt#L17-L76>) → [MediaStoreExporter.kt:31–48](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/MediaStoreExporter.kt#L31-L48>)。
- 普通图集：[DownloadManager.kt:248–270](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L248-L270>) → 下载 → 逐张导出。
- JM：[DownloadManager.kt:185–245](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L185-L245>) → [JmDownloader.kt:135–260](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt#L135-L260>)；官方 ZIP 优先，否则章节计划、并发抓图/重排、顺序 ZIP → 导出。选择页逐张导出。
- 服务：[DownloadService.kt:20–65](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadService.kt#L20-L65>)，类型 dataSync，声明 [AndroidManifest.xml:40–43](<F:/18752/Documents/douyin download/android/app/src/main/AndroidManifest.xml#L40-L43>)。目标版本 34、最低版本 24，见 [build.gradle.kts:9–14](<F:/18752/Documents/douyin download/android/app/build.gradle.kts#L9-L14>)；不把 target 35 的 dataSync 时限当作当前确定问题。

## 发现

### D01 · P1 · 新任务注册和最后任务停止服务仍有竞态（E1，系统影响 E2）

**证据：** [DownloadManager.kt:49–65](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L49-L65>) 先 start 服务，后登记 jobs；完成回调 remove / isEmpty / stop 非原子。LAZY 只消除了“同一任务先运行后登记”，没有保护整个生命周期。

**触发：** A 为最后一个旧任务；B enqueue 执行 start 后、登记前，A 完成看到 jobs 为空而 stop，B 接着登记和 start Job，但不再启动服务。另一交错是 A 的 isEmpty 得到 true 后，B start/登记，再 A stop。后台下载仍运行却失去前台保护，系统回收后任务丢失。

**最小修复：** 串行化登记、移除和服务启停决策；更稳妥让服务拥有任务生命周期，用 generation/startId 防止过期 stop。不能仅把 start 移到 jobs 赋值后，因为检查与停止之间仍能穿插。

**测试：** 用可控服务门面和 barrier 固定上述两种交错；断言任何非空 jobs 都不会被上一代 stop 撤去保护。

### D02 · P1 · JM 96MB 预算并不约束实际峰值，首批高清页可 OOM（E1/E2）

**证据：** [JmDownloader.kt:88–92](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt#L88-L92>) 最少两路；[JmDownloader.kt:203–241](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt#L203-L241>) 初始固定 8 路，32 页块完成后才重估；[JmDownloader.kt:398–418](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt#L398-L418>) 整体抓 bytes、解全尺寸图并建第二张图；[JmScramble.kt:67–74](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/parse/JmScramble.kt#L67-L74>) 确认创建同尺寸 ARGB 副本。[JmClient.kt:537–546](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L537-L546>) 无响应大小限制。[DownloadManager.kt:59–67](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L59-L67>) 每次入队立即并行且无全局 gate，多本预算再相乘。

**触发：** 多张 1600×2400 页同时解码时仅双位图 8×约29.3MiB≈234MiB，尚未计响应、编码、UI；更大页即使收窄为 2 路也可能超 96MiB。OOM 为 Error，不能依赖 runTask 的 catch(Exception) 隔离。

**最小修复：** 先读尺寸且设置像素/响应上限；解码前获取全局按字节计的许可，允许 1 路甚至拒绝过大页；不要等解码完成后才估算；限制跨任务资源。位图及压缩流用 finally 回收，检查 compress 返回值。

**测试：** 本地生成高清图片、低堆设备和同时两本；测首批/尺寸突增页峰值、异常/取消回收。现有 [JmConcurrencyTest.kt:20–53](<F:/18752/Documents/douyin download/android/app/src/test/java/com/xd/vdl/core/download/JmConcurrencyTest.kt#L20-L53>) 只验证计算函数，且把最少两路当成正确行为，不能证明内存受控。

### D03 · P1 · Android 7–9 同名 ZIP 覆盖已有公共文件，失败还可能毁坏旧文件（E1）

**证据：** [DownloadManager.kt:196–203](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L196-L203>) 固定以 jmId 命名；[MediaStoreExporter.kt:85–100](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/MediaStoreExporter.kt#L85-L100>) 对公共 Download 目录执行 copyTo(overwrite=true)，无冲突策略/临时原子落盘。重复下载相同本子会命中同一个目标。

**触发：** API24–28 下载已有同 ID 本子；或目标已有同名用户文件。覆盖已发生后遇 ENOSPC/进程终止，可留下截断目标，原文件不可恢复；并发下载同 ID 可互相截断。

**最小修复：** 默认创建唯一名称；如明确支持替换，先同目录临时文件写完校验，再原子替换，并向用户确认。失败清理临时文件而不删除旧成品。

**测试：** 预置目标内容，重复/并发导出、注入中途写失败，断言旧文件不变或唯一新文件完整。

### D04 · P2 · Job 开始执行前取消可使任务永久 PENDING（E1）

**证据：** [DownloadManager.kt:59–73](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L59-L73>) 完成回调只移除 Job/停服务；CANCELED 仅在 [DownloadManager.kt:108–111](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L108-L111>) 的协程体 catch 设置。任务发布后、IO 调度执行前被 cancel，协程体不运行，就没有最终状态。[DownloadTask.kt:31–33](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadTask.kt#L31-L33>) 使它不能按 finished 清除。

**最小修复：** 完成回调按取消 cause 对仍未终结任务补齐 CANCELED，状态转移保持原子且不能覆盖已提交 DONE；必要时同步登记取消入口。

**测试：** 暂停的测试 dispatcher，enqueue → cancel → 执行队列，断言任务终结、jobs 清空、可 clearFinished。

### D05 · P2 · 取消不能中止阻塞请求；合并/导出期间取消仍可能保存并显示 DONE（E1）

**证据：** [Downloader.kt:60–75](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/Downloader.kt#L60-L75>) 同步 execute/read，没有将取消绑定 Call.cancel，仅循环前 ensureActive；[Http.kt:132–140](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L132-L140>) 读取超时30秒。[Muxer.kt:64–76](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/Muxer.kt#L64-L76>) 全部为同步循环，完全不查取消；[MediaStoreExporter.kt:140–153](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/MediaStoreExporter.kt#L140-L153>) 同步 copy。[DownloadManager.kt:159–182](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L159-L182>) 合并后到导出无挂起/取消检查，随后 [DownloadManager.kt:105–106](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L105-L106>) 无条件 DONE。withContext(IO) 不会自动使这些同步调用可取消。

**触发：** 网络无响应时 cancel 延迟至超时/下一次读；下载两轨后、merge/copy 内 cancel，则仍能对公共存储产生副作用并把状态写 DONE。JM 网络同样同步，参见 [JmClient.kt:475–488](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L475-L488>) 和 [JmClient.kt:537–546](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L537-L546>)。

**最小修复：** 可取消 OkHttp bridge；每段合并/复制循环检查 Job，导出前/提交前检查取消；定义提交点：提交前取消回滚，提交后取消不把完整成品误报取消。不能仅在 copy 之后 ensureActive，否则成品已发布。

**测试：** 本地 socket 阻塞头/体、人工慢 mux/copy，各阶段取消；验证延迟、无继续请求、无提交前公开条目、最终状态和文件一致。

### D06 · P2 · MediaStore pending 发布失败不回滚，也不检查 update 行数（E1）

**证据：** [MediaStoreExporter.kt:140–153](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/MediaStoreExporter.kt#L140-L153>) 仅复制被 runCatching 保护；清 IS_PENDING 的 update 在保护外且返回值被忽略。[DownloadManager.kt:180–182](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L180-L182>) 只看是否返回非 null。

**触发：** API29+ copy 成功但 update 抛异常：任务失败、pending 行保留且工作文件被 finally 删除；update 返回0：仍返回 uri，可能 DONE 但不可见/条目已失效。insert 抛错虽可由 manager 标 FAILED，但这里没有统一事务语义。

**最小修复：** insert/copy/发布统一 try/finally，成功标记只在 update 行数正确后置位；失败删除本次行，保留错误原因与必要的可重试来源。考虑进程中断后清理本应用 pending 行。

**测试：** 假 ContentResolver 分别模拟 open=null、写失败、update=0、update 抛错、delete 抛错；断言不虚报成功且可观察到清理失败。

### D07 · P2 · START_STICKY 重建的是空服务，不恢复下载（E1）

**证据：** [DownloadService.kt:29–33](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadService.kt#L29-L33>) 无条件返回 START_STICKY、无论 intent 是否为空都显示“准备下载”；[DownloadManager.kt:31–35](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L31-L35>) 队列/Job 仅在进程内。服务启动不检查 active jobs，也没有恢复记录。

**触发：** 系统回收后重建服务，得到常驻空前台通知，原下载/状态均不恢复；未恢复的 app-specific 工作目录或 JM cache 临时包也没有启动清扫路径（本审计读取范围内）。

**最小修复：** 不实现恢复时使用 START_NOT_STICKY，空队列立即 stopSelf；若要恢复，持久化任务和工作文件所有权并明确恢复/清理协议。

**测试：** 服务 null intent 冷启动、模拟进程重建；验证无空服务常驻、任务状态可解释、孤儿临时文件受控。

### D08 · P2 · 前台启动/提权失败被静默吞掉，任务仍被接受（E1/E2）

**证据：** [DownloadService.kt:29–33](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadService.kt#L29-L33>) 与 [DownloadService.kt:52–56](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadService.kt#L52-L56>) runCatching 丢弃结果；[DownloadManager.kt:49–67](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L49-L67>) 不判断服务是否已成功前台化，立即启动工作。

**触发：** 系统因启动场景限制抛异常，或 startForeground 失败；界面仍报已入队，下载却没有保活，部分系统会因未及时前台化终止进程。清单已有 FOREGROUND_SERVICE / DATA_SYNC 和 service type，不能把它误报为“缺少声明”。拒绝 POST_NOTIFICATIONS 本身不等于不能启动 FGS。

**最小修复：** 返回/传递具体启动失败并让任务明确失败或等待合法前台入口；前台服务内部出错不得继续假装运行，记录原因并安全停机。

**测试：** 注入启动异常、startForeground 异常、通知权限拒绝；区分通知可见性与 FGS 真实存活。

### D09 · P2 · JM 空章节被跳过，部分本子可被当整本成功（E1）

**证据：** [JmParser.kt:157–166](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/parse/JmParser.kt#L157-L166>) 缺失 images 返回空列表；[JmDownloader.kt:165–190](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt#L165-L190>) 对每个空 files 直接 continue，只有所有 jobs 均空才报错；[JmDownloader.kt:245–255](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt#L245-L255>) entryCount>0 即交付成功。

**触发：** 多章本子某章 API 返回缺字段/空列表，其余章正常。用户得到少章 ZIP，total 已基于被过滤后的 jobs 计算，仍显示100%与 DONE。

**最小修复：** 默认所有预期章节都必须有有效计划，否则明确失败；若允许合法空章/部分下载，按明确策略记录章节清单和缺失原因，不可静默跳过。

**测试：** 两章计划一章空、一章正常；缺 images 字段/合法空章节分别测试，不允许被误标整本完整。

### D10 · P2 · 通用下载只验证传输长度，不验证媒体，错误 HTML 可导出为 mp4/jpg（E1）

**证据：** [Downloader.kt:62–88](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/Downloader.kt#L62-L88>) 只要求2xx、字节数匹配、非空；[DownloadManager.kt:178–182](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L178-L182>) 只检查非空就以 video/mp4 导出；[DownloadManager.kt:254–264](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L254-L264>) 按 URL 是否包含 webp 决定扩展名/MIME，不读取真实格式。

**触发：** CDN/风控200错误页、有长度的 JSON，或 URL 无 webp 但返回 WebP/PNG/AVIF。候选首个“错误成功”会阻止切换备用地址；图库出现坏文件/类型不符。

**最小修复：** MIME/header 作为初筛，下载后按实际容器/图片头与可解码性校验；失败进入候选回退；扩展名和 MIME 来自检测结果而非 URL 子串。

**测试：** 本地服务200 HTML/JSON、PNG URL伪jpg、未知长度正确媒体、截断/空体。[DownloaderIntegrityTest.kt:14–39](<F:/18752/Documents/douyin download/android/app/src/test/java/com/xd/vdl/core/download/DownloaderIntegrityTest.kt#L14-L39>) 仅覆盖已知长度短响应，不能证明语义完整性。

### D11 · P2 · JM 原图和官方 ZIP 的“完整性校验”过弱（E1）

**证据：** [JmDownloader.kt:402–406](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt#L402-L406>) num<=0 时原 bytes 未验证即包装成 .jpg；[JmDownloader.kt:340–347](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt#L340-L347>) 官方包只数中央目录里扩展名像图片的条目，既不读条目也不验 CRC/大小/图片；[JmDownloader.kt:315–328](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt#L315-L328>) count>0 就交付。

**触发：** 无需重排页返回200错误内容/空体，或 ZIP 中有空/损坏图片、名字伪装的文本、损坏压缩数据但中央目录仍能读取。[JmOfficialZipCheckTest.kt:22–40](<F:/18752/Documents/douyin download/android/app/src/test/java/com/xd/vdl/core/download/JmOfficialZipCheckTest.kt#L22-L40>) 用4个零字节伪图片就期望通过，证明测试只测目录计数。

**最小修复：** 原图路径至少检查非空和真实图片格式；ZIP 在受限解压预算下读完条目验证长度/CRC和媒体头，校验预期章节/页数（如可取得）；避免为了校验引入解压炸弹。

**测试：** 空 jpg、文本 jpg、坏 CRC/压缩数据、超大展开体积、已知正常包；不接入第三方服务。

### D12 · P2 · 必须合并的音频轨缺失时仍成功输出无声视频（E1）

**证据：** [DownloadManager.kt:124–161](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L124-L161>) needMerge 分支下载独立音频后调用 merge；[Muxer.kt:37–55](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/Muxer.kt#L37-L55>) 找不到 audioFormat 时 aTrack=-1，跳过音轨并正常 stop；videoFormat 则会明确失败。

**触发：** 音频文件是可打开但没有 audio/* 轨的合法容器，或选错候选。最终非空 mp4 可标 DONE，音频却丢失。

**最小修复：** 要求音频合并的接口必须检查找到音轨且写入有效音频样本；有意支持静音应显式传策略并告知用户。

**测试：** 本地视频轨正常、音频输入只含视频/空轨/无样本，断言错误而非静默无声成功。补充正常双轨和关键时间戳验证。

### D13 · P2 · 低版本拒绝存储权限后仍下载到最后才失败（E1）

**证据：** [AndroidManifest.xml:9–11](<F:/18752/Documents/douyin download/android/app/src/main/AndroidManifest.xml#L9-L11>) 正确声明 maxSdk28；[MainActivity.kt:163–172](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/MainActivity.kt#L163-L172>) 启动时请求，但无结果处理；[AppViewModel.kt:124–154](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/ui/AppViewModel.kt#L124-L154>) 下载入口不检查授权，直接 enqueue；[MediaStoreExporter.kt:85–93](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/MediaStoreExporter.kt#L85-L93>) 需要写公共目录。

**触发：** API24–28 拒绝/永久拒绝 WRITE_EXTERNAL_STORAGE；仍消耗完整下载流量，导出抛异常/返回null，最后失败，JM官方路径还可能把本地导出错误当作官方失败再次逐图下载（见327及333–336行）。

**最小修复：** 低版本入队前检查需要的公共写权限，以下载动作触发授权并处理结果/拒绝；导出前再次检查撤权。区分网络/验证错误与本地导出错误，不因后者回退重下。

**测试：** API28 授权/拒绝/永久拒绝/下载中撤权；拒绝时不发网络请求。API29+ 不应请求旧存储权限。

### D14 · P2 · API24–28 视频/图片的自定义目录没有被使用（E1，具体落点 E2）

**证据：** [MediaStoreExporter.kt:31–47](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/MediaStoreExporter.kt#L31-L47>) 和 [MediaStoreExporter.kt:54–69](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/MediaStoreExporter.kt#L54-L69>) 计算 dir，但只在Q以上放入 RELATIVE_PATH；低版本没有 DATA 或显式目录文件写入/扫描路径。[SaveSettings.kt:127–131](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/SaveSettings.kt#L127-L131>) 却总是预览 Movies/Pictures 下配置目录。

**触发：** API24–28 设置自定义相册目录后导出，Provider自行决定落点，无法保证设置所展示目录，且与 archive 的显式目录行为不一致。

**最小修复：** preQ 在经清洗的公共目录创建唯一文件、原子写入，再 MediaScanner/正确 MediaStore 注册；或者UI明确低版本不支持该设置。

**测试：** API24/28 各检查视频/图片实际 DATA 父目录与预览一致，拒权时无残留；API29/34 回归 RELATIVE_PATH。

### D15 · P2 · 图集部分提交后失败/取消，没有可用的部分结果入口（E1）

**证据：** [DownloadManager.kt:235–244](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L235-L244>)、[DownloadManager.kt:262–270](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L262-L270>) 每张独立导出并累计 URI；后续失败仅设置 FAILED/CANCELED，不回滚已发布条目。[DownloadTask.kt:35–41](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadTask.kt#L35-L41>) 只允许 DONE 的 canPreview，因此部分有效 URI 不满足预览条件，和“下完前也能预览”的注释不符。

**触发：** 多图第2张写失败/取消，第1张已经进公共存储，任务却整体失败且模型不允许预览，重试又新增重复图片。此项不是建议无条件删除用户已保存媒体，而是提交策略/状态缺失。

**最小修复：** 明确“部分完成”状态和可预览结果，重试仅缺失项；或按产品约定以全事务方式回滚本任务创建的条目。提交后仍应保留结果可发现性。

**测试：** 三图第二张失败/第三张取消，验证公共条目、URI、状态、预览和重试一致，无无意重复或误删。

### D16 · P3 · 导出器声称目录再次 sanitize，但实际只检查空值（E1）

**证据：** [MediaStoreExporter.kt:23–24](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/MediaStoreExporter.kt#L23-L24>) 注释宣称兜底；实际 [MediaStoreExporter.kt:123–124](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/MediaStoreExporter.kt#L123-L124>) 只 ifEmpty，[SaveSettings.kt:78–81](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/SaveSettings.kt#L78-L81>) 读取旧/异常配置不做 sanitize。正常 setter [SaveSettings.kt:84–87](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/SaveSettings.kt#L84-L87>) 已清洗，故不能宣称一般用户输入即可越权。

**触发：** 遗留、恢复或迁移后的异常偏好含斜杠/纯点/控制字符；Q Provider 可能拒绝或产生非预期多层路径，preQ archive 直接用作 File 的子路径。是否能越出授权目录需具体输入/系统实测，未当作已证实安全漏洞。

**最小修复：** 在 read 或导出边界调用 sanitize 并按媒体类型给默认值；preQ 再验证 canonical 目标仍在允许公共目录内。

**测试：** 直接提供异常 preference，覆盖斜杠、点目录、控制字符、过长值；全部落入单级安全目录或明确拒绝。

## 已有防护与未升级为问题的观察

- [Downloader.kt:66–95](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/Downloader.kt#L66-L95>) 先 .part 写入、Content-Length 校验、非空校验、取消后不重试其他候选、写完再重命名是正向防护。取消或失败的 part 在 DownloadManager 工作目录 finally 下清理；没有把独立调用 Downloader 的残留无条件认定为当前入口泄漏。
- [Pipeline.kt:26–41](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/Pipeline.kt#L26-L41>) 窗口内最多 width 个 deferred、按输入顺序 await/consume，结构化作用域传播失败。未发现 ZIP 并发写入竞态。
- [MediaStoreExporter.kt:141–147](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/MediaStoreExporter.kt#L141-L147>) 正常 copy 失败会尝试删除插入行，流 use 会关闭；问题在发布阶段和进程中断而不是“所有路径完全不清理”。
- [Notifications.kt:17–38](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/Notifications.kt#L17-L38>) 有API26通知渠道、不可变 PendingIntent、ongoing通知；清单有前台服务类型权限。拒绝通知权限不能直接等同下载不能运行。
- [Muxer.kt:57–60](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/Muxer.kt#L57-L60>) 正常/异常路径释放 muxer/extractor；固定1MiB样本缓冲在极大单帧媒体上存在适配风险（[Muxer.kt:64–74](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/Muxer.kt#L64-L74>)），但没有媒体样本和设备复现，作为测试边界保留，不增加确定缺陷计数。
- [FileName.kt:9–17](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/FileName.kt#L9-L17>) 文件名过滤常见非法字符但不覆盖所有控制字符/UTF-8字节长边界；在普通用户标题下未据此认定路径穿越。建议增加控制字符、emoji截断、最大UTF-8文件名字节数测试。
- 除任务受控的临时文件外，服务重建/进程终止时孤儿清扫、scrambleIds无限缓存和任务历史线性增长都应做容量策略；未做长时运行，不给出虚构的泄漏实测值。

## 测试覆盖与后续验证建议

本次**未执行任何测试**，不声称通过构建或真机验证。下面的建议都可使用本地假数据/假Provider/本地服务器，不需要真实第三方下载。

1. 先纯逻辑状态机：D01/D04 固定交错；取消、重复取消、clearFinished、终态不回退。
2. 可取消网络：阻塞头/体、已知/未知长度、短响应、候选重试以及200伪媒体；保留现有完整性测试并增加 .part 清理断言。
3. 导出故障注入：insert=null/抛错、open=null、写/close失败、update=0/抛错、delete失败、磁盘满、进程中断。
4. Android API24/28/29/33/34 小矩阵：存储授权/拒绝、通知拒绝、自定义目录、同名导出、后台服务生命周期，使用本地微型媒体。
5. 媒体：本地双轨/缺轨/空轨、大单帧、时间戳/时长；ZIP正常/坏CRC/空条目/伪图片/展开量上限。
6. 内存：首批高清页、32页边界后突然增大、同时多本、异常回收；不要仅验证 concurrencyFor 返回数字。
7. [PipelineOrderedTest.kt:58–69](<F:/18752/Documents/douyin download/android/app/src/test/java/com/xd/vdl/core/download/PipelineOrderedTest.kt#L58-L69>) 名称称验证串行顺序，实际只 assertEquals(5, started.size)，建议断言完整顺序和无重入；[PipelineOrderedTest.kt:100–124](<F:/18752/Documents/douyin download/android/app/src/test/java/com/xd/vdl/core/download/PipelineOrderedTest.kt#L100-L124>) 用墙钟和固定性能倍数，繁忙CI可能抖动，改用虚拟时间/受控同步。补充 transform/consume 异常与取消子任务测试。

## 完整阅读清单（逐个 read 覆盖全部行）

| 文件 | 已完整阅读行数 |
|---|---:|
| [DownloadManager.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt>) | 1–292 |
| [DownloadService.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadService.kt>) | 1–67 |
| [DownloadTask.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/DownloadTask.kt>) | 1–42 |
| [Notifications.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/Notifications.kt>) | 1–40 |
| [FileName.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/FileName.kt>) | 1–18 |
| [SaveSettings.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/SaveSettings.kt>) | 1–132 |
| [MediaKind.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/MediaKind.kt>) | 1–107 |
| [Downloader.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/Downloader.kt>) | 1–99 |
| [MediaStoreExporter.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/MediaStoreExporter.kt>) | 1–155 |
| [Muxer.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/Muxer.kt>) | 1–78 |
| [Pipeline.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/Pipeline.kt>) | 1–41 |
| [JmDownloadMode.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/JmDownloadMode.kt>) | 1–44 |
| [JmDownloader.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt>) | 1–436 |
| [AndroidManifest.xml](<F:/18752/Documents/douyin download/android/app/src/main/AndroidManifest.xml>) | 1–56 |
| [file_paths.xml](<F:/18752/Documents/douyin download/android/app/src/main/res/xml/file_paths.xml>) | 1–14 |
| [build.gradle.kts](<F:/18752/Documents/douyin download/android/app/build.gradle.kts>) | 1–73 |
| [DownloaderIntegrityTest.kt](<F:/18752/Documents/douyin download/android/app/src/test/java/com/xd/vdl/core/download/DownloaderIntegrityTest.kt>) | 1–46 |
| [PipelineOrderedTest.kt](<F:/18752/Documents/douyin download/android/app/src/test/java/com/xd/vdl/core/download/PipelineOrderedTest.kt>) | 1–126 |
| [JmOfficialZipCheckTest.kt](<F:/18752/Documents/douyin download/android/app/src/test/java/com/xd/vdl/core/download/JmOfficialZipCheckTest.kt>) | 1–65 |
| [JmConcurrencyTest.kt](<F:/18752/Documents/douyin download/android/app/src/test/java/com/xd/vdl/core/download/JmConcurrencyTest.kt>) | 1–69 |
| [JmAlbumZipTest.kt](<F:/18752/Documents/douyin download/android/app/src/test/java/com/xd/vdl/core/parse/JmAlbumZipTest.kt>) | 1–76 |
| [JmAlbumZip.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/parse/JmAlbumZip.kt>) | 1–60 |
| [JmScramble.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/parse/JmScramble.kt>) | 1–102 |
| [Http.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/net/Http.kt>) | 1–333 |
| [MainActivity.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/MainActivity.kt>) | 1–262 |
| [AppViewModel.kt](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/ui/AppViewModel.kt>) | 1–156 |

core/download 通过 glob 发现的6个文件均完整阅读；下载直接测试4个和官方包元数据测试完整阅读。未发现 androidTest 文件（glob结果为空）。

## 部分阅读与明确未读

- [JmClient.kt:440–550](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L440-L550>)：仅完整读取该区间，1–439未读；本文仅据已读 downloadTo/fetchTo/fetchImage 实现讨论传输与取消，不评价其完整认证、TLS、代理行为。
- [JmParser.kt:150–184](<F:/18752/Documents/douyin download/android/app/src/main/java/com/xd/vdl/core/parse/JmParser.kt#L150-L184>)：仅该区间，1–149和185–210未读；用于确认空 images 列表的来源。
- 其他 parser、net、UI资源及相关界面（含任务卡实际渲染、预览Activity/Composable）、平台/质量模型、AppLog等未完整阅读，不把它们视作已审计；D15只确定 canPreview 模型限制，不虚构UI按钮现状。
- 路由、登录、Cookie、注入、ZoomState 等其余测试仅由glob/grep发现或排除，未逐个读取，未声称覆盖其功能。
- 未读合并后的Manifest、第三方库实现、设备系统MediaProvider、编译输出；Provider/系统差异结论明确标记E2。未读其他既有审计报告，不借用其结果。

## 交付约束自检

唯一写入目标是本报告；未编辑Android源码、Windows文件或其他报告，未执行git命令、构建、测试、网络下载和权限/数据操作。报告中行号均来自本次read结果，后续代码如变化需重新定位。
