"""NovelAI access token resolution.

The Project P3 workstation keeps a local NovelAI client helper outside this
repository. This module reads only literal token returns from that file so the
gateway can reuse the local credential without copying secrets into config.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

DEFAULT_NAI_CLIENT_PATH = Path(r"F:\my_project\new\tags_machine\novelai\client.py")


def _is_placeholder(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("${") and stripped.endswith("}")


def _usable_token(value: str | None) -> str | None:
    if not value:
        return None
    token = value.strip()
    if not token or _is_placeholder(token):
        return None
    return token


FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def _literal_return_from_function(node: FunctionNode, function_name: str) -> str | None:
    if node.name != function_name:
        return None
    for child in ast.walk(node):
        if isinstance(child, ast.Return) and isinstance(child.value, ast.Constant):
            token = child.value.value
            if isinstance(token, str):
                return _usable_token(token)
    return None


def _extract_token_from_client_source(source: str) -> str | None:
    try:
        module = ast.parse(source)
    except SyntaxError:
        return None

    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == "NAIClient":
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    token = _literal_return_from_function(child, "get_access_token")
                    if token:
                        return token

    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            token = _literal_return_from_function(node, "get_access_token")
            if token:
                return token

    return None


def load_access_token_from_client_py(path: str | Path) -> str | None:
    client_path = Path(path)
    if not client_path.exists():
        return None
    try:
        source = client_path.read_text(encoding="utf-8")
    except OSError:
        return None
    return _extract_token_from_client_source(source)


def resolve_novelai_access_token(
    configured_token: str | None = None,
    *,
    client_py_path: str | Path | None = None,
) -> str | None:
    """Resolve a NovelAI access token without logging or persisting it."""
    token = _usable_token(configured_token)
    if token:
        return token

    token = _usable_token(os.environ.get("NAI_ACCESS_TOKEN"))
    if token:
        return token

    path = (
        client_py_path
        or os.environ.get("NAI_CLIENT_PY")
        or DEFAULT_NAI_CLIENT_PATH
    )
    return load_access_token_from_client_py(path)
