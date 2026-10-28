"""§31前端真实模块内存回归，不调用浏览器或服务。"""
import json
from pathlib import Path
import shutil
import subprocess
import pytest

def test_search_and_touch_boundaries():
 node=shutil.which('node') or 'C:/Program Files/nodejs/node.exe'
 if not Path(node).is_file():pytest.skip('Node required')
 result=subprocess.run([node,str(Path(__file__).parent/'frontend/search_touch_check.cjs')],capture_output=True,text=True,encoding='utf-8',timeout=20)
 assert result.returncode==0,result.stderr
 assert json.loads(result.stdout)=={'passed':16,'failed':0}
