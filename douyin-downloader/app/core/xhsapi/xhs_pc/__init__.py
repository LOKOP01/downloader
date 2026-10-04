"""Public XHS PC authentication and signing-state API.

Package layout:

- ``auth``: user inputs and parameter ownership.
- ``state``: browser/device/session lifecycle.
- ``params``: request parameter assembly.
- ``runtime``: Node adapter; no duplicate Python crypto implementation.
- ``dsl``: remote DS anchor retrieval.
- ``js``: the only cryptographic algorithm implementation.
"""

from .auth import PC_PARAMETER_SOURCES, XHSAuth, XHSPcAuth
from .http import PcHttpClient
from .state import B1RuntimeState, PcDeviceProfile

__all__ = [
    'PC_PARAMETER_SOURCES',
    'XHSAuth',
    'XHSPcAuth',
    'PcHttpClient',
    'B1RuntimeState',
    'PcDeviceProfile',
]
