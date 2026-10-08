import React,{useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {AnimatePresence,motion} from 'framer-motion';
import {Upload,FileText,Table2,Play,RefreshCw,CheckCircle2,AlertTriangle,Search,Download,ArrowRight,ShieldCheck} from 'lucide-react';
import './styles.css';

type FilePick={file:File|null};
type Job={job_id:string;status:string;progress:number;message:string;identical_sources?:boolean;analysis_skipped?:boolean;matched?:number;unmatched_a?:number|null;unmatched_b?:number|null;exact_identity?:number;exact_text_matches?:number;very_high?:number;high?:number;source_a?:{assets:number|null;file:string};source_b?:{assets:number|null;file:string}};
type Match={match_id:string;source_a_asset_id:string;source_b_asset_id:string;source_a_file:string;source_b_file:string;source_a_page?:number;source_a_sheet?:string;source_a_row?:number;source_b_page?:number;source_b_sheet?:string;source_b_row?:number;source_a_field?:string;source_b_field?:string;source_a_text?:string;source_b_text?:string;application_a:string;application_b:string;application_no_equal:boolean;score:number;decision:string;asset_a_url?:string|null;asset_b_url?:string|null;metrics?:{phash:number;dhash:number;ahash:number;ssim:number;contour_similarity:number;mask_iou:number;mask_ssim:number;sift_inliers:number}};
type ResultPage={items:Match[];total:number};
type MarkAsset={asset_id:string;source_file:string;source_type:string;application_no?:string;company?:string;mark?:string;mark_text?:string;mark_content_type?:string;page?:number;sheet?:string;row?:number;field_label?:string;image_file?:string;asset_url?:string|null};
type AssetPage={items:MarkAsset[];total:number};

const API=(import.meta.env.VITE_API_URL||'http://127.0.0.1:8020').replace(/\/$/,'');
const pct=(x:number)=>`${Math.round(x*100)}%`;

function Drop({label,pick,setPick}:{label:string;pick:FilePick;setPick:(x:FilePick)=>void}){
 const input=useRef<HTMLInputElement>(null);const [over,setOver]=useState(false);
 const accept='.pdf,.xlsx,.xlsm';
 const choose=(f?:File)=>{if(f && ['.pdf','.xlsx','.xlsm'].some(x=>f.name.toLowerCase().endsWith(x)))setPick({file:f})};
 return <motion.div className={`drop ${over?'over':''}`} whileHover={{y:-4}} onDragOver={e=>{e.preventDefault();setOver(true)}} onDragLeave={()=>setOver(false)} onDrop={e=>{e.preventDefault();setOver(false);choose(e.dataTransfer.files[0])}} onClick={()=>input.current?.click()}>
   <input ref={input} type="file" accept={accept} hidden onChange={e=>choose(e.target.files?.[0])}/>
   <div className="dropIcon">{pick.file?.name.endsWith('.pdf')?<FileText/>:<Table2/>}</div>
   <div className="eyebrow">{label}</div>
   <h3>{pick.file?pick.file.name:'Drop PDF / XLSX / XLSM'}</h3>
   <p>{pick.file?`${(pick.file.size/1024/1024).toFixed(2)} MB · ready`: 'Application No. is not required for visual matching.'}</p>
   <button type="button" className="ghost">{pick.file?'Change file':'Choose file'}</button>
 </motion.div>
}

function NetworkVisual(){
 return <div className="networkVisual" aria-label="Connected data network for trademark analysis" role="img">
  <div className="networkTop"><span><i/> SYSTEM ONLINE</span><b>APX / 04</b></div>
  <div className="networkStage">
   <div className="networkLine lineA"/><div className="networkLine lineB"/><div className="networkLine lineC"/><div className="networkLine lineD"/>
   <div className="networkNode nodeA"><span>PDF</span><b>01</b></div>
   <div className="networkNode nodeB"><span>MARK</span><b>540</b></div>
   <div className="networkNode nodeC"><span>SHEETS</span><b>ALL</b></div>
   <div className="networkCore"><span>AI</span><b>VISUAL<br/>ENGINE</b><i/></div>
   <div className="networkNode nodeD"><span>COMPARE</span><b>LIVE</b></div>
   <div className="networkNode nodeE"><span>RESULTS</span><b>DATA</b></div>
  </div>
  <div className="networkFoot"><span>IMAGE + TEXT RECOGNITION</span><strong><i/> SECURE ANALYSIS</strong></div>
 </div>
}

function Metric({name,value}:{name:string;value:number}){return <div className="metric"><span>{name}</span><b>{pct(value)}</b><div className="bar"><i style={{width:`${Math.max(0,Math.min(1,value))*100}%`}}/></div></div>}

function App(){
 const [a,setA]=useState<FilePick>({file:null}),[b,setB]=useState<FilePick>({file:null}),[job,setJob]=useState<Job|null>(null),[results,setResults]=useState<Match[]>([]),[resultCount,setResultCount]=useState(0),[filter,setFilter]=useState('ALL'),[query,setQuery]=useState(''),[loading,setLoading]=useState(false),[error,setError]=useState(''),[resultsLoading,setResultsLoading]=useState(false),[resultsError,setResultsError]=useState(''),[page,setPage]=useState(1),[inventorySide,setInventorySide]=useState<'A'|'B'>('A'),[inventoryItems,setInventoryItems]=useState<MarkAsset[]>([]),[inventoryTotal,setInventoryTotal]=useState(0),[inventoryPage,setInventoryPage]=useState(1),[inventoryQuery,setInventoryQuery]=useState(''),[inventoryLoading,setInventoryLoading]=useState(false),[inventoryError,setInventoryError]=useState('');
 const run=async()=>{
  if(!a.file||!b.file)return;
  setLoading(true);setError('');setResults([]);
  try{
   const fd=new FormData();fd.append('file_a',a.file);fd.append('file_b',b.file);
   const response=await fetch(`${API}/api/analyze`,{method:'POST',body:fd});
   const data=await response.json().catch(()=>({}));
   if(!response.ok)throw new Error(data.detail||`Upload failed (${response.status}).`);
   if(!data.job_id)throw new Error('The server did not return an analysis job ID.');
   setJob(data);
  }catch(requestError){
   setError(requestError instanceof Error?requestError.message:'Analysis could not be started.');
   setLoading(false);
  }
 };
 useEffect(()=>{if(!job?.job_id||job.status==='completed'||job.status==='error')return;const t=setInterval(async()=>{const r=await fetch(`${API}/api/jobs/${job.job_id}`);const state=await r.json().catch(()=>({}));if(!r.ok){setJob(current=>current?.job_id===job.job_id?{...current,status:'error',message:state.detail||'The analysis job is no longer available. Start a new analysis.'}:current);return}setJob({...state,job_id:job.job_id})},1000);return()=>clearInterval(t)},[job?.job_id,job?.status]);
 useEffect(()=>{if(job?.status==='completed'||job?.status==='error')setLoading(false)},[job?.status]);
 useEffect(()=>{
  if(job?.status!=='completed')return;
  const controller=new AbortController();
  const timer=window.setTimeout(async()=>{
   setResultsLoading(true);setResultsError('');
   const params=new URLSearchParams({offset:String((page-1)*100),limit:'100'});
   if(query.trim())params.set('q',query.trim());
   const decisions:Record<string,string>={EXACT:'EXACT',VERY_HIGH:'VERY_HIGH_VISUAL_SIMILARITY',HIGH:'HIGH_VISUAL_SIMILARITY'};
   if(decisions[filter])params.set('decision',decisions[filter]);
   try{
    const response=await fetch(`${API}/api/jobs/${job.job_id}/results?${params}`,{signal:controller.signal});
    const data=await response.json();
    if(!response.ok)throw new Error(data.detail||'Unable to load search results.');
    const resultPage=data as ResultPage;
    setResults(resultPage.items);setResultCount(resultPage.total);
   }catch(requestError){
    if(!controller.signal.aborted)setResultsError(requestError instanceof Error?requestError.message:'Unable to load search results.');
   }finally{
    if(!controller.signal.aborted)setResultsLoading(false);
   }
  },200);
  return()=>{window.clearTimeout(timer);controller.abort()};
 },[job?.job_id,job?.status,page,filter,query]);
 useEffect(()=>{
  if(job?.status!=='completed')return;
  const controller=new AbortController();
  const params=new URLSearchParams({offset:String((inventoryPage-1)*100),limit:'100'});
  if(inventoryQuery.trim())params.set('q',inventoryQuery.trim());
  setInventoryLoading(true);setInventoryError('');
  fetch(`${API}/api/jobs/${job.job_id}/assets/${inventorySide}?${params}`,{signal:controller.signal})
   .then(async response=>{const data=await response.json();if(!response.ok)throw new Error(data.detail||'Unable to load extracted marks.');return data as AssetPage})
   .then(data=>{setInventoryItems(data.items);setInventoryTotal(data.total)})
   .catch(requestError=>{if(!controller.signal.aborted)setInventoryError(requestError instanceof Error?requestError.message:'Unable to load extracted marks.')})
   .finally(()=>{if(!controller.signal.aborted)setInventoryLoading(false)});
  return()=>controller.abort();
 },[job?.job_id,job?.status,inventorySide,inventoryPage,inventoryQuery]);
 const pageCount=Math.max(1,Math.ceil(resultCount/100));
 const inventoryPageCount=Math.max(1,Math.ceil(inventoryTotal/100));

 return <div className="app">
  <div className="noise"/><header><div className="brand"><div className="brandMark">A</div><div><strong>APEXIVE AI</strong><span>TRADEMARK VISUAL CONFLICT</span></div></div><div className="headerPill"><ShieldCheck size={15}/> VISUAL EVIDENCE ENGINE v4</div></header>
  <main>
   <section className="hero"><div className="heroCopy"><div className="eyebrow">GLOBAL TRADEMARK INTELLIGENCE</div><h1>Trademark<br/><em>Conflict Analysis</em></h1><p>Compare artwork and text marks across PDF records and every worksheet in your spreadsheets. Get structured evidence, precise source locations, and visual similarity insights.</p><div className="heroSignals"><span><i/> MULTI-FORMAT INGESTION</span><span><i/> PAGE &amp; SHEET INDEXED</span></div></div><NetworkVisual/></section>
   <section className="uploadGrid"><Drop label="SOURCE A" pick={a} setPick={setA}/><div className="versus"><span>VS</span><ArrowRight/></div><Drop label="SOURCE B" pick={b} setPick={setB}/></section>
   <div className="action"><button className="run" disabled={!a.file||!b.file||loading} onClick={run}><Play size={18} fill="currentColor"/>{loading?'ANALYSIS RUNNING':'RUN VISUAL COMPARISON'}</button>{job&&<button className="reset" onClick={()=>{setJob(null);setResults([]);setA({file:null});setB({file:null});setError('')}}><RefreshCw size={16}/> New analysis</button>}</div>
   {error&&<div className="request-error" role="alert"><AlertTriangle size={16}/>{error}</div>}
   {job&&<section className="progressCard"><div className="progressTop"><span>{job.message||job.status}</span><b>{job.progress||0}%</b></div><div className="progress"><i style={{width:`${job.progress||0}%`}}/></div>{job.status==='error'?<div className="error"><AlertTriangle size={16}/>{job.message}</div>:<div className="tiny">{job.analysis_skipped?job.message:'Application-number equality is metadata only · visual matching remains independent.'}</div>}</section>}
   {job?.status==='completed'&&<>
    <section className="stats"><Stat title="SOURCE A" value={job.source_a?.assets??0}/><Stat title="SOURCE B" value={job.source_b?.assets??0}/><Stat title="MATCHED MARKS" value={job.matched??0} accent/><Stat title="EXACT" value={(job.exact_identity??0)+(job.exact_text_matches??0)}/><Stat title="UNMATCHED A" value={job.unmatched_a??0}/><Stat title="UNMATCHED B" value={job.unmatched_b??0}/></section>
    <section className="inventory">
     <div className="resultsHead"><div><div className="eyebrow">EXTRACTED MARKS</div><h2>{inventoryTotal.toLocaleString()} marks · SOURCE {inventorySide}</h2></div><div className="tools"><div className="filters">{(['A','B'] as const).map(side=><button key={side} className={inventorySide===side?'active':''} onClick={()=>{setInventorySide(side);setInventoryPage(1)}}>SOURCE {side}</button>)}</div><div className="search"><Search size={16}/><input aria-label="Search extracted marks" placeholder="Search mark, sheet, or application" value={inventoryQuery} onChange={e=>{setInventoryQuery(e.target.value);setInventoryPage(1)}}/></div></div></div>
     {inventoryError&&<div className="error"><AlertTriangle size={16}/>{inventoryError}</div>}
     {inventoryLoading&&<div className="tiny">Loading extracted marks…</div>}
     {!inventoryLoading&&!inventoryError&&inventoryTotal===0&&<div className="tiny">No mark assets were found in this source.</div>}
     <div className="assetGrid">{inventoryItems.map(asset=><AssetCard key={asset.asset_id} asset={asset}/>)}</div>
     <div className="pager"><button disabled={inventoryPage<=1||inventoryLoading} onClick={()=>setInventoryPage(p=>p-1)}>← Previous</button><span>Page {inventoryPage} / {inventoryPageCount} · {inventoryTotal.toLocaleString()} marks</span><button disabled={inventoryPage>=inventoryPageCount||inventoryLoading} onClick={()=>setInventoryPage(p=>p+1)}>Next →</button></div>
    </section>
    <section className="resultsHead"><div><div className="eyebrow">MATCH RESULTS</div><h2>{resultCount.toLocaleString()} mark matches</h2></div><div className="tools"><div className="search"><Search size={16}/><input aria-label="Search results" placeholder="ᴛʏᴘᴇ ꜱᴏᴍᴇᴛʜɪɴɢ ᴛᴏ ꜱᴛᴀʀᴛ" value={query} onChange={e=>{setQuery(e.target.value);setPage(1)}}/></div><div className="filters">{['ALL','EXACT','VERY_HIGH','HIGH'].map(x=><button key={x} className={filter===x?'active':''} onClick={()=>{setFilter(x);setPage(1)}}>{x.replace('_',' ')}</button>)}</div><a className="download" href={`${API}/api/jobs/${job.job_id}/files/matches.csv`}><Download size={15}/> CSV</a></div></section>
    {resultsError&&<div className="error"><AlertTriangle size={16}/>{resultsError}</div>}
    {resultsLoading&&<div className="tiny">Searching all matching logos…</div>}
    {!resultsLoading&&!resultsError&&resultCount===0&&<div className="tiny">No matching logos found. Try another application number or search term.</div>}
    <div className="matchList">{results.map((m,i)=><MatchCard key={m.match_id} m={m} i={i}/>)}</div><div className="pager"><button disabled={page<=1||resultsLoading} onClick={()=>setPage(p=>p-1)}>← Previous</button><span>Page {page} / {pageCount} · {resultCount.toLocaleString()} results</span><button disabled={page>=pageCount||resultsLoading} onClick={()=>setPage(p=>p+1)}>Next →</button></div>
   </>}
  </main>
  <footer>Apexive AI · Technical visual evidence only · Application No. does not gate logo matching</footer>
 </div>
}
function Stat({title,value,accent=false}:{title:string;value:number|string;accent?:boolean}){return <div className={`stat ${accent?'accent':''}`}><span>{title}</span><strong>{typeof value==='number'?value.toLocaleString():value}</strong></div>}
function MarkPreview({url,text,alt}:{url?:string|null;text?:string;alt:string}){return <div className="markPreview">{url?<img src={`${API}${url}`} alt={alt} loading="lazy"/>:<div className="markText">{text||'No image or readable text mark was extracted.'}</div>}</div>}
function AssetCard({asset}:{asset:MarkAsset}){const text=asset.mark_text||asset.mark;const location=asset.page?`page ${asset.page}`:asset.sheet?`${asset.sheet} · row ${asset.row}`:'—';return <article className="assetCard"><MarkPreview url={asset.asset_url} text={text} alt={`${asset.asset_id} extracted mark`}/><div className="assetCardInfo"><b>{asset.asset_id}</b><span>{location}</span>{text&&asset.asset_url&&<small>{text}</small>}{asset.application_no&&<small>{asset.application_no}</small>}</div></article>}
function MatchCard({m,i}:{m:Match;i:number}){return <motion.article className="match" initial={{opacity:0,y:15}} animate={{opacity:1,y:0}} transition={{delay:Math.min(i*.015,.25)}}>
 <div className="matchId"><span>{m.match_id}</span><b>{m.decision.replaceAll('_',' ')}</b></div>
 <div className="visualPair"><div className="asset"><span>SOURCE A</span><MarkPreview url={m.asset_a_url} text={m.source_a_text} alt={`${m.source_a_asset_id} mark`}/><small>{m.source_a_file}{m.source_a_page?` · page ${m.source_a_page}`:m.source_a_sheet?` · ${m.source_a_sheet} / row ${m.source_a_row}`:''}{m.source_a_field?` · ${m.source_a_field}`:''}</small></div><div className="linkLine"><div>↔</div><strong>{pct(m.score)}</strong></div><div className="asset"><span>SOURCE B</span><MarkPreview url={m.asset_b_url} text={m.source_b_text} alt={`${m.source_b_asset_id} mark`}/><small>{m.source_b_file}{m.source_b_page?` · page ${m.source_b_page}`:m.source_b_sheet?` · ${m.source_b_sheet} / row ${m.source_b_row}`:''}{m.source_b_field?` · ${m.source_b_field}`:''}</small></div></div>
 <div className="meta"><div><span>Application A</span><b>{m.application_a||'—'}</b></div><div><span>Application B</span><b>{m.application_b||'—'}</b></div><div><span>App No. equal</span><b>{m.application_no_equal?'YES':'NO — STILL MATCHED'}</b></div></div>
 {m.metrics&&<div className="metrics"><Metric name="pHash" value={m.metrics.phash}/><Metric name="dHash" value={m.metrics.dhash}/><Metric name="SSIM" value={m.metrics.ssim}/><Metric name="Contour" value={m.metrics.contour_similarity}/><Metric name="Mask IoU" value={m.metrics.mask_iou}/><Metric name="Mask SSIM" value={m.metrics.mask_ssim}/><div className="sift"><span>SIFT inliers</span><b>{m.metrics.sift_inliers}</b></div></div>}
 </motion.article>}
createRoot(document.getElementById('root')!).render(<App/>);
