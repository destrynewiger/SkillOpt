#!/usr/bin/env bash
# Install the SkillOpt-Sleep Devin integration into a project.
# Copies the SessionEnd hook and rules snippet into .devin/, and prints
# the MCP server registration command. Idempotent.
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
PROJECT="${1:-$(pwd)}"

echo "[install] repo: $REPO_ROOT"
echo "[install] project: $PROJECT"

DEVIN_DIR="$PROJECT/.devin"
mkdir -p "$DEVIN_DIR/hooks" "$DEVIN_DIR/rules"

# 1) SessionEnd hook (on by default — provides activity signal for nightly harvest)
#    Merge into existing hooks.v1.json instead of overwriting, so we don't
#    destroy other project hooks.
HOOK_SCRIPT_SRC="$PLUGIN_DIR/hooks/on-session-end.sh"
HOOK_SCRIPT_DST="$DEVIN_DIR/hooks/skillopt-sleep-on-session-end.sh"
cp "$HOOK_SCRIPT_SRC" "$HOOK_SCRIPT_DST"
chmod +x "$HOOK_SCRIPT_DST"
echo "[install] hook script       -> $HOOK_SCRIPT_DST"

HOOK_CONFIG="$DEVIN_DIR/hooks.v1.json"
if [ -f "$HOOK_CONFIG" ]; then
  # Python is already required by the plugin. Merge event arrays without
  # replacing existing hooks, and skip exact duplicates on repeated installs.
  python3 - "$HOOK_CONFIG" "$PLUGIN_DIR/hooks/hooks.v1.json" <<'PY'
import json
import os
import stat
import sys
import tempfile

destination, addition = sys.argv[1:]


def load_object(path):
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"hook config must be a JSON object: {path}")
    return value


base = load_object(destination)
incoming = load_object(addition)
for event, entries in incoming.items():
    if not isinstance(entries, list):
        raise ValueError(f"hook event {event!r} must be an array")
    existing = base.setdefault(event, [])
    if not isinstance(existing, list):
        raise ValueError(f"existing hook event {event!r} must be an array")
    for entry in entries:
        if entry not in existing:
            existing.append(entry)

directory = os.path.dirname(os.path.abspath(destination))
fd, temporary = tempfile.mkstemp(prefix=".hooks.v1.", suffix=".tmp", dir=directory)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(base, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(temporary, stat.S_IMODE(os.stat(destination).st_mode))
    os.replace(temporary, destination)
except Exception:
    try:
        os.unlink(temporary)
    except OSError:
        pass
    raise
PY
  echo "[install] session-end hook  -> $HOOK_CONFIG (merged)"
else
  cp "$PLUGIN_DIR/hooks/hooks.v1.json" "$HOOK_CONFIG"
  echo "[install] session-end hook  -> $HOOK_CONFIG"
fi

# 2) Rules snippet so Devin proactively offers the tools
cp "$PLUGIN_DIR/devin-rules.snippet.md" "$DEVIN_DIR/rules/skillopt-sleep.md"
echo "[install] rules snippet     -> $DEVIN_DIR/rules/skillopt-sleep.md"

# 3) Print the MCP server registration command
printf -v MCP_SERVER_QUOTED '%q' "$PLUGIN_DIR/mcp_server.py"
cat <<EOF

[install] Register the MCP server (run once per machine):

  devin mcp add skillopt-sleep \\
    --env "SKILLOPT_DEVIN_CLAUDE_HOME=\$HOME/.skillopt-sleep-devin" \\
    -- python3 $MCP_SERVER_QUOTED

Done. Try asking Devin:
  Run the sleep cycle for this project.
EOF
