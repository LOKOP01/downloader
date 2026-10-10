# 视频下载器图标

设计：深海蓝圆角底板、白色下载箭头、箭头内镂空播放符号、接收托盘。
播放与下载组合表达视频留存；较粗的轮廓适合标题栏、任务栏与手机桌面。
沿用现有桌面设计系统的深海蓝 `#1A4A8E` 和前景白 `#FAFAFA`。
无文字、渐变、阴影或发光，底板外保留透明像素。

## 交付文件

- `app-v2.svg`：可编辑的 1024 × 1024 矢量源文件。
- `app-v2.png`：1024 × 1024 RGBA 图片。
- `app-v2.ico`：16、20、24、32、40、48、64、96、128、256 像素 Windows 图标。
- `app-v2-preview.png`：浅色、深色背景与实际像素尺寸预览。
- `../../android/app/src/main/res/` 中带 `v2` 名称的资源：五档密度的常规／圆形图标，Android 8+ 自适应图标，以及 Android 13+ 主题图标。

图标由 SVG 直接绘制和渲染，未使用 AI 图片生成或 API。
旧图标保留，新版使用独立文件名；桌面运行、EXE 打包、安装包和 Android Manifest 已引用新版。
现有已打包的 EXE／APK 需要重新构建才会采用新图标。

## 重新导出

在 `douyin-downloader` 目录运行：

```powershell
& .\.venv\Scripts\python.exe .\assets\render_icon.py
```

使用项目已有的 PySide6 与 Pillow，无额外依赖。
编辑 SVG 后重新执行，会同步更新所有新版资源。
