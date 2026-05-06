"""Config 加载测试。"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from ai_image_gateway.config import GatewayConfig, load_config
from ai_image_gateway.errors import ConfigError


class TestLoadConfig:
    def test_default_config_no_file(self, tmp_path, monkeypatch):
        """无配置文件时返回纯默认值。"""
        monkeypatch.chdir(tmp_path)
        cfg = load_config(None)
        assert isinstance(cfg, GatewayConfig)
        assert cfg.default_provider.generate == "mock"

    def test_load_from_yaml(self, tmp_path):
        """从 YAML 文件加载。"""
        cfg_data = {
            "default_provider": {"generate": "novelai", "inpaint": "gemini"},
            "providers": {
                "novelai": {
                    "enabled": True,
                    "auth": {"username": "test@example.com"},
                    "settings": {"use_nai4": True},
                },
            },
        }
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.dump(cfg_data), encoding="utf-8")

        cfg = load_config(cfg_path)
        assert cfg.default_provider.generate == "novelai"
        assert cfg.providers["novelai"].auth["username"] == "test@example.com"
        assert cfg.providers["novelai"].settings["use_nai4"] is True

    def test_env_var_resolution(self, tmp_path, monkeypatch):
        """${ENV_VAR} 被正确替换。"""
        monkeypatch.setenv("TEST_API_KEY", "secret_key_123")
        cfg_data = {
            "providers": {
                "gemini": {
                    "auth": {"api_key": "${TEST_API_KEY}"},
                },
            },
        }
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.dump(cfg_data), encoding="utf-8")

        cfg = load_config(cfg_path)
        assert cfg.providers["gemini"].auth["api_key"] == "secret_key_123"

    def test_missing_env_var_preserved(self, tmp_path):
        """未设置的环境变量保留占位符。"""
        cfg_data = {
            "providers": {
                "gemini": {
                    "auth": {"api_key": "${NONEXISTENT_KEY_12345}"},
                },
            },
        }
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.dump(cfg_data), encoding="utf-8")

        cfg = load_config(cfg_path)
        assert cfg.providers["gemini"].auth["api_key"] == "${NONEXISTENT_KEY_12345}"

    def test_file_not_found(self):
        with pytest.raises(ConfigError):
            load_config("/nonexistent/path/config.yaml")
