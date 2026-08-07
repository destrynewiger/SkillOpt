# Contributing to SkillOpt

Thank you for your interest in contributing! SkillOpt welcomes contributions of all kinds.

## Getting Started

```bash
git clone https://github.com/microsoft/SkillOpt.git
cd SkillOpt
python -m pip install -e ".[dev,docs]"
```

## How to Contribute

### 🐛 Bug Reports
Open a GitHub issue with reproduction steps, expected/actual behavior, and your config file (remove API keys).

### 🔧 Add a Benchmark
See the [guide](docs/guide/new-benchmark.md) and use the scaffold at
`skillopt/envs/_template/`. Register the adapter lazily in both
`scripts/train.py` and `scripts/eval_only.py`, and add focused tests.

### 🤖 Add a Model Backend
First check whether the built-in `openai_compatible` backend covers the
provider. Otherwise follow the function-based backend contract in the
[backend guide](docs/guide/new-backend.md), including routing, configuration,
token accounting, and no-network tests.

### 📝 Improve Documentation
```bash
python -m mkdocs serve   # Preview at http://localhost:8000
```

## Pull Request Process

1. Fork the repo and create a feature branch
2. Make changes and run focused tests plus `python -m pytest -q`
3. Submit a PR with a clear description
4. For documentation changes, run `python -m mkdocs build --strict`
5. Ensure CI passes

## Code Style
- Follow existing patterns in the codebase
- Use type hints for function signatures
- Keep docstrings concise

## License
By contributing, you agree your contributions are licensed under the [MIT License](LICENSE).
