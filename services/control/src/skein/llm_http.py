"""OpenAI 兼容网关客户端。只发 JSON 补全，不持有业务语义。"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger("skein.llm")


class LLMError(RuntimeError):
    pass


class HttpLLM:
    def __init__(self, base: str, key: str, timeout: float = 60.0) -> None:
        self._base = base.rstrip("/")
        self._key = key
        self._timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def complete(self, request: dict) -> dict:
        body = dict(request)
        body.setdefault("response_format", {"type": "json_object"})
        data = json.dumps(body, ensure_ascii=False).encode()
        http_request = urllib.request.Request(
            f"{self._base}/chat/completions",
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._key}",
            },
        )
        try:
            with self._opener.open(http_request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise LLMError(f"gateway returned {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(str(exc.reason)) from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError("gateway returned a non-json body") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
            content_text = (
                content
                if isinstance(content, str)
                else json.dumps(content, ensure_ascii=False, default=str)
            )
            return json.loads(content_text)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError("gateway returned an unparsable body") from exc
