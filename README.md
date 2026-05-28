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
cp .env.example .env
```

### Benchmark Sanity (official-aligned)

Check runtime config:

```bash
python -m v2.cli --check-llm
```

Run official benchmark modes (example `limit=10`):

```bash
python -m v2.benchmark.deeprare \
  --mode plain_llm_deeprare_official \
  --limit 10 \
  --sample-order random \
  --seed 42 \
  --data-source local \
  --dataset-file /home/shuotong/DeepRare/dataset/rarebench_local/rarebench_local_sample.csv \
  --official-eval

python -m v2.benchmark.deeprare \
  --mode react_agent_without_tool_deeprare_official \
  --limit 10 \
  --sample-order random \
  --seed 42 \
  --data-source local \
  --dataset-file /home/shuotong/DeepRare/dataset/rarebench_local/rarebench_local_sample.csv \
  --official-eval

python -m v2.benchmark.deeprare \
  --mode react_agent_with_tool_deeprare_official \
  --limit 10 \
  --sample-order random \
  --seed 42 \
  --data-source local \
  --dataset-file /home/shuotong/DeepRare/dataset/rarebench_local/rarebench_local_sample.csv \
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
