"""Help is available without importing any provider SDK or constructing clients."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("script", ["train", "eval_only"])
def test_help_without_model_provider_imports(script):
    entry = Path(__file__).resolve().parents[1] / "scripts" / (script + ".py")
    code = """
import importlib.abc, runpy, sys
class NoProviders(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'skillopt.model' or fullname.startswith(('openai', 'azure')):
            raise AssertionError('help imported a provider: ' + fullname)
sys.meta_path.insert(0, NoProviders())
sys.argv = [sys.argv[1], '--help']
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    result = subprocess.run([sys.executable, "-c", code, str(entry)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "--optimizer_backend" in result.stdout and "--target_backend" in result.stdout
