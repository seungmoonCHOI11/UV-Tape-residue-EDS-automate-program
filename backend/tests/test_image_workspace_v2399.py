import time, threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from app import db

def test_page_has_all_assets_over_response_cap(monkeypatch):
    class Query:
        def __init__(self,table):self.table=table;self.ids=None;self.bounds=None
        def select(self,*args):return self
        def order(self,*args,**kwargs):return self
        def in_(self,key,ids):self.ids=ids;return self
        def range(self,a,b):self.bounds=(a,b);return self
        def execute(self):
            if self.table=='points':rows=[{'id':str(i)} for i in range(81)]
            elif self.table=='point_assets':rows=[{'point_id':i,'asset_type':f'map{k}','storage_path':f'{i}/{k}'} for i in self.ids for k in range(25)]
            else:rows=[{'point_id':i,'features':{}} for i in self.ids]
            a,b=self.bounds or (0,999);return SimpleNamespace(data=rows[a:b+1][:1000])
    monkeypatch.setattr(db,'get_client',lambda:SimpleNamespace(table=lambda table:Query(table)))
    first=db.get_all_points_with_data(offset=0,limit=60);last=db.get_all_points_with_data(offset=60,limit=60)
    assert len(first)==60 and len(last)==21
    assert all(len(assets)==25 for row,analysis,assets in first+last)

def test_dynamic_images_serialized_and_recover_after_failure(monkeypatch):
    from app import main
    count=0;peak=0
    def make(pid,kind):
        nonlocal count,peak
        count+=1;peak=max(peak,count);time.sleep(.01);count-=1;return kind
    monkeypatch.setattr(main,'_dynamic_asset_unlocked',make)
    with ThreadPoolExecutor(max_workers=4) as pool:assert list(pool.map(lambda i:main._dynamic_asset('p',str(i)),range(4)))==['0','1','2','3']
    assert peak==1
    def fail(*args):raise ValueError('synthetic failure')
    monkeypatch.setattr(main,'_dynamic_asset_unlocked',fail)
    from fastapi import HTTPException
    import pytest
    with pytest.raises(HTTPException) as e:main._dynamic_asset('p','sem')
    assert e.value.status_code==503
    monkeypatch.setattr(main,'_dynamic_asset_unlocked',make)
    assert main._dynamic_asset('p','sem')=='sem'

def test_workspace_http_pagination(monkeypatch):
    from app import main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main.db,'configured',lambda:False)
    monkeypatch.setattr(main,'PROJECTS',{'batch':{'records':[str(i) for i in range(81)]}})
    monkeypatch.setattr(main,'RECORDS',{str(i):{'id':str(i),'point':i+1,'wafer':1,'assets':{},'features':{}} for i in range(81)})
    c=TestClient(main.app)
    first=c.get('/api/workspace?offset=0&limit=60').json();last=c.get('/api/workspace?offset=60&limit=60').json()
    assert len(first['points'])==60 and first['has_more']
    assert len(last['points'])==21 and not last['has_more']
    assert c.get('/api/workspace?limit=121').status_code==422
