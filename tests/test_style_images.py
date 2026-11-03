"""§38：文生图来源、排队、幂等与旧照片兼容；全部假服务。"""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import db
from app.services import purchase_reference as phone
from scripts.bridge import style_image_bridge as style, desktop_bridge, manage
from test_purchase_reference import SPEC, PNG, wait_until


def generated(data=PNG):
    return {'id':'c1','mime':'image/png','sha256':hashlib.sha256(data).hexdigest(),'origin':'generated','image_model':style.IMAGE_MODEL,'name':'AI款式示意','data':base64.b64encode(data).decode()}


def test_generated_photos_require_server_expected_origin_and_no_fake_url(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'IMAGES_DIR',tmp_path)
    row=generated();result=phone._photos({'references':[row]},'generated')
    assert result[0]['origin']=='generated' and 'source_url' not in result[0]
    assert phone._cache_usable(result)
    with pytest.raises(ValueError):phone._photos({'references':[row]})
    for mutation in ({'origin':'product_photo'},{'image_model':'other'},{'source_url':'https://invented.example.com/item'}):
        with pytest.raises(ValueError):phone._photos({'references':[{**row,**mutation}]},'generated')


def test_generated_size_contract_accepts_over_two_mb_but_real_does_not(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'IMAGES_DIR',tmp_path)
    data=PNG+b'x'*2_000_000;row=generated(data)
    result=phone._photos({'references':[row]},'generated');assert phone._cache_usable(result)
    real={k:v for k,v in row.items() if k not in ('origin','image_model')};real['source_url']='https://shop.example.com/item'
    with pytest.raises(ValueError):phone._photos({'references':[real]})


