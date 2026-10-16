# Android 网络、登录与 WebView 只读审计

- 审计日期：2026-10-15（Asia/Shanghai）。工作区由 Get-Location 确认；用户指定分支 audit/android-full-20261015，遵守“不执行 git 操作”，因此不独立验证分支或工作树状态。
- 边界：不修改源码、权限、配置或用户数据；不执行构建/测试（构建与部分现有测试会写额外文件）；未读取真实用户 Cookie/凭据，未向外部发送凭据。仅获取公开 OkHttp/AOSP 源码辅助验证。唯一主动写入为本报告。
- 结论依据：完整源码逐行静态审阅，另用依赖的准确版本上游源码验证重定向行为；未进行 Android 设备动态复现，不将推演写成已实测。
- 等级：P1 为应优先阻断的凭据泄露路径；P2 为具有明确触发条件的安全/可靠性缺陷；P3 为低风险改进。本轮无 P0 结论。

## 结果摘要

| 编号 | 等级 | 真实问题 |
|---|---|---|
| N01 | P1 | JM 登录及认证请求关闭证书链、主机名校验，可被中间人窃取密码/JWT/AVS |
| N02 | P1 | 初始请求仅按平台附加 Cookie，不按实际目标域授权，媒体/CDN 收到平台登录凭据 |
| N03 | P1 | 跨域重定向不删除继承 Cookie；同 host 降级也恢复凭据 |
| N04 | P2 | 会话 CookieJar 未检查 Secure/path；WebView 导入再次丢失 Secure/HttpOnly |
| N05 | P2 | 敏感 SharedPreferences 进入默认备份范围，未排除 Cookie、JWT/AVS、代理密码 |
| N06 | P2 | 清除 Cookie 不清 HTTP 会话，且清任一平台会清除全部 WebView Cookie |
| N07 | P2 | WebView 代理反射类型不匹配且失败继续联网；JM 不走统一代理 |
| N08 | P2 | 代理缓存忽略 base client，使解析/下载超时配置互相污染 |
| N09 | P2 | WebView 返回值未绑定可信来源/主框架，iframe 或跳转后页面可伪造解析结果 |
| N10 | P2 | 同步 OkHttp 调用未随协程取消，关闭登录/取消任务后请求仍继续 |

## N01 — P1：JM TLS 身份认证被完全关闭

**证据与位置**

- [JmClient.kt:65–89](<android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L65-L89>)：checkServerTrusted 空实现，HostnameVerifier 恒 true，并安装到独立 OkHttpClient。
- [JmClient.kt:335–350](<android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L335-L350>)：用户名、密码直接放 FormBody，通过上述 client 发起登录。
- [JmClient.kt:228–245](<android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L228-L245>)、[JmClient.kt:157–160](<android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L157-L160>)：认证请求附带 Cookie: AVS 与 Authorization: Bearer。

**触发条件与影响**：设备连接攻击者可干预的网络/DNS/路由，或实际网络代理可劫持 JM 连接。攻击者提供任意证书即被接受，不需要用户安装攻击者 CA；可读取登录密码、JWT/AVS、篡改 API/下载内容。代码注释称“自签名/官方不校验”不是安全边界。

**最小修复**：恢复平台默认 TrustManager 与默认主机名验证；确有私有 CA 时，只信任经验证的该 CA/公钥并仍验证主机名，不使用信任所有证书。为登录/API/下载均增加无效证书、错误主机名必须失败的测试。

**非问题边界**：公开移动端协议签名/解密常量不是用户私钥或生产账号秘密，本报告不把这些常量列为密钥泄露。

## N02 — P1：初始请求的 Cookie 授权与目标 host 脱钩

**证据与位置**

