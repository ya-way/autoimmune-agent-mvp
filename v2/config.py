from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11 fallback
    import tomli as tomllib


def _set_env_default(key: str, value: str) -> None:
    if not key:
        return
    current = os.environ.get(key)
    if current is None or not current.strip():
        os.environ[key] = value


def _load_secret_toml_if_exists() -> Path | None:
    secret_path_raw = os.getenv("V2_SECRET_TOML_PATH", "").strip()
    if secret_path_raw:
        secret_path = Path(secret_path_raw).expanduser()
    else:
        secret_path = Path(__file__).resolve().parent.parent / "secret.toml"
    if not secret_path.exists():
        return None

    with secret_path.open("rb") as fp:
        data = tomllib.load(fp)

    candidate_maps = []
    if isinstance(data, dict):
        if isinstance(data.get("secrets"), dict):
            candidate_maps.append(data["secrets"])
        if isinstance(data.get("env"), dict):
            candidate_maps.append(data["env"])
        candidate_maps.append(data)

    for mapping in candidate_maps:
        for key, raw_value in mapping.items():
            if not isinstance(key, str):
                continue
            if isinstance(raw_value, bool):
                value = "true" if raw_value else "false"
            elif isinstance(raw_value, (int, float)):
                value = str(raw_value)
            elif isinstance(raw_value, str):
                value = raw_value
            else:
                continue
            _set_env_default(key.strip(), value.strip())
    return secret_path


@dataclass(frozen=True)
class V2Config:
    llm_provider: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    anysearch_api_key: str
    anysearch_base_url: str
    openfda_api_key: str
    deeprare_repo_path: str
    rarebench_local_csv: str
    request_timeout_s: int
    top_k: int
    logs_root: Path


@lru_cache(maxsize=1)
def get_config() -> V2Config:
    loaded_secret = _load_secret_toml_if_exists()
    logs_root = Path(__file__).resolve().parent / "logs"
    cfg = V2Config(
        llm_provider=os.getenv("LLM_PROVIDER", "deepseek").strip(),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").strip(),
        llm_model=os.getenv("LLM_MODEL", "deepseek-chat").strip(),
        anysearch_api_key=os.getenv("ANYSEARCH_API_KEY", "").strip(),
        anysearch_base_url=os.getenv("ANYSEARCH_BASE_URL", "https://api.anysearch.ai").strip(),
        openfda_api_key=os.getenv("OPENFDA_API_KEY", "").strip(),
        deeprare_repo_path=os.getenv("DEEPRARE_REPO_PATH", "").strip(),
        rarebench_local_csv=os.getenv("RAREBENCH_LOCAL_CSV", "").strip(),
        request_timeout_s=int(os.getenv("V2_REQUEST_TIMEOUT_S", "30")),
        top_k=int(os.getenv("V2_TOP_K", "5")),
        logs_root=logs_root,
    )
    print(f"[CONFIG] LLM_PROVIDER={cfg.llm_provider}")
    print(f"[CONFIG] LLM_MODEL={cfg.llm_model}")
    print(f"[CONFIG] LLM_BASE_URL={cfg.llm_base_url}")
    print(f"[CONFIG] LLM_API_KEY={'set' if bool(cfg.llm_api_key) else 'missing'}")
    print(f"[CONFIG] ANYSEARCH_API_KEY={'set' if bool(cfg.anysearch_api_key) else 'missing'}")
    print(f"[CONFIG] OPENFDA_API_KEY={'set' if bool(cfg.openfda_api_key) else 'missing'}")
    print(f"[CONFIG] RAREBENCH_LOCAL_CSV={cfg.rarebench_local_csv or 'not_set'}")
    if loaded_secret is not None:
        print(f"[CONFIG] SECRET_TOML={loaded_secret}")

    if not cfg.llm_api_key:
        raise RuntimeError("LLM_API_KEY is required (env var or secret.toml)")

    return cfg

