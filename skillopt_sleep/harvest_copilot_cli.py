"""SkillOpt-Sleep GitHub Copilot CLI session harvesting.

The Copilot CLI keeps a global SQLite index at ``~/.copilot/session-store.db``
that already carries everything a :class:`SessionDigest` needs: per-session
``cwd``/``branch`` and per-turn user/assistant text. Reading it is far cheaper
than replaying the multi-gigabyte per-session ``events.jsonl`` logs, and it is
the same data the CLI itself exposes.

The store is written by live CLI sessions, so all reads go through a read-only
connection and fall back to a private snapshot when the live WAL cannot be
opened read-only. A harvest must never block or corrupt an in-flight session.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from typing import Any, List, Optional
from urllib.request import pathname2url

from skillopt_sleep.harvest import (
    _detect_feedback,
    _is_agent_session,
    _is_headless_replay,
    _is_meta_prompt,
    _project_matches,
)
from skillopt_sleep.staging import redact_secrets
from skillopt_sleep.types import SessionDigest

# Bound per-session text so one pathological session cannot dominate a night's
# harvest. Mining only needs intent, not a full transcript. Only the final few
# assistant answers matter downstream (mining reads assistant_finals[-1]), so
# we keep just the last few, consistent with the other harvesters.
_MAX_PROMPTS_PER_SESSION = 40
_MAX_FINALS_PER_SESSION = 5
_MAX_TEXT_CHARS = 4000


def default_session_store() -> str:
    """Return the default Copilot CLI session-store path."""
    return os.path.join(os.path.expanduser("~"), ".copilot", "session-store.db")


def _clip(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    # Redact before truncating: clipping first could retain and persist only a
    # secret fragment that no longer matches the shared redaction patterns.
    text = str(redact_secrets(text)).strip()
    return text[:_MAX_TEXT_CHARS]


def _ro_uri(path: str) -> str:
    """Build a read-only ``file:`` URI from a filesystem path.

    String-interpolating a raw path breaks on Windows (backslashes, ``C:``
    drive letters) and on any path containing URI-special characters, and can
    silently prevent the read-only open. ``pathname2url`` produces a correctly
    escaped, absolute URI on every platform.
    """
    return "file:" + pathname2url(os.path.abspath(path)) + "?mode=ro"


def _norm_ts(value: Any) -> str:
    """Normalize ``YYYY-MM-DD HH:MM:SS`` to ISO ``T`` form.

    The Copilot CLI store uses a space separator, but the shared
    :func:`_is_headless_replay` duration heuristic strptimes a ``T``-separated
    timestamp; without this, short programmatic sessions slip past the filter.
    """
    if not isinstance(value, str):
        return ""
    v = value.strip()
    if len(v) >= 19 and v[10] == " ":
        v = v[:10] + "T" + v[11:]
    return v


def _connect(store_path: str) -> tuple[sqlite3.Connection, Optional[str]]:
    """Open ``store_path`` read-only, snapshotting if the live WAL blocks it.

    Returns the connection and the temp directory to clean up, if any. On any
    failure the temp directory is removed and the error is re-raised so the
    caller can fail closed.
    """
    con = None
    try:
        con = sqlite3.connect(_ro_uri(store_path), uri=True, timeout=0)
        con.execute("SELECT 1 FROM sessions LIMIT 1").fetchone()
        return con, None
    except sqlite3.Error:
        # Close the half-open connection before falling back to a snapshot.
        if con is not None:
            con.close()

    tmpdir = tempfile.mkdtemp(prefix="skillopt-sleep-copilot-cli-")
    try:
        snapshot = os.path.join(tmpdir, "session-store.db")
        shutil.copyfile(store_path, snapshot)
        for suffix in ("-wal", "-shm"):
            sidecar = store_path + suffix
            if os.path.exists(sidecar):
                shutil.copyfile(sidecar, snapshot + suffix)
        snap_con = sqlite3.connect(_ro_uri(snapshot), uri=True, timeout=0)
        # Validate the snapshot schema too, so a later query cannot abort the run.
        snap_con.execute("SELECT 1 FROM sessions LIMIT 1").fetchone()
        return snap_con, tmpdir
    except (sqlite3.Error, OSError):
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


def harvest_copilot_cli(
    session_store: str = "",
    *,
    scope: Any = "all",
    invoked_project: str = "",
    since_iso: Optional[str] = None,
    limit: int = 0,
) -> List[SessionDigest]:
    """Read the Copilot CLI session store and return digests matching scope/time."""
    store_path = session_store or default_session_store()
    if not os.path.isfile(store_path):
        return []

    try:
        con, tmpdir = _connect(store_path)
    except (sqlite3.Error, OSError):
        # A harvest must never block or abort a run: a locked, unreadable, or
        # permission-denied store simply yields no digests.
        return []
    try:
        con.row_factory = sqlite3.Row
        params: list[Any] = []
        where = ""
        if since_iso:
            # since_iso is a cutoff on when a session *ended*, matching the
            # other harvesters; filter on updated_at so a long-lived session
            # that started earlier but ended after the cutoff is still kept.
            # Timestamps mix "YYYY-MM-DD HH:MM:SS" and ISO-8601 text, which only
            # compare safely at day granularity.
            where = "WHERE substr(updated_at, 1, 10) >= substr(?, 1, 10)"
            params.append(since_iso)
        rows = con.execute(
            "SELECT id, cwd, repository, branch, created_at, updated_at "
            f"FROM sessions {where} ORDER BY updated_at DESC",
            params,
        ).fetchall()

        digests: List[SessionDigest] = []
        for row in rows:
            # A session without a stable cwd cannot be scoped (_project_matches
            # needs an abspath) and would collide with others when mine.py
            # hashes project+intent, so skip it -- as harvest_copilot() does.
            project = row["cwd"]
            if not project:
                continue
            if not _project_matches(project, scope, invoked_project):
                continue

            turns = con.execute(
                "SELECT user_message, assistant_response FROM turns WHERE session_id = ? ORDER BY turn_index",
                (row["id"],),
            ).fetchall()

            prompts: List[str] = []
            finals: List[str] = []
            feedback: List[str] = []
            n_user = 0
            n_asst = 0
            for turn in turns:
                user_text = _clip(turn["user_message"])
                if user_text:
                    n_user += 1
                    feedback.extend(_detect_feedback(user_text))
                    if not _is_meta_prompt(user_text) and len(prompts) < _MAX_PROMPTS_PER_SESSION:
                        prompts.append(user_text)
                asst_text = _clip(turn["assistant_response"])
                if asst_text:
                    n_asst += 1
                    # Keep only the last few answers (rolling window) so a long
                    # session does not balloon the digest.
                    finals.append(asst_text)
                    if len(finals) > _MAX_FINALS_PER_SESSION:
                        finals.pop(0)

            if not prompts:
                continue

            files = [
                r["file_path"]
                for r in con.execute(
                    "SELECT DISTINCT file_path FROM session_files "
                    "WHERE session_id = ? ORDER BY file_path",
                    (row["id"],),
                )
                if r["file_path"]
            ]
            tools = sorted(
                {
                    r["tool_name"]
                    for r in con.execute(
                        "SELECT DISTINCT tool_name FROM session_files WHERE session_id = ?",
                        (row["id"],),
                    )
                    if r["tool_name"]
                }
            )

            digests.append(
                SessionDigest(
                    session_id=str(row["id"]),
                    project=project,
                    git_branch=row["branch"] or "",
                    started_at=_norm_ts(row["created_at"]),
                    ended_at=_norm_ts(row["updated_at"]),
                    user_prompts=prompts,
                    assistant_finals=finals,
                    tools_used=tools,
                    files_touched=files,
                    feedback_signals=sorted(set(feedback)),
                    n_user_turns=n_user,
                    n_assistant_turns=n_asst,
                    raw_path=f"{store_path}#{row['id']}",
                )
            )
            # SkillOpt's own Copilot backend calls land in this same store, so
            # an unfiltered harvest would train the engine on its own output.
            if _is_headless_replay(digests[-1]) or _is_agent_session(digests[-1]):
                digests.pop()
                continue
            if limit and len(digests) >= limit:
                break
        return digests
    except sqlite3.Error:
        # Schema drift or mid-read corruption must not abort the run.
        return []
    finally:
        con.close()
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
