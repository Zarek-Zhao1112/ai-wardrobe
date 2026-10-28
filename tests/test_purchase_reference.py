"""§31：来源链、公开网页、任务幂等及读取不收费，全部为合成数据。"""
import base64
import hashlib
import json
import threading
import time
import http.client
import socket
import socketserver
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import db, config
from app.services import purchase, purchase_reference as phone
from scripts import reference_bridge as pc, reference_net as net

SPEC={'type':'tshirt','primary_color':'white','title':'白色圆领T恤','color':'纯白','fit':'常规合身','pattern':'纯色','texture':'棉质'}
PNG=b'\x89PNG\r\n\x1a\n'+b'\x00\x00\x00\x0dIHDR'+(600).to_bytes(4,'big')+(800).to_bytes(4,'big')+b'public-test-photo'*100

def events(query='query'):
 return [{'type':'system','subtype':'init','apiKeySource':'copilot.tencent.com','model':pc.MODEL},
  {'type':'assistant','message':{'content':[{'type':'tool_use','id':'call-1','name':'WebSearch','input':{'query':query}}]}},
  {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'call-1','_meta':{'rawResponse':{'type':'web_search_tool_result','searchType':'text2text','query':query,'results':[{'title':'实际商品','url':'https://product.example.com/item'}]}}}]}},
  {'type':'result','subtype':'success','is_error':False}]

def wait_until(fn):
 deadline=time.monotonic()+3
 while time.monotonic()<deadline:
  if fn():return
  time.sleep(.01)
 raise AssertionError('合成任务未结束')

@pytest.mark.parametrize('url',['http://127.0.0.1/a','http://192.168.1.4/a','http://169.254.169.254/','http://localhost/','http://a.local/','file:///etc/x','https://u:p@example.com/a','https://example.com:8891/a','https://example.com/a#b','https://example.com/\nvalue'])
def test_reject_nonpublic_urls(url):
 with pytest.raises(ValueError):net.public_url(url)

def test_dns_private_result_never_connects(monkeypatch):
 monkeypatch.setattr(net.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('10.0.0.1',443))])
 monkeypatch.setattr(net.socket,'create_connection',lambda *a,**k:pytest.fail('内网不得连接'))
 with pytest.raises(ValueError):net.fetch_public('https://public.example.com/item')

def test_actual_tool_chain_required():
 assert pc.parse_search(events(),'query')[0]['url']=='https://product.example.com/item'
 bad=events();bad[2]['message']['content'][0]['_meta']={}
 bad[-1]['result']='https://invented.example.com/photo.jpg'
 with pytest.raises(ValueError):pc.parse_search(bad,'query')

@pytest.mark.parametrize('mutation',[lambda e:e[0].update(model='other'),lambda e:e[-1].update(cancelled=True),lambda e:e[1]['message']['content'][0].update(name='Bash'),lambda e:e[2]['message']['content'][0].update(tool_use_id='unknown')])
def test_reject_wrong_model_cancel_or_tool(mutation):
 data=events();mutation(data)
 with pytest.raises(ValueError):pc.parse_search(data,'query')

def test_product_metadata_handles_malformed_nodes():
 html='<script type="application/ld+json">'+json.dumps({'@graph':[{'@type':None},{'@type':2},{'@type':'Product','name':'白色衣物','image':['/front.jpg','http://127.0.0.1/private.png']}]})+'</script>'
 rows=net.product_images(html,'https://shop.example.com/products/1')
 assert rows==[{'image_url':'https://shop.example.com/front.jpg','source_url':'https://shop.example.com/products/1','name':'白色衣物'}]

def test_unproven_logo_not_product_photo():
 assert net.product_images('<meta property="og:image" content="https://example.com/logo.jpg">','https://example.com/')==[]

def test_slow_response_has_total_budget(monkeypatch):
 real_clock=time.monotonic;clock_calls=[]
 def clock():clock_calls.append(1);return 0 if len(clock_calls)==1 else 11.9
 class Slow(socketserver.BaseRequestHandler):
  def handle(self):
   try:
    self.request.recv(4096)
    for _ in range(180):self.request.sendall(b'H');time.sleep(.01)
   except OSError:pass
 server=socketserver.TCPServer(('127.0.0.1',0),Slow);worker=threading.Thread(target=server.handle_request,daemon=True);worker.start()
 monkeypatch.setattr(net.time,'monotonic',clock)
 monkeypatch.setattr(net.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('8.8.8.8',80))])
 def connect(address,timeout):
  assert address==('8.8.8.8',80)
  stream=socket.socket();stream.settimeout(timeout);stream.connect(server.server_address);return stream
 monkeypatch.setattr(net.socket,'create_connection',connect)
 start=real_clock()
 try:
  with pytest.raises((ValueError,OSError,http.client.HTTPException)):net.fetch_public('http://public.example.com/item')
  assert real_clock()-start<.8
 finally:server.server_close();worker.join(.5)

