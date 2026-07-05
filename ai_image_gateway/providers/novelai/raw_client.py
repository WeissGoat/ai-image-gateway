from __future__ import annotations

import asyncio
import base64
import json
from hashlib import blake2b
from typing import Any

import httpx
from loguru import logger

from ...auth import resolve_novelai_access_token
from ...config import ProviderConfig
from ...contracts import NovelAIRawPayload, NovelAIRawResult, RetryRecord
from ...errors import ProviderError, RateLimitError
from .decode import extract_zip_images
from .payloads import AUTH_URL, BASE_URL


def _argon_hash(email: str, password: str, size: int, domain: str) -> str:
    try:
        import argon2.low_level
    except ImportError:
        raise ProviderError(
            "novelai",
            "argon2-cffi is required for username/password auth. Install with: pip install argon2-cffi",
        )
    pre_salt = f"{password[:6]}{email}{domain}"
    blake = blake2b(digest_size=16)
    blake.update(pre_salt.encode())
    salt = blake.digest()
    raw = argon2.low_level.hash_secret_raw(
        password.encode(),
        salt,
        time_cost=2,
        memory_cost=int(2000000 / 1024),
        parallelism=1,
        hash_len=size,
        type=argon2.low_level.Type.ID,
    )
    return base64.urlsafe_b64encode(raw).decode()


def _get_access_key(email: str, password: str) -> str:
    return _argon_hash(email, password, 64, "novelai_data_access_key")[:64]


