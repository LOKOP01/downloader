# reference/ — 第三方参考实现（不参与构建）

本目录**不参与打包，也没有任何代码 import 它**，仅作为算法溯源保留。

## ref_xbogus.py

- **来源**：[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)，
  Copyright (C) 2021 Evil0ctal，**Apache License 2.0**（文件头的许可证声明保持原样，请勿删除）。
- **内容**：抖音 `X-Bogus` 请求签名的纯 Python 参考实现。
- **为什么留着**：本项目当前解析抖音走的是「分享页 `RENDER_DATA`」路线，
  **并不需要 `X-Bogus`**（见 `app/core/sites.py` 的抖音分支）。但若将来抖音收紧
  接口、需要走 `aweme/detail` 这类要签名的接口，这份实现是可以直接对照的参考，
  也记录了签名算法的来源与授权，删掉反而丢失溯源信息。
- **注意**：它是**参考实现**，不是本项目的依赖。不要 `import reference.ref_xbogus`，
  也不要在 `build_exe.spec` 里加进打包（当前 `build_exe.spec` 未包含它）。

> 与 `app/core/xhsapi/` 的区别：那个目录是**真正参与构建**的 vendor 代码
> （小红书签名直连，被 `sites.py` 延迟 import），其上游未声明许可证，
> 授权状态见 `app/core/xhsapi/__init__.py` 的说明。
