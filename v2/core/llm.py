from __future__ import annotations

import hashlib
from time import perf_counter
from typing import Any

import requests

from v2.config import V2Config, get_config
from v2.core.logger import V2RunLogger


class LLMClient:
    def __init__(self, logger: V2RunLogger, config: V2Config | None = None) -> None:
        self.config = config or get_config()
        self.logger = logger
        self._force_mock = self.config.mock_llm

    def _use_mock(self) -> bool:
        return self._force_mock

    def complete(self, prompt: str, caller: str, system_prompt: str = "You are a rare disease diagnosis assistant.") -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        if self._use_mock():
            return self._mock_complete(prompt=prompt, caller=caller, messages=messages)
        return self._real_complete(prompt=prompt, caller=caller, messages=messages)

    def _mock_complete(self, prompt: str, caller: str, messages: list[dict[str, str]]) -> str:
        start = perf_counter()
        print(f"[LLM CALL] caller={caller} model=mock-llm")
        lower = prompt.lower()
        if "anca" in lower or "hematuria" in lower:
            diseases = [
                "ANCA-associated vasculitis",
                "Infective endocarditis mimic",
                "Systemic lupus erythematosus",
                "Anti-GBM disease",
                "Polyarteritis nodosa",
            ]
        elif "malar" in lower or "photosensitive" in lower or "dsdna" in lower:
            diseases = [
                "Systemic lupus erythematosus",
                "Mixed connective tissue disease",
                "Dermatomyositis",
                "Sjogren syndrome",
                "Adult-onset Still disease",
            ]
        elif "rf" in lower or "anti-ccp" in lower or "symmetric" in lower:
            diseases = [
                "Rheumatoid arthritis",
                "Psoriatic arthritis",
                "Viral arthritis mimic",
                "Systemic lupus erythematosus",
                "Reactive arthritis",
            ]
        else:
            seed = hashlib.md5(prompt.encode("utf-8")).hexdigest()[:6]
            diseases = [
                f"Rare disease hypothesis A-{seed}",
                f"Rare disease hypothesis B-{seed}",
                f"Rare disease hypothesis C-{seed}",
                f"Rare disease hypothesis D-{seed}",
                f"Rare disease hypothesis E-{seed}",
            ]
        output = "\n".join([f"{idx}. {name}" for idx, name in enumerate(diseases, start=1)])
        latency_ms = round((perf_counter() - start) * 1000, 2)
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
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
            latency_ms = round((perf_counter() - start) * 1000, 2)
            err = str(exc)
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
