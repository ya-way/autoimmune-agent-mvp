# autoimmune-agent-mvp

Biomedical constrained ReAct evidence harness.

**Not medical advice. Not a clinical decision tool.**

## Current Official Mainline

- DeepRare official benchmark sanity line
- constrained ReAct showcase line

The benchmark line is for eval-chain sanity and anti-self-hype checks.
The showcase line is for trajectory/audit demonstration.

## Minimal Architecture

- `v2/core/react_agent.py`
- `v2/core/action_registry.py`
- `v2/tools/phenotype.py`
- `v2/tools/literature.py`
- `v2/benchmark/deeprare.py`

## Quickstart

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp secret.toml.example secret.toml
```

`secret.toml` contains private keys and local-only paths. It is gitignored by default.
If needed, you can still use environment variables; environment variables take precedence.
You can also load a custom path with `V2_SECRET_TOML_PATH=/path/to/secret.toml`.

Fill `secret.toml` before running:

- `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`
- `ANYSEARCH_API_KEY` (optional for better web evidence quality)
- `DEEPRARE_REPO_PATH` and `RAREBENCH_LOCAL_CSV` (required for official benchmark eval)

### Repro Check (recommended before benchmark)

```bash
python -m v2.cli --check-llm
python -m v2.cli --check-search
python -m v2.cli --ask "29-year-old female with malar rash, photosensitivity, arthralgia, ANA+, anti-dsDNA+. Give top-5 differential diagnoses."
```

Expected:

- `--check-llm` prints `raw_response=LLM_OK`
- `--check-search` may return `count=0` in restricted networks, but this is non-fatal
- `--ask` should always return answer fields and a `log_path`

### Benchmark Sanity (official-aligned)

Official benchmark hard constraints:

- `LLM_MODEL` must be `deepseek-chat`
- Use real data source (`local`/`hf`), not smoke
- `DEEPRARE_REPO_PATH` must point to a valid DeepRare repo with `eval.py`

Run official benchmark modes (example `limit=10`, local csv):

```bash
export RAREBENCH_LOCAL_CSV=/absolute/path/to/rarebench_local_sample.csv

python -m v2.benchmark.deeprare \
  --mode plain_llm_deeprare_official \
  --limit 10 \
  --sample-order random \
  --seed 42 \
  --data-source local \
  --dataset-file "$RAREBENCH_LOCAL_CSV" \
  --official-eval

python -m v2.benchmark.deeprare \
  --mode react_agent_without_tool_deeprare_official \
  --limit 10 \
  --sample-order random \
  --seed 42 \
  --data-source local \
  --dataset-file "$RAREBENCH_LOCAL_CSV" \
  --official-eval

python -m v2.benchmark.deeprare \
  --mode react_agent_with_tool_deeprare_official \
  --limit 10 \
  --sample-order random \
  --seed 42 \
  --data-source local \
  --dataset-file "$RAREBENCH_LOCAL_CSV" \
  --official-eval
```

You can switch to `limit=5` for faster sanity checks.

### Showcase

See:

- `v2/logs/showcase_final/SHOWCASE_INDEX.md`

## Important Boundaries

- DeepRare benchmark is a sanity/eval harness.
- Showcase logs demonstrate constrained ReAct trajectory and auditability.
- Legacy directories are not the current official mainline.
- No claim of clinical utility.
- No claim of outperforming the DeepRare paper.