class _NovelAITransportProvider:
    name = "novelai"

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._access_token = ""
        self._client: httpx.AsyncClient | None = None
        settings = self._config.settings
        self._base_url: str = settings.get("base_url", BASE_URL)
        self._timeout: int = settings.get("timeout", 120)
        self._retry: int = settings.get("retry", 3)
        self._retry_interval: float | None = settings.get("retry_interval")

    async def initialize(self) -> None:
        auth = self._config.auth
        access_token = resolve_novelai_access_token(
            auth.get("access_token"),
            client_py_path=auth.get("client_py_path") or auth.get("client_py"),
        )
        if access_token:
            self._access_token = access_token
        elif "access_key" in auth and auth["access_key"]:
            self._access_token = await self._login(auth["access_key"])
        elif "username" in auth and "password" in auth:
            access_key = _get_access_key(auth["username"], auth["password"])
            self._access_token = await self._login(access_key)
        else:
            raise ProviderError(
                self.name,
                "No valid auth config. Provide access_token, NAI_ACCESS_TOKEN, client_py_path, access_key, or username+password.",
            )
        self._client = httpx.AsyncClient(timeout=self._timeout)
        logger.info("[NovelAI] Initialized raw transport")

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("[NovelAI] Closed raw transport")

    async def generate_raw(self, payload: NovelAIRawPayload | dict[str, Any]) -> NovelAIRawResult:
        raw_payload = NovelAIRawPayload.model_validate(payload)
        return await self._request(payload=raw_payload)

    async def generate_multipart(
        self,
        payload: NovelAIRawPayload,
        *,
        image_bytes: bytes,
        mask_bytes: bytes,
    ) -> NovelAIRawResult:
        request_json = json.dumps(payload.model_dump()).encode("utf-8")
        files = [
            ("image", ("blob", image_bytes, "image/png")),
            ("mask", ("blob", mask_bytes, "image/png")),
            ("request", ("blob", request_json, "application/json")),
        ]
        return await self._request(payload=payload, files=files)

    async def _request(
        self,
        *,
        payload: NovelAIRawPayload,
        files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
    ) -> NovelAIRawResult:
        assert self._client is not None

        headers = {"Authorization": f"Bearer {self._access_token}"}
        retry_records: list[RetryRecord] = []
        last_error: Exception | None = None

        for attempt in range(1, self._retry + 1):
            try:
                request_kwargs: dict[str, Any] = {
                    "headers": headers,
                    "timeout": self._timeout,
                }
                if files is None:
                    request_kwargs["json"] = payload.model_dump()
                else:
                    request_kwargs["files"] = files

                resp = await self._client.post(
                    f"{self._base_url}/ai/generate-image",
                    **request_kwargs,
                )

                if resp.status_code == 429:
                    wait = self._retry_wait_seconds(attempt, default=float(attempt * 5))
                    retry_records.append(
                        RetryRecord(
                            attempt=attempt,
                            status_code=resp.status_code,
                            error="Rate limited",
                            retryable=attempt < self._retry,
                            sleep_seconds=wait if attempt < self._retry else None,
                        )
                    )
                    if attempt >= self._retry:
                        raise RateLimitError(self.name, retry_after=wait)
                    logger.warning("[NovelAI] Raw transport rate limited, waiting {}s...", wait)
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 400:
                    retryable = resp.status_code in {500, 502, 503, 504} and attempt < self._retry
                    wait = self._retry_wait_seconds(attempt, default=2.0) if retryable else None
                    detail = f"HTTP {resp.status_code}: {resp.text[:500]}"
                    retry_records.append(
                        RetryRecord(
                            attempt=attempt,
                            status_code=resp.status_code,
                            error=detail,
                            retryable=retryable,
                            sleep_seconds=wait,
                        )
                    )
                    if not retryable:
                        raise ProviderError(self.name, detail)
                    logger.warning(
                        "[NovelAI] Raw transport attempt {}/{} failed: {}",
                        attempt,
                        self._retry,
                        detail,
                    )
                    await asyncio.sleep(wait)
                    continue

                retry_records.append(
                    RetryRecord(
                        attempt=attempt,
                        status_code=resp.status_code,
                        retryable=False,
                    )
                )
                return NovelAIRawResult(
                    images=extract_zip_images(resp.content),
                    request_payload=payload,
                    retry_records=retry_records,
                    response_headers={str(k): str(v) for k, v in resp.headers.items()},
                )
            except (RateLimitError, ProviderError) as exc:
                last_error = exc
                if retry_records and retry_records[-1].attempt == attempt:
                    if not retry_records[-1].retryable:
                        break
                else:
                    retry_records.append(
                        RetryRecord(
                            attempt=attempt,
                            error=str(exc),
                            retryable=False,
                        )
                    )
                    break
            except httpx.TimeoutException as exc:
                retryable = attempt < self._retry
                wait = self._retry_wait_seconds(attempt, default=2.0) if retryable else None
                last_error = ProviderError(self.name, f"Timeout: {exc}", exc)
                retry_records.append(
                    RetryRecord(
                        attempt=attempt,
                        error=f"Timeout: {exc}",
                        retryable=retryable,
                        sleep_seconds=wait,
                    )
                )
                if not retryable:
                    break
                logger.warning("[NovelAI] Raw transport timeout on attempt {}/{}", attempt, self._retry)
                await asyncio.sleep(wait)

        message = str(last_error or ProviderError(self.name, "All retries exhausted"))
        raise ProviderError(self.name, f"{message}; retry_records={retry_records!r}")

    def _retry_wait_seconds(self, attempt: int, *, default: float) -> float:
        if self._retry_interval is None:
            return default
        return max(0.0, float(self._retry_interval))

    async def _login(self, access_key: str) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{AUTH_URL}/user/login",
                json={"key": access_key},
            )
            if resp.status_code != 200:
                raise ProviderError(self.name, f"Login failed: {resp.status_code} {resp.text}")
            return resp.json()["accessToken"]


class NovelAIRawClient:
    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._provider = _NovelAITransportProvider(config)

    async def initialize(self) -> None:
        await self._provider.initialize()

    async def close(self) -> None:
        await self._provider.close()

    async def generate_raw(self, payload: NovelAIRawPayload) -> NovelAIRawResult:
        return await self._provider.generate_raw(payload)
