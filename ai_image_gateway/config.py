"""
配置加载与管理。

支持 YAML 配置文件 + 环境变量覆盖。
凭证字段使用 ${ENV_VAR} 语法引用环境变量，运行时自动替换。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .errors import ConfigError


# ---------------------------------------------------------------------------
# Config Models
# ---------------------------------------------------------------------------

class ProviderAuthConfig(BaseModel):
    """Provider 认证配置 (具体字段由各 provider 自行解读)。"""
    model_config = {"extra": "allow"}


class ProviderConfig(BaseModel):
    """单个 Provider 的配置。"""

    enabled: bool = True
    auth: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)


class DefaultProviderConfig(BaseModel):
    """各能力的默认 provider 路由。"""

    generate: str = "mock"
    image_to_image: str = "mock"
    inpaint: str = "mock"
    upscale: str = "mock"


class LoggingConfig(BaseModel):
    """日志配置。"""

    level: str = "INFO"
    file: str | None = None


class GatewayConfig(BaseModel):
    """Gateway 顶层配置。"""

    default_provider: DefaultProviderConfig = Field(default_factory=DefaultProviderConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


# ---------------------------------------------------------------------------
# 环境变量替换
# ---------------------------------------------------------------------------

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env_vars(value: Any) -> Any:
    """递归替换配置值中的 ${ENV_VAR} 引用。"""
    if isinstance(value, str):
        def _replacer(match: re.Match) -> str:
            env_key = match.group(1)
            env_val = os.environ.get(env_key)
            if env_val is None:
                # 环境变量不存在时保留原始占位符，不崩溃
                return match.group(0)
            return env_val
        return _ENV_PATTERN.sub(_replacer, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# 加载入口
# ---------------------------------------------------------------------------

def load_config(path: str | Path | None = None) -> GatewayConfig:
    """
    加载 Gateway 配置。

    优先级: 指定路径 > 当前目录 config.yaml > 纯默认值。
    """
    if path is not None:
        config_path = Path(path)
        if not config_path.exists():
            raise ConfigError(f"Config file not found: {config_path}")
    else:
        config_path = Path("config.yaml")
        if not config_path.exists():
            # 无配置文件时使用纯默认值 (仅 MockProvider 可用)
            return GatewayConfig()

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse config: {e}") from e

    if raw is None:
        return GatewayConfig()

    resolved = _resolve_env_vars(raw)
    return GatewayConfig.model_validate(resolved)
