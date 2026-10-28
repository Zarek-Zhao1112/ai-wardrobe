"""§29：通过Node执行前端编排的收费边界回归，不访问网络。"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_import_queue_paid_boundaries():
    executable = shutil.which('node')
    if executable is None:
        windows = Path('C:/Program Files/nodejs/node.exe')
        if not windows.is_file():
            pytest.skip('Node is required for frontend queue checks')
        executable = str(windows)
    script = Path(__file__).parent / 'frontend' / 'import_queue_check.cjs'
    result = subprocess.run([executable, str(script)], capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['passed'] == 12 and report['failed'] == 0
