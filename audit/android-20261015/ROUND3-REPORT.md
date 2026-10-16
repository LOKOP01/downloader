# Android 审计第三轮报告（Cookie / 代理 / 异步日志 / B-01～B-09 复核）

- 日期：2026-10-16
- 分支：`audit/android-full-20261015`（未推送、未合并、未发布）
- 依据：本轮所有结论均来自**实际源码**与前两轮报告原文，未依据摘要推测

## 0. 上下文核对

先重读 [REPORT.md](REPORT.md)、[ROUND2-REPORT.md](ROUND2-REPORT.md) 与四份 review-*.md，并逐项对照源码确认修复状态：

| 项 | 前两轮状态 | 本轮核对结果 |
|---|---|---|
| B-01 | 已修 | 已修；本轮补边界测试并**新发现 1 处语义缺口**（见 1.1） |
| B-02 | 已修 | 已修，复核无新问题 |
| B-03 | 已修 | **修复不完整**：代理变更后 JmClient 不重建（见 1.2） |
| B-04 | 已修 | 已修；本轮补自动化测试 |
| B-05 | 已修 | 已修，复核无新问题 |
| B-06 | 已修 | 已修，空列表边界已正确处理 |
| B-07 | 已修 | **修复不完整**：flush() 语义错误且从未被调用（见 1.3） |
| B-08 | 已修 | 已修，复核无新问题 |
| B-09 | 部分修复 | 仍为部分修复（下载主链路已接入，其余 14 处未改） |

## 1. 本轮修复

### 1.1 Cookie 语义边界（B-01 深化）

**新发现的缺口**：原实现对「路径前缀」的判断依赖 OkHttp 的 `Cookie.matches`，语义本身正确；但缺少对**易错边界**的覆盖。本轮补齐 10 项边界测试，全部通过：

| 场景 | 断言 |
|---|---|
| 路径前缀 | `/account` 匹配 `/account` 与 `/account/profile`，但**不匹配** `/accountx` |
| 根路径 | `/` 匹配所有路径 |
| 尾斜杠 | `/api/` 匹配 `/api/v1`，不匹配 `/api` |
| 协议切换 | Secure Cookie 只在 HTTPS 发送；普通 Cookie 两种协议都发 |
| 端口 | 同 host 不同端口共享 Cookie（RFC 6265） |
| 登录态往返 | 服务端下发→连续 5 次请求都能带上；续期后只保留一条且值更新 |
| 同 host 双 Cookie | hostOnly 与 Domain Cookie 同名共存，子域只看到 Domain 那条 |
| 跨域隔离 | `www.douyin.com` 与 `www.iesdouyin.com` 的 hostOnly Cookie 互不串用 |

**关键结论**：本轮**没有修改 Cookie 匹配逻辑**，只补测试。现有实现对 `/accountx` 这类「字符串前缀相同但路径前缀不同」的边界处理是正确的。

### 1.2 B-03：代理变更不传播给 JmClient（真实缺陷）

**根因**：第二轮引入 `ProxySettings.wrapInitialized(baseClient)` 时，JmClient 的 client 仍是

```kotlin
private val client: OkHttpClient by lazy { ... wrapInitialized(built) }
```

`by lazy` 只求值一次。**用户首次使用禁漫后，再改代理设置不会生效**（只有重启 App 才会重建），而设置页保存时调用的是 `Http.onProxyChanged`，它只清 `Http` 自己的缓存，JmClient 拿不到通知。

**修复**：
- `ProxySettings` 新增 `revision`（每次 `invalidate()` 递增）；
- JmClient 拆成 `baseClient`（TLS 配置，`by lazy` 不变）+ 按 `revision` 比较的 `client` getter，配置一变就重新包装。

TLS 配置未被触碰，仍然是「证书链放宽 + 主机名限禁漫白名单」。

### 1.3 B-07：异步日志的 flush 语义（真实缺陷）

