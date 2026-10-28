"""§30：真实摄像头取消和照片误配的前端回归，不调用摄像头或模型。"""
import json
from pathlib import Path
import shutil
import subprocess
import pytest


def test_camera_cancellation_and_reference_matching():
    node = shutil.which('node') or 'C:/Program Files/nodejs/node.exe'
    if not Path(node).is_file():
        pytest.skip('Node is required for frontend boundary checks')
    script = Path(__file__).parent / 'frontend/tryon_reference_check.cjs'
    result = subprocess.run([node, str(script)], capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report == {'passed': 14, 'failed': 0}
