"""
providers/llm_openrouter.py — LLM provider using OpenRouter chat completions API.

Endpoint: POST https://openrouter.ai/api/v1/chat/completions
Auth:     Authorization: Bearer <OPENROUTER_API_KEY>
          HTTP-Referer: <OPENROUTER_SITE_URL>
          X-Title: <OPENROUTER_APP_NAME>
"""
import json
import logging
import time
from typing import Optional

import httpx

from .base import LLMProvider, LLMResult

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
CHAT_ENDPOINT = f"{OPENROUTER_BASE}/chat/completions"


class OpenRouterLLMProvider(LLMProvider):
    """
    Async LLM provider for OpenRouter.
    A single persistent httpx.AsyncClient is reused across calls.
    """

    def __init__(self):
        # Import here to avoid circular at module load time
        from config import settings
        self._settings = settings
        self._client: Optional[httpx.AsyncClient] = None

    def _client_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._settings.OPENROUTER_API_KEY}",
            "HTTP-Referer": self._settings.OPENROUTER_SITE_URL,
            "X-Title": self._settings.OPENROUTER_APP_NAME,
            "Content-Type": "application/json",
        }

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def generate(self, messages: list[dict], system_prompt: str) -> LLMResult:
        if not self._settings.OPENROUTER_API_KEY:
            logger.error("OPENROUTER_API_KEY is not set — falling back to error message.")
            return LLMResult(
                text="API key not configured. Please set OPENROUTER_API_KEY in your .env file.",
                model="none",
            )

        full_messages = [{"role": "system", "content": system_prompt}] + messages

        payload = {
            "model": self._settings.OPENROUTER_MODEL,
            "messages": full_messages,
            "max_tokens": 300,
            "temperature": 0.7,
        }

        t_start = time.perf_counter()
        first_token_at: Optional[float] = None

        try:
            client = self._get_client()
            response = await client.post(
                CHAT_ENDPOINT,
                json=payload,
                headers=self._client_headers(),
            )
            first_token_at = time.perf_counter() - t_start
            response.raise_for_status()

            data = response.json()
            text = data["choices"][0]["message"]["content"].strip()
            return LLMResult(
                text=text,
                model=self._settings.OPENROUTER_MODEL,
                llm_first_token_at=first_token_at,
            )

        except httpx.HTTPStatusError as e:
            body = e.response.text[:300]
            logger.error(f"OpenRouter HTTP {e.response.status_code}: {body}")
            return LLMResult(
                text="I'm sorry, the language model returned an error. Please try again.",
                model=self._settings.OPENROUTER_MODEL,
            )
        except httpx.TimeoutException:
            logger.error("OpenRouter request timed out.")
            return LLMResult(
                text="I'm sorry, the response timed out. Please try again.",
                model=self._settings.OPENROUTER_MODEL,
            )
        except Exception as e:
            logger.error(f"OpenRouter unexpected error: {e}", exc_info=True)
            return LLMResult(
                text="I'm sorry, I encountered an unexpected error.",
                model=self._settings.OPENROUTER_MODEL,
            )

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def generate_stream(self, messages: list[dict], system_prompt: str):
        """Stream tokens via OpenRouter SSE. Yields str chunks as they arrive."""
        if not self._settings.OPENROUTER_API_KEY:
            yield "API key not configured. Please set OPENROUTER_API_KEY in your .env file."
            return

        full_messages = [{"role": "system", "content": system_prompt}] + messages
        payload = {
            "model":       self._settings.OPENROUTER_MODEL,
            "messages":    full_messages,
            "max_tokens":  300,
            "temperature": 0.7,
            "stream":      True,
        }

        try:
            client = self._get_client()
            async with client.stream(
                "POST", CHAT_ENDPOINT,
                json=payload, headers=self._client_headers(),
                timeout=30.0,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        token = (chunk["choices"][0]["delta"].get("content") or "")
                        if token:
                            yield token
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

        except httpx.HTTPStatusError as e:
            logger.error(f"OpenRouter stream HTTP {e.response.status_code}: {e.response.text[:200]}")
            yield "I'm sorry, the language model returned an error. Please try again."
        except httpx.TimeoutException:
            logger.error("OpenRouter stream timed out.")
            yield "I'm sorry, the response timed out. Please try again."
        except Exception as e:
            logger.error(f"OpenRouter stream error: {e}", exc_info=True)
            yield "I'm sorry, something went wrong. Please try again."
