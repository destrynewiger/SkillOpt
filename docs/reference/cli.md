# CLI Reference

> **Version note.** This reference tracks `main`. PyPI 0.2.0 does not yet
> include the generic research `openai_compatible` backend, Sleep handoff,
> Sleep support for non-Azure OpenAI-compatible endpoints, the Sleep
> `--preferences` flag, the research `cursor_exec` target harness, or Cursor
> source/backend/plugin support, Pi source/backend support, or VS Code Copilot
> transcript harvesting; use a source install from `main` for those features
> until the next release.

## Training

```bash
python scripts/train.py --config <config.yaml> [overrides...]
# Installed equivalent:
skillopt-train --config <config.yaml> [overrides...]
```

### Arguments

| Argument | Description |
|---|---|
| `--config` | Path to YAML config file (required) |
| `--cfg-options key=value [...]` | Override structured config parameters |

### Examples

```bash
# Basic training
python scripts/train.py \
  --config configs/searchqa/default.yaml \
  --out_root outputs/searchqa_run

# With overrides
python scripts/train.py \
  --config configs/searchqa/default.yaml \
  --cfg-options optimizer.learning_rate=16 optimizer.lr_scheduler=linear

# With custom initial skill
python scripts/train.py \
  --config configs/searchqa/default.yaml \
  --cfg-options env.skill_init=skills/my_seed.md
```

## Evaluation

```bash
python scripts/eval_only.py --config <config.yaml> --skill <skill.md>
# Installed equivalent:
skillopt-eval --config <config.yaml> --skill <skill.md>
```

### Arguments

| Argument | Description |
|---|---|
| `--config` | Path to YAML config file (required) |
| `--skill` | Path to skill document to evaluate (required) |
| `--split` | `train`, `valid_seen`, `valid_unseen`, or `all` (default) |
| `--cfg-options` | One or more `section.key=value` overrides |

### Examples

```bash
# Evaluate best skill on test set
python scripts/eval_only.py \
  --config configs/searchqa/default.yaml \
  --skill outputs/searchqa_run/best_skill.md \
  --split valid_unseen

# Evaluate on validation set
python scripts/eval_only.py \
  --config configs/searchqa/default.yaml \
  --skill outputs/searchqa_run/best_skill.md \
  --split valid_seen
```

`--skill` consumes the artifact produced by training. Unless `--out_root` is
set for evaluation, `eval_only.py` creates a separate timestamped
`outputs/eval_<env>_<model>_<timestamp>/` directory and writes
`eval_summary.json` there; it does not modify the training run directory.

For the generic OpenAI-compatible research backend, select the role backends
explicitly:

```bash
python scripts/train.py \
  --config configs/searchqa/default.yaml \
  --cfg-options \
    model.optimizer_backend=openai_compatible \
    model.target_backend=openai_compatible \
    model.optimizer=deepseek-chat \
    model.target=deepseek-chat
```

To benchmark an installed, authenticated Cursor Agent through an environment
that supports exec targets:

```bash
python scripts/eval_only.py \
  --config configs/searchqa/default.yaml \
  --skill skills/my_skill.md \
  --cfg-options \
    model.optimizer_backend=openai_chat \
    model.target_backend=cursor_exec \
    model.target=composer-2.5
```

`cursor_exec` runs the target only; the optimizer remains separately
configured. Read-only rollouts use Cursor Ask mode. Rollouts that request file
edits use `--force` inside the benchmark workspace, with Cursor sandboxing
enabled. The harness refuses file-edit rollouts when the Cursor sandbox is
disabled. Read-only Ask-mode rollouts may explicitly disable it. Override the
executable or sandbox through `model.cursor_exec_path` and
`model.cursor_exec_sandbox`.

## SkillOpt-Sleep

```bash
skillopt-sleep <action> [options]
# Equivalent from a source checkout:
python -m skillopt_sleep <action> [options]
```

Actions are `run`, `dry-run`, `status`, `adopt`, `harvest`, `schedule`, and
`unschedule`. Common options include:

