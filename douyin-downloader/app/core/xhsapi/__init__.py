"""小红书 PC 签名直连后端（vendor 自 Spider_XHS，裁剪到笔记详情路径）。

签名算法唯一实现位于 ``js/``（Node 运行），Python 侧只负责组装请求与传输。
"""

from .apis import XHS_Apis
from .xhs_pc import XHSAuth, XHSPcAuth

__all__ = ['XHS_Apis', 'XHSAuth', 'XHSPcAuth']
