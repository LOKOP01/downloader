# Android 解析器只读审计

## 范围与方法

- 工作区：用户指定工作区；分支名称采用用户提供的 audit/android-full-20261015，未用 git 验证，也未执行任何 git 操作。
- 只读完整阅读下列 14 个目标源码文件、7 个相关测试；额外按需阅读网络、下载、界面调用点。未读取排除的 WebViewBridge/JmSession 实现，桥接内部生命周期与会话持久化结论留给对应代理。
- 未编辑源码、未构建、未运行测试、未访问第三方、未发送任何攻击请求。下列结论为静态调用链分析，不伪称动态复现。
- 评级：高/P1 为凭据泄漏、可导致进程内存耗尽或核心链不可用；中/P2 为错误作品、下载内容不完整、取消/时限缺陷；低/P3 为诊断与弱校验。评级包含明确触发前提，不代表这些前提已在真实平台发生。

## 主要结论

### PAR-01 高/P1：禁漫 TLS 完全不认证服务器，账号密码及令牌可被中间人读取

**证据**：[JmClient.kt:71-89](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L71-L89) 的 checkServerTrusted 为 Unit，HostnameVerifier 永远 true；所有 API、登录、图片、ZIP 共用此 client。[JmClient.kt:335-349](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L335-L349) 向该 client 提交 username/password；[JmClient.kt:241-245](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L241-L245) 加 AVS/JWT。

**触发/影响**：用户网络、代理出口或 DNS 路径被中间人控制，或收到不可信证书，即可冒充任意目标；登录口令、会话令牌及下载内容的真实性失去保障。代码注释所称兼容特殊证书不是安全边界。

**建议测试**：本地测试服务使用错误主机名、未知 CA、过期证书，三者均应被拒；再用显式可信测试 CA 验证正常请求。不访问真实站点。建议使用系统信任或受控证书/公钥固定，不全局放行。

### PAR-02 高/P1：官方 ZIP 响应指定任意 URL，下载时无条件携带 AVS/JWT

