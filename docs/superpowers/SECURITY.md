# Security Considerations for Superpowers Adapter

## Scope: trusted candidates only

This adapter evaluates **trusted, locally-authored** candidate skills. It is
explicitly **not** hardened against a hostile candidate, and must not be pointed
at model-generated or third-party skills.

There is no OS-level boundary. The evaluated agent gets `Bash`, `Read`, `Write`
and `Edit`, and runs as the same OS user as the harness, so it can:

- Execute arbitrary shell commands
- Read/write any file that user can reach, including the harness's own evidence
- Read the environment passed to the process
- Make unrestricted network requests

`--allowedTools` scopes which tools the agent may call. It is **not** an
isolation boundary.

## What the adapter does do

1. **No host credential reuse by default.** The scenario `HOME` is empty; host
   `~/.claude/credentials.json` and `settings.json` are never copied or
   symlinked. Reuse is opt-in via `SKILLOPT_HOST_AUTH=1`, which warns.
2. **Fail closed.** With neither `ANTHROPIC_API_KEY` nor `SKILLOPT_HOST_AUTH=1`,
   the scenario errors (`NO_AUTH`) instead of running unauthenticated.
3. **Scrubbed environment.** Only `HOME`, `PATH`, `TERM`, `LANG` and (if set)
   `ANTHROPIC_API_KEY` are passed; the host environment is not inherited. `PATH`
   is minimal by default (shim dir + `/usr/bin:/bin`); opt in to the host `PATH`
   with `SKILLOPT_INHERIT_PATH=1`. Hygiene, not a boundary — a `Bash`-holding
   agent can still call absolute paths.
4. **Isolated project and HOME** per scenario, inside a temp workspace.
5. **Execution evidence.** All of it is tamper-**evident**, not tamper-proof —
   the agent can reach the shim, the nonce and the audit log, and can modify the
   project tree the harness re-runs from. It is meaningful because the candidate
   is trusted; it is not an adversarial oracle.
   - `harness_test_passes` — the harness re-runs the protected test paths after
     the agent exits, ignoring project pytest config and `conftest.py`, so agent
     *output* alone cannot fake a pass. A pass requires at least one executed
     passing test and no failures, errors or skips.
   - `pytest_runs` — count of nonce-tagged invocations of the `pytest`/`python`
     shims.
   - `pytest_successes` / `pytest_failures` — completed shim invocations are
     classified from JUnit results; exit code 0 without an executed passing test
     (for example `pytest --help` or an all-skipped run) is inconclusive and is
     counted as neither a success nor a failure.
   - `pytest_after_edit` — the last shim invocation is newer than the newest
     project `*.py`, so "fix, then claim done without re-running" fails.
   - Protected scenario fixtures are hashed before the agent runs. A modified,
     deleted or symlink-replaced fixture fails the scenario and is not executed
     by the harness re-run.

   ⚠️ The verification re-run **executes agent-modified project code on the
   host**. `ANTHROPIC_API_KEY` is dropped from that re-run's environment, but
   nothing else confines it.

## Known Limitations

- **API key exposure**: `ANTHROPIC_API_KEY`, if set, is visible to the agent
  process. Use a scoped/disposable key.
- **`SKILLOPT_HOST_AUTH=1` exposes host credentials** to the candidate.
- **`SKILLOPT_UNSAFE=1`** disables permission checks entirely.
- **No network isolation**, for either the agent or the verification re-run.

## Usage

```bash
ANTHROPIC_API_KEY=... python -m skillopt_sleep.adapters.superpowers --candidate my_skill.md
```

## Follow-up Work

Supporting untrusted candidates is deliberately out of scope for this adapter as
shipped. It would need, at minimum:

- [ ] Verification oracle and evidence (nonce, shim, audit log) held outside
      every agent-writable mount
- [ ] Harness re-run from an immutable copy of the test inputs
- [ ] A validated OS-level sandbox: published image with Claude Code + pytest,
      exercised in CI, fail-closed on an unrecognised mode
- [ ] Network egress allowlist (api.anthropic.com only)
- [ ] Per-run scoped API keys