第二轮把磁盘写入移到后台线程，但 **flush 的实现是错的**：

```kotlin
while (queue.isNotEmpty() && ...) Thread.sleep(5)   // 原实现
```

写线程 `poll` 之后**队列就空了**，但文件可能还没写完（甚至还没开始写）。于是 flush 立即返回，调用方以为已落盘，随后进程被杀，最后几条就丢了。而且 `flush()` **从未被任何地方调用**。

**修复**（抽取为可独立测试的 [LogSink](<../../android/app/src/main/java/com/xd/vdl/core/LogSink.kt>)）：

| 缺陷 | 修复 |
|---|---|
| 用「队列空」判断写入完成 | 改用 `pending` 计数（入队先加、写完再减），flush 等它归零 |
| 写失败会让 pending 永不清零，flush 只能等超时 | `finally` 保证递减 |
| pending 可能变负（先入队后加计数） | 顺序改为**先加计数再入队**，失败时回滚 |
| 队列满静默丢弃 | 累加 `droppedLines()` 计数 |
| close 先 interrupt 会丢日志 | 改为**先 flush 再 interrupt** |
| 无关闭语义，可重复提交 | `closed` 标志，关闭后 submit 返回 false |
| 未接入生命周期 | `MainActivity.onStop` 接入，**后台线程 + 无积压时零开销** |

**进程强杀风险**：这是固有风险，只能降低概率不能消除 —— 系统直接 KILL 不会走任何回调。已用 `onStop` 尽力落盘，并未改回同步写（那会让主线程做 IO）。这一点在报告与代码注释中都明确标注为「不是保证」。

### 1.4 B-04 自动化验证

新增 [BackupRulesTest](<../../android/app/src/test/java/com/xd/vdl/core/BackupRulesTest.kt>)，验证规则文件本身：两份规则都排除 3 份凭据 prefs + WebView 目录、普通配置（`vdl_save`/`vdl_jm`）**未**被误伤、Manifest 正确引用两份规则。

## 2. 修改文件清单

| 文件 | 类型 | 原因 |
|---|---|---|
| `core/LogSink.kt` | **新增** | 抽出可测试的日志落盘器，承载 flush 语义修复 |
| `core/AppLog.kt` | 重构 | 改为委托 LogSink；新增 `hasPending()`/`close()` |
| `MainActivity.kt` | 修改 | onStop 接入 flush（后台线程，无积压零开销） |
| `core/net/ProxySettings.kt` | 修改 | 新增 `revision` 供外部缓存失效判断 |
| `core/parse/JmClient.kt` | 修改 | client 按 revision 重建，修复代理变更不传播 |
| `test/core/LogSinkTest.kt` | **新增** | 10 项：排空/异常写入/并发/超时/关闭/轮转 |
| `test/core/net/SessionCookieJarBoundaryTest.kt` | **新增** | 10 项 Cookie 边界 |
| `test/core/BackupRulesTest.kt` | **新增** | 4 项备份规则 |
| `test/core/Round2FixRegressionTest.kt` | 补充 | +1 项代理 revision |

**未变更**：依赖版本、权限声明、数据库结构、网络协议、持久化格式、minSdk/targetSdk、TLS 配置、Cookie 匹配逻辑。

## 3. 自动化执行结果（实际命令与输出）

| 命令 | 结果 |
|---|---|
| `gradlew clean assembleDebug assembleRelease test lint` | **BUILD SUCCESSFUL in 1m 29s** |
| 单元测试 | **108 项，0 失败**（上轮 83 → 本轮 +25） |
| Lint | **0 error，2 warning** |

测试分布（新增部分加粗）：LogSinkTest **10**、SessionCookieJarBoundaryTest **10**、BackupRulesTest **4**、Round2FixRegressionTest 4(+1)、SessionCookieJarTest 10、CancellableCallTest 4、AuditFixRegressionTest 6、其余既有 60。

## 4. 按状态分类

### 4.1 已修复并通过自动化测试