- [Http.kt:160–187](<android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L160-L187>)：cookieForRequest(platform, url) 无条件取得整个平台已保存 Cookie；url 仅用于补充 SessionCookieJar，没有用于限制登录 Cookie 的发送范围。
- [Http.kt:199–210](<android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L199-L210>)、[Http.kt:237–248](<android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L237-L248>)、[Http.kt:291–305](<android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L291-L305>)、[Http.kt:318–329](<android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L318-L329>)：GET/通用请求/HEAD 都使用该结果。
- [Downloader.kt:53–60](<android/app/src/main/java/com/xd/vdl/core/download/Downloader.kt#L53-L60>)：下载媒体实际调用 Http.request(url, platform)。因此不是仅有一个无人调用的危险 API。
- JM 同类问题：[JmClient.kt:464–475](<android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L464-L475>)、[JmClient.kt:537–543](<android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L537-L543>)：对传入的下载/图片 URL 无 host 限制执行 applyAuth，连 JWT 也附加。

**触发条件与影响**：用户已保存平台登录 Cookie，下载或 HEAD 查询目标是媒体 CDN/第三方镜像；不需要重定向就会把原本用于登录域的整套凭据发送过去。若解析结果/第三方媒体地址可控，攻击者可接收凭据。不能据此断言每个 CDN 都恶意，但凭据已越过本应限定的源站边界。

**最小修复**：在最终发出请求时按真实 HTTPS URL 精确授权。API/页面 client 才允许预先定义的登录域 Cookie；媒体 client 默认不带登录 Cookie/JWT，仅在已证明必须且明确列举的源上提供最小必要凭据。不要用“属于同一平台”替代 Cookie 域、路径、Secure 规则；短链解析首跳默认无登录态。

**建议回归**：伪造凭据，仅在离线/环回环境构造受信登录域与第三方媒体目标，断言跨目标构建出的请求没有登录 Cookie/Authorization；覆盖 GET、HEAD、下载及 JM 路径。不得向真实第三方发送用户 Cookie。

## N03 — P1：重定向的跨域分支没有剥离已有 Cookie，HTTPS 可降级

**证据与位置**

- [PreserveLoginCookieInterceptor.kt:11–17](<android/app/src/main/java/com/xd/vdl/core/net/PreserveLoginCookieInterceptor.kt#L11-L17>)：original 与 outgoing 的 host 不同时，仅原样 proceed(outgoing)，并没有 removeHeader("Cookie")。同 host 则合并回原始显式 Cookie，不比较 scheme/port。
- [Http.kt:86–95](<android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L86-L95>)：followRedirects(true)、followSslRedirects(true)。[AndroidManifest.xml:19](<android/app/src/main/AndroidManifest.xml#L19>) 全局允许明文流量。
- 本项目使用 OkHttp 4.12.0，见 [build.gradle.kts:63](<android/app/build.gradle.kts#L63>)。对照准确版本上游 [RetryAndFollowUpInterceptor](<https://raw.githubusercontent.com/square/okhttp/parent-4.12.0/okhttp/src/main/kotlin/okhttp3/internal/http/RetryAndFollowUpInterceptor.kt>) 的 buildRedirectRequest：从 userResponse.request.newBuilder() 继承头，跨源只移除 Authorization，没有移除 Cookie。上游 [BridgeInterceptor](<https://raw.githubusercontent.com/square/okhttp/parent-4.12.0/okhttp/src/main/kotlin/okhttp3/internal/http/BridgeInterceptor.kt>) 仅在 CookieJar 返回非空时覆盖 Cookie，否则保留原显式头。

**可确定的触发链**：初始请求带 Cookie: SESSDATA=假值；响应 302 指向另一 host；目标 host 尚无会话 Cookie。重定向沿用显式 Cookie → Bridge 因目标 jar 为空不覆盖 → 本项目 interceptor 跨 host 分支直接放行 → 目标收到原 Cookie。代码注释“跨域重定向不携带它们”与实现不一致。该链同样影响使用 NO_COOKIES 的 B 站登录验证 client，因为它仍继承该 network interceptor，且空 jar 更不会覆盖显式 Cookie。

**同 host 降级触发**：HTTPS 端点返回 HTTP 同 host Location；客户端允许跟随，interceptor 再次恢复原 Cookie，网络观察者可读明文。需要出现这类跳转，不声称攻击者可无条件篡改正确验证的 HTTPS 首跳。

**最小修复**：禁止认证流的 HTTPS→HTTP 跳转；对每跳基于目标可信 HTTPS 域重新计算凭据，而不是继承旧头。无权目标明确删除继承的敏感头，保留目标自身合法 Cookie 时也应重新从具备 Cookie.matches 的 jar 获取，避免一刀切误删目标自己的正常会话。host 相同并不代表跨 scheme/port 仍有授权。

**现有测试缺口**：[CookieHeaderPreservationTest.kt:8–57](<android/app/src/test/java/com/xd/vdl/core/net/CookieHeaderPreservationTest.kt#L8-L57>) 只有同一环回服务器连续 200 请求与合并覆盖测试，没有任何 30x、不同 host、不同 scheme/port 测试，不能证明跨域安全。

## N04 — P2：Cookie 的 Secure/path 约束被绕过，导入 WebView 丢失 HttpOnly

**证据与位置**

- [Http.kt:39–58](<android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L39-L58>)：存储按响应 host 分桶，加载仅清过期并返回整桶，不调用 Cookie.matches(url)。
- [CookieStore.kt:59–94](<android/app/src/main/java/com/xd/vdl/core/net/CookieStore.kt#L59-L94>) 将 Domain/Path/Secure/HttpOnly 等属性丢弃；[CookieStore.kt:254–267](<android/app/src/main/java/com/xd/vdl/core/net/CookieStore.kt#L254-L267>) 将每项重建成仅 name=value 写入 HTTPS Cookie URL，未加 Secure 或 HttpOnly。
- [WebViewBridge.kt:341–347](<android/app/src/main/java/com/xd/vdl/core/parse/WebViewBridge.kt#L341-L347>) 同样将 Instagram 登录 Cookie 写为 name=value; Path=/，没有 Secure/HttpOnly。

**触发条件与影响**：服务端设置 Secure 或 Path=/account 的会话 Cookie 后，同 host 的 HTTP 或其他路径请求仍会由自定义 jar 带上；导入 Cookie 原本为 HttpOnly 时，重建版本不再禁止该源页面脚本读取。结合 HTTP 放行、混合内容 ALWAYS_ALLOW（见 [WebViewBridge.kt:69–84](<android/app/src/main/java/com/xd/vdl/core/parse/WebViewBridge.kt#L69-L84>)）或页面 XSS，扩大凭据暴露面。不同 host 桶本身不能替代 Secure/path 校验；目前未见任意 host 可直接注入其他 host 桶的证据。

**最小修复**：loadForRequest 过滤 expiresAt 与 matches(url)，替换键包含 name/domain/path；Cookie 导入保留已知属性，对登录秘密至少显式 Secure、HttpOnly、Path=/，确需 JS 读取的 csrftoken 单独允许，避免盲目 HttpOnly 破坏 CSRF 逻辑。非必要混合内容改为 NEVER_ALLOW。对只接收 Cookie 请求头的手填入口，明确采取受限默认策略而非假装保留了原属性。

## N05 — P2：凭据明文偏好存储进入默认备份

**证据与位置**

- [AndroidManifest.xml:13–20](<android/app/src/main/AndroidManifest.xml#L13-L20>)：allowBackup=true，没有 fullBackupContent/dataExtractionRules 排除规则；本次枚举主 res/xml 只有 [file_paths.xml](<android/app/src/main/res/xml/file_paths.xml>)。
- [CookieStore.kt:44–47](<android/app/src/main/java/com/xd/vdl/core/net/CookieStore.kt#L44-L47>)、[CookieStore.kt:195–201](<android/app/src/main/java/com/xd/vdl/core/net/CookieStore.kt#L195-L201>)：平台 Cookie 写入普通 SharedPreferences。
- [JmSession.kt:69–77](<android/app/src/main/java/com/xd/vdl/core/parse/JmSession.kt#L69-L77>)：JWT/AVS/account 写入普通 SharedPreferences。
- [ProxySettings.kt:60–62](<android/app/src/main/java/com/xd/vdl/core/net/ProxySettings.kt#L60-L62>)、[ProxySettings.kt:76–81](<android/app/src/main/java/com/xd/vdl/core/net/ProxySettings.kt#L76-L81>)：含 userinfo 的原始代理 URL（可能有密码）整串保存。

**触发条件与影响**：设备允许且实际发生应用备份/迁移时，凭据偏好文件默认可进入备份/恢复渠道，凭据保护不再仅取决于当前设备应用沙箱。不是“任意其他 App 可直接读取 MODE_PRIVATE”，也不声称所有 Android/OEM 都会以同样方式备份。WebView 会话存储也应一并列入备份数据分类核实。

**最小修复**：敏感会话默认不备份：为旧版 fullBackupContent、新版 dataExtractionRules 同时显式排除上述偏好与实际 WebView 敏感数据位置，或根据产品政策禁止应用备份并核实 OEM 迁移差异；若需落盘保密，使用 Android Keystore 保护密钥加密会话，仍需独立备份排除与失效策略。本轮未更改 Manifest 或用户数据。

## N06 — P2：退出不清 HTTP 会话，清单个平台又破坏其他 WebView 会话

**证据与位置**

- [CookieStore.kt:238–242](<android/app/src/main/java/com/xd/vdl/core/net/CookieStore.kt#L238-L242>) 仅删除平台偏好并处理 WebView Cookie，没有清 HTTP jar。
- [Http.kt:36–63](<android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L36-L63>)：服务端 Set-Cookie 保留在进程 jar；clear 仅在私有对象内部声明，全仓源码检索未发现调用。后续 [Http.kt:180–187](<android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L180-L187>) 继续合并这些 Cookie。
- [CookieStore.kt:276–289](<android/app/src/main/java/com/xd/vdl/core/net/CookieStore.kt#L276-L289>) 使用 removeAllCookies(null)，再仅把其他平台偏好里的 name=value 写回；没有保存的 WebView Cookie 会消失，Domain/path/属性也无法恢复，抖音的 iesdouyin 同步副本亦未按保存逻辑重建。

**触发条件与影响**：服务端响应曾下发可用于认证的会话 Cookie，用户在设置页清除平台 Cookie 后，后续 HTTP 请求仍可能使用旧会话；是否仍获认证取决于服务端是否已撤销该会话。同时任何一个平台的清除会使其他平台仅存在 CookieManager 的会话失效。不能将“游客 ttwid 总是登录凭据”作为前提：风险条件是 jar 中实际出现认证 Cookie。

**最小修复**：增加按受信域/平台清除 HTTP jar 的明确 API，并与偏好/WebView 原子协调；WebView 使用已知 Cookie 名与域/path 的过期删除，仅处理目标平台，不全局清空再不完整重建。异步 Cookie 清除应等待完成再报告成功；服务端支持时同时撤销 token。

## N07 — P2：代理设置未对所有网络栈生效，失败后仍继续连接

**证据与位置**

- [WebViewProxy.kt:37–46](<android/app/src/main/java/com/xd/vdl/core/net/WebViewProxy.kt#L37-L46>) 在 applyInner 成功前就写 applied，失败只 Log.w，同值再次调用被跳过。
- [WebViewProxy.kt:50–72](<android/app/src/main/java/com/xd/vdl/core/net/WebViewProxy.kt#L50-L72>)：Android Q 以下直接跳过，Q 及以上反射 android.net.ProxyProperties 并寻找 setGlobalProxy(ProxyProperties)。Android Q 的 [AOSP IConnectivityManager.aidl](<https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/android-10.0.0_r1/core/java/android/net/IConnectivityManager.aidl>) 接口明确是 ProxyInfo getGlobalProxy / setGlobalProxy(in ProxyInfo p)，不能把 ProxyProperties 当作这个签名的参数；该代码不是可依赖的普通应用 WebView 代理实现。未进行 OEM 设备运行验证，不断言每种 ROM 的具体异常文本。
- [Http.kt:105–109](<android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L105-L109>) 调用代理设置后，WebView 加载并不等待可验证的成功状态；[WebViewBridge.kt:310](<android/app/src/main/java/com/xd/vdl/core/parse/WebViewBridge.kt#L310>) 等照常 loadUrl。
- [JmClient.kt:79–89](<android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L79-L89>) 的独立 client 从未应用 ProxySettings.wrap。故设置界面“应用所有请求走同一个代理”的描述与 JM 实际不符。

**触发条件与影响**：用户显式配置代理，依赖其可达性或出口隔离；代理反射失败/被跳过时，WebView 仍使用系统网络路径（系统无代理即直连），JM 也不会按应用代理走。可导致真实出口暴露、登录与解析网络路径不一致或站点不可达；没有系统路由/抓包实测，不宣称观察到真实 IP 泄漏。

**最小修复**：使用项目已依赖 AndroidX WebKit 的 ProxyController/ProxyConfig 和 PROXY_OVERRIDE 能力检测，成功回调后再启动 WebView；不支持时明确提示并由用户决定是否允许直连。JM client 也经过统一代理策略包装。仅在设置成功后更新 applied，失败不缓存为成功；不要尝试获取额外系统特权或修改设备全局代理权限。

**同类输入注意**：[ProxySettings.kt:76–82](<android/app/src/main/java/com/xd/vdl/core/net/ProxySettings.kt#L76-L82>) 接受 https 代理 URL，但 [ProxySettings.kt:140–145](<android/app/src/main/java/com/xd/vdl/core/net/ProxySettings.kt#L140-L145>) 仍固定创建 HTTP 代理，未实现到代理本身的 TLS；应拒绝未实现的代理 scheme 或明确标注，避免用户把代理 Basic 凭据误认为经 HTTPS 保护。本项属于输入/承诺不一致，不声称 HTTPS 目标站 TLS 会因此自动失效。

## N08 — P2：代理 client 缓存键不含 base，下载超时失效

**证据与位置**

- [ProxySettings.kt:95–103](<android/app/src/main/java/com/xd/vdl/core/net/ProxySettings.kt#L95-L103>)：只有 cachedUrl == value 就返回 cachedClient，不检查传入 base；[ProxySettings.kt:126–128](<android/app/src/main/java/com/xd/vdl/core/net/ProxySettings.kt#L126-L128>) 存储单一 client。
- [Http.kt:126–146](<android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L126-L146>) 解析与下载分别传入不同 base；下载预期 10s 建连、30s 读写，原始解析 base 为 20/60/60 秒。

**触发条件与影响**：配置一个非空有效代理，先访问 Http.client，再访问 Http.downloadClient。第二次 wrap 返回第一次缓存的解析 client，下载的 10/30 秒“坏 CDN 判死”不生效；反向访问则解析也受到较短超时影响。还会使未来新增的各 base 拦截器/安全策略串用，当前已确定影响为 timeout 配置。

**最小修复**：删除 ProxySettings 内跨 base 单例缓存（Http 已分别缓存），或缓存键至少包含 base 身份及代理配置；独立保持各 base 的 TLS、jar、interceptor、timeout 配置。用两个不同 timeout 的 base 验证顺序无关性。

## N09 — P2：WebView 解析桥未将结果绑定可信主框架来源

**证据与位置**

- [WebViewBridge.kt:357–368](<android/app/src/main/java/com/xd/vdl/core/parse/WebViewBridge.kt#L357-L368>)：addJavascriptInterface 将 vdlResult.deliver 暴露给所有 frame，deliver 接收任意字符串即 finish；onPageFinished 的 Instagram 前缀检查只决定何时注入，不认证 deliver 的调用来源。800ms 延时执行前也未重新验证当前页面 URL。
- [WebViewBridge.kt:264–281](<android/app/src/main/java/com/xd/vdl/core/parse/WebViewBridge.kt#L264-L281>) 对所有 origin（setOf("*")) 安装文档开始 hook，导航回调没有可信域限制；[WebViewBridge.kt:216–231](<android/app/src/main/java/com/xd/vdl/core/parse/WebViewBridge.kt#L216-L231>) 信任页面可写的 __vdlCap；[WebViewBridge.kt:605–626](<android/app/src/main/java/com/xd/vdl/core/parse/WebViewBridge.kt#L605-L626>) 只匹配 URL 子串。
- [InstagramParser.kt:44–76](<android/app/src/main/java/com/xd/vdl/core/parse/InstagramParser.kt#L44-L76>) 将 bridge 返回的 api/video/image 继续用于生成媒体结果；部分路径验证 code，但匹配 code 不等于验证调用 origin，攻击脚本可知当前帖子的 code。回退 video 只要求 HTTPS。

**触发条件与影响**：被加载页面包含攻击者能运行脚本的第三方 iframe、受控重定向，或可信站点页面发生脚本注入。iframe 可直接调用 bridge 返回伪造结果，或跳转页面提供伪造状态，劫持解析结果/诱导下载攻击者媒体；与 N02 组合可扩大凭据泄漏。此处不是“任意外部链接就能直接执行原生任意代码”，bridge 目前仅提供 deliver，未见读取任意本地文件等方法。也未证明真实 Instagram 页面存在可控恶意 iframe；条件需保留，不能宣称已攻陷站点。

**最小修复**：用 WebViewCompat.addWebMessageListener 精确 allowedOriginRules，并校验 isMainFrame、sourceOrigin 与一次性随机请求标识；或取消 addJavascriptInterface，采用仅对已验证当前主框架执行 evaluateJavascript 的结果回调。注入脚本限制到实际可信 HTTPS origins，主框架跳出允许域即中止，延时任务执行时再次检查来源与任务状态；返回媒体 URL 仍须按 N02 做独立凭据授权。

## N10 — P2：协程取消没有取消进行中的 OkHttp Call

**证据与位置**

- [BiliWebLoginDialog.kt:73–88](<android/app/src/main/java/com/xd/vdl/ui/BiliWebLoginDialog.kt#L73-L88>)：Dispatchers.IO 内同步 execute，虽有 15s callTimeout，但没有在取消时调用 Call.cancel；[BiliWebLoginDialog.kt:98–99](<android/app/src/main/java/com/xd/vdl/ui/BiliWebLoginDialog.kt#L98-L99>) 正确重抛 CancellationException，[BiliWebLoginDialog.kt:88](<android/app/src/main/java/com/xd/vdl/ui/BiliWebLoginDialog.kt#L88>) ensureActive 防止取消后保存，因此本项不误报“取消登录仍必定保存凭据”。
- [Http.kt:214–219](<android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L214-L219>)、[Http.kt:312–313](<android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L312-L313>) 同步 execute 没有可取消包装。
- [WebViewBridge.kt:509–526](<android/app/src/main/java/com/xd/vdl/core/parse/WebViewBridge.kt#L509-L526>) 的重放也同步 execute，cont.invokeOnCancellation 仅移除 Handler 回调（[WebViewBridge.kt:450](<android/app/src/main/java/com/xd/vdl/core/parse/WebViewBridge.kt#L450>)），未持有并取消重放 Call；该旧重放入口本轮未查到实际调用者，不能据此单独认定当前主流程会触发，但公共实现存在同样不足。
- [Downloader.kt:60–75](<android/app/src/main/java/com/xd/vdl/core/download/Downloader.kt#L60-L75>)：任务取消只在阻塞读取前 ensureActive，已阻塞的 execute/read 不会主动被取消。

**触发条件与影响**：请求处于慢建连/阻塞读取时取消任务、关闭登录窗口；协程不能让同步套接字立即退出，请求仍会占线程/连接并可继续收发到超时。一般 Http 没有总 callTimeout，持续小流量响应可反复重置 read timeout，不能保证总生命周期在 60 秒内结束。

**最小修复**：提供统一 suspend 可取消 HTTP 包装（enqueue + suspendCancellableCoroutine；取消时 call.cancel），读响应体期间也保持取消挂接并在 finally 关闭 body；配置适当总 callTimeout。保留正确的 CancellationException 重抛。WebView 本身的 finally stopLoading/destroy 是正向控制，不应删掉。

## 日志、备份与 FileProvider 专项结论（避免误报）

1. [CookieStore.kt:205–209](<android/app/src/main/java/com/xd/vdl/core/net/CookieStore.kt#L205-L209>) 只记 Cookie 数量与键名；[BiliWebLoginDialog.kt:93–102](<android/app/src/main/java/com/xd/vdl/ui/BiliWebLoginDialog.kt#L93-L102>) 只记成功文案或异常类型；[JmSession.kt:78–80](<android/app/src/main/java/com/xd/vdl/core/parse/JmSession.kt#L78-L80>) 记账号及 token 长度，不记 token 值。**未在这些明确路径发现真实 Cookie/JWT/密码直接写日志的证据。** 账号仍是个人信息，不等同会话密钥。
2. [AppLog.kt:45–68](<android/app/src/main/java/com/xd/vdl/core/AppLog.kt#L45-L68>) 同时输出 Logcat 和本地持久文件，未做全局 URL/查询参数脱敏；[WebViewBridge.kt:205–206](<android/app/src/main/java/com/xd/vdl/core/parse/WebViewBridge.kt#L205-L206>) 失败记录 pageUrl。应防御性去除 URL 查询中的访问 token，不把目前读取到的固定抖音 detail URL 误称含用户 Cookie。JM 登录错误构造含响应片段（[JmClient.kt:261–268](<android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L261-L268>)、[JmClient.kt:303–324](<android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L303-L324>)），建议只输出错误码/形态，不回显原文；本轮未建立真实凭据因此写日志的必经链，不单独定级。
3. [file_paths.xml:3–13](<android/app/src/main/res/xml/file_paths.xml#L3-L13>) 的 files-path/cache-path 根范围偏宽；但 [AndroidManifest.xml:45–49](<android/app/src/main/AndroidManifest.xml#L45-L49>) provider exported=false 且需 URI 临时授权。[MediaStoreExporter.kt:85–100](<android/app/src/main/java/com/xd/vdl/core/download/MediaStoreExporter.kt#L85-L100>) 已审到的授权对象是明确导出的下载文件。**不能仅凭 path="." 就断言任何 App 能读取 Cookie 偏好或私有日志**，本轮无可利用任意 URI 授权路径证据；最小加固是把根映射收窄到实际分享/升级目录，绝不为此扩大权限。
4. B 站登录 URL allowlist 使用解析后的 HTTPS host + 点分域边界（[BiliWebLogin.kt:12–16](<android/app/src/main/java/com/xd/vdl/core/net/BiliWebLogin.kt#L12-L16>)），不是 endsWith("bilibili.com") 那类后缀伪装缺陷；禁用 file/content access，未加 JS 原生桥，服务端 nav 明确验证 isLogin。这些正向控制有效，但不能替代 N03 的 HTTP 重定向 Cookie 策略。
5. 没有把公开平台 app ID、UA、协议签名/解密常量、测试中的占位 JWT/Cookie 当作真实密钥泄漏。

## 测试状态与建议优先顺序

- 已完整阅读下列六个相关测试，**未执行**。理由：用户仅允许写本报告；Gradle 会生成 build/缓存，且 [DouyinHookInjectionTest.kt:87–93](<android/app/src/test/java/com/xd/vdl/core/parse/DouyinHookInjectionTest.kt#L87-L93>) 明确写 hook-export 文件，不符合本轮只读限制。
- 首先用假凭据补 N02/N03 的“目标无 jar Cookie 跨 host 302”“HTTPS 同 host→HTTP”“跨端口”“重定向链返回原 host”“第三方 CDN 初始请求”；再覆盖 N01 TLS 拒绝、N04 Cookie.matches、N06 logout、N08 两种 base 访问顺序、N09 iframe 非主框架拒绝、N10 慢响应取消。
- WebView/备份/代理结论的设备侧行为需由后续获准的 instrumentation 测试确认。没有执行设备抓包、登录、真实 Cookie 请求或任何权限/数据变更。

## 完整阅读覆盖

以下为使用 read 工具逐行完整阅读，非 grep 命中代替全文。指定生产目标、Manifest/file_paths 与六个相关测试合计 **2,283 行**。

| 类别 | 完整读取文件 | 行范围 |
|---|---|---|
| core/net 全部 6 个 Kotlin | [CookieStore.kt](<android/app/src/main/java/com/xd/vdl/core/net/CookieStore.kt>) | 1–296 |
| core/net | [WebViewProxy.kt](<android/app/src/main/java/com/xd/vdl/core/net/WebViewProxy.kt>) | 1–84 |
| core/net | [ProxySettings.kt](<android/app/src/main/java/com/xd/vdl/core/net/ProxySettings.kt>) | 1–152 |
| core/net | [PreserveLoginCookieInterceptor.kt](<android/app/src/main/java/com/xd/vdl/core/net/PreserveLoginCookieInterceptor.kt>) | 1–32 |
| core/net | [Http.kt](<android/app/src/main/java/com/xd/vdl/core/net/Http.kt>) | 1–333 |
| core/net | [BiliWebLogin.kt](<android/app/src/main/java/com/xd/vdl/core/net/BiliWebLogin.kt>) | 1–39 |
| 指定 UI | [BiliWebLoginDialog.kt](<android/app/src/main/java/com/xd/vdl/ui/BiliWebLoginDialog.kt>) | 1–146 |
| 指定解析 | [WebViewBridge.kt](<android/app/src/main/java/com/xd/vdl/core/parse/WebViewBridge.kt>) | 1–667（分段 1–250、251–470、471–667） |
| 指定会话 | [JmSession.kt](<android/app/src/main/java/com/xd/vdl/core/parse/JmSession.kt>) | 1–88 |
| 配置 | [AndroidManifest.xml](<android/app/src/main/AndroidManifest.xml>) | 1–56 |
| 配置 | [file_paths.xml](<android/app/src/main/res/xml/file_paths.xml>) | 1–14 |
| 网络测试 | [CookieHeaderPreservationTest.kt](<android/app/src/test/java/com/xd/vdl/core/net/CookieHeaderPreservationTest.kt>) | 1–58 |
| 网络测试 | [BiliWebLoginTest.kt](<android/app/src/test/java/com/xd/vdl/core/net/BiliWebLoginTest.kt>) | 1–34 |
| 注入测试 | [DouyinHookInjectionTest.kt](<android/app/src/test/java/com/xd/vdl/core/parse/DouyinHookInjectionTest.kt>) | 1–128 |
| 登录测试 | [JmLoginResultTest.kt](<android/app/src/test/java/com/xd/vdl/core/parse/JmLoginResultTest.kt>) | 1–93 |
| 路由测试 | [ShareLinkRoutingTest.kt](<android/app/src/test/java/com/xd/vdl/core/ShareLinkRoutingTest.kt>) | 1–34 |
| 路由测试 | [DouyinNoteRoutingTest.kt](<android/app/src/test/java/com/xd/vdl/core/DouyinNoteRoutingTest.kt>) | 1–29 |

### 为验证触发链额外完整阅读（1,167 行）

- [JmClient.kt](<android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt>)：1–550，分 1–200、201–420、421–550。
- [AppLog.kt](<android/app/src/main/java/com/xd/vdl/core/AppLog.kt>)：1–71。
- [Platform.kt](<android/app/src/main/java/com/xd/vdl/core/Platform.kt>)：1–97。
- [build.gradle.kts](<android/app/build.gradle.kts>)：1–73。
- [InstagramParser.kt](<android/app/src/main/java/com/xd/vdl/core/parse/InstagramParser.kt>)：1–148。
- [XiaohongshuParser.kt](<android/app/src/main/java/com/xd/vdl/core/parse/XiaohongshuParser.kt>)：1–129。
- [Downloader.kt](<android/app/src/main/java/com/xd/vdl/core/download/Downloader.kt>)：1–99。

### 额外上下文局部读取（不冒充全文覆盖）

- [MediaStoreExporter.kt:70–134](<android/app/src/main/java/com/xd/vdl/core/download/MediaStoreExporter.kt#L70-L134>)：仅 70–134。
- [SettingsScreen.kt:220–349](<android/app/src/main/java/com/xd/vdl/ui/SettingsScreen.kt#L220-L349>)：仅 220–349。
- [AppViewModel.kt:75–139](<android/app/src/main/java/com/xd/vdl/ui/AppViewModel.kt#L75-L139>)：仅 75–139。

### 验证限制

没有 git 状态/差异操作；未覆盖未列明的其他文件全文或所有应用网络调用；上游大文件首次 AOSP ConnectivityManager 获取被截断，不作为全文覆盖依据，代理类型结论另以完整获取的 IConnectivityManager.aidl 验证。公开资料搜索工具认证失败，不影响上述本地源码与直接获取的上游源码证据。没有将工具失败视为项目缺陷。
