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
| 禁漫天堂 | 图片自动解码（AVS 混淆） |

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
│   │   │   ├── ffmpeg.py       # 音视频合并组件管理
│   │   │   ├── browser_bridge.py  # Playwright 驱动本机 Edge 取 Cookie
│   │   │   └── jmcomic_bridge.py  # 禁漫图片解码
│   │   ├── view/               # 界面（qfluentwidgets）
│   │   └── main_window.py
│   ├── assets/                 # 图标资源
│   ├── installer/              # Inno Setup 安装包脚本
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

### 使用

1. 粘链接，程序识别站点并解析，选画质，下载。
2. 下载完成后可自动复制文件到剪贴板（配置项 `auto_copy_file`）。
3. 需要登录的站点（X / Instagram / 小红书 / 禁漫）在设置页填 Cookie，或用内置的浏览器登录流程导出。
4. B 站 DASH 合并需要 ffmpeg，在「设置 → 合并组件（ffmpeg）」里下载，不是 pip 依赖。
5. 解析失败或下载卡住，看 `logs/app.log`。

### 配置文件 `config.json`

```jsonc
{
  "download_path": "",              // 留空则用 <用户目录>/Downloads/DouyinDownloader
  "cookie": "",                     // 抖音 Cookie
  "x_cookie": "",                   // X(Twitter)
  "ins_cookie": "",                 // Instagram
  "bili_cookie": "",                // Bilibili
  "xhs_cookie": "",                 // 小红书
  "jm_cookie": "",                  // 禁漫天堂（AVS）
  "naming_rule": "timestamp",       // timestamp / author_title / title / id_title
  "max_concurrent": 3,              // 同时下载数
  "theme": "dark",                  // dark / light / auto
  "accent_color": "indigo",         // 界面强调色
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
- Cookie 按平台分开存，与桌面端是同一套填写方式。

## 几点说明

- Cookie 在桌面端明文存在 `config.json`，安卓端存在 `SharedPreferences`（`vdl_cookies`），都不加密。
- 桌面端用 Playwright 驱动本机 Edge 时会生成 `edge_profile/`，里面是登录态，已在 `.gitignore` 排除。
- `JmClient.kt` / `jmcomic_bridge.py` 里的 `SECRET`（如 `185Hcomic3PAPP7R`）是该 App 通信协议的公开逆向常量，不是个人凭据。

## 免责

个人学习与备份用途，解析能力都来自各平台公开的网页接口。下载、存储、传播内容的责任由使用者自负。
