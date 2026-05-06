"""
统一异常体系。

所有 gateway 内部异常均继承自 GatewayError，
上层调用方只需捕获 GatewayError 即可统一处理。
"""

from __future__ import annotations


class GatewayError(Exception):
    """Gateway 基础异常。"""


class ConfigError(GatewayError):
    """配置加载或校验错误。"""


class ProviderNotFoundError(GatewayError):
    """请求的 provider 未注册或未启用。"""

    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name
        super().__init__(f"Provider not found or not enabled: '{provider_name}'")


class ProviderCapabilityError(GatewayError):
    """Provider 不支持请求的能力。"""

    def __init__(self, provider_name: str, capability: str) -> None:
        self.provider_name = provider_name
        self.capability = capability
        super().__init__(
            f"Provider '{provider_name}' does not support capability: '{capability}'"
        )


class ProviderError(GatewayError):
    """Provider 调用过程中发生的错误 (网络、认证、限流等)。"""

    def __init__(self, provider_name: str, detail: str, cause: Exception | None = None) -> None:
        self.provider_name = provider_name
        self.detail = detail
        self.__cause__ = cause
        super().__init__(f"[{provider_name}] {detail}")


class RateLimitError(ProviderError):
    """Provider 限流。"""

    def __init__(self, provider_name: str, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        detail = "Rate limited"
        if retry_after is not None:
            detail += f", retry after {retry_after:.1f}s"
        super().__init__(provider_name, detail)
