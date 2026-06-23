"""OpenAI-compatible image providers.

These adapters target proxy services that expose standard OpenAI-style HTTP
surfaces.  They intentionally do not depend on the official OpenAI SDK so a
project-local relay can be used by changing only configuration.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any

import httpx
from loguru import logger

from ..errors import ProviderCapabilityError, ProviderError, RateLimitError
from ..image_inputs import (
    DATA_IMAGE_URL_RE,
    decode_image_data_url,
    detect_image_mime_type,
    image_bytes_to_data_url,
)
from ..schema import Capability, GenerateRequest, ImageResult, ImageToImageRequest, InpaintRequest
from .base import BaseImageProvider


_DATA_URL_RE = DATA_IMAGE_URL_RE
_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\((?P<url><[^>]+>|[^\s)]+)(?:\s+[\"'][^\"']*[\"'])?\)"
)
_HTTP_IMAGE_URL_RE = re.compile(r"https?://[^\s<>'\")]+")
_BASE64_PAYLOAD_RE = re.compile(r"^[A-Za-z0-9+/=\s]{64,}$")


def _join_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def _decode_base64_image(value: str) -> bytes:
    cleaned = "".join(value.split())
    try:
        return base64.b64decode(cleaned, validate=True)
    except binascii.Error as exc:
        raise ValueError("Invalid base64 image data") from exc


def _merge_prompt(prompt: str, negative_prompt: str) -> str:
    if not negative_prompt:
        return prompt
    return f"{prompt}\n\nNegative prompt: {negative_prompt}"


def _response_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if payload.get("msg"):
            return str(payload["msg"])
        if payload.get("message"):
            return str(payload["message"])
    return response.text[:500]


def _copy_passthrough_params(
    payload: dict[str, Any],
    *,
    settings: dict[str, Any],
    extra: dict[str, Any],
    keys: tuple[str, ...],
) -> None:
    for key in keys:
        if key in settings:
            payload[key] = settings[key]
        if key in extra:
            payload[key] = extra[key]


def _copy_chat_passthrough_params(
    payload: dict[str, Any],
    *,
    settings: dict[str, Any],
    extra: dict[str, Any],
    keys: tuple[str, ...],
) -> None:
    for key in keys:
        value_missing = object()
        value: Any = value_missing
        if key in settings:
            value = settings[key]
        if key in extra:
            value = extra[key]
        if value is value_missing:
            continue
        if key == "response_format" and not isinstance(value, dict):
            # Chat Completions expects an object here; Images API strings such as
            # "b64_json" are deliberately not forwarded to chat image relays.
            continue
        payload[key] = value


def _parse_sse_events(response_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    data_lines: list[str] = []

    def flush_data_lines() -> None:
        if not data_lines:
            return
        data = "\n".join(data_lines).strip()
        data_lines.clear()
        if not data or data == "[DONE]":
            return
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed SSE JSON: {data[:200]}") from exc
        if isinstance(parsed, dict):
            events.append(parsed)

    for raw_line in response_text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            flush_data_lines()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())

    flush_data_lines()
    return events


class _OpenAICompatibleBase(BaseImageProvider):
    """Shared HTTP and parsing utilities for OpenAI-compatible providers."""

    name = "openai_compatible"
    _default_endpoint = ""

    def __init__(self, config) -> None:
        super().__init__(config)
        settings = self._config.settings
        self._api_key = self._config.auth.get("api_key", "")
        self._base_url = settings.get("base_url", "https://api.openai.com/v1")
        self._endpoint = settings.get("endpoint", self._default_endpoint)
        self._model = settings.get("model", "")
        self._timeout = settings.get("timeout", 120)
        self._retry = settings.get("retry", 2)
        self._client: httpx.AsyncClient | None = None

    async def initialize(self) -> None:
        if not self._api_key:
            raise ProviderError(self.name, "Missing auth.api_key")
        if not self._model:
            raise ProviderError(self.name, "Missing settings.model")
        self._client = httpx.AsyncClient(timeout=self._timeout)
        logger.info("[{}] Initialized, model={}", self.name, self._model)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("[{}] Closed", self.name)

    def supports(self, capability: Capability) -> bool:
        return capability == Capability.GENERATE

    async def inpaint(self, request: InpaintRequest) -> list[ImageResult]:
        raise ProviderCapabilityError(self.name, Capability.INPAINT.value)

    async def _post_json(self, payload: dict[str, Any], *, endpoint: str | None = None) -> dict[str, Any]:
        assert self._client is not None
        url = _join_url(self._base_url, endpoint or self._endpoint)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(self._retry + 1):
            try:
                response = await self._client.post(url, json=payload, headers=headers)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "5"))
                    raise RateLimitError(self.name, retry_after=retry_after)
                if response.status_code >= 400:
                    raise ProviderError(
                        self.name,
                        f"HTTP {response.status_code}: {_response_error_message(response)}",
                    )
                return self._parse_response_payload(response)
            except RateLimitError as exc:
                last_error = exc
                if attempt >= self._retry:
                    break
                import asyncio
                wait = exc.retry_after or float((attempt + 1) * 5)
                logger.warning("[{}] Rate limited, waiting {}s", self.name, wait)
                await asyncio.sleep(wait)
            except httpx.TimeoutException as exc:
                last_error = ProviderError(self.name, f"Timeout: {exc}", exc)
                if attempt >= self._retry:
                    break
                import asyncio
                await asyncio.sleep(2)
            except httpx.HTTPError as exc:
                last_error = ProviderError(self.name, f"HTTP transport error: {exc}", exc)
                if attempt >= self._retry:
                    break
                import asyncio
                await asyncio.sleep(2)
        raise last_error or ProviderError(self.name, "All retries exhausted")

    async def _post_multipart(
        self,
        *,
        endpoint: str,
        data: dict[str, Any],
        files: list[tuple[str, tuple[str, bytes, str]]],
    ) -> dict[str, Any]:
        assert self._client is not None
        url = _join_url(self._base_url, endpoint)
        headers = {"Authorization": f"Bearer {self._api_key}"}
        form_data = {key: str(value) for key, value in data.items() if value is not None}
        last_error: Exception | None = None
        for attempt in range(self._retry + 1):
            try:
                response = await self._client.post(url, data=form_data, files=files, headers=headers)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "5"))
                    raise RateLimitError(self.name, retry_after=retry_after)
                if response.status_code >= 400:
                    raise ProviderError(
                        self.name,
                        f"HTTP {response.status_code}: {_response_error_message(response)}",
                    )
                return self._parse_response_payload(response)
            except RateLimitError as exc:
                last_error = exc
                if attempt >= self._retry:
                    break
                import asyncio
                wait = exc.retry_after or float((attempt + 1) * 5)
                logger.warning("[{}] Rate limited, waiting {}s", self.name, wait)
                await asyncio.sleep(wait)
            except httpx.TimeoutException as exc:
                last_error = ProviderError(self.name, f"Timeout: {exc}", exc)
                if attempt >= self._retry:
                    break
                import asyncio
                await asyncio.sleep(2)
            except httpx.HTTPError as exc:
                last_error = ProviderError(self.name, f"HTTP transport error: {exc}", exc)
                if attempt >= self._retry:
                    break
                import asyncio
                await asyncio.sleep(2)
        raise last_error or ProviderError(self.name, "All retries exhausted")

    def _parse_response_payload(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            text = response.text
            content_type = ""
            try:
                content_type = str(response.headers.get("Content-Type", ""))
            except AttributeError:
                content_type = ""
            if "text/event-stream" in content_type or text.lstrip().startswith("data:"):
                try:
                    return {"_sse_events": _parse_sse_events(text)}
                except ValueError as exc:
                    raise ProviderError(self.name, str(exc), exc) from exc
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ProviderError(self.name, f"Non-JSON response: {text[:500]}", exc) from exc
            if not isinstance(parsed, dict):
                raise ProviderError(self.name, "Expected JSON object response")
            return parsed
        if not isinstance(payload, dict):
            raise ProviderError(self.name, "Expected JSON object response")
        return payload

    async def _image_bytes_from_url(self, url: str) -> bytes:
        assert self._client is not None
        if url.startswith("data:image/"):
            try:
                return decode_image_data_url(url).image_bytes
            except ValueError as exc:
                raise ProviderError(self.name, "Invalid image data URL", exc) from exc
        response = await self._client.get(url)
        if response.status_code >= 400:
            raise ProviderError(self.name, f"Image download HTTP {response.status_code}: {response.text[:500]}")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type and not content_type.startswith("image/") and content_type != "application/octet-stream":
            raise ProviderError(self.name, f"Image download returned non-image content type: {content_type}")
        return response.content

    async def _extract_image_results(
        self,
        response: dict[str, Any],
        *,
        request: GenerateRequest | ImageToImageRequest | InpaintRequest,
        model: str,
        generation_params: dict[str, Any],
    ) -> list[ImageResult]:
        image_items = self._extract_image_items(response)
        results: list[ImageResult] = []
        for index, item in enumerate(image_items):
            image_bytes: bytes | None = None
            if item.get("b64_json") or item.get("base64"):
                try:
                    raw_base64 = item.get("b64_json") or item.get("base64")
                    image_bytes = _decode_base64_image(str(raw_base64))
                except ValueError:
                    continue
            elif item.get("url"):
                image_bytes = await self._image_bytes_from_url(str(item["url"]))
            if image_bytes is None:
                continue

            seed = item.get("seed")
            if seed is None and request.seed is not None:
                seed = request.seed + index
            results.append(ImageResult(
                image_bytes=image_bytes,
                seed=seed,
                provider_name=self.name,
                model_name=model,
                generation_params={**generation_params, "result_index": index},
                cost=float(item.get("cost", 0.0) or 0.0),
            ))

        if not results:
            error_message = self._extract_provider_error_message(response)
            if error_message:
                raise ProviderError(self.name, f"Provider returned error: {error_message}")
            raise ProviderError(self.name, "No image data found in provider response")
        return results

    def _extract_image_items(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        data = response.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    items.append(item)
        return items


class OpenAIImagesProvider(_OpenAICompatibleBase):
    """Provider for the standard /v1/images/generations API."""

    name = "openai_images"
    _default_endpoint = "/images/generations"

    def supports(self, capability: Capability) -> bool:
        return capability in (Capability.GENERATE, Capability.IMAGE_TO_IMAGE)

    async def generate(self, request: GenerateRequest) -> list[ImageResult]:
        settings = self._config.settings
        model = str(request.extra.get("model", self._model))
        size = str(request.extra.get("size", settings.get("size", f"{request.width}x{request.height}")))
        response_format = str(request.extra.get("response_format", settings.get("response_format", "b64_json")))

        payload: dict[str, Any] = {
            "model": model,
            "prompt": _merge_prompt(request.prompt, request.negative_prompt),
            "n": request.count,
            "size": size,
            "response_format": response_format,
        }
        passthrough_keys = (
            "quality",
            "style",
            "background",
            "moderation",
            "output_format",
            "output_compression",
            "user",
        )
        _copy_passthrough_params(payload, settings=settings, extra=request.extra, keys=passthrough_keys)

        response = await self._post_json(payload)
        generation_params = {
            "api_surface": "images/generations",
            "model": model,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "size": size,
            "count": request.count,
            "response_format": response_format,
        }
        return await self._extract_image_results(
            response,
            request=request,
            model=model,
            generation_params=generation_params,
        )

    async def image_to_image(self, request: ImageToImageRequest) -> list[ImageResult]:
        settings = self._config.settings
        model = str(request.extra.get("model", self._model))
        endpoint = str(request.extra.get(
            "edit_endpoint",
            settings.get("edit_endpoint", "/images/edits"),
        ))
        size = request.extra.get("size", settings.get("size"))
        if size is None and request.width and request.height:
            size = f"{request.width}x{request.height}"
        response_format = str(request.extra.get("response_format", settings.get("response_format", "b64_json")))

        payload: dict[str, Any] = {
            "model": model,
            "prompt": _merge_prompt(request.prompt, request.negative_prompt),
            "n": request.count,
            "response_format": response_format,
        }
        if size:
            payload["size"] = str(size)
        passthrough_keys = (
            "quality",
            "background",
            "input_fidelity",
            "output_format",
            "output_compression",
            "user",
        )
        _copy_passthrough_params(payload, settings=settings, extra=request.extra, keys=passthrough_keys)

        image_field = "image" if len(request.images) == 1 else "image[]"
        files = [
            (
                image_field,
                (f"image_{index}.png", image, detect_image_mime_type(image)),
            )
            for index, image in enumerate(request.images)
        ]

        response = await self._post_multipart(
            endpoint=endpoint,
            data=payload,
            files=files,
        )
        generation_params = {
            "api_surface": "images/edits",
            "mode": "image_to_image",
            "model": model,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "size": size,
            "count": request.count,
            "response_format": response_format,
            "reference_image_count": len(request.images),
        }
        return await self._extract_image_results(
            response,
            request=request,
            model=model,
            generation_params=generation_params,
        )


class OpenAIChatImageProvider(_OpenAICompatibleBase):
    """Provider for image generation proxied through /v1/chat/completions."""

    name = "openai_chat_image"
    _default_endpoint = "/chat/completions"

    def supports(self, capability: Capability) -> bool:
        return capability in (Capability.GENERATE, Capability.IMAGE_TO_IMAGE)

    async def generate(self, request: GenerateRequest) -> list[ImageResult]:
        settings = self._config.settings
        model = str(request.extra.get("model", self._model))
        system_prompt = settings.get(
            "system_prompt",
            "You generate images. Return image output as base64 data URL, markdown image URL, or JSON with b64_json/url.",
        )
        user_content = self._build_user_content(request)
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        passthrough_keys = (
            "temperature",
            "top_p",
            "max_tokens",
            "stream",
            "response_format",
            "n",
            "user",
        )
        _copy_chat_passthrough_params(payload, settings=settings, extra=request.extra, keys=passthrough_keys)

        response = await self._post_json(payload)
        generation_params = {
            "api_surface": "chat/completions",
            "model": model,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "width": request.width,
            "height": request.height,
            "count": request.count,
        }
        return await self._extract_image_results(
            response,
            request=request,
            model=model,
            generation_params=generation_params,
        )

    async def image_to_image(self, request: ImageToImageRequest) -> list[ImageResult]:
        settings = self._config.settings
        model = str(request.extra.get("model", self._model))
        system_prompt = settings.get(
            "system_prompt",
            "You generate images from text and reference images. Return image output as base64 data URL, markdown image URL, or JSON with b64_json/url.",
        )
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._build_image_to_image_content(request)},
            ],
        }
        passthrough_keys = (
            "temperature",
            "top_p",
            "max_tokens",
            "stream",
            "response_format",
            "n",
            "user",
        )
        _copy_chat_passthrough_params(payload, settings=settings, extra=request.extra, keys=passthrough_keys)

        response = await self._post_json(payload)
        generation_params = {
            "api_surface": "chat/completions",
            "mode": "image_to_image",
            "model": model,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "width": request.width,
            "height": request.height,
            "count": request.count,
            "reference_image_count": len(request.images),
        }
        return await self._extract_image_results(
            response,
            request=request,
            model=model,
            generation_params=generation_params,
        )

    def _build_user_content(self, request: GenerateRequest) -> str:
        parts = [
            request.prompt,
            f"Target size: {request.width}x{request.height}.",
        ]
        if request.negative_prompt:
            parts.append(f"Negative prompt: {request.negative_prompt}.")
        if request.output_format:
            parts.append(f"Preferred output format: {request.output_format.value}.")
        return "\n".join(parts)

    def _build_image_to_image_content(self, request: ImageToImageRequest) -> list[dict[str, Any]]:
        text_parts = [request.prompt]
        if request.width and request.height:
            text_parts.append(f"Target size: {request.width}x{request.height}.")
        if request.negative_prompt:
            text_parts.append(f"Negative prompt: {request.negative_prompt}.")
        if request.output_format:
            text_parts.append(f"Preferred output format: {request.output_format.value}.")

        content: list[dict[str, Any]] = [
            {"type": "text", "text": "\n".join(text_parts)}
        ]
        for image in request.images:
            content.append({
                "type": "image_url",
                "image_url": {"url": image_bytes_to_data_url(image)},
            })
        return content

    def _extract_image_items(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        items = super()._extract_image_items(response)
        payloads = self._chat_payloads(response)

        for payload in payloads:
            items.extend(self._extract_items_from_value(payload))
        for text in self._collect_chat_completion_text(payloads):
            items.extend(self._extract_items_from_text(text))
        return self._dedupe_items(items)

    def _extract_provider_error_message(self, response: dict[str, Any]) -> str | None:
        for payload in self._chat_payloads(response):
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if message:
                    return str(message)
            elif isinstance(error, str) and error:
                return error
        return None

    def _chat_payloads(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        events = response.get("_sse_events")
        if isinstance(events, list):
            return [event for event in events if isinstance(event, dict)]
        return [response]

    def _collect_chat_completion_text(self, payloads: list[dict[str, Any]]) -> list[str]:
        chunks_by_choice: dict[int, list[str]] = {}
        for payload in payloads:
            choices = payload.get("choices", [])
            if not isinstance(choices, list):
                continue
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                index = int(choice.get("index") or 0)
                chunks = chunks_by_choice.setdefault(index, [])
                for key in ("message", "delta"):
                    container = choice.get(key)
                    if not isinstance(container, dict):
                        continue
                    content = container.get("content")
                    if isinstance(content, str):
                        chunks.append(content)
        return ["".join(chunks) for chunks in chunks_by_choice.values() if chunks]

    def _extract_items_from_content(self, content: Any) -> list[dict[str, Any]]:
        return self._extract_items_from_value(content)

    def _extract_items_from_dict(self, value: dict[str, Any]) -> list[dict[str, Any]]:
        return self._extract_items_from_value(value)

    def _extract_items_from_value(self, value: Any, key_hint: str = "") -> list[dict[str, Any]]:
        if isinstance(value, str):
            stripped = value.strip()
            if key_hint in {"url", "image_url", "file_uri", "fileUri"}:
                item = self._item_from_string_reference(stripped)
                return [item] if item else []
            if key_hint in {"b64_json", "base64"} and stripped:
                return [{"b64_json": stripped}]
            if key_hint in {"data", "result"} and _BASE64_PAYLOAD_RE.fullmatch(stripped):
                return [{"b64_json": stripped}]
            return self._extract_items_from_text(stripped)

        if isinstance(value, list):
            items: list[dict[str, Any]] = []
            for item in value:
                items.extend(self._extract_items_from_value(item, key_hint))
            return items

        if isinstance(value, dict):
            items: list[dict[str, Any]] = []
            for key in ("b64_json", "base64"):
                raw = value.get(key)
                if isinstance(raw, str) and raw.strip():
                    image_item = dict(value)
                    image_item["b64_json"] = raw.strip()
                    items.append(image_item)
            raw_url = value.get("url")
            if isinstance(raw_url, str):
                item = self._item_from_string_reference(raw_url)
                if item:
                    items.append({**value, **item})
            image_url = value.get("image_url")
            if isinstance(image_url, dict):
                raw_nested_url = image_url.get("url")
                if isinstance(raw_nested_url, str):
                    item = self._item_from_string_reference(raw_nested_url)
                    if item:
                        items.append(item)
            elif isinstance(image_url, str):
                item = self._item_from_string_reference(image_url)
                if item:
                    items.append(item)

            for key, child in value.items():
                if key in {"b64_json", "base64", "url", "image_url"}:
                    continue
                items.extend(self._extract_items_from_value(child, str(key)))
            return items

        return []

    def _item_from_string_reference(self, value: str) -> dict[str, Any] | None:
        text = value.strip().strip("<>")
        if not text:
            return None
        data_match = _DATA_URL_RE.fullmatch(text)
        if data_match:
            return {"b64_json": data_match.group("data")}
        if text.startswith(("http://", "https://")):
            return {"url": text}
        return None

    def _extract_items_from_text(self, text: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def add_item(item: dict[str, Any]) -> None:
            key = ""
            kind = ""
            for candidate_key in ("b64_json", "base64", "url"):
                if item.get(candidate_key):
                    kind = candidate_key
                    key = str(item[candidate_key])
                    break
            if not kind:
                items.append(item)
                return
            marker = (kind, key)
            if marker not in seen:
                seen.add(marker)
                items.append(item)

        stripped = text.strip()
        if stripped:
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                for item in self._extract_items_from_value(parsed):
                    add_item(item)

        for match in _DATA_URL_RE.finditer(text):
            add_item({"b64_json": match.group("data")})
        for match in _MARKDOWN_IMAGE_RE.finditer(text):
            url = match.group("url").strip().strip("<>")
            if url.startswith("data:image/"):
                data_match = _DATA_URL_RE.search(url)
                if data_match:
                    add_item({"b64_json": data_match.group("data")})
            else:
                add_item({"url": url})
        for match in _HTTP_IMAGE_URL_RE.finditer(text):
            add_item({"url": match.group(0)})
        return items

    def _dedupe_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in items:
            key = ""
            kind = ""
            for candidate_key in ("b64_json", "base64", "url"):
                if item.get(candidate_key):
                    kind = candidate_key
                    key = str(item[candidate_key])
                    break
            if not kind:
                continue
            marker = (kind, key)
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(item)
        return deduped


class GeminiChatImageProvider(OpenAIChatImageProvider):
    """Configured alias for Gemini image models exposed via chat completions."""

    name = "gemini_chat_image"


class GrokChatImageProvider(OpenAIChatImageProvider):
    """Configured alias for Grok image models exposed via chat completions."""

    name = "grok_chat_image"
