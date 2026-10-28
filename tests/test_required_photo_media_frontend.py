"""§32：媒体解码与坏缓存更新，无真实设备或网络。"""
import json
from pathlib import Path
import shutil
import subprocess

def test_required_photo_decode_and_result_phases():
    node=shutil.which('node') or 'C:/Program Files/nodejs/node.exe'
    result=subprocess.run([node,str(Path(__file__).parent/'frontend/required_photo_media_check.cjs')],capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'passed':3,'failed':0}
