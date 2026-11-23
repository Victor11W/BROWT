# Working on BROWT

The current direction is research and scientific visualization. Read
[the plan](docs/roadmap.qmd), [the protocol](docs/methodology.qmd) and
[the evidence log](docs/progress.qmd) before starting a new milestone.

## Development

Use Python 3.11 or 3.12 and an isolated environment:

```text
python -m pip install -e ".[research,dev]"
python -m pytest
python -m ruff check .
python -m ruff format --check src tests
```

The CPU tools must remain usable without Ultralytics, CUDA or an emulator.
Heavy dependencies are imported only in their execution paths.

## Experimental evidence

Keep one unique directory per run. Preserve source datasets, frozen partitions
and original checkpoints. Record actual library versions and input hashes.
Keep observed and synthetic data distinguishable.

A smoke test is not a scientific result. A double of a learning library
validates orchestration, not accuracy or learning. Never publish invented
metrics or treat a missing result as zero.

Before promoting a conclusion, connect it to a clean archived revision,
configurations, data, checkpoints and the exact analysis script.
Define the evaluation protocol before using the final test.

## Documentation

CI and publication both use Quarto 1.11.9.

Update implementation, tests and the evidence log together.
Keep planned features separate from verified behavior.
Generate figures with the versioned code:

```text
python -m BROWT demo --output runs/research --figures docs/assets/figures
quarto render docs --fail-if-warnings
```

The demonstration uses a synthetic geometry fixture. Its CPU conversion
microbenchmark is not a detector or gameplay benchmark.
Public documentation changes only after a deliberate publication.
