"""小红书 PC 签名直连后端（vendor 自 Spider_XHS，裁剪到笔记详情路径）。

签名算法唯一实现位于 ``js/``（Node 运行），Python 侧只负责组装请求与传输。

出处与许可
----------
上游：https://github.com/cv-cat/Spider_XHS
本目录是它的**派生裁剪版**：只保留「笔记详情」这一条路径（``get_note_info``
+ bootstrap/get_user_me + 签名材料），去掉了原项目的私信 / 直播 / 运营等模块，
并把日志从 loguru 换成 stdlib。

**注意：上游仓库未声明任何许可证**（无 LICENSE 文件，GitHub 的 license 接口
返回 404）。未声明许可证 = 默认保留所有权利，因此这段派生代码的再分发授权
状态是**不明确的**。本仓库目前是个人自用、公开但无许可证的状态；
若将来要给本仓库加许可证或做任何形式的对外分发，**必须先解决这一条**：
要么取得上游授权，要么替换掉本目录的实现。

上游标识保留情况：原代码里的 ``xhs_utils`` 包名在本仓库已改为
``app.core.xhsapi``，相关注释与示例已同步修正，避免出现指向不存在模块的引用。
"""
from .apis import XHS_Apis
from .xhs_pc import XHSAuth, XHSPcAuth

__all__ = ['XHS_Apis', 'XHSAuth', 'XHSPcAuth']
