# Android 本地漫画书架与离线阅读（1.0.47）

实现日期：2026-10-17　　交付物：`视频下载器-1.0.47.apk`

## 一、现状（改动前）

Android 端原本已经具备完整的下载链路，但**下载完的东西在应用内留不住**：

| 能力 | 改动前状态 |
|---|---|
| 禁漫下载 | ✅ 逐张下载并打包成 `<车号>.zip`，写到 `Download/<目录>/` |
| 图集下载 | ✅ 逐张保存进相册，任务里保留 URI 列表 |
| 应用内预览 | ✅ `PreviewDialog` 支持视频 / 图片横滑翻页 / zip 就地解压翻页 |
| **下载后找得到** | ❌ `DownloadTask` 只是内存里的 `StateFlow`，**进程重启即清空** |
| **本地书架** | ❌ 不存在 |
| **阅读进度** | ❌ 不存在（预览每次都从第 1 页） |
| **离线打开** | ⚠️ 仅当任务还在内存里、且用户记得点预览 |

因此「下载一本本子 → 关掉应用 → 第二天想再看」这个最普通的场景是走不通的。

## 二、改了什么

### 新增（`app/src/main/java/com/xd/vdl/core/manga/`）

| 文件 | 职责 |
|---|---|
| `NaturalSort.kt` | 文件名自然排序，与桌面端 `app/core/manga.py` 的 `natural_key` 逐条等价 |
| `MangaBook.kt` | 书籍模型 + JSON 序列化 + 进度恢复（`restoreIndex` / `markRead`）+ 稳定 key |
| `MangaStore.kt` | 单文件 JSON 书架：原子替换、`.bak` 备份、损坏留档、读失败禁止保存 |
| `MangaLibrary.kt` | 应用级单例：内存快照 + 串行落盘 + 阅读进度 + 扫描下载目录 |
| `MangaFiles.kt` | 已下载文件的统一访问（`content://` 与 `file://` 都吃） |
| `ZipUnpacker.kt` | 解包核心（纯逻辑、可单测）：防 zip slip、保序、整页完整性、上限告知 |
| `ChapterSplitter.kt` | 按 zip 内第一级目录划分章节（纯函数） |
| `ZipPageCache.kt` | 页缓存：解包到 `cacheDir/manga-pages/<key>/` + manifest，翻页只读缓存 |
| `MangaSources.kt` | 页源抽象（`ZipPageSource` / `ImageListPageSource`）+ 工厂 |
| `MangaIntake.kt` | 「下载完成 → 书架记录」转换（纯函数，可单测） |
| `MangaSort.kt` | 书架排序（最近阅读 / 加入时间 / 标题 / 进度）与搜索 |

### 新增 UI

- `ui/LibraryScreen.kt` —— 「书架」页：卡片列表、缩略图、收藏、搜索、排序、移除、清理缓存。
- `ui/LocalMangaReader.kt` —— 本地阅读器：翻页 / 双击与双指缩放 / 复位 / 重试 / 进度自动保存。

### 修改

| 文件 | 改动 |
|---|---|
| `core/DownloadManager.kt` | 禁漫与图集在导出成功后入库；目录名取 MediaStore 实际值 |
| `core/DownloadTask.kt` | 新增 `readableAsManga`：只有 `DONE` **且确有产物**才算漫画 |
| `core/AppLog.kt` | `android.util.Log` 失败时吞掉，便于 JVM 单测运行同一条链路 |
| `ui/TasksScreen.kt` | 完成态卡片加「去书架阅读（可离线）」按钮 |
| `ui/AppViewModel.kt` | 新增 `openLibrary()` 跳转请求 |
| `MainActivity.kt` | 初始化书架、底部导航加「书架」tab |
| `build.gradle.kts` | 版本 1.0.46 → 1.0.47（versionCode 48） |
| `README.md` | 补「本地漫画书架（离线阅读）」章节 |

## 三、数据与兼容性

```
filesDir/manga_library.json          书架主文件（原子替换）
filesDir/manga_library.json.bak      上一份有效备份
filesDir/manga_library.json.corrupt  损坏时的原始留档
cacheDir/manga-pages/<uri尾段-大小>/ 解包出的页（manifest.json + 00000.bin…）
```

字段名与桌面端 `manga_library.json` 对齐（`page_index` / `page_count` / `last_page` /
`last_read` / `favorite` / `preferences`），未来两端可互读。

**不迁移、不改名、不搬运任何已下载文件**，也不新增任何 Android 权限。

## 四、验证

```bash
cd android
export JAVA_HOME=/c/Users/18752/.workbuddy/binaries/jdk17/jdk-17.0.2
./gradlew.bat --no-daemon clean assembleDebug testDebugUnitTest lintDebug
```

- 单元测试：**194 个用例全部通过**（原有 108 + 本次新增 86）。
- Lint：`BUILD SUCCESSFUL`；无 Error/Fatal，仅 2 条 Warning ——
  `CustomX509TrustManager`（禁漫 SSL 放行，既有设计）与 `IconLauncherShape`（既有图标）。

  本次新增的 86 个用例分布：

  | 测试类 | 用例数 | 覆盖 |
  |---|---|---|
  | `core/manga/NaturalSortTest` | 11 | 自然排序（含与桌面端 Python 逐条比对） |
  | `core/manga/MangaBookTest` | 14 | 序列化往返、进度恢复、稳定 key |
  | `core/manga/MangaStoreTest` | 12 | 原子写入、备份、损坏留档、读失败禁止保存、合并 |
  | `core/manga/MangaSorterTest` | 7 | 四种排序与搜索 |
  | `core/manga/ZipUnpackerTest` | 14 | zip slip、页序、整页完整性、上限告知 |
  | `core/manga/MangaIntakeTest` | 10 | 入库转换、key 冲突、单图不入库 |
  | `core/manga/MangaLibraryIntakeTest` | 6 | 入库/进度/移除（不清缓存路径） |
  | `core/manga/LocalReadingFlowTest` | 5 | 下载→入库→重启→断网续读 全链路 |
  | `core/DownloadTaskMangaTest` | 7 | **什么算下载成功**（失败/取消/部分下载不进书架） |
- 本机**无 adb 设备**，真机行为未验证；已用 dex 核对确认新代码进入产物。