def test_alternate_product_views_and_one_reference_per_page(tmp_path):
 pages=[{'url':'https://shop.example.com/products/a'},{'url':'https://shop.example.com/products/b'}]
 def fetch(url,limit):
  if '/products/' in url:
   key=url.rsplit('/',1)[-1];data={'@type':'Product','name':'白色圆领衣物','image':['/'+key+'-model.jpg','/'+key+'-flat.jpg']}
   return ('<script type="application/ld+json">'+json.dumps(data)+'</script>').encode(),'text/html',url
  return PNG+url.encode(),'image/png',url
 def chat(system,content):
  assert len([r for r in content if r['type']=='image'])==4
  return json.dumps({'selected':['c3','c1','c4'],'checks':[{'id':k,'single_garment':True,'no_person':True,'no_marketing_text':True,'visual_match':True} for k in ['c3','c1','c4']]})
 rows=pc.find_references(Path('fake'),Path('fake'),tmp_path,SPEC,lambda:{},chat,search=lambda *a:pages,fetch=fetch)
 assert [r['id'] for r in rows]==['c3','c4']
 assert len({r['source_url'] for r in rows})==2
 assert (tmp_path/'sources.json').is_file()

def test_pc_job_once_restart_and_corruption(tmp_path):
 calls=[];release=threading.Event()
 def runner(exe,entry,directory,spec,environment,chat):
  calls.append(1);release.wait(2);(directory/'candidate-1.png').write_bytes(PNG)
  return [{'id':'c1','file':'candidate-1.png','mime':'image/png','sha256':hashlib.sha256(PNG).hexdigest(),'source_url':'https://shop.example.com/item','name':'白色衣物'}]
 jobs=pc.ReferenceJobs(tmp_path,Path('fake'),Path('fake'),threading.BoundedSemaphore(1),lambda:{},lambda *a:'{}',runner=runner)
 key=str(uuid4());payload={'job_id':key,'direction':SPEC}
 jobs.start(payload);jobs.start(payload);wait_until(lambda:len(calls)==1)
 assert len(calls)==1
 release.set();wait_until(lambda:key not in jobs.active)
 assert jobs.status(key)['status']=='complete'
 (tmp_path/key/'candidate-1.png').unlink()
 assert jobs.status(key)['status']=='failed'
 old=json.loads((tmp_path/key/'job.json').read_text(encoding='utf-8'));old.update(status='running',references=[]);pc.write_json(tmp_path/key/'job.json',old)
 assert jobs.status(key)['status']=='interrupted'
 assert len(calls)==1

@pytest.fixture
def owner(tmp_path,monkeypatch):
 uid=db.new_id('u');plan={'id':'plan-'+uid,'directions':[{'id':'direction-'+uid,**SPEC,'owned':[]}],'fingerprint':'fake'}
 purchase._write(uid,'latest',plan)
 photos=tmp_path/'images';photos.mkdir();monkeypatch.setattr(db,'IMAGES_DIR',photos);monkeypatch.setattr(config,'DATA_DIR',tmp_path)
 monkeypatch.setattr(phone,'load_settings',lambda:{'url':'http://127.0.0.1:1','token':'fake'})
 return uid,plan,plan['directions'][0]

def test_phone_get_does_not_start_search(owner,monkeypatch):
 uid,plan,d=owner
 monkeypatch.setattr(phone,'bridge_request',lambda *a,**k:pytest.fail('读取不应提交搜索'))
 assert phone.snapshots(uid,[plan])=={}

def test_phone_start_idempotent_and_unknown_only_queries(owner,monkeypatch):
 uid,plan,d=owner;calls=[]
 def request(settings,path,**kw):
  calls.append(('POST' if kw else 'GET',path))
  if kw:raise phone.LLMError('响应未知',code='bridge_timeout')
  return {'job_id':path.rsplit('/',1)[-1],'status':'running','references':[]}
 monkeypatch.setattr(phone,'bridge_request',request)
 key=str(uuid4());phone.start(uid,plan['id'],d['id'],key);wait_until(lambda:(uid,key) not in phone._posting)
 assert phone.start(uid,plan['id'],d['id'],str(uuid4()),retry=True)['job_id']==key
 assert phone.status(uid,key)['status']=='running'
 assert [verb for verb,_ in calls]==['POST','GET']