| Argument | Description |
|---|---|
| `--project PATH` | Project used for transcript scope, targets, state, and staging (default: current directory) |
| `--scope invoked\|all` | Harvest this project or all projects |
| `--source claude\|codex\|copilot\|cursor\|pi\|auto` | Transcript source; `auto` keeps Codex-then-Claude precedence and does not select Copilot, Cursor, or Pi |
| `--backend mock\|claude\|codex\|copilot\|cursor\|pi\|handoff\|azure_openai` | Replay/optimizer backend |
| `--model NAME` | Backend-specific model override |
| `--cursor-home PATH` | Override `~/.cursor` for Cursor transcript harvesting |
| `--pi-home PATH` | Parent directory containing Pi's `agent/sessions` tree (default: `~/.pi`) |
| `--vscode-workspace-storage PATH` | Override VS Code's `User/workspaceStorage` root for Copilot transcript harvesting |
| `--cursor-path PATH` | Path to the installed Cursor Agent CLI |
| `--pi-path PATH` | Path to the installed Pi coding-agent CLI |
| `--preferences TEXT` | House rules supplied to reflection |
| `--lookback-hours N` | Initial transcript lookback; `0` scans all history |
| `--max-sessions N` / `--max-tasks N` | Bound the harvested workload |
| `--target-skill-path PATH` | Explicit skill document to stage/adopt |
| `--tasks-file PATH` | Replay a reviewed task JSON file instead of harvesting |
| `--edit-budget N` | Maximum bounded edits for the night |
| `--progress` / `--json` | Progress or machine-readable output |
| `--auto-adopt` | Apply an accepted staged proposal automatically |

The `mock` and `handoff` backends make no network calls. A real backend sends
mining, replay, judging, and reflection prompts derived from harvested
transcripts and tasks to its selected provider. Review that provider's
data-retention and privacy policy before processing sensitive sessions.

### VS Code GitHub Copilot Chat source

`--source copilot` reads local VS Code GitHub Copilot Chat session logs from
the platform's stable and Insiders `User/workspaceStorage` locations, plus the
portable location when `VSCODE_PORTABLE` is available. Use
`--vscode-workspace-storage PATH` for a nonstandard root. Each workspace must
have a readable adjacent `workspace.json` mapping to a local project; unmapped
windows are skipped so project scoping cannot silently mix unrelated sessions.
`--source auto` does not select Copilot.

The harvester retains user-entered prompts, visible assistant Markdown, and
tool names from requests confirmed as GitHub Copilot Chat. It excludes
system-initiated notifications, reasoning, tool inputs and outputs, rendered
context, and account/model metadata. Known secret-shaped strings are redacted
as defense in depth, but review harvested tasks before sending them to a model
provider. Transcript harvesting is independent of `--backend copilot`, which
invokes the GitHub Copilot CLI for model calls.

The managed `schedule` command does not persist `--source` or
`--vscode-workspace-storage`. Before scheduling this source, set
`"transcript_source": "copilot"` and, when needed,
`"vscode_workspace_storage": "/absolute/path/to/workspaceStorage"` in
`~/.skillopt-sleep/config.json`.

### Pi source and backend

`--source pi` reads local session JSONL files below
`~/.pi/agent/sessions`; use `--pi-home PATH` to select the parent directory that
contains `agent/sessions`. This local source does not require the Pi CLI or
provider authentication. It retains user/assistant text, tool names, and lexical
feedback found in user text, while excluding thinking, tool arguments, tool
outputs, images, and unrelated metadata. The absolute project `cwd` from the
session header is retained for scope filtering and may appear in miner prompts
sent to a real backend and its provider. Known secret-shaped strings in retained
message text are redacted as defense in depth, not as a guarantee. Pi is an explicit source: `--source auto`
retains Codex-then-Claude precedence and does not select it.

Transcript source and model backend are independent. `--backend pi` launches a
locally installed, authenticated Pi CLI and makes real provider calls for
mining, replay, judging, and reflection. Use `--pi-path PATH` to select its
executable and `--model NAME` to override its configured model:

```bash
skillopt-sleep run --project "$(pwd)" \
  --source pi --backend pi --pi-path /absolute/path/to/pi \
  --model provider/model --max-sessions 5 --max-tasks 3 --progress
```

For these calls, SkillOpt disables Pi tools, skills, context files, extensions,
prompt templates, themes, and session writes. Pi authentication and model
configuration remain available. It also enables Pi's offline startup mode, so
configured npm/git packages are not installed or updated and model catalogs are
not refreshed; the selected model provider is still contacted for generation.
These controls should not be treated as permanent or complete isolation. The
provider selected in Pi receives the transcript-derived prompts.

The managed `schedule` command preserves the backend but not `--source`,
`--pi-home`, `--pi-path`, or `--model`. Before scheduling Pi, put
`transcript_source`, `pi_home`, `pi_path`, and `model` in
`~/.skillopt-sleep/config.json`; use an absolute `pi_path` and verify
authentication for the scheduled account.

