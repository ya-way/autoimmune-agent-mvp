from __future__ import annotations

from time import perf_counter, sleep
from typing import Any

import requests

from v2.config import V2Config, get_config
from v2.core.logger import V2RunLogger


class LLMClient:
    def __init__(self, logger: V2RunLogger, config: V2Config | None = None) -> None:
        self.config = config or get_config()
        self.logger = logger

    def complete(self, prompt: str, caller: str, system_prompt: str = "You are a rare disease diagnosis assistant.") -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        return self._real_complete(prompt=prompt, caller=caller, messages=messages)

    def _real_complete(self, prompt: str, caller: str, messages: list[dict[str, str]]) -> str:
        start = perf_counter()
        print(f"[LLM CALL] caller={caller} model={self.config.llm_model}")
        url = f"{self.config.llm_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.config.llm_model,
            "messages": messages,
            "temperature": 0.1,
        }
        max_attempts = 3
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                resp = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.config.request_timeout_s,
                )
                resp.raise_for_status()
                data = resp.json()
                output = str(data["choices"][0]["message"]["content"])
                usage = data.get("usage", {})
                latency_ms = round((perf_counter() - start) * 1000, 2)
                print(f"[LLM RESPONSE] success=True latency_ms={latency_ms} usage={usage}")
                self.logger.log_llm_call(
                    caller=caller,
                    prompt=prompt,
                    messages=messages,
                    output=output,
                    latency_ms=latency_ms,
                    success=True,
                    usage=usage,
                )
                return output
            except Exception as exc:
                last_exc = exc
                err = str(exc)
                transient = (
                    isinstance(exc, requests.exceptions.RequestException)
                    or "timed out" in err.lower()
                    or "connection" in err.lower()
                    or "remote end closed connection" in err.lower()
                )
                if transient and attempt < max_attempts:
                    print(f"[LLM RETRY] caller={caller} attempt={attempt}/{max_attempts} reason={err}")
                    sleep(0.8 * attempt)
                    continue
                latency_ms = round((perf_counter() - start) * 1000, 2)
                print(f"[LLM RESPONSE] success=False latency_ms={latency_ms} usage={{}}")
                self.logger.log_llm_call(
                    caller=caller,
                    prompt=prompt,
                    messages=messages,
                    output="",
                    latency_ms=latency_ms,
                    success=False,
                    error=err,
                    usage={},
                )
                raise RuntimeError(f"LLM real call failed: {err}") from exc
        latency_ms = round((perf_counter() - start) * 1000, 2)
        err = str(last_exc) if last_exc is not None else "unknown llm failure"
        print(f"[LLM RESPONSE] success=False latency_ms={latency_ms} usage={{}}")
        self.logger.log_llm_call(
            caller=caller,
            prompt=prompt,
            messages=messages,
            output="",
            latency_ms=latency_ms,
            success=False,
            error=err,
            usage={},
        )
        raise RuntimeError(f"LLM real call failed: {err}")
