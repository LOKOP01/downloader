# 视频下载器

个人自用的多站点视频 / 图片下载工具，分 Windows 桌面端（Python + PySide6）和 Android 端（Kotlin + Compose）。

仅供个人学习与备份使用。

## 支持的站点

| 站点 | 说明 |
|---|---|
| 抖音 | 去水印、作者主页批量 |
| B 站 | DASH 音视频自动合并 |
| 小红书 | 需用 App 分享的链接（含 `xsec_token`） |
| X (Twitter) | 敏感内容推文需填 `auth_token` / `ct0` |
| Instagram | 需登录的内容要填 Cookie |
| Iwara | |
| Pornhub | |
| hanime1 | |
| 禁漫天堂 | 种子号解析 |

> 上表是**桌面端**的完整列表。安卓端少三个：Iwara / Pornhub / hanime1 目前只有桌面端支持。

## 目录结构

```
.
├── douyin-downloader/          # Windows 桌面端（Python + PySide6）
│   ├── main.py                 # 入口
│   ├── app/
│   │   ├── core/               # 核心逻辑：解析、下载、配置、日志
│   │   │   ├── sites.py        # 各站点解析规则（主要体量在这里）
│   │   │   ├── downloader.py   # 分段下载（IDM 式多连接）
│   │   │   ├── parser.py       # 链接识别与路由
│   │   │   ├── domain.py       # 各站点域名集合与 Referer 规则
│   │   │   ├── site_ctx.py     # 站点上下文（Cookie / profile 目录 / 选项）
│   │   │   ├── models.py       # 数据模型（VideoInfo 等）
│   │   │   ├── manga.py        # 本地漫画书架、自然排序、阅读进度与书架迁移
│   │   │   ├── config.py       # 配置读写（DEFAULT_CONFIG 是默认值唯一来源）
│   │   │   ├── workers.py      # 后台线程（登录、预览、封面）
│   │   │   ├── proxy.py        # 系统代理读取
│   │   │   ├── ffmpeg.py       # 音视频合并组件管理
│   │   │   ├── browser_bridge.py  # Playwright 驱动本机 Edge 取 Cookie
│   │   │   ├── jmcomic_bridge.py  # 禁漫图片解码
│   │   │   └── xhsapi/         # 小红书签名直连（vendor，见该目录 __init__ 的许可说明）
│   │   ├── view/               # 界面（qfluentwidgets）
│   │   └── main_window.py
│   ├── assets/                 # 图标资源
│   ├── installer/              # Inno Setup 安装包脚本
│   ├── reference/              # 第三方参考实现，不参与构建（见其 README）
│   ├── tests/                  # 单元测试
│   ├── build_exe.spec          # PyInstaller 打包配置
│   ├── app_version.txt         # 版本号唯一来源（exe 版本资源）
│   ├── requirements.txt
│   └── config.json             # 运行时配置（首次运行自动生成）
│
└── android/                    # Android 端（Kotlin + Jetpack Compose）
    └── app/src/main/java/com/xd/vdl/
        ├── core/
        │   ├── parse/          # 各站点解析器（与桌面端同一套思路）
        │   ├── download/       # 下载、分段、音视频合并、媒体库导出
        │   └── net/            # HTTP 与 Cookie 管理
        └── ui/                 # Compose 界面
```

仓库只提交源码。`dist/`、`build/`、`.venv/`、`edge_profile/`、`*.exe`、`*.apk` 等构建产物和敏感目录都在 `.gitignore` 里，需要自行构建。

## 桌面端

### 环境要求

- Windows 10 / 11
- Python 3.10+（开发时用 3.13）
- 本机已安装 Microsoft Edge。Playwright 直接调用本机 Edge，不需要 `playwright install chromium`

### 安装依赖