### Cursor source and backend

`--source cursor` reads local Cursor JSONL transcripts from
`~/.cursor/projects/<workspace>/agent-transcripts/*/*.jsonl`. Invoked scope uses
Cursor's recorded workspace path, including when `--project` is a nested
directory, and falls back to the sanitized storage name when metadata is not
available. `--scope all` scans every workspace below `cursor_home`. The
harvester retains user/assistant text, explicit turn errors, and tool names,
while excluding tool arguments, tool outputs, and non-message records. It
redacts known secret patterns and filters SkillOpt-generated replay sessions,
but redaction is not a guarantee that outbound prompts contain no sensitive
data.

`--backend cursor` launches an installed, authenticated `cursor-agent`, sends
prompts over stdin, and parses its JSON result. SkillOpt reads the target skill
and includes its text in replay prompts; it does not invoke that file as a native
Cursor skill. Ordinary mining, replay, judging, and reflection calls use
read-only Ask mode in a new empty temporary workspace. Project file reads, file
writes, and MCP tools are denied. `--project` does not change that execution
workspace.

Cursor tool-aware replay is temporarily disabled pending live Cursor
permission-boundary validation. A task with a `tool_called` check fails nonzero
before Agent mode starts and does not stage, adopt, cache, persist state, or
advance the harvest checkpoint. Use another backend for such tasks. The current
Cursor backend therefore does not provide end-to-end validation for skills that
need repository inspection, real CLIs, browsers, running services, or file
changes.

There is no implemented fresh-worktree Cursor replay. If a report says
`replay: mock`, that is the prompt-replay label and does not mean the mock model
backend was selected. Both `run` and `dry-run` perform real-backend provider
calls; `dry-run` suppresses staging, adoption, and persisted state changes, not
spend. Session and task limits do not impose hard provider-call, token, time, or
monetary budgets.
Cursor and its selected model provider can receive the prompt content.

Cursor-specific settings are available through the CLI, config, and environment:

| Purpose | CLI | `~/.skillopt-sleep/config.json` | Environment |
|---|---|---|---|
| Transcript home | `--cursor-home PATH` | `"cursor_home": "/path/to/.cursor"` | none |
| Agent executable | `--cursor-path PATH` | `"cursor_path": "/path/to/cursor-agent"` | `SKILLOPT_SLEEP_CURSOR_PATH` |
| Model | `--model NAME` | `"model": "NAME"` | `SKILLOPT_SLEEP_CURSOR_MODEL` |

Use `cursor-agent --list-models` to inspect model identifiers available to the
authenticated account. When cost depends on a model variant, confirm the billed
variant in Cursor's usage reporting rather than relying only on its display
name.

Target the learned project skill explicitly so accepted updates are visible to
Cursor without modifying the plugin's own `skillopt-sleep` workflow skill:

```bash
skillopt-sleep run --project "$(pwd)" \
  --source cursor --backend cursor \
  --target-skill-path .cursor/skills/skillopt-sleep-learned/SKILL.md \
  --max-sessions 5 --max-tasks 3 --progress
```

The first harvest uses a 72-hour lookback unless `--lookback-hours` is set. A
value of `0` considers all available history while still respecting
`--max-sessions`. A stateful `run`, including a run that mines no tasks, records
a new harvest checkpoint; subsequent runs use that checkpoint rather than the
initial lookback. Use `harvest` or `dry-run` to verify counts before the first
stateful run.

The managed `schedule` command persists the project, backend, time, and optional
auto-adopt setting only. It does not copy source, Cursor paths, model, or target
skill flags into the scheduled command. Put `transcript_source`, `cursor_home`,
`cursor_path`, `model`, and `target_skill_path` in the user config before
scheduling Cursor. Keep `target_skill_path` project-relative as
`.cursor/skills/skillopt-sleep-learned/SKILL.md`, prefer an absolute
`cursor_path`, and verify authentication for the scheduled account because cron
and Task Scheduler may have a minimal environment.

Backend-specific setup for compatible endpoints is documented in
[OpenAI-compatible endpoints for SkillOpt-Sleep](../sleep/openai-compatible-endpoints.md).

## WebUI

```bash
python -m skillopt_webui.app [--port PORT] [--share]
```

| Argument | Default | Description |
|---|---|---|
| `--port` | 7860 | Port number |
| `--host` | `0.0.0.0` | Server bind address |
| `--share` | false | Create public Gradio link |

The default host binds every network interface. Use `--host 127.0.0.1` when
the dashboard should be reachable only from the local machine.
