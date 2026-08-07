"""Tests for subprocess environment isolation in the spreadsheet executor.

``run_generated_code`` runs LLM-generated Python in a child process. To avoid
leaking API keys / cloud credentials into untrusted generated code, the child
must run with a minimal, scrubbed environment rather than inheriting the
parent process environment. These tests assert that scrubbing behaviour.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

from skillopt.envs.spreadsheetbench.codegen_agent import _build_codex_driver
from skillopt.envs.spreadsheetbench.executor import generated_code_env, run_generated_code
from skillopt.envs.spreadsheetbench.react_agent import _run_bash


# User code that records whether a given env var is visible to the child.
_PROBE = (
    "import os\n"
    "with open(OUTPUT_PATH, 'w', encoding='utf-8') as _f:\n"
    "    _f.write(os.environ.get('SUPER_SECRET_TOKEN', 'ABSENT'))\n"
)


def test_secret_env_not_visible_to_generated_code(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "leak-me-please")
    out = tmp_path / "out.txt"

    ok, err = run_generated_code(_PROBE, str(tmp_path / "in.xlsx"), str(out))

    assert ok, err
    assert out.read_text(encoding="utf-8") == "ABSENT"


def test_path_still_available_to_generated_code(tmp_path) -> None:
    # PATH must be preserved so the child can still locate the interpreter's
    # tooling; only sensitive vars are dropped.
    probe = (
        "import os\n"
        "with open(OUTPUT_PATH, 'w', encoding='utf-8') as _f:\n"
        "    _f.write('YES' if os.environ.get('PATH') else 'NO')\n"
    )
    out = tmp_path / "out.txt"

    ok, err = run_generated_code(probe, str(tmp_path / "in.xlsx"), str(out))

    assert ok, err
    assert out.read_text(encoding="utf-8") == "YES"


def test_non_secret_python_environment_is_preserved(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/safe/development/path")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "must-not-leak")

    env = generated_code_env(str(tmp_path), str(tmp_path / "scratch"))

    assert env["PYTHONPATH"] == "/safe/development/path"
    assert env["LANG"] == "C.UTF-8"
    assert "AZURE_OPENAI_API_KEY" not in env


def test_installed_spreadsheet_dependency_is_importable(tmp_path) -> None:
    probe = (
        "import openpyxl\n"
        "with open(OUTPUT_PATH, 'w', encoding='utf-8') as _f:\n"
        "    _f.write('YES')\n"
    )
    out = tmp_path / "out.txt"

    ok, err = run_generated_code(probe, str(tmp_path / "in.xlsx"), str(out))

    assert ok, err
    assert out.read_text(encoding="utf-8") == "YES"


def test_generated_scratch_directory_is_private_and_cleaned(tmp_path) -> None:
    probe = (
        "import tempfile\n"
        "scratch = tempfile.NamedTemporaryFile(delete=False)\n"
        "scratch.close()\n"
        "with open(OUTPUT_PATH, 'w', encoding='utf-8') as _f:\n"
        "    _f.write(scratch.name)\n"
    )
    out = tmp_path / "out.txt"

    ok, err = run_generated_code(probe, str(tmp_path / "in.xlsx"), str(out))

    assert ok, err
    scratch_path = out.read_text(encoding="utf-8")
    assert os.path.dirname(scratch_path) != str(tmp_path)
    assert not os.path.exists(scratch_path)


def test_codex_driver_scrubs_env_sets_tempdir_and_cleans_runner(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "do-not-inherit")
    monkeypatch.setenv("PYTHONPATH", "/safe/development/path")
    sentinel = tmp_path / "_driver_runner.py"
    sentinel.write_text("do not overwrite", encoding="utf-8")
    (tmp_path / "solution.py").write_text(
        "import os\n"
        "with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:\n"
        "    f.write('|'.join([\n"
        "        os.environ.get('SUPER_SECRET_TOKEN', 'ABSENT'),\n"
        "        'TMPDIR' if os.environ.get('TMPDIR') else 'NO_TMPDIR',\n"
        "        'PRIVATE_TMP' if os.environ.get('TMPDIR') != os.getcwd() else 'BAD_TMP',\n"
        "        'PRIVATE_HOME' if os.environ.get('HOME') == os.getcwd() else 'BAD_HOME',\n"
        "        'DEV_PATH' if os.environ.get('PYTHONPATH') == '/safe/development/path' else 'NO_DEV_PATH',\n"
        "    ]))\n",
        encoding="utf-8",
    )
    driver = tmp_path / "run_solution.py"
    driver.write_text(_build_codex_driver(), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(driver)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (
        tmp_path / "output.xlsx"
    ).read_text(encoding="utf-8") == (
        "ABSENT|TMPDIR|PRIVATE_TMP|PRIVATE_HOME|DEV_PATH"
    )
    assert sentinel.read_text(encoding="utf-8") == "do not overwrite"


def test_generated_code_tempdirs_tolerate_windows_cleanup_races(
    tmp_path, monkeypatch
) -> None:
    cleanup_modes = []

    def simulated_windows_rmtree(cls, name, ignore_errors=False, repeated=False):
        del cls, repeated
        cleanup_modes.append(ignore_errors)
        if not ignore_errors:
            raise PermissionError(32, "directory is still in use", name)
        shutil.rmtree(name, ignore_errors=True)

    monkeypatch.setattr(
        tempfile.TemporaryDirectory,
        "_rmtree",
        classmethod(simulated_windows_rmtree),
    )
    monkeypatch.setattr(
        "skillopt.envs.spreadsheetbench.executor.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    out = tmp_path / "out.txt"
    out.write_text("created", encoding="utf-8")

    ok, err = run_generated_code("pass", "input.xlsx", str(out))

    assert ok, err
    assert cleanup_modes == [True]


def test_react_tempdir_tolerates_windows_cleanup_races(tmp_path, monkeypatch) -> None:
    cleanup_modes = []

    def simulated_windows_rmtree(cls, name, ignore_errors=False, repeated=False):
        del cls, repeated
        cleanup_modes.append(ignore_errors)
        if not ignore_errors:
            raise PermissionError(32, "directory is still in use", name)
        shutil.rmtree(name, ignore_errors=True)

    monkeypatch.setattr(
        tempfile.TemporaryDirectory,
        "_rmtree",
        classmethod(simulated_windows_rmtree),
    )
    monkeypatch.setattr(
        "skillopt.envs.spreadsheetbench.react_agent.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="success", stderr=""
        ),
    )

    assert _run_bash("python -c pass", str(tmp_path)) == "success"
    assert cleanup_modes == [True]


def test_codex_driver_uses_cleanup_tolerant_tempdir() -> None:
    assert "shutil.rmtree(_temp_dir, ignore_errors=True)" in _build_codex_driver()