def test_phone_completion_cached_and_bad_photo_can_retry(owner,monkeypatch):
 uid,plan,d=owner;calls=[];key=str(uuid4())
 digest=hashlib.sha256(PNG).hexdigest()
 def request(settings,path,**kw):
  calls.append(bool(kw));identifier=kw['payload']['job_id'] if kw else path.rsplit('/',1)[-1]
  return {'job_id':identifier,'status':'complete','references':[{'id':'c1','name':'白色T恤','mime':'image/png','sha256':digest,'data':base64.b64encode(PNG).decode(),'source_url':'https://shop.example.com/item'}]}
 monkeypatch.setattr(phone,'bridge_request',request)
 phone.start(uid,plan['id'],d['id'],key);wait_until(lambda:(uid,key) not in phone._posting)
 result=phone.status(uid,key)
 assert result['status']=='complete' and (db.IMAGES_DIR/result['references'][0]['image_path']).read_bytes()==PNG
 assert phone.start(uid,plan['id'],d['id'],str(uuid4()))['job_id']==key
 assert calls==[True]
 record=phone._read(uid,'job:'+key);record.update(status='running');phone._write(uid,'job:'+key,record)
 def corrupt(settings,path,**kw):return {'job_id':key,'status':'complete','references':[{'sha256':'0'*64,'data':base64.b64encode(PNG).decode(),'mime':'image/png','source_url':'https://shop.example.com/item'}]}
 monkeypatch.setattr(phone,'bridge_request',corrupt)
 failed=phone.status(uid,key);assert failed['status']=='failed' and failed['references']==result['references']
 monkeypatch.setattr(phone,'bridge_request',request)
 new=str(uuid4());assert phone.start(uid,plan['id'],d['id'],new,retry=True)['job_id']==new;wait_until(lambda:(uid,new) not in phone._posting)

def test_phone_user_and_direction_binding(owner,monkeypatch):
 uid,plan,d=owner
 with pytest.raises(purchase.PurchaseError):phone.start(uid+'other',plan['id'],d['id'],str(uuid4()))
 with pytest.raises(purchase.PurchaseError):phone.status(uid,str(uuid4()))

def test_new_plan_launch_requires_bridge_and_get_never_replays(owner,monkeypatch):
 uid,plan,d=owner;calls=[]
 monkeypatch.setattr(phone,'start',lambda *a,**k:calls.append(a))
 phone.launch_for_plan(uid,plan);assert len(calls)==1
 phone.snapshots(uid,[plan]);assert len(calls)==1
 monkeypatch.setattr(phone,'load_settings',lambda:None);phone.launch_for_plan(uid,plan);assert len(calls)==1


def test_social_share_cards_rejected_before_model():
 assert pc.product_photo_size(PNG,'https://cdn.example.com/garment.png')
 assert not pc.product_photo_size(PNG,'https://shop.example.com/api/og?item=1')
 banner=PNG[:16]+(1200).to_bytes(4,'big')+(630).to_bytes(4,'big')+PNG[24:]
 assert not pc.product_photo_size(banner,'https://cdn.example.com/banner.png')

def test_visible_product_photo_after_social_metadata():
 html='<title>Gray Knit Cardigan | Shop</title><meta property="og:type" content><meta property="og:image" content="/api/og?item=1"><img alt class><img src="https://cdn.example.com/cardigan.jpg" alt="Gray Knit Cardigan">'
 rows=net.product_images(html,'https://shop.example.com/products/cardigan')
 assert rows[-1]['image_url']=='https://cdn.example.com/cardigan.jpg'

def test_conflicting_or_incomplete_quality_checks_do_not_select():
 good={'id':'c1','single_garment':True,'no_person':True,'no_marketing_text':True,'visual_match':True}
 assert pc.quality_ids([good])=={'c1'}
 assert pc.quality_ids([good,{**good,'no_marketing_text':False}])==set()
 assert pc.quality_ids([{**good,'no_person':1}])==set()
 assert pc.quality_ids([{**good,'id':[]}])==set()


@pytest.mark.parametrize('outcome',['empty','failed','interrupted','unknown','complete'])
def test_required_ensure_never_replays_existing_terminal_or_unknown(owner,monkeypatch,outcome):
 uid,plan,d=owner;calls=[]
 def bridge(settings,path,**kw):
  calls.append(path);key=kw['payload']['job_id']
  if outcome=='unknown':raise phone.LLMError('提交未知',code='bridge_timeout')
  if outcome=='failed':raise phone.LLMError('需要登录',code='bridge_login')
  rows=[{'mime':'image/png','sha256':hashlib.sha256(PNG).hexdigest(),'data':base64.b64encode(PNG).decode(),'source_url':'https://shop.example.com/item'}] if outcome=='complete' else []
  return {'job_id':key,'status':outcome,'references':rows}
 monkeypatch.setattr(phone,'bridge_request',bridge)
 first=phone.ensure_for_plan(uid,plan['id'])['references'][d['id']]
 wait_until(lambda:(uid,first['job_id']) not in phone._posting)
 for _ in range(3):
  row=phone.ensure_for_plan(uid,plan['id'])['references'][d['id']]
  assert row['job_id']==first['job_id'] and row['status']==outcome
 assert len(calls)==1
 assert phone.snapshots(uid,[plan])[d['id']]['job_id']==first['job_id']