```bash
cd douyin-downloader
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 运行

```bash
python main.py
```

或双击 `run.bat`（用 `pythonw.exe` 启动，不弹控制台窗口）。

### 打包成 exe

```bash
pyinstaller build_exe.spec
```

产物在 `dist/` 下。安装包用 `installer/setup.iss` 配合 Inno Setup 编译。

若单文件 EXE 提示 `Could not create temporary directory!`，说明启动解包所需的系统临时目录不可用。可构建不依赖启动时临时解包的文件夹版（PowerShell）：

```powershell
$env:VDL_BUILD_MODE = "onedir"
python -m PyInstaller --noconfirm --distpath dist/portable-build build_exe.spec
```

文件夹版的 EXE 必须与 `_internal/` 一起保留，不能只复制 EXE。程序运行期临时文件使用自身目录的 `.runtime-temp/`，只改变本进程及其子进程环境，不修改Windows用户环境变量。

### 使用

1. 粘链接，程序识别站点并解析，选画质，下载。
2. 下载完成后可自动复制文件到剪贴板（配置项 `auto_copy_file`）。
3. 需要登录的站点（X / Instagram / 小红书 / 禁漫）在设置页填 Cookie，或用内置的浏览器登录流程导出。
4. B 站 DASH 合并需要 ffmpeg，在「设置 → 合并组件（ffmpeg）」里下载，不是 pip 依赖。
5. 解析失败或下载卡住，看 `logs/app.log`。

### 漫画阅读器（Windows）

顶部「漫画阅读器」提供本地漫画书架，不需要另外安装阅读器或 .NET 运行环境。

- **添加漫画**：选择一本漫画的文件夹，每张 `.jpg` / `.jpeg` 是一页（支持大写扩展名与章节子目录）。
- **导入漫画库**：选择总目录，每个子文件夹作为一本漫画。可将一本或多本的文件夹直接拖进书架。
- 页面按相对路径自然排序，例如 `第2章/2.jpg` 在 `第2章/10.jpg`、`第10章/1.jpg` 之前。
- 点击封面继续阅读；支持搜索、最近阅读、收藏、按名称或进度排序，右键可移除记录，原图片保留。
- 禁漫任务下载成功后自动加入漫画书架（不自动弹窗），保留已有收藏与阅读进度；失败和取消不入库，书架忙时排队处理。
- 下载任务里的独立文件夹 JPG 图集、禁漫任务仍提供「阅读漫画」按钮；视频封面不会把整个下载目录误导入。
- 「更多 → 导入独立页间阅读器书架」可选择 `%LOCALAPPDATA%\FolderMangaReader\library.json`，导入此前独立版的收藏与进度；已有记录不会被覆盖。
- 新增或移除图片后可刷新书架；每次打开漫画也会重新扫描。
- 当前阅读器支持 JPG/JPEG 文件夹，不直接读取 ZIP/CBZ；单张源图超过 1600 万像素会明确提示，避免解码内存失控。
- 多实例同时修改书架时会拒绝陈旧快照保存并提示重新加载，不会静默覆盖另一实例的新记录。

阅读快捷键：左右键、PageUp / PageDown 翻页；Home / End 跳到首尾；空格向下滚一屏，到底后翻页；Ctrl + 滚轮、`+` / `-` 缩放，`0` 恢复适配。F11 或双击全屏，Tab 隐藏工具栏，Esc 退出全屏或返回书架。「适合宽度」用于长图，「从右到左」交换左右键和图片两侧的翻页方向。

书架、进度与阅读偏好保存在程序目录的 `manga_library.json`，使用原子替换并保留上一份有效备份 `manga_library.json.bak`。损坏记录会尝试备份恢复，原漫画图片不会被修改。书架包含个人本地路径，已加入 `.gitignore`。移动漫画文件夹后请重新添加新路径，再移除旧记录。

漫画功能复用 PySide6 与项目现有 UI 组件，无新增运行依赖。相关测试：

```bash
cd douyin-downloader
python -m unittest discover -s tests -p "test_manga*.py" -v
```

### 配置文件 `config.json`

下列默认值与 `app/core/config.py` 的 `DEFAULT_CONFIG` 保持一致（那里是默认值的唯一来源）：

```jsonc
{
  "download_path": "",              // 留空则用 <用户目录>/Downloads/DouyinDownloader
  "cookie": "",                     // 抖音 Cookie
  "x_cookie": "",                   // X(Twitter)
  "ins_cookie": "",                 // Instagram
  "bili_cookie": "",                // Bilibili
  "xhs_cookie": "",                 // 小红书
  "jm_cookie": "",                  // 禁漫天堂（AVS）
  "naming_rule": "unique_id",       // unique_id / timestamp / author_title / title / id_title
  "max_concurrent": 3,              // 同时下载数
  "theme": "dark",                  // dark / light / auto
  "accent_color": "deepsea",        // 界面强调色（见 view/design.ACCENTS，共 9 套）
  "create_author_folder": true,     // 按作者建子目录
  "download_cover": false,          // 同时下载封面
  "auto_copy_file": true,           // 下载完成后复制文件到剪贴板
  "mica_effect": false,             // Win11 云母特效（会降低滚动流畅度）
  "reduce_motion": false,           // 关闭界面动效
  "timeout": 15,
  "segment_download": true,         // 多连接分段下载（IDM 式加速）
  "segment_connections": 8          // 每文件并发连接数，1-32
}
```

`config.json` 里会存 Cookie，不要提交到公开仓库。

## Android 端

### 环境要求

- Android SDK 34（`compileSdk` / `targetSdk` = 34，`minSdk` = 24，即 Android 7.0+）
- JDK 17
- Gradle 8.7（用仓库自带的 `gradlew` 即可）

首次构建前，在 `android/local.properties` 里指定 SDK 路径：

```properties
sdk.dir=C\:\\Users\\<你的用户名>\\AppData\\Local\\Android\\Sdk
```

### 构建

```bash
cd android
./gradlew assembleDebug          # Windows 用 gradlew.bat
```

APK 输出在 `android/app/build/outputs/apk/`。

### 使用

- 主要入口是系统分享：在抖音 / B 站 / 小红书等 App 里点「分享 → 视频下载器」。
- 界面是 Compose，分首页、任务列表、设置三页。
- 下载完成后通过 MediaStore 导出到系统相册。
- Cookie 按平台分开存；B站可在设置里使用「网页登录（自动保存登录态）」，完成官方页面登录并通过服务端校验后保存，无需手动复制。
- B站登录不改变视频本身及账号会员画质权限；其他平台仍支持手动填写Cookie。

## 几点说明

- Cookie 在桌面端明文存在 `config.json`，安卓端存在 `SharedPreferences`（`vdl_cookies`），都不加密。
- 桌面端用 Playwright 驱动本机 Edge 时会生成 `edge_profile*/`（每个平台一个），里面是登录态，已在 `.gitignore` 排除。
  新增平台时记得同步 `core/config.py` 的属性与 `installer/setup.iss` 卸载逻辑，否则卸载后会残留登录凭据。
- `JmClient.kt` / `jmcomic_bridge.py` 里的 `SECRET`（如 `185Hcomic3PAPP7R`）是该 App 通信协议的公开逆向常量，不是个人凭据。
- 桌面端支持多连接分段下载；若需要代理的站点报 `CONNECT tunnel failed, response 502`，
  多半是本地代理端口变了而系统代理没跟着更新 —— `core/proxy.py` 会自动探测可用端口。

## 第三方代码与许可

本仓库**未声明许可证**（个人自用项目，保留所有权利）。未授权任何人使用、复制、
修改或分发本仓库内容。仓库内另含以下第三方代码，授权状态各不相同：

| 位置 | 来源 | 许可证 | 参与构建 |
|---|---|---|---|
| `douyin-downloader/app/core/xhsapi/` | [cv-cat/Spider_XHS](https://github.com/cv-cat/Spider_XHS)（派生裁剪） | **上游未声明** | 是 |
| `douyin-downloader/reference/ref_xbogus.py` | [Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) | Apache-2.0 | 否（仅参考） |
| `douyin-downloader/installer/ChineseSimplified.isl` | [Inno Setup 简体中文翻译](https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation)（文件头保留维护者信息） | 随上游 | 是 |

> **`xhsapi/` 的授权状态不明确**：上游仓库没有 LICENSE 文件（GitHub 的 license 接口返回 404），
> 未声明许可证即默认保留所有权利。本仓库目前是个人自用，但**若要对外分发或给本仓库加许可证，
> 必须先解决这一条** —— 详见 `app/core/xhsapi/__init__.py` 里的说明。

## 免责

个人学习与备份用途，解析能力都来自各平台公开的网页接口。下载、存储、传播内容的责任由使用者自负。
