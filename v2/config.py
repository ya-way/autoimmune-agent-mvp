from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _load_dotenv_if_exists() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        # 系统环境优先，不覆盖已存在变量
        os.environ.setdefault(key, value)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class V2Config:
    llm_provider: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    mock_llm: bool
    anysearch_api_key: str
    anysearch_base_url: str
    mock_search: bool
    openfda_api_key: str
    deeprare_repo_path: str
    rarebench_local_csv: str
    request_timeout_s: int
    top_k: int
    logs_root: Path


@lru_cache(maxsize=1)
def get_config() -> V2Config:
    _load_dotenv_if_exists()
    logs_root = Path(__file__).resolve().parent / "logs"
    cfg = V2Config(
        llm_provider=os.getenv("LLM_PROVIDER", "deepseek").strip(),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").strip(),
        llm_model=os.getenv("LLM_MODEL", "deepseek-chat").strip(),
        mock_llm=_env_bool("MOCK_LLM", default=False),
        anysearch_api_key=os.getenv("ANYSEARCH_API_KEY", "").strip(),
        anysearch_base_url=os.getenv("ANYSEARCH_BASE_URL", "https://api.anysearch.ai").strip(),
        mock_search=_env_bool("MOCK_SEARCH", default=False),
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
    print(f"[CONFIG] MOCK_LLM={'true' if cfg.mock_llm else 'false'}")
    print(f"[CONFIG] ANYSEARCH_API_KEY={'set' if bool(cfg.anysearch_api_key) else 'missing'}")
    print(f"[CONFIG] OPENFDA_API_KEY={'set' if bool(cfg.openfda_api_key) else 'missing'}")
    print(f"[CONFIG] MOCK_SEARCH={'true' if cfg.mock_search else 'false'}")
    print(f"[CONFIG] RAREBENCH_LOCAL_CSV={cfg.rarebench_local_csv or 'not_set'}")

    if not cfg.mock_llm and not cfg.llm_api_key:
        raise RuntimeError("LLM_API_KEY is required when MOCK_LLM=false")
    if not cfg.mock_search and not cfg.anysearch_api_key:
        raise RuntimeError("ANYSEARCH_API_KEY is required when MOCK_SEARCH=false")

    return cfg

