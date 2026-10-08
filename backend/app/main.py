from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from threading import Thread
import uuid, shutil, traceback, json
from .engine import run

BASE=Path(__file__).resolve().parent.parent
DATA=BASE/'data';DATA.mkdir(exist_ok=True)
JOBS={}
app=FastAPI(title='Apexive AI Trademark Visual Conflict API',version='4.0.0')
app.add_middleware(CORSMiddleware,allow_origins=['https://trademark-conflict-detector.vercel.app', 'https://trademark.apexiveai.com', 'http://localhost:5173', 'http://127.0.0.1:5173'],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])

ALLOWED={'.pdf','.xlsx','.xlsm'}

def clean_name(name):return Path(name or 'upload').name

@app.get("/")
def root():
    return {"status": "ok"}

@app.get('/health')
def health():return {'engine':'v4'}

@app.post('/api/analyze')
async def analyze(file_a:UploadFile=File(...),file_b:UploadFile=File(...)):
    ea=Path(clean_name(file_a.filename)).suffix.lower();eb=Path(clean_name(file_b.filename)).suffix.lower()
    if ea not in ALLOWED or eb not in ALLOWED:raise HTTPException(400,'Each source must be PDF, XLSX, or XLSM.')
    jid=uuid.uuid4().hex;d=DATA/jid;inp=d/'input';inp.mkdir(parents=True)
    pa=inp/f'source_a{ea}';pb=inp/f'source_b{eb}'
    with pa.open('wb') as f:shutil.copyfileobj(file_a.file,f)
    with pb.open('wb') as f:shutil.copyfileobj(file_b.file,f)
    JOBS[jid]={'status':'queued','progress':0,'message':'Queued','source_a':file_a.filename,'source_b':file_b.filename}
    def worker():
        try:
            def update(p,msg):JOBS[jid].update({'status':'running','progress':p,'message':msg})
            summary=run(d,pa,pb,update);JOBS[jid].update({'status':'completed','progress':100,'message':'Completed',**summary})
        except Exception as e:JOBS[jid].update({'status':'error','progress':100,'message':str(e),'traceback':traceback.format_exc()})
    Thread(target=worker,daemon=True).start()
    return {'job_id':jid,'status':'queued'}

@app.get('/api/jobs/{jid}')
def job(jid:str):
    if jid not in JOBS:raise HTTPException(404,'Job not found')
    return {'job_id':jid,**JOBS[jid]}

@app.get('/api/jobs/{jid}/results')
def results(jid:str,offset:int=0,limit:int=100,q:str='',decision:str=''):
    if jid not in JOBS:raise HTTPException(404,'Job not found')
    p=DATA/jid/'report'/'matches.json'
    limit=max(1,min(limit,500));offset=max(0,offset)
    if not p.exists():return {'items':[],'total':0,'offset':offset,'limit':limit}
    items=json.loads(p.read_text(encoding='utf8'))
    query=q.strip().casefold()
    if query:
        searchable_fields=('match_id','source_a_asset_id','source_b_asset_id','source_a_file','source_b_file','source_a_sheet','source_b_sheet','source_a_text','source_b_text','application_a','application_b')
        items=[item for item in items if any(query in str(item.get(field) or '').casefold() for field in searchable_fields)]
    if decision:
        if decision=='EXACT':
            items=[item for item in items if item.get('decision') in {'EXACT_VISUAL_IDENTITY','EXACT_TEXT_MARK_MATCH'}]
        else:
            items=[item for item in items if item.get('decision')==decision]
    return {'items':items[offset:offset+limit],'total':len(items),'offset':offset,'limit':limit}

@app.get('/api/jobs/{jid}/assets/{side}')
def assets(jid:str,side:str,offset:int=0,limit:int=100,q:str=''):
    if jid not in JOBS:raise HTTPException(404,'Job not found')
    if side not in {'A','B'}:raise HTTPException(400,'Invalid side')
    p=DATA/jid/'report'/f'assets_{side.lower()}.json'
    if not p.exists():return {'items':[],'total':0,'offset':max(0,offset),'limit':max(1,min(limit,500))}
    limit=max(1,min(limit,500));offset=max(0,offset)
    items=json.loads(p.read_text(encoding='utf8'))
    query=q.strip().casefold()
    if query:
        fields=('asset_id','source_file','application_no','company','mark','mark_text','sheet','page','row','field_label')
        items=[item for item in items if any(query in str(item.get(field) or '').casefold() for field in fields)]
    total=len(items)
    page_items=items[offset:offset+limit]
    for item in page_items:
        item['asset_url']=f"/api/jobs/{jid}/assets/{side}/{item['image_file']}" if item.get('image_file') else None
    return {'items':page_items,'total':total,'offset':offset,'limit':limit}

@app.get('/api/jobs/{jid}/assets/{side}/{filename}')
def asset(jid:str,side:str,filename:str):
    if side not in {'A','B'}:raise HTTPException(400,'Invalid side')
    p=DATA/jid/'assets'/filename
    if not p.exists():raise HTTPException(404,'Asset not found')
    return FileResponse(p,media_type='image/png')

@app.get('/api/jobs/{jid}/files/{filename:path}')
def output_file(jid:str,filename:str):
    p=DATA/jid/'report'/filename
    if not p.exists() or not p.is_file():raise HTTPException(404,'File not found')
    return FileResponse(p)