def test_origin_and_prompt_version_isolate_cache_signatures():
    assert phone._signature(SPEC)!=phone._signature(SPEC,'generated')
    assert phone._signature(SPEC)==hashlib.sha256(json.dumps(SPEC,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def test_style_prompt_keeps_title_only_sleeve_information():
    spec={**SPEC,'title':'白色合身长袖衬衫','type':'shirt','fit':'合体'}
    assert '长袖' in style.make_prompt(spec)


def fake_runner(calls,release=None):
    def run(exe,entry,folder,spec,env):
        calls.append(spec['type'])
        if release:assert release.wait(3)
        (folder/'candidate-1.png').write_bytes(PNG)
        return [{**{k:v for k,v in generated().items() if k!='data'},'file':'candidate-1.png'}]
    return run


def test_style_queue_three_directions_wait_for_busy_and_no_duplicate(tmp_path):
    lock=threading.BoundedSemaphore(1);lock.acquire();calls=[]
    jobs=style.StyleJobs(tmp_path,Path('fake'),Path('fake'),lock,lambda:{},runner=fake_runner(calls))
    ids=[str(uuid4()) for _ in range(3)]
    for identifier in ids:jobs.start({'job_id':identifier,'direction':SPEC})
    jobs.start({'job_id':ids[0],'direction':SPEC});assert not calls
    lock.release();wait_until(lambda:not jobs.active)
    assert len(calls)==3 and all(jobs.status(k)['status']=='complete' for k in ids)
    assert jobs.status(ids[0])['references'][0]['origin']=='generated'
    assert 'source_url' not in jobs.status(ids[0])['references'][0]
    with pytest.raises(HTTPException) as error:jobs.start({'job_id':ids[0],'direction':{**SPEC,'fit':'宽松'}})
    assert error.value.status_code==409 and len(calls)==3


def test_style_restart_persists_interruption_and_does_not_replay(tmp_path):
    state=tmp_path/'state';directory=state/'styles';directory.mkdir(parents=True)
    identifier=str(uuid4());folder=directory/identifier;folder.mkdir()
    (folder/'job.json').write_text(json.dumps({'job_id':identifier,'signature':'old','status':'running','phase':'generating','references':[]}),encoding='utf-8')
    with pytest.raises(ValueError):manage.ensure_idle(state)
    calls=[];jobs=style.StyleJobs(directory,Path('fake'),Path('fake'),threading.BoundedSemaphore(1),lambda:{},runner=fake_runner(calls))
    assert jobs.status(identifier)['status']=='interrupted' and not calls
    assert json.loads((folder/'job.json').read_text(encoding='utf-8'))['status']=='interrupted'
    manage.ensure_idle(state)


def test_claimed_but_unconfirmed_does_not_repeat(tmp_path):
    calls=[]
    def run(exe,entry,folder,spec,env):
        calls.append(1);(folder/'tool-claimed').write_text('1');raise TimeoutError('private detail')
    jobs=style.StyleJobs(tmp_path,Path('fake'),Path('fake'),threading.BoundedSemaphore(1),lambda:{},runner=run)
    payload={'job_id':str(uuid4()),'direction':SPEC};jobs.start(payload);wait_until(lambda:not jobs.active)
    for _ in range(3):assert jobs.start(payload)['status']=='interrupted'
    assert len(calls)==1 and 'private' not in jobs.status(payload['job_id'])['error']


def test_style_endpoint_generation_and_search_paused_by_default(tmp_path):
    executable=tmp_path/'node';executable.touch();entry=tmp_path/'cli';entry.touch();calls=[]
    app=desktop_bridge.create_app(executable,'test-token',cli_entry=entry,reference_dir=tmp_path/'references',style_runner=fake_runner(calls))
    with TestClient(app) as client:
        headers={'Authorization':'Bearer test-token'}
        health=client.get('/health',headers=headers).json()
        assert health['style_generation'] is True and health['reference_search'] is False
        payload={'job_id':str(uuid4()),'direction':SPEC}
        assert client.post('/reference/jobs',headers=headers,json=payload).status_code==409
        response=client.post('/style/jobs',headers=headers,json=payload);assert response.status_code==200
        wait_until(lambda:client.get('/style/jobs/'+payload['job_id'],headers=headers).json()['status']=='complete')
        assert len(calls)==1


def test_style_http_can_queue_while_recognition_holds_cli(tmp_path):
    executable=tmp_path/'node';executable.touch();entry=tmp_path/'cli';entry.touch()
    entered=threading.Event();release=threading.Event();calls=[]
    def chat(*args,**kwargs):entered.set();assert release.wait(3);return '{}'
    app=desktop_bridge.create_app(executable,'test-token',chat,cli_entry=entry,reference_dir=tmp_path/'references',style_runner=fake_runner(calls))
    with TestClient(app) as client:
        headers={'Authorization':'Bearer test-token'}
        worker=threading.Thread(target=lambda:client.post('/chat',headers=headers,json={'model':desktop_bridge.MODEL,'messages':[{'role':'user','content':'synthetic'}]}));worker.start()
        assert entered.wait(2)
        try:
            identifier=str(uuid4());reply=client.post('/style/jobs',headers=headers,json={'job_id':identifier,'direction':SPEC})
            assert reply.status_code==200 and not calls
            assert client.post('/admin/drain',headers=headers).status_code==409
        finally:release.set();worker.join(3)
        wait_until(lambda:client.get('/style/jobs/'+identifier,headers=headers).json()['status']=='complete')
        assert len(calls)==1


def test_style_cli_uses_fixed_model_exactly_one_tool_and_no_source(tmp_path,monkeypatch):
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        if len(calls)==1:return SimpleNamespace(returncode=0,stdout=b'')
        request=json.loads((tmp_path/'guard.json').read_text(encoding='utf-8'));assert 'image1' not in request
        output=Path(request['output_dir'])/'output.png';output.write_bytes(PNG);(tmp_path/'tool-claimed').touch()
        events=[{'type':'system','subtype':'init','apiKeySource':'copilot.tencent.com','model':style.MODEL},
          {'type':'assistant','message':{'content':[{'type':'tool_use','id':'img','name':'ImageGen','input':request}]}},
          {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'img','content':[{'type':'text','text':json.dumps({'type':'image_gen_tool_result','localPath':str(output)})}]}]}}]
        return SimpleNamespace(returncode=0,stdout='\n'.join(json.dumps(e) for e in events).encode())
    monkeypatch.setattr(style.subprocess,'run',run)
    rows=style.run_style_image(Path('node'),Path('entry'),tmp_path,SPEC,lambda:{})
    assert len(calls)==2 and calls[1][calls[1].index('--text-to-image-model')+1]=='hunyuan-image-alpha'
    assert rows[0]['origin']=='generated' and 'source_url' not in rows[0]


@pytest.mark.parametrize('mutation',[{'image1':''},{'image2':None},{'model':'other'},{'revise':True},{'size':'512x512'},{'prompt':'changed'}])
def test_style_guard_rejects_extra_images_or_parameter_changes(tmp_path,mutation):
    node=shutil.which('node')
    if not node:pytest.skip('Node missing')
    request={'prompt':'fixed','size':'1024x1024','revise':False,'output_dir':str(tmp_path/'output')}
    (tmp_path/'guard.json').write_text(json.dumps(request),encoding='utf-8')
    guard=Path(style.__file__).with_name('style_image_guard.cjs')
    program="process.exit(require(process.argv[1]).guard(process.argv[2]))"
    result=subprocess.run([node,'-e',program,str(guard),str(tmp_path)],input=json.dumps({'tool_name':'ImageGen','tool_input':{**request,**mutation}}).encode(),capture_output=True)
    assert result.returncode==2 and not (tmp_path/'tool-claimed').exists()
