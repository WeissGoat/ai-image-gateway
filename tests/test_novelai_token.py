from pathlib import Path

from ai_image_gateway.auth.novelai_token import (
    load_access_token_from_client_py,
    resolve_novelai_access_token,
)


def _write_client(path: Path, token: str = "pst-test-token") -> None:
    path.write_text(
        "\n".join([
            "class NAIClient:",
            "    async def get_access_token(self):",
            f"        return {token!r}",
            "",
        ]),
        encoding="utf-8",
    )


def test_configured_token_wins(monkeypatch, tmp_path):
    client_py = tmp_path / "client.py"
    _write_client(client_py, "pst-from-client")
    monkeypatch.setenv("NAI_ACCESS_TOKEN", "pst-from-env")
    monkeypatch.setenv("NAI_CLIENT_PY", str(client_py))

    assert resolve_novelai_access_token("pst-from-config") == "pst-from-config"


def test_env_token_wins_over_client_py(monkeypatch, tmp_path):
    client_py = tmp_path / "client.py"
    _write_client(client_py, "pst-from-client")
    monkeypatch.setenv("NAI_ACCESS_TOKEN", "pst-from-env")
    monkeypatch.setenv("NAI_CLIENT_PY", str(client_py))

    assert resolve_novelai_access_token("${NAI_ACCESS_TOKEN}") == "pst-from-env"


def test_loads_literal_return_from_client_py(monkeypatch, tmp_path):
    client_py = tmp_path / "client.py"
    _write_client(client_py, "pst-from-client")
    monkeypatch.delenv("NAI_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("NAI_CLIENT_PY", str(client_py))

    assert resolve_novelai_access_token("${NAI_ACCESS_TOKEN}") == "pst-from-client"
    assert load_access_token_from_client_py(client_py) == "pst-from-client"


def test_missing_client_py_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("NAI_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("NAI_CLIENT_PY", str(tmp_path / "missing.py"))

    assert resolve_novelai_access_token("${NAI_ACCESS_TOKEN}") is None
