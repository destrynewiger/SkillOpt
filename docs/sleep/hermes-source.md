# Owner-scoped Hermes exports and recovery fixes

Hermes harvesting is opt-in. The default remains the Claude source, mock backend and review-gated adoption. Selecting `hermes` never scans a home directory, imports global skills, enables cron sessions, or calls a provider.

## Input contract

This adapter accepts `skillopt.hermes.session.v1`, an owner-qualified export of the Hermes JSON session shape. An unlabelled native Hermes session is rejected. The exporting owner must establish the source account and project before adding the export envelope. A directory name or the project passed to SkillOpt is not evidence of ownership. Keep personal, Byteport, Rootly, John and operations exports in separate roots, manifests, state and project directories. Do not include another owner's conversation in an export, even if the current account can read it.

Each selected JSON object contains:

- `format`: `skillopt.hermes.session.v1`.
- `owner`: one literal identifier matching the manifest and `--hermes-owner`.
- `project`: one canonical absolute project path matching the manifest and `--project`.
- `session_id`: the original nonempty session identifier, at most 512 characters. Output contains a SHA-256-derived identifier, never this raw value.
- `session_start` and `last_updated`: valid ISO timestamps with seconds and an explicit UTC `Z` or `+00:00` suffix. Start cannot follow end; end cannot exceed the verification clock by more than 60 seconds.
- `platform` and `messages`: native platform and a bounded message array. Message objects have `role` and string/text-block `content`. User and assistant text is retained after secret redaction. System prompts, tool results, arguments, title, model and arbitrary metadata are omitted. Tool names are accepted only when short, identifier-shaped and unchanged by secret redaction.

Cron sessions are always excluded. `HERMES_INCLUDE_CRON` has no effect. Secret-looking project paths are rejected rather than silently changing the destination identity. Source provenance is `hermes:sha256:<digest>`; filesystem paths and raw session identifiers are not copied into the digest.

The manifest object contains `format: skillopt.hermes.manifest.v1`, `owner`, `project`, `sessions_dir`, `generated_at`, `expires_at`, and `sources`. Each source entry has exactly `path` and `sha256`. Paths are direct filenames matching `session_[A-Za-z0-9_-]+.json`, never globs, directories or traversal. Every selected byte sequence is pinned by SHA-256. Duplicate source paths, hashes or session identities are rejected. Manifest validity is at most 24 hours; expired or future grants fail closed. Renew a manifest when source bytes change.

Both manifest and sources must be regular files with one hard link. Every directory component and file is opened without following symlinks. FIFOs are rejected without blocking. Platforms lacking POSIX directory-descriptor/no-follow access fail closed. Use canonical paths such as `/private/var/...` on macOS when `/var` is a symlink.

Limits are 128 KiB per manifest, 200 sources, 2 MiB per source, 20 MiB per batch, 2,000 messages per session, 16,384 characters per message and 65,536 retained text characters and 128 distinct tool names per digest. An oversized, malformed, missing, changed or wrong-owner source fails the entire batch. Result limits and lookback filters never bypass validation of later allowlisted files. There is no directory enumeration.

## Prepare a manifest

After source ownership and export format have been qualified, create a private manifest from exactly the selected exports. This example accepts the export directory, project, owner and explicit filenames as arguments. It does not add or change ownership labels in the source. Run it only against that owner's approved export copies:

```sh
python - /absolute/owned/exports /absolute/owned/project personal session_selected.json <<'PY'
import hashlib, json, os, sys
from datetime import datetime, timedelta, timezone
root, project, owner, *names = sys.argv[1:]
from skillopt_sleep.harvest_hermes import _absolute, _open_directory, _read_at, _json, _digest, MAX_SOURCE_BYTES
root, project = _absolute(root), _absolute(project)
if not names:
    raise ValueError("select at least one qualified source")
now = datetime.now(timezone.utc)
fd = _open_directory(root)
sources = []
try:
    import re
    for name in names:
        if not re.fullmatch(r'session_[A-Za-z0-9_-]{1,120}\.json', name):
            raise ValueError('unsafe source filename')
        raw = _read_at(fd, name, MAX_SOURCE_BYTES)
        sha = hashlib.sha256(raw).hexdigest()
        _digest(_json(raw), owner=owner, project=project, source_hash=sha, now=now)
        sources.append({'path': name, 'sha256': sha})
finally:
    os.close(fd)
payload = dict(format='skillopt.hermes.manifest.v1', owner=owner, project=project,
               sessions_dir=root, generated_at=now.isoformat(),
               expires_at=(now + timedelta(hours=1)).isoformat(), sources=sources)
# An existing manifest is never overwritten by this example.
out = os.open(os.path.join(root, 'manifest.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(out, 'w') as stream:
    json.dump(payload, stream, indent=2)
    stream.flush()
    os.fsync(stream.fileno())
PY
```

