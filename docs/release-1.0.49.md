# 1.0.49 发布与清理记录

日期：2026-10-17

## 提交与推送

- 提交：`99a0d41` feat(android): 本地漫画书架与离线阅读，修复禁漫多章少页 → 1.0.49（44 文件，+5061/−35）
- 推送目标（均成功）：
  - GitHub `origin/main`、`origin/audit/android-full-20261015`
  - Gitee `gitee/main`（c535e91..99a0d41）、`gitee/audit/android-full-20261015`
- 标签：`android-v1.0.49`（注释标签，已推送）

## Release

- https://github.com/LOKOP01/downloader/releases/tag/android-v1.0.49
- 状态：已发布（非草稿、非预发布）
- 附件：
  - `VideoDownloader-Android-1.0.49.apk`（17,612,732 字节）
  - `SHA256SUMS.txt`
- APK SHA256：`2B715EE9A36F9BBD5B8F2367EA9C22A4BA61204BDA68CA057A01B47D26F1B224`

## 清理的旧安装包

删除前已记录校验值（APK 不在版本控制内，删除后不可从 git 恢复）：

| 文件 | 字节 | SHA256 | 是否有对应 Release |
|---|---|---|---|
| 视频下载器-1.0.44.apk | 17,793,537 | BBB823827AFD0B8F6CD9FBAECDDB4EB66CE6D95B71444139B15653199A1AA0D0 | 有（v1.8.10.34） |
| 视频下载器-1.0.45.apk | 17,465,214 | FC9AAD04A1F4E6B8A548AEC1D9B6873BD7B2D0364270CF26103660C2BAC2B79D | **无** |
| 视频下载器-1.0.46.apk | 17,465,214 | AAB0DCCCF641B7C1C9F15D41B0D5A7D7CCE89A98159589C0F84B813FAD9EF55C | **无** |

1.0.45 与 1.0.46 从未发布：git 历史里没有对应版本号提交，也没有 Release 记录；
两者字节数完全相同，推测是同一份产物被复制成了两个版本名。已按用户要求一并清理。

保留：`视频下载器-1.0.49.apk`（当前构建）与 `release-notes/assets/`（本次上传的副本）。

## 未清理（避免误删）

以下内容不属于「旧安装包」，未触碰：

- `douyin-downloader/dist/**` 下的桌面端 exe 与构建目录（含 `.budget-fix`、`.directory-fix`、`.shell-fix`、`portable-build` 等历史构建）
- 工作区里未跟踪的调试脚本 `douyin-downloader/_*.py` 与 `cfg_chk_*/` 目录（非本次改动产生）