| 项 | 验证 |
|---|---|
| B-07 flush 语义（pending 计数、写失败不挂住、关闭顺序、队列满计数） | LogSinkTest 10 项 |
| B-01 Cookie 路径/协议/端口/登录态边界 | SessionCookieJarBoundaryTest 10 项 + SessionCookieJarTest 10 项 |
| B-04 备份排除规则 | BackupRulesTest 4 项 |
| B-03 代理变更传播（revision 推进） | Round2FixRegressionTest 代理 2 项 |

### 4.2 已修复但缺乏充分测试覆盖

| 项 | 说明 |
|---|---|
| B-03 JmClient 重建 client | 只测到「revision 会推进」，**未**测「JmClient 真的因此重建了 client」（需要注入可观察的 OkHttpClient，改动面大于收益） |
| B-07 flush 接入 onStop | 逻辑经编译与 LogSink 单测间接覆盖；Activity 生命周期时序本身未测 |
| B-05 解析竞态 | 编译通过且逻辑复核无问题，但没有可观测的协程调度测试 |
| B-06 预览当前页 | 编译通过，Compose 状态流转未测 |

### 4.3 尚未修复

| 项 | 原因 |
|---|---|
| B-09 其余 14 处 `execute()` | 逐个改造需回归各自调用链；下载主链路与封面已接入 `await()` |
| N04 WebView Cookie 导入丢失 Secure/HttpOnly | 需区分「JS 可读」与「仅 HTTP」的 Cookie，属独立设计 |
| N05 凭据明文存 SharedPreferences | 改用 Keystore 属架构级改动 |
| N06 clear 不清 HTTP 会话罐 / 清单平台清掉全部 WebView Cookie | 需重新设计跨平台清理语义 |
| N09 WebView JS 接口无来源校验 | 需改用 `addWebMessageListener`，改动面大 |

### 4.4 受 Android 系统环境限制，无法通过 JVM 验证

以下为**代码路径分析结论**，不是实测结论：

| 项 | 代码路径 | 风险分析 |
|---|---|---|
| WebView 全局代理反射 | `WebViewProxy.applyInner` → `ConnectivityManager.setGlobalProxy` | 已按 `ProxyInfo`→`ProxyProperties` 顺序探测并处理「都找不到」；实际是否设置成功取决于 ROM 是否暴露该隐藏 API，JVM 无法调用 |
| 备份服务是否执行排除规则 | 规则文件 + Manifest 声明 | 规则内容已单测覆盖；系统备份/迁移服务是否采纳属框架行为 |
| onStop 时机与进程回收 | `MainActivity.onStop` | 若进程不经过 onStop 就被回收，队列中日志仍会丢（已注释说明） |
| SharedPreferences 实际文件名 | `getSharedPreferences("vdl_cookies")` → `vdl_cookies.xml` | 排除路径按 Android 约定书写，实际落盘由框架决定 |
| POST_NOTIFICATIONS 拒绝后的通知行为 | `DownloadService.setText` 显式检查权限 | 权限检查逻辑正确，运行时行为依赖系统 |

## 5. 剩余风险

1. **Cookie 匹配收紧的兼容性**：若某站点原本依赖「侥幸带上」的 Cookie，收紧后会拿不到。20 项语义测试已覆盖预期行为，但真实站点的 Cookie 组合无法在 JVM 复现。
2. **日志异步化的丢失窗口**：进程被强杀时队列中未落盘的日志会丢。已通过 onStop + flush 降低概率，未改为同步写。
3. **代理缓存弱引用**：`WeakHashMap` 的键是 base client，若 base 被回收，对应缓存条目自动消失（下次重建），行为正确但会多一次包装。
4. **Lint 剩余 2 条 warning**：`CustomX509TrustManager`（禁漫自签名证书的功能必需放宽，已用主机名白名单收窄范围）与 `IconLauncherShape`（圆角图标美术规范）。二者均不影响正确性。