def test_required_ensure_concurrent_and_user_retry(owner,monkeypatch):
 from concurrent.futures import ThreadPoolExecutor
 uid,plan,d=owner;calls=[];release=threading.Event()
 def bridge(settings,path,**kw):
  calls.append(kw['payload']['job_id']);release.wait(2)
  return {'job_id':kw['payload']['job_id'],'status':'empty','references':[]}
 monkeypatch.setattr(phone,'bridge_request',bridge)
 with ThreadPoolExecutor(max_workers=4) as pool:
  results=list(pool.map(lambda _:phone.ensure_for_plan(uid,plan['id']),range(8)))
 key=results[0]['references'][d['id']]['job_id'];release.set();wait_until(lambda:(uid,key) not in phone._posting)
 assert all(r['references'][d['id']]['job_id']==key for r in results) and calls==[key]
 retry=str(uuid4());phone.start(uid,plan['id'],d['id'],retry,retry=True);wait_until(lambda:(uid,retry) not in phone._posting)
 assert len(calls)==2 and calls[-1]==retry


def test_required_ensure_ownership_and_missing_cached_photo(owner,monkeypatch):
 uid,plan,d=owner
 with pytest.raises(purchase.PurchaseError):phone.ensure_for_plan(uid+'other',plan['id'])
 key=str(uuid4());record={'job_id':key,'plan_id':plan['id'],'direction_id':d['id'],'issued_at':time.time(),'status':'complete','references':[{'image_path':'reference-'+'1'*64+'.png'}],'error':''}
 phone._write(uid,phone._pointer(plan['id'],d['id']),key);phone._write(uid,'job:'+key,record)
 monkeypatch.setattr(phone,'bridge_request',lambda *a,**k:pytest.fail('缓存缺图不得自动重生'))
 result=phone.ensure_for_plan(uid,plan['id'])['references'][d['id']]
 assert result['status']=='failed' and result['references']==[]


def test_required_ensure_route_uses_current_user_and_validates_payload(owner,monkeypatch):
 from app.routes import wardrobe
 from pydantic import ValidationError
 uid,plan,d=owner;calls=[]
 monkeypatch.setattr(wardrobe,'_uid',lambda:uid)
 monkeypatch.setattr(phone,'ensure_for_plan',lambda who,key:(calls.append((who,key)) or {'references':{}}))
 assert wardrobe.purchase_reference_ensure(wardrobe.PurchaseReferenceEnsureIn(plan_id=plan['id']))=={'references':{}}
 assert calls==[(uid,plan['id'])]
 with pytest.raises(ValidationError):wardrobe.PurchaseReferenceEnsureIn(plan_id='')


def test_required_same_plan_saved_fragments_are_all_prepared(owner,monkeypatch):
 import copy
 uid,plan,d=owner;other=copy.deepcopy(d);other['id']='second-'+d['id']
 purchase._write(uid,'latest',None)
 purchase._write(uid,'choices',{'saved':[{**plan,'directions':[d]},{**plan,'directions':[other]}]})
 monkeypatch.setattr(phone,'_submit',lambda *a:None)
 result=phone.ensure_for_plan(uid,plan['id'])['references']
 assert set(result)=={d['id'],other['id']}
 assert len({r['job_id'] for r in result.values()})==2


def test_required_bad_cache_is_repaired_with_valid_returned_bytes(owner,monkeypatch):
 uid,plan,d=owner;digest=hashlib.sha256(PNG).hexdigest();target=db.IMAGES_DIR/('reference-'+digest+'.png');target.write_bytes(b'corrupt')
 assert not phone._cache_usable([{'image_path':target.name}])
 rows=phone._photos({'references':[{'mime':'image/png','sha256':digest,'data':base64.b64encode(PNG).decode(),'source_url':'https://shop.example.com/item'}]})
 assert target.read_bytes()==PNG and phone._cache_usable(rows)


def test_pc_status_file_replace_sharing_violation_does_not_rerun_task(tmp_path,monkeypatch):
 original=Path.replace;calls=[]
 def replace(path,target):
  calls.append(1)
  if len(calls)<3:raise PermissionError('synthetic sharing violation')
  return original(path,target)
 monkeypatch.setattr(Path,'replace',replace)
 pc.write_json(tmp_path/'job.json',{'status':'complete'})
 assert len(calls)==3 and json.loads((tmp_path/'job.json').read_text())=={'status':'complete'}