**证据链**：[JmAlbumZip.kt:28-28](android/app/src/main/java/com/xd/vdl/core/parse/JmAlbumZip.kt#L28-L28) 仅要求 loggedIn 与非空 downloadUrl；[JmAlbumZip.kt:40-56](android/app/src/main/java/com/xd/vdl/core/parse/JmAlbumZip.kt#L40-L56) 原样接收 download_url。[JmDownloader.kt:295-308](android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt#L295-L308) 对任意以 http 开头的绝对地址直接下载；[JmClient.kt:470-475](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L470-L475) 未限定 scheme/host 即 applyAuth；实际认证头见 [JmClient.kt:228-245](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L228-L245)。图片入口 [JmClient.kt:537-546](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L537-L546) 同样无条件带认证，[JmClient.kt:437-441](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L437-L441) 的图片分类又只是字符串 contains，不能作为授权边界。

**触发/影响**：接口响应被篡改（PAR-01 可形成现实前置条件），或接口误返外域绝对地址时，首次向该外域请求已带真实 AVS/JWT；无需依赖重定向是否保留 Authorization。若允许 http 且系统明文策略放行，还会明文发送。下载直链正常可能跨 CDN，因此不能简单声称“所有跨域均恶意”，但认证不应无条件跨域。

**建议测试**：注入 download_url 为本地第二测试域、http、userinfo、端口变化及跨域重定向；断言外域不收到 Cookie/Authorization。区分 API 认证域与匿名/签名 URL 下载域；精确解析 host，不用 contains。现有 [JmAlbumZipTest.kt:28](android/app/src/test/java/com/xd/vdl/core/parse/JmAlbumZipTest.kt#L28) 只测正常 status/字段，不覆盖目的地与认证隔离。

### PAR-03 中/P2：B站分P参数被忽略，始终取顶层 cid

**证据**：[BiliParser.kt:103-127](android/app/src/main/java/com/xd/vdl/core/parse/BiliParser.kt#L103-L127) 从整条 URL 提 BV/av，但不读取 p；view 响应只取 data.cid、data.duration，没有选择 pages[p-1]；[BiliParser.kt:140-144](android/app/src/main/java/com/xd/vdl/core/parse/BiliParser.kt#L140-L144) 直接按此 cid 请求播放地址。

**触发/影响**：用户分享多P视频的 ?p=2 等非首P链接，返回首P播放内容与总/默认时长，而不是指定分P。无需特殊平台异常。

**建议测试**：本地 fixture 提供两页不同 cid、duration、part；p=2 必须请求第二 cid 并返回第二页时长；补充 p 缺省、0、负数、越界、重复参数、短链跳转保留 p。页码应按 URI 查询参数解析并定义错误策略。

### PAR-04 中/P2：B站 durl 多分段被当作互斥清晰度，最终只下载一个片段

**证据**：[BiliParser.kt:223-239](android/app/src/main/java/com/xd/vdl/core/parse/BiliParser.kt#L223-L239) 每个 durl/order 生成独立 Quality；模型 [VideoInfo.kt:10-24](android/app/src/main/java/com/xd/vdl/core/model/VideoInfo.kt#L10-L24) 只有单视频候选与备用地址，没有连续片段模型。[AppViewModel.kt:141-153](android/app/src/main/java/com/xd/vdl/ui/AppViewModel.kt#L141-L153) 只选一项 Quality；[DownloadManager.kt:120-138](android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L120-L138) 下载该 Quality，而不是顺序拼接全部 durl。

**触发/影响**：playurl 没 DASH 且 durl.length > 1 时，页面显示“第N段”档位，用户下载只得到其中一段却无完整性提示。不是备用 CDN 问题，不能把各段放 urlBackups。

**建议测试**：两段本地媒体 fixture，断言两段都按 order 下载并拼接；若当前不支持分段，应明确拒绝而非报完整视频成功。覆盖单段 durl 与 DASH 路径避免回归。

### PAR-05 中/P2：多媒体模型压缩为单媒体，混合 X 推文尤其会静默丢视频

**证据**：[XParser.kt:375-407](android/app/src/main/java/com/xd/vdl/core/parse/XParser.kt#L375-L407) 遍历媒体时把先出现的照片加入 images，找到首视频后设置 qualities 并 break；后续照片/视频丢失。若照片先于视频，则结果同时有 images 与 qualities。[VideoInfo.kt:50-54](android/app/src/main/java/com/xd/vdl/core/model/VideoInfo.kt#L50-L54) 只要 images 非空便 isImage；[AppViewModel.kt:141-153](android/app/src/main/java/com/xd/vdl/ui/AppViewModel.kt#L141-L153) 此时不用视频 Quality；[DownloadManager.kt:101-105](android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L101-L105) 路由图片下载。Instagram [InstagramParser.kt:107-130](android/app/src/main/java/com/xd/vdl/core/parse/InstagramParser.kt#L107-L130) 仅选 carouselVideos 首个非空集合，存在视频就把轮播照片全部清空。

**触发/影响**：X 为“照片、视频”顺序时只下载照片，视频完全不下载；“视频、照片”时只剩视频；多视频只保留首个。Instagram 两视频轮播只保留第一条，混合轮播照片丢失。结果不能代表完整帖子，但上层无部分结果告警。

**建议测试**：照片→视频、视频→照片、两视频、纯照片 fixture，断言媒体数与顺序完整；改成媒体项列表/媒体组，清晰度属于每个视频项。若产品只支持首媒体，必须显式标识并提示，不用 isImage 隐式决定。

### PAR-06 中/P2：阻塞请求不关联协程取消，兜底异常处理还会吞取消

**证据**：[JmClient.kt:153-194](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L153-L194)、[JmClient.kt:338-360](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L338-L360) 同步 execute 且 catch(Exception) 后继续下一个域；[JmParser.kt:78-82](android/app/src/main/java/com/xd/vdl/core/parse/JmParser.kt#L78-L82) 把非 ParseException（含 CancellationException）包装成普通解析异常。X [XParser.kt:105-132](android/app/src/main/java/com/xd/vdl/core/parse/XParser.kt#L105-L132) 三路 catch(Exception) 不先重新抛取消；Instagram [InstagramParser.kt:31-61](android/app/src/main/java/com/xd/vdl/core/parse/InstagramParser.kt#L31-L61) 对 fetchInPage 的 suspend 调用用 runCatching.getOrNull 吞取消。网络共享工具 [Http.kt:251-266](android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L251-L266)、[Http.kt:312-332](android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L312-L332) 同步 execute，无 Job→Call.cancel 绑定。

**触发/影响**：取消发生在建连/读取中时，协程无法及时终止底层 Call；失败后可能继续循环/兜底（具体何时下一 suspend 点重新观察到取消依调用方而定）。不能因为 withContext 在返回分发时可能再次抛取消，就认为底层请求已被及时取消。禁漫最多轮询4个 API 域，图片最多6域。

**建议测试**：本地永不返回头、慢速响应、读取中断三个场景取消 Job，断言 Call 被取消、没有后续域/后备源请求，CancellationException 原样传播。所有边界先重抛取消；使用可取消的异步适配或注册完成钩子取消 Call。桥接自身取消清理属于排除范围，未下结论。

### PAR-07 中/P2：抖音“并行不挡路”实际等待全部 HTTP 兜底，且没有总预算

**证据**：[DouyinParser.kt:75-86](android/app/src/main/java/com/xd/vdl/core/parse/DouyinParser.kt#L75-L86) 先得到 WebView rich 后仍无条件 httpDeferred.await；[DouyinParser.kt:116-130](android/app/src/main/java/com/xd/vdl/core/parse/DouyinParser.kt#L116-L130) 串行 share/iteminfo/detail；[DouyinParser.kt:252-265](android/app/src/main/java/com/xd/vdl/core/parse/DouyinParser.kt#L252-L265) share 内又最多两轮×两页，共4次。最坏合计6次 HTTP，底层 [Http.kt:86-95](android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L86-L95) 只有连接/读写超时，无总 callTimeout；主流程也无整体 withTimeout。

**触发/影响**：WebView 很快取得完整有效结果，但 HTTP 一路缓慢或持续传少量字节，最终解析仍等待该路。readTimeout 是单次读取停滞限制，不是整条调用/全部解析时限，因此不能简单把最大耗时断言为固定秒数。

**建议测试**：注入 rich 立即成功、HTTP 可控挂起，断言按产品预算返回且取消/关闭 HTTP；测试 HTTP 信息确实更全时的有限等待策略。若要比较质量，应设置严格尾部预算，而不是无条件 await。

### PAR-08 高/P1（条件性资源风险）：响应字节、图像像素、ZIP 下载及解析工作量没有硬上限

**证据**：[JmClient.kt:160-161](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L160-L161) API string 全量读取再 JSON/base64/AES，多份内存；[JmClient.kt:543-546](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L543-L546) 图片 bytes 全量读取；[JmScramble.kt:67-74](android/app/src/main/java/com/xd/vdl/core/parse/JmScramble.kt#L67-L74) 再创建同尺寸 ARGB_8888 位图。消费方 [JmDownloader.kt:398-418](android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt#L398-L418) 未在 decode 前用 inJustDecodeBounds 核验像素，直接 decode→复制→编码；[JmDownloader.kt:209-235](android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt#L209-L235) 第一批并发8且32页后才重估。[JmClient.kt:478-490](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L478-L490) ZIP 写盘没有字节上限，也未核验 EOF 时 done==声明长度。[XParser.kt:460-466](android/app/src/main/java/com/xd/vdl/core/parse/XParser.kt#L460-L466) 每个 qualities 启动一个 HEAD，无固定并发门限。

**触发/影响**：远端/损坏响应异常大、极端像素图片、缓慢超大 ZIP 或异常多 variants 时，可能 OOM、过度并发或填满缓存。96MB 的估算式并非硬限制；[JmDownloader.kt:88-92](android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt#L88-L92) 最低并发2，即便单页200MB仍给2；现有 [JmConcurrencyTest.kt:38](android/app/src/test/java/com/xd/vdl/core/download/JmConcurrencyTest.kt#L38) 恰好断言这一不安全下限。未用真实大文件复现，评级依据可达全量分配与缺乏拒绝边界。

**建议测试**：只用本地小型边界 fixture/声明超大尺寸样本，不真正耗尽宿主资源；断言超限在全量分配前拒绝。设置 API/图片响应字节数、像素/单图预算、目录项总数、ZIP配额与整体时间预算；HEAD semaphore；首次即按尺寸预算调度，允许降至1或拒绝。补截断 Content-Length 的本地测试。

### PAR-09 中/P2：禁漫自建客户端绕过应用内代理设置

**证据**：[JmClient.kt:79-89](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L79-L89) 私有 OkHttpClient.Builder 不经过 Http；相比 [Http.kt:126-146](android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L126-L146) 统一 client 会走 ProxySettings.wrap，[Http.kt:149-153](android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L149-L153) 代理变更只失效 Http 缓存。禁漫所有请求直接使用私有 client。

**触发/影响**：用户仅配置 App 内 HTTP/SOCKS 代理而没有系统代理/VPN时，其他平台走代理、禁漫仍直连；用户更改代理也不会刷新该 client，易误报“请确认代理已开启”。不等于 VPN 或 JVM/系统代理必定失效。

**建议测试**：本地直连失败、测试代理可达环境，配置/更换代理后断言禁漫 API、登录、图片、ZIP均经过预期代理；在统一出口上局部配置证书策略，勿另建永久绕过出口。

### PAR-10 低/P3：X 网络错误判断恒真，业务异常全部误报代理故障

**证据**：[XParser.kt:134-148](android/app/src/main/java/com/xd/vdl/core/parse/XParser.kt#L134-L148)：reasons.all { NET_ERRORS.any { it.contains(it, true) } }。内层 it 遮蔽外层 reason，每个非空标记必然包含自身，NET_ERRORS 非空，故每个 reason 都判 true。

**触发/影响**：三路均失败时，即便是“推文不含媒体”“敏感内容需登录”“queryId过期”，都进入网络/代理提示，掩盖真实处理方式。

**建议测试**：纯业务失败→非网络；全部 timeout→网络；混合失败→保留各路原因。给外层命名 reason、内层 marker，实际执行 reason.contains(marker,true)。

### PAR-11 中/P2：输入 ID 提取过宽/溢出，可能查询错误作品

**证据**：[DouyinParser.kt:58-69](android/app/src/main/java/com/xd/vdl/core/parse/DouyinParser.kt#L58-L69) URL 无标准作品路径时，ID_ANY 从任意位置取15~20位数字，存在此数字就不 resolve；主页拒绝只放在“没有先取到 id”的分支。[InstagramParser.kt:15-22](android/app/src/main/java/com/xd/vdl/core/parse/InstagramParser.kt#L15-L22) shortcode 长度无限制，用 ULong 乘64累加，无溢出检查。[Platform.kt:59-62](android/app/src/main/java/com/xd/vdl/core/Platform.kt#L59-L62) 以区分大小写的 startsWith("http") 判 scheme，尽管 URL 提取是忽略大小写的，HTTPS://... 会走错误补前缀路径。[JmParser.kt:53-60](android/app/src/main/java/com/xd/vdl/core/parse/JmParser.kt#L53-L60) 对任意文本/域名的 /photo/、/album/、JM数字子串直接认车号，与剪切板“整段就是车号”注释不符。

**触发/影响**：抖音短链或主页 URL 查询参数带15~20位追踪号时误当作品 ID；Instagram 超范围 shortcode 回绕为别的数值（后续 code 相等检查可阻止 API错误作品返回，但仍发无意义 API 请求并进入兜底）；大写 scheme 的合法平台链接不能稳定路由。任意电商 /album/123 链接被误判禁漫。这里不是主张可直接任意平台注入，而是输入语义不一致。

**建议测试**：标准作品路径优先、query数字不作为 ID、用户主页始终拒绝、短链总是 resolve；shortcode 超范围明确拒绝；https/HTTPS/混合scheme；未知域 /album/ 路径、普通句子含 JM数字、纯车号全串边界。统一 URI 结构解析，限制 ID 长度和字符集。

## 其他核查与边界观察

- B站音频挑选只按最大 bandwidth，[BiliParser.kt:168-181](android/app/src/main/java/com/xd/vdl/core/parse/BiliParser.kt#L168-L181) 可被“最高码率音频无URL、较低码率有URL”响应变成 audioUrl 空；Quality.needMerge 由是否有 audioUrl 决定，可能把仅视频流当已合并。视频同档最大码率优先也在过滤 URL 前，[BiliParser.kt:185-202](android/app/src/main/java/com/xd/vdl/core/parse/BiliParser.kt#L185-L202) 可能丢有效同档候选。建议先验可用 URL 再排名，用缺URL与0/负bandwidth fixture 验证；平台是否会返回此形态未确认，列为响应健壮性问题。
- 禁漫 series 注释有 sort，但 [JmParser.kt:119-133](android/app/src/main/java/com/xd/vdl/core/parse/JmParser.kt#L119-L133) 不按 sort 排序/去重，封面还用首章ID而不是传入 albumId；如服务端不保证目录已排序，成品章节顺序会不符业务。建议 unordered/duplicate series 测试，并与协议约定确认；不武断声称真实响应当前乱序。
- [JmClient.kt:303-326](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L303-L326) unwrapLoginBody 参数 code 实际取 HTTP 状态，未检查 JSON 内 code；[JmClient.kt:261-270](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L261-L270) 只要 jwt/avs 任意非空即成功。当前“HTTP200、业务401、data=[]”会被当不认识data，丢 errorMsg；若 data 携凭据但业务失败则误认成功。建议 HTTP状态×业务code 矩阵和缺失/JSONObject.NULL字段测试。不要以任何非空token等同服务端确认登录。
- [JmClient.kt:108-111](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L108-L111) 只读最后 padding 长度，不验证其余 padding 字节；建议标准 PKCS7 校验与损坏密文测试。该检查不能替代认证加密/TLS认证。
- [JmClient.kt:409-413](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L409-L413) 未命中 scramble_id 默认220980；[JmScramble.kt:43-46](android/app/src/main/java/com/xd/vdl/core/parse/JmScramble.kt#L43-L46) id超Int/无效值静默返回0；[JmDownloader.kt:363-365](android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt#L363-L365) 默认值还被缓存。接口HTML变化或ID越界可能静默存错位原图，建议可见降级、严格数字范围与边界向量220979/220980/268849/268850/421925/421926，算法余数高度与 h<num 测试。
- 小红书 [XiaohongshuParser.kt:58-71](android/app/src/main/java/com/xd/vdl/core/parse/XiaohongshuParser.kt#L58-L71) 的单对象400000字符上限有帮助，但不限制整个HTML/关键字出现次数，总扫描仍可能反复扫描大量重叠对象；全量Http读取发生在该限制之前。[XiaohongshuParser.kt:69-70](android/app/src/main/java/com/xd/vdl/core/parse/XiaohongshuParser.kt#L69-L70) 还对对象原文全局替换 undefined/NaN，会误改字符串正文（如 desc="a:undefined,b"）；建议词法状态内只替换未加引号字面量，补正文保真测试。
- Instagram fallback [InstagramParser.kt:62-76](android/app/src/main/java/com/xd/vdl/core/parse/InstagramParser.kt#L62-L76) 只用 video.isBlank 做放行条件，却仅对 startsWith("https://") 的 video产出Quality：非空http/blob视频可让解析器返回空媒体（上层会拒绝，并非一定报成功）。CDN后缀 endsWith("fbcdn.net") 未要求点边界，且OG fallback不验证帖子code；建议缺失/错误协议与登录墙/跳转页面fixture。
- [QualityFilter.kt:31-38](android/app/src/main/java/com/xd/vdl/core/model/QualityFilter.kt#L31-L38) 容许5位宽高但用Int相乘，99999×99999溢出；建议 Long 面积并钳制范围。默认 keep=3 且正常标签范围下未见必然清空列表；自定义 keep<=0未校验属于接口边界。
- 没有发现以ZIP条目名解压到任意路径的链：当前官方包仅读中央目录计数，不解压，不能将“未校验URL/包内容”误报为Zip Slip。中央目录按扩展名计数不等于媒体完整性/CRC校验，该项交下载审计合并。

## 完整 read 覆盖清单

以下“完整”指逐行 read 覆盖1..EOF，不以 grep 代替；工具输出曾截断时，后续补读对应范围。链接采用工作区相对完整路径。

### 目标生产源码（14个，2706行）

| 文件 | 完整覆盖行 |
|---|---:|
| [Parser.kt:1-53](android/app/src/main/java/com/xd/vdl/core/parse/Parser.kt#L1-L53) | 1–53 |
| [BiliParser.kt:1-245](android/app/src/main/java/com/xd/vdl/core/parse/BiliParser.kt#L1-L245) | 1–245 |
| [DouyinParser.kt:1-440](android/app/src/main/java/com/xd/vdl/core/parse/DouyinParser.kt#L1-L440) | 1–440 |
| [InstagramParser.kt:1-148](android/app/src/main/java/com/xd/vdl/core/parse/InstagramParser.kt#L1-L148) | 1–148 |
| [JmParser.kt:1-210](android/app/src/main/java/com/xd/vdl/core/parse/JmParser.kt#L1-L210) | 1–210 |
| [XParser.kt:1-488](android/app/src/main/java/com/xd/vdl/core/parse/XParser.kt#L1-L488) | 1–488 |
| [XiaohongshuParser.kt:1-129](android/app/src/main/java/com/xd/vdl/core/parse/XiaohongshuParser.kt#L1-L129) | 1–129 |
| [JmClient.kt:1-550](android/app/src/main/java/com/xd/vdl/core/parse/JmClient.kt#L1-L550) | 1–550 |
| [JmAlbumZip.kt:1-60](android/app/src/main/java/com/xd/vdl/core/parse/JmAlbumZip.kt#L1-L60) | 1–60 |
| [JmScramble.kt:1-102](android/app/src/main/java/com/xd/vdl/core/parse/JmScramble.kt#L1-L102) | 1–102 |
| [Platform.kt:1-97](android/app/src/main/java/com/xd/vdl/core/Platform.kt#L1-L97) | 1–97 |
| [ClipDetect.kt:1-45](android/app/src/main/java/com/xd/vdl/core/ClipDetect.kt#L1-L45) | 1–45 |
| [VideoInfo.kt:1-73](android/app/src/main/java/com/xd/vdl/core/model/VideoInfo.kt#L1-L73) | 1–73 |
| [QualityFilter.kt:1-66](android/app/src/main/java/com/xd/vdl/core/model/QualityFilter.kt#L1-L66) | 1–66 |

### 相关测试（7个，494行）

- [ShareLinkRoutingTest.kt:1](android/app/src/test/java/com/xd/vdl/core/ShareLinkRoutingTest.kt#L1)：1–34完整读取。
- [DouyinNoteRoutingTest.kt:1](android/app/src/test/java/com/xd/vdl/core/DouyinNoteRoutingTest.kt#L1)：1–29完整读取。
- [DouyinHookInjectionTest.kt:1](android/app/src/test/java/com/xd/vdl/core/parse/DouyinHookInjectionTest.kt#L1)：1–128完整读取。
- [JmAlbumZipTest.kt:1](android/app/src/test/java/com/xd/vdl/core/parse/JmAlbumZipTest.kt#L1)：1–76完整读取。
- [JmLoginResultTest.kt:1](android/app/src/test/java/com/xd/vdl/core/parse/JmLoginResultTest.kt#L1)：1–93完整读取。
- [JmOfficialZipCheckTest.kt:1](android/app/src/test/java/com/xd/vdl/core/download/JmOfficialZipCheckTest.kt#L1)：1–65完整读取。
- [JmConcurrencyTest.kt:1](android/app/src/test/java/com/xd/vdl/core/download/JmConcurrencyTest.kt#L1)：1–69完整读取。

测试发现：没有搜索到 Bili/X/Instagram/JmScramble/QualityFilter 的独立JVM解析测试；小红书只有路由测试，抖音只有页面顺序/脚本与预算测试，没有上述媒体业务/取消/资源用例。未发现 androidTest 文件。脚本导出测试会写 build/tmp，故本次未运行，避免违反唯一可写报告限制。

### 补充调用链读取（不计入14个目标文件）

- [Http.kt:1-333](android/app/src/main/java/com/xd/vdl/core/net/Http.kt#L1-L333)：1–333完整读取。
- [JmDownloader.kt:1-436](android/app/src/main/java/com/xd/vdl/core/download/JmDownloader.kt#L1-L436)：1–436完整读取。
- [Downloader.kt:1-60](android/app/src/main/java/com/xd/vdl/core/download/Downloader.kt#L1-L60)：仅1–60。
- [AppViewModel.kt:70-154](android/app/src/main/java/com/xd/vdl/ui/AppViewModel.kt#L70-L154)：仅70–154。
- [DownloadManager.kt:90-139](android/app/src/main/java/com/xd/vdl/core/DownloadManager.kt#L90-L139)：仅90–139。

本报告只写入审计目录的指定Markdown文件，未触碰Windows端或Android源码未提交修改。下一步建议优先合并PAR-01/02与网络审计结论，再补本地fixture回归；在用户另行授权前不实施修改。