Then inspect the harvested task payload using explicit arguments:

```sh
python -m skillopt_sleep harvest --source hermes \
  --hermes-sessions-dir /absolute/owned/exports \
  --hermes-manifest /absolute/owned/exports/manifest.json \
  --hermes-owner personal --project /absolute/owned/project \
  --claude-home /absolute/owned/isolated-home --json
```

`harvest` produces a task payload marked unreviewed. Source ownership, manifest hashes and valid timestamps do not authorize real model replay or adoption. Review the redacted task payload and preserve the existing review gate. `auto` retains its existing Codex-then-Claude behavior; it does not include Hermes. A configured project list is accepted only when it contains exactly the invoked project; `all` is rejected.

## Backend and handoff recovery

Claude clears `last_call_error` before text and tool attempts. Failed or empty text calls are not cached; a cached success clears an unrelated previous error. Both paths are covered by failed-then-success tests with mocked processes.

Keep one active handoff invocation per isolated state directory. A corrupt `tasks.json` or `digests.json` pin is copied to `<state_dir>/handoff-recovery/` before removal and re-mining. The directory is mode 0700 and each recovery copy is mode 0600. Recovery failure leaves the original pin in place and fails visibly. Corrupt bytes are never printed. A completed no-task run saves its harvest window and removes the digest pin, allowing the next run to discover new work. Dry runs do not advance the window, remove existing pins, create new pins or create recovery copies. Existing handoff prompt/answer exchange semantics remain unchanged.

MiniMax text and message optimizer calls use `OPTIMIZER_DEPLOYMENT`; target calls use `TARGET_DEPLOYMENT`. Calling `set_optimizer_deployment("")` restores fallback to the current target, including later target changes. The facade retains the current OpenAI-compatible and Codex setters alongside the new MiniMax setter.

## Validation, rollout and rollback

Run `python -m pytest -q` in an isolated home with no credentials. Focused acceptance lives in `tests/test_harvest_hermes.py`, `tests/test_backend_handoff_recovery.py`, `tests/test_minimax_backend.py`, `tests/test_handoff_backend.py` and `tests/test_sleep_engine.py`. The Hermes fixtures are synthetic; they prove the contract and negative controls, not installed Hermes export compatibility or account attribution. No real transcript, provider call, scheduler, skill installation or adoption is part of source verification.

Merge the source first. Do not change existing source/backend/adoption defaults, install schedules, or run against `~/.hermes`. Before a real canary, qualify the installed export schema with one redacted owner-approved fixture, establish the literal account/project owner, create isolated state and a short-lived exact-file manifest, then run `harvest`. Check one digest/task and a repeated harvest for stable identity and secret-free provenance. A wrong-owner, expired, modified or unqualified source must fail. Only a separately reviewed task payload may proceed through the existing replay gate. Provider delivery and adoption remain unverified until their own receipts exist.

To roll back an inactive source rollout, revert the implementation commit or select the prior explicit transcript source. Do not delete transcripts, recovery copies, state or user skills. To restore an interrupted pin for diagnosis, stop that isolated handoff invocation, copy the matching private `.corrupt` file to a separate diagnostic directory, inspect it privately, repair the schema and rerun tests before restoring it as a pin. Do not restore malformed bytes over a newer valid snapshot. An existing failed cached response requires a new backend instance; the new implementation avoids creating such cache entries.
