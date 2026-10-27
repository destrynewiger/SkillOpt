"""Read only explicitly attested Hermes exports. Never scan a global home.

The manifest pins source bytes, owner and project for at most 24 hours. Each
export must carry matching owner/project metadata. Native unlabelled sessions
require owner qualification before use; selecting a project cannot relabel them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from typing import Any

from skillopt_sleep.harvest import _detect_feedback, _is_meta_prompt, _text_from_content
from skillopt_sleep.staging import redact_secrets
from skillopt_sleep.types import SessionDigest

MAX_MANIFEST_BYTES = 128 * 1024
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_SOURCES = 200
MAX_MESSAGES = 2000
MAX_TEXT_CHARS = 16_384
MAX_DIGEST_CHARS = 65_536
MAX_TOOL_NAMES = 128
_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_FILE = re.compile(r"session_[A-Za-z0-9_-]{1,120}\.json\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")


class HermesSourceError(ValueError):
    """Invalid authority or source; messages never include transcript content."""


def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise HermesSourceError("Hermes timestamp must be an explicit UTC ISO value")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value):
        raise HermesSourceError("Hermes timestamp must be an explicit UTC ISO value")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HermesSourceError("Hermes timestamp is invalid") from None


def _absolute(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value:
        raise HermesSourceError("Hermes path must be an absolute canonical path")
    if not os.path.isabs(value) or os.path.normpath(value) != value or value.startswith("//"):
        raise HermesSourceError("Hermes path must be an absolute canonical path")
    if redact_secrets(value) != value:
        raise HermesSourceError("Hermes path contains sensitive metadata")
    return value


def _open_directory(path: str) -> int:
    """Walk from / with openat and no-follow; refuse unsupported platforms."""
    _absolute(path)
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise HermesSourceError("Hermes safe file access is unsupported on this platform")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.split("/")[1:]:
            if not component:
                continue
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except OSError:
        os.close(fd)
        raise HermesSourceError("Hermes source directory is unavailable or unsafe") from None


def _read_at(directory: int, name: str, maximum: int) -> bytes:
    try:
        # Nonblocking open avoids hanging on a FIFO before the regular-file check.
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
                raise HermesSourceError("Hermes source must be a bounded, single-link regular file")
            data = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
            if len(data) > maximum or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns
            ):
                raise HermesSourceError("Hermes source changed during reading or exceeded its limit")
            return data
    except OSError:
        raise HermesSourceError("Hermes source is unavailable or unsafe") from None


def _json(data: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise HermesSourceError("Hermes JSON has duplicate keys")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise HermesSourceError("Hermes JSON contains a non-finite number")

    try:
        value = json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise HermesSourceError("Hermes JSON is malformed") from None
    if not isinstance(value, dict):
        raise HermesSourceError("Hermes JSON must contain an object")
    return value


def _text(content: Any) -> str:
    if isinstance(content, list) and any(
        not isinstance(block, dict) or (block.get("type") == "text" and not isinstance(block.get("text"), str))
        for block in content
    ):
        raise HermesSourceError("Hermes text blocks are malformed")
    text = _text_from_content(content).replace("\x00", "").strip()
    try:
        text.encode("utf-8")
    except UnicodeError:
        raise HermesSourceError("Hermes text is not valid Unicode") from None
    if len(text) > MAX_TEXT_CHARS:
        raise HermesSourceError("Hermes message exceeds its text limit")
    # Preserve no injected delivery instructions, including malformed blocks.
    if text.startswith("[IMPORTANT"):
        depth = 0
        for index, char in enumerate(text):
            depth += (char == "[") - (char == "]")
            if depth == 0:
                text = text[index + 1:].strip()
                break
        else:
            return ""
    if _is_meta_prompt(text):
        return ""
    return str(redact_secrets(text))


def _digest(data: dict, *, owner: str, project: str, source_hash: str, now: datetime) -> SessionDigest | None:
    if data.get("format") != "skillopt.hermes.session.v1":
        raise HermesSourceError("Hermes export schema is not qualified")
    if data.get("owner") != owner or data.get("project") != project:
        raise HermesSourceError("Hermes export owner or project does not match its manifest")
    sid = data.get("session_id")
    if not isinstance(sid, str) or not sid or len(sid) > 512:
        raise HermesSourceError("Hermes session identifier is invalid")
    started, ended = _utc(data.get("session_start")), _utc(data.get("last_updated"))
    if started > ended or ended > now + timedelta(seconds=60):
        raise HermesSourceError("Hermes session time range is invalid")
    messages = data.get("messages")
    if not isinstance(messages, list) or len(messages) > MAX_MESSAGES:
        raise HermesSourceError("Hermes messages must be a bounded array")
    if data.get("platform") == "cron" or sid.startswith("cron"):
        return None
    prompts, finals, names, feedback = [], [], [], []
    seen_names = set()
    total_chars = 0
    for msg in messages:
        if not isinstance(msg, dict) or not isinstance(msg.get("role"), str):
            raise HermesSourceError("Hermes message schema is malformed")
        role = msg["role"]
        # Do not ingest system prompts, tool output, arguments or arbitrary metadata.
        if role not in {"user", "assistant"}:
            continue
        content = msg.get("content")
        if not isinstance(content, (str, list)) and content is not None:
            raise HermesSourceError("Hermes message content is malformed")
        text = _text(content)
        total_chars += len(text)
        if total_chars > MAX_DIGEST_CHARS:
            raise HermesSourceError("Hermes digest exceeds its text limit")
        if text:
            if role == "user":
                prompts.append(text)
                feedback.extend(_detect_feedback(text))
            else:
                finals.append(text)
        for call in (msg.get("tool_calls") or []) if isinstance(msg.get("tool_calls", []), list) else []:
            fn = call.get("function", {}) if isinstance(call, dict) else {}
            name = fn.get("name") if isinstance(fn, dict) else None
            if isinstance(name, str) and len(name) <= 80 and redact_secrets(name) == name:
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,79}", name) and name not in seen_names:
                    if len(names) >= MAX_TOOL_NAMES:
                        raise HermesSourceError("Hermes tool names exceed their metadata limit")
                    seen_names.add(name)
                    names.append(name)
    if not prompts and not finals:
        return None
    identity = hashlib.sha256(json.dumps([owner, project, sid], separators=(",", ":")).encode()).hexdigest()
    return SessionDigest(
        session_id="hermes-" + identity,
        project=project,
        started_at=started.isoformat(), ended_at=ended.isoformat(),
        user_prompts=prompts, assistant_finals=finals[-5:], tools_used=names,
        feedback_signals=feedback, n_user_turns=len(prompts), n_assistant_turns=len(finals),
        raw_path="hermes:sha256:" + source_hash,
    )


def harvest_hermes(
    sessions_dir: str, *, manifest_path: str, owner: str, scope: Any = "invoked",
    invoked_project: str = "", since_iso: str | None = None, limit: int = 0,
    now: datetime | None = None,
) -> list[SessionDigest]:
    """Validate every allowlisted file, then return newest matching sessions.

    Any malformed or stale source invalidates the complete harvest, even beyond
    ``limit``. No directory enumeration, environment override or default root.
    """
    project = _absolute(invoked_project)
    root = _absolute(sessions_dir)
    manifest_path = _absolute(manifest_path)
    if not isinstance(owner, str) or not _ID.fullmatch(owner):
        raise HermesSourceError("Hermes requires an explicit owner identifier")
    if scope != "invoked" and scope != [project]:
        raise HermesSourceError("Hermes requires exactly one explicit project")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0 or limit > MAX_SOURCES:
        raise HermesSourceError("Hermes result limit is invalid")
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
        raise HermesSourceError("Hermes clock must be UTC")
    since = _utc(since_iso) if since_iso is not None else None
    directory = _open_directory(os.path.dirname(manifest_path))
    try:
        manifest = _json(_read_at(directory, os.path.basename(manifest_path), MAX_MANIFEST_BYTES))
    finally:
        os.close(directory)
    if manifest.get("format") != "skillopt.hermes.manifest.v1":
        raise HermesSourceError("Hermes manifest schema is unsupported")
    if manifest.get("owner") != owner or manifest.get("project") != project:
        raise HermesSourceError("Hermes manifest owner or project mismatch")
    if manifest.get("sessions_dir") != root:
        raise HermesSourceError("Hermes source root differs from its manifest")
    generated, expires = _utc(manifest.get("generated_at")), _utc(manifest.get("expires_at"))
    if not generated <= observed < expires or expires - generated > timedelta(hours=24):
        raise HermesSourceError("Hermes manifest is expired or outside its validity window")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        raise HermesSourceError("Hermes manifest needs a bounded explicit source list")
    seen_paths, seen_hashes = set(), set()
    for item in sources:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise HermesSourceError("Hermes source entry is malformed")
        name, fingerprint = item["path"], item["sha256"]
        if not isinstance(name, str) or not _FILE.fullmatch(name) or name in seen_paths:
            raise HermesSourceError("Hermes source name is unsafe or duplicated")
        if not isinstance(fingerprint, str) or not _SHA.fullmatch(fingerprint) or fingerprint in seen_hashes:
            raise HermesSourceError("Hermes source hash is invalid or duplicated")
        seen_paths.add(name)
        seen_hashes.add(fingerprint)
    directory = _open_directory(root)
    total, digests, identities = 0, [], set()
    try:
        for item in sources:
            payload = _read_at(directory, item["path"], MAX_SOURCE_BYTES)
            total += len(payload)
            if total > MAX_TOTAL_BYTES:
                raise HermesSourceError("Hermes batch exceeds its byte limit")
            if hashlib.sha256(payload).hexdigest() != item["sha256"]:
                raise HermesSourceError("Hermes source hash changed; renew the owner manifest")
            digest = _digest(_json(payload), owner=owner, project=project, source_hash=item["sha256"], now=observed)
            if digest is None:
                continue
            if digest.session_id in identities:
                raise HermesSourceError("Hermes session identity appears more than once")
            identities.add(digest.session_id)
            if since is None or _utc(digest.ended_at) >= since:
                digests.append(digest)
    finally:
        os.close(directory)
    digests.sort(key=lambda d: (d.ended_at, d.session_id), reverse=True)
    return digests[:limit] if limit else digests
