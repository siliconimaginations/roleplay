# Roleplay

[![CI](https://github.com/siliconimaginations/roleplay/actions/workflows/ci.yml/badge.svg)](https://github.com/siliconimaginations/roleplay/actions/workflows/ci.yml)
[![Coverage](https://raw.githubusercontent.com/siliconimaginations/roleplay/badges/coverage.svg)](https://github.com/siliconimaginations/roleplay/tree/badges)

A multi-party interaction simulator. Configure parties — people, organizations, or environments — give them personas, memories, and goals, then watch LLM agents drive their interactions across discrete episodes.

## Use cases

- **Social simulation** — a small town where residents go about their lives, form relationships, and react to events
- **Organizational negotiation** — data center builders, grid operators, and transmission owners working through interconnection bottlenecks
- **Training & game development** — a clean API lets developers build games, training scenarios, or research tools on top

## Tech stack

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.12+ |
| Package manager | uv |
| LLM providers | Gemini (default), Claude, extensible |
| Persistence | SQLite (local dev), pluggable |
| CLI | Built-in (web UI planned) |
| CI/CD | GitHub Actions |

## Quick start

```bash
# Install uv (if you don't have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/siliconimaginations/roleplay.git
cd roleplay
uv sync --extra dev

# Run tests
uv run pytest

# Lint
bash scripts/lint.sh
```

## Project structure

```
roleplay/
├── src/roleplay/       # Core simulator library
├── tests/              # Unit + integration tests
├── docs/
│   ├── engineering/    # Per-module engineering design specs
│   └── process/        # Tech debt cadence, QA workflow
├── scripts/            # Dev utilities (lint.sh)
└── .github/
    └── workflows/      # CI (ci.yml) + Gemini review (gemini-review.yml)
```

## Contributing

See [ENGINEERING_PRINCIPLES.md](ENGINEERING_PRINCIPLES.md) — all contributors (human and AI) follow the same design-before-code workflow.

Work plan and stage breakdown: [WORK_PLAN.md](WORK_PLAN.md).

## License

AGPL-3.0 open source + commercial license available. See [LICENSE](LICENSE) and [LICENSE_COMMERCIAL](LICENSE_COMMERCIAL).
