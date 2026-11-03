"""§36：Node内存状态机回归，无网络、真实账号或图片写入。"""
import json
from pathlib import Path
import shutil
import subprocess
import pytest


def test_purchase_intent_request_boundaries():
    executable=shutil.which('node')
    if not executable:
        pytest.skip('Node unavailable')
    result=subprocess.run([executable,str(Path(__file__).parent/'frontend/purchase_intent_check.cjs')],capture_output=True,text=True,encoding='utf-8',timeout=25)
    assert result.returncode==0,result.stderr
    report=json.loads(result.stdout)
    assert report['failed']==0 and report['passed']>=7
