"""Entry-point scripts must not die on a non-UTF-8 console."""

from __future__ import annotations

import io
import os
import runpy
import sys

import pytest

_SCRIPTS = ["train", "eval_only"]
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("script", _SCRIPTS)
def test_script_reconfigures_streams_to_utf8(script: str, monkeypatch) -> None:
    """Progress output uses arrows and box-drawing characters.

    A cp1252 console raises UnicodeEncodeError partway through a run, after
    real work has been done, so the scripts force UTF-8 at import time.
    """
    recorded: list[dict] = []

    class _Stream(io.StringIO):
        encoding = "cp1252"

        def reconfigure(self, **kwargs):
            recorded.append(kwargs)

    monkeypatch.setattr(sys, "stdout", _Stream())
    monkeypatch.setattr(sys, "stderr", _Stream())
    monkeypatch.setattr(sys, "argv", [script])

    # run_name is not "__main__", so main() never runs; stream setup happens
    # at import time, which is the point.
    runpy.run_path(os.path.join(_ROOT, "scripts", f"{script}.py"), run_name="not_main")

    assert {"encoding": "utf-8", "errors": "replace"} in recorded


def test_cp1252_console_survives_arrow_output() -> None:
    """The characters that used to crash a run must now be writable."""
    buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    buf.reconfigure(encoding="utf-8", errors="replace")
    buf.write("[2/6 REFLECT] failure=0→0 groups — SkillOpt")
    buf.flush()


@pytest.mark.parametrize(
    ("encoding", "expect_reconfigure"),
    [("cp1252", True), ("utf-8", False), ("utf_8", False), ("UTF8", False)],
)
def test_helper_skips_streams_already_utf8(encoding, expect_reconfigure, monkeypatch) -> None:
    from skillopt.utils.console import force_utf8_stdout_stderr

    recorded: list[dict] = []

    class _Stream:
        def __init__(self, enc):
            self.encoding = enc

        def reconfigure(self, **kwargs):
            recorded.append(kwargs)

    monkeypatch.setattr(sys, "stdout", _Stream(encoding))
    monkeypatch.setattr(sys, "stderr", _Stream(encoding))
    force_utf8_stdout_stderr()

    assert bool(recorded) is expect_reconfigure

