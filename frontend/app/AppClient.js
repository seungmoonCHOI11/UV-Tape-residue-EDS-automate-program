"use client";
import {useEffect,useRef,useState} from "react";
import {Upload,LayoutDashboard,BrainCircuit,Images,GitCompare,FileText,Settings,FileUp,Play,Check,Download,ChevronLeft,ChevronRight,SkipForward,Search,Database,Activity,Plus,Trash2} from "lucide-react";

const API=process.env.NEXT_PUBLIC_API_BASE_URL||"https://uv-tape-residue-eds-backend.onrender.com";
const DEFAULT_CONDITION={power:"150",time:"30",wafers:"1,4,5",points:"1-9"};

function parseSelection(value){
 const out=[];
 String(value||"").split(",").forEach(part=>{
   const t=part.trim(); if(!t)return;
   if(t.includes("-")){const [a,b]=t.split("-").map(Number);if(Number.isFinite(a)&&Number.isFinite(b)){for(let n=Math.min(a,b);n<=Math.max(a,b);n++)out.push(n)}} else {const n=Number(t);if(Number.isFinite(n))out.push(n)}
 });
 return [...new Set(out)].sort((a,b)=>a-b);
}

export default function App(){
 const [mounted,setMounted]=useState(false),[initialLoading,setInitialLoading]=useState(true),[page,setPage]=useState("Dashboard"),[points,setPoints]=useState([]),[project,setProject]=useState(null),[files,setFiles]=useState([]),[idx,setIdx]=useState(0),[msg,setMsg]=useState(""),[aiAnalysis,setAiAnalysis]=useState(null),[aiBusy,setAiBusy]=useState(false),input=useRef();
 const [q,setQ]=useState(""),[result,setResult]=useState("All"),[conditions,setConditions]=useState([{...DEFAULT_CONDITION}]),[substrateType,setSubstrateType]=useState("SiCN"),[sampleCategory,setSampleCategory]=useState("MAIN"),[pagesPerPoint,setPagesPerPoint]=useState(3),[dragging,setDragging]=useState(false),[busy,setBusy]=useState(false),[progress,setProgress]=useState(0),[progressPhase,setProgressPhase]=useState("idle"),[progressMessage,setProgressMessage]=useState(""),[verificationOpen,setVerificationOpen]=useState(false);
 const notify=x=>{setMsg(x);setTimeout(()=>setMsg(""),3200)};
 async function loadData(preferredId=null){
   setInitialLoading(true);
   const savedId=preferredId||(typeof window!=="undefined"?localStorage.getItem("uvtape:selectedProject"):null);
   let lastError=null;
   try{
     // Render can take a little time to wake from sleep. Retry before showing
     // an error so a transient cold-start does not look like lost project data.
     for(let attempt=0;attempt<4;attempt++){
       try{
         const target=savedId?`${API}/api/projects/${savedId}`:`${API}/api/projects/latest`;
         let r=await fetch(target,{cache:"no-store"});
         if(!r.ok && savedId){
           r=await fetch(`${API}/api/projects/latest`,{cache:"no-store"});
         }
         if(!r.ok) throw new Error(`HTTP ${r.status}`);
         const j=await r.json();
         if(j?.project_id || j?.id){
           const pid=j.project_id||j.id;
           const arr=(j.points||[]).map(x=>({...x,human_result:x.human_result||null}));
           setProject(pid);setPoints(arr);
           const first=arr.findIndex(z=>!z.human_result);setIdx(first>=0?first:0);
           if(typeof window!=="undefined") localStorage.setItem("uvtape:selectedProject",pid);
           return arr;
         }
         setProject(null);setPoints([]);return [];
       }catch(e){
         lastError=e;
         if(attempt<3) await new Promise(r=>setTimeout(r,[1200,2500,4500][attempt]));
       }
     }
     notify(`프로젝트 데이터를 불러오지 못했습니다. ${lastError?.message||""}`.trim());
     return [];
   }finally{setInitialLoading(false);}
 }
 useEffect(()=>{setMounted(true);loadData()},[]);
 useEffect(()=>{const h=e=>{if(typeof e.detail?.index!=="number")return;setIdx(e.detail.index);setVerificationOpen(true)};window.addEventListener("uvtape:open-point",h);return()=>window.removeEventListener("uvtape:open-point",h)},[]);
function nextUnverified(start=0){
 for(let i=Math.max(0,start);i<points.length;i++) if(!points[i].human_result) return i;
 return -1;
}
function addFiles(list){const incoming=Array.from(list||[]).filter(f=>f.name.toLowerCase().endsWith(".pdf"));if(!incoming.length)return notify("PDF 파일만 추가할 수 있습니다.");setFiles(prev=>[...prev,...incoming]);}
 function updateCondition(i,key,value){setConditions(cs=>cs.map((c,n)=>n===i?{...c,[key]:value}:c));}
 function addCondition(){setConditions(cs=>[...cs,{...DEFAULT_CONDITION}]);}
 function removeCondition(i){setConditions(cs=>cs.length===1?cs:cs.filter((_,n)=>n!==i));}
 function expectedPoints(){return conditions.reduce((sum,c)=>{const wafers=String(c.wafers).split(",").flatMap(x=>{x=x.trim();if(!x)return[];if(x.includes("-")){const [a,b]=x.split("-").map(Number);return Number.isFinite(a)&&Number.isFinite(b)?Array.from({length:Math.max(0,b-a+1)},(_,i)=>a+i):[]}return [Number(x)]}).filter(Number.isFinite);const pts=String(c.points).split(",").flatMap(x=>{x=x.trim();if(!x)return[];if(x.includes("-")){const [a,b]=x.split("-").map(Number);return Number.isFinite(a)&&Number.isFinite(b)?Array.from({length:Math.max(0,b-a+1)},(_,i)=>a+i):[]}return [Number(x)]}).filter(Number.isFinite);return sum+new Set(wafers).size*new Set(pts).size},0)}
 async function upload(){
   if(!files.length)return notify("EDS PDF를 먼저 선택하세요.");
   const n=expectedPoints();
   if(!n)return notify("Power / Time / Wafer / Point 조건을 확인하세요.");
   const fd=new FormData();files.forEach(f=>fd.append("files",f));fd.append("conditions_json",JSON.stringify(conditions));fd.append("pages_per_point",String(pagesPerPoint));fd.append("substrate_type",substrateType);fd.append("sample_category",sampleCategory);
   setBusy(true);setProgress(0);setProgressPhase("upload");setProgressMessage("PDF 파일을 서버에 업로드하는 중...");
   try{
     const j=await new Promise((resolve,reject)=>{
       const xhr=new XMLHttpRequest();
       xhr.open("POST",`${API}/api/upload`);
       xhr.upload.onprogress=e=>{if(e.lengthComputable){const pct=Math.round((e.loaded/e.total)*10);setProgress(pct);setProgressMessage(`PDF 업로드 중 · ${Math.round(e.loaded/e.total*100)}%`);}};
       xhr.onerror=()=>reject(new Error("서버 연결에 실패했습니다."));
       xhr.ontimeout=()=>reject(new Error("업로드 시간이 초과되었습니다."));
       xhr.onload=()=>{let body=xhr.responseText;let data={};try{data=JSON.parse(body)}catch{}if(xhr.status<200||xhr.status>=300){reject(new Error(`HTTP ${xhr.status}: ${data.detail||body||"server error"}`));return}resolve(data)};
       xhr.send(fd);
     });
     setProgress(10);setProgressPhase("processing");setProgressMessage("파일 검증 완료 · 분석을 시작합니다...");
     let done=false;
     while(!done){
       await new Promise(r=>setTimeout(r,1000));
       const r=await fetch(`${API}/api/jobs/${j.job_id}`);const st=await r.json();
       if(!r.ok)throw new Error(st.detail||"분석 작업 상태를 확인할 수 없습니다.");
       setProgress(Math.max(10,Math.min(100,st.progress||10)));setProgressPhase(st.phase||"processing");setProgressMessage(st.message||"분석 중...");
       if(st.status==="completed"){
         done=true;
         const pr=await fetch(`${API}/api/projects/${st.project_id}`);const pj=await pr.json();
         setProject(st.project_id);if(typeof window!=="undefined")localStorage.setItem("uvtape:selectedProject",st.project_id);const loaded=(pj.points||[]).map(x=>({...x,human_result:x.human_result||null}));setPoints(loaded);setIdx(nextUnverified(0)>=0?nextUnverified(0):0);setPage("Dashboard");setVerificationOpen(true);notify(`${st.count||loaded.length||n} points 분석 완료`);
       }else if(st.status==="failed")throw new Error(st.error||st.message||"분석에 실패했습니다.");
     }
   }catch(e){notify(`Backend 오류: ${e?.message||"network error"}`)}finally{setBusy(false);setTimeout(()=>{setProgressPhase("idle");setProgressMessage("");setProgress(0)},700)}
 }
 async function human(v){
   const p=points[idx]; if(!p)return;
   try{
     const r=await fetch(`${API}/api/points/${p.id}/human?result=${encodeURIComponent(v)}`,{method:"POST"});
     if(!r.ok) throw new Error("검증 결과 저장 실패");
     const j=await r.json();
     const updated=points.map(z=>z.id===p.id?j:z); setPoints(updated);if(typeof window!=="undefined")localStorage.setItem("uvtape:lastPointId", p.id);
     if(v==="Skip"){setIdx(Math.min(updated.length-1,idx+1));return;}
     const ni=updated.findIndex((z,i)=>i>idx&&!z.human_result);
     if(ni>=0){setIdx(ni);return;}
     const first=updated.findIndex(z=>!z.human_result);
     if(first>=0){setIdx(first);return;}
     setVerificationOpen(false);notify("모든 Point의 검증이 완료되었습니다.");
   }catch(e){notify(e.message||"검증 결과 저장 실패")}
 }
 async function runProjectAI(){if(!project)return notify("먼저 분석 데이터를 불러오세요.");setAiBusy(true);notify("OpenAI가 전체 실험 결과를 분석하고 있습니다...");try{const r=await fetch(`${API}/api/projects/${project}/ai-analysis`,{method:"POST"});const j=await r.json();if(!r.ok)throw new Error(j.detail||j.message||"AI analysis failed");setAiAnalysis(j);setPage("AI Analysis");notify("AI 연구 분석 완료")}catch(e){notify("AI 분석 오류: "+e.message)}finally{setAiBusy(false)}}
async function reanalyzeProject(){if(!project)return notify("현재 프로젝트가 없습니다.");if(!window.confirm("R2에 저장된 원본 PDF로 현재 프로젝트를 다시 분석할까요? 기존 분석 결과가 새 분석 결과로 갱신됩니다."))return;setBusy(true);try{const r=await fetch(`${API}/api/projects/${project}/reanalyze`,{method:"POST"});const j=await r.json();if(!r.ok)throw new Error(j.detail||j.message||"re-analysis failed");const poll=async()=>{const pr=await fetch(`${API}/api/jobs/${j.job_id}`);const st=await pr.json();setProgress(st.progress||0);setProgressPhase(st.phase||"reanalysis");setProgressMessage(st.message||"재분석 중...");if(st.status==="completed"){setBusy(false);const loaded=await loadData(project);const ni=loaded.findIndex(z=>!z.human_result);if(ni>=0)setIdx(ni);setVerificationOpen(loaded.length>0);notify("기존 데이터 재분석 완료");setTimeout(()=>{setProgressPhase("idle");setProgressMessage("");setProgress(0)},700);return;}if(st.status==="failed"){setBusy(false);throw new Error(st.error||"재분석 실패");}setTimeout(poll,1200)};await poll()}catch(e){setBusy(false);notify("재분석 오류: "+e.message)}}

 async function openVerification(){
 const ni=points.findIndex(z=>!z.human_result);
 if(ni>=0){setIdx(ni);setPage("Dashboard");setVerificationOpen(true)}
 else notify("모든 Point가 검증되었습니다.");
}
function goNav(n){if(n==="Verification"){openVerification();return}setVerificationOpen(false);setPage(n)}
 async function exportFile(kind){if(!project)return notify("먼저 실제 PDF를 업로드해 project를 생성하세요.");const r=await fetch(`${API}/api/projects/${project}/export/${kind}`,{method:"POST"});if(!r.ok)return notify("Export 실패");const blob=await r.blob(),u=URL.createObjectURL(blob),a=document.createElement("a");a.href=u;a.download=`point_report.${kind==="ppt"?"pptx":kind}`;a.click();URL.revokeObjectURL(u);notify(`${kind.toUpperCase()} export 완료`)}
 if(!mounted)return <div className="app"><main><InitialLoading/></main></div>;
 if(initialLoading)return <div className="app"><main><InitialLoading/></main></div>;
 const p=points[idx],filtered=points.filter(x=>(result==="All"||(x.human_result||x.features?.result)===result)&&Object.values(x).join(" ").toLowerCase().includes(q.toLowerCase()));
 return <div className="app"><aside><div className="logo"><b>EDS</b><span>Insight Lab</span></div><div className="project"><small>PROJECT</small><strong>{project?"UV Tape Residue":"No project selected"}</strong><span>{points.length} points</span></div>{[["Dashboard",LayoutDashboard],["New Analysis",Upload],["Verification",Check],["AI Analysis",BrainCircuit],["Image Gallery",Images],["Condition Compare",GitCompare],["Reports",FileText]].map(([n,I])=><button className={page===n?"nav active":"nav"} key={n} onClick={()=>goNav(n)}><I size={16}/>{n}</button>)}<div className="sideBottom"><button className="nav"><Settings size={16}/>Settings</button></div></aside><main><header><div><small>Projects / UV Tape Residue / {page}</small><h1>{page}</h1></div><div className="headerActions">{project&&<button className="secondary reanalyzeBtn" onClick={reanalyzeProject} disabled={busy}><Database size={13}/>{busy?"Working...":"Re-analyze"}</button>}<span className="ready">● {points.length?"Data loaded":"Ready"}</span></div></header>
 {page==="Dashboard"&&<Dashboard points={points} go={setPage}/>}
 {page==="New Analysis"&&<UploadPage input={input} files={files} setFiles={setFiles} upload={upload} conditions={conditions} updateCondition={updateCondition} addCondition={addCondition} removeCondition={removeCondition} pagesPerPoint={pagesPerPoint} setPagesPerPoint={setPagesPerPoint} dragging={dragging} setDragging={setDragging} addFiles={addFiles} expectedPoints={expectedPoints} busy={busy} substrateType={substrateType} setSubstrateType={setSubstrateType} sampleCategory={sampleCategory} setSampleCategory={setSampleCategory}/>} 
 {page==="Verification"&&(p?<Review p={p} idx={idx} total={points.length} prev={()=>setIdx(Math.max(0,idx-1))} next={()=>setIdx(Math.min(points.length-1,idx+1))} human={human}/>:<Empty title="분석 데이터가 없습니다" text="EDS PDF를 업로드하면 Point별 분석 결과가 이 화면에 표시됩니다." go={()=>setPage("New Analysis")}/>)}
 {page==="AI Analysis"&&<AIAnalysis points={points} analysis={aiAnalysis} busy={aiBusy} run={runProjectAI}/>}
 {page==="Image Gallery"&&<Gallery points={filtered} q={q} setQ={setQ} result={result} setResult={setResult}/>}
 {page==="Condition Compare"&&<Compare points={points}/>} {page==="Reports"&&<Reports exportFile={exportFile} project={project}/>} </main>{verificationOpen&&p&&<VerificationModal p={p} idx={idx} total={points.length} close={()=>setVerificationOpen(false)} prev={()=>setIdx(Math.max(0,idx-1))} next={()=>setIdx(Math.min(points.length-1,idx+1))} human={human}/>} {busy&&<AnalysisProgress progress={progress} phase={progressPhase} message={progressMessage}/>} {msg&&<div className="toast"><Check size={14}/>{msg}</div>}</div>
}
function InitialLoading(){return <div className="initialLoading"><div className="initialLoadingCard"><div className="initialLoadingBrand"><b>EDS</b><span>Insight Lab</span></div><div className="initialLoadingTitle">Project data loading</div><p>기존 분석 데이터와 Point 정보를 불러오는 중입니다.</p><div className="initialLoadingTrack"><div className="initialLoadingBar"/></div><div className="initialLoadingMeta"><span>Loading project data</span><span>잠시만 기다려주세요</span></div></div></div>}
function AnalysisProgress({progress,phase,message}){
 const label=phase==="upload"?"파일 업로드":phase==="queued"?"분석 준비":phase==="analysis"?"Point 분석":phase==="database"?"결과 저장":"분석 진행";
 return <div className="progressOverlay"><div className="progressModal"><div className="progressTop"><div><span className="badge"><Activity size={13}/> ANALYSIS IN PROGRESS</span><h3>{label}</h3></div><b>{Math.round(progress)}%</b></div><div className="progressTrack"><div className="progressBar" style={{width:`${Math.max(2,Math.min(100,progress))}%`}}/></div><p>{message||"분석 중입니다. 잠시만 기다려주세요."}</p><small>창을 닫거나 새로고침하지 마세요.</small></div></div>
}
function Dashboard({points,go}){
 const key=p=>`${p.power||"-"} / ${p.time||"-"}`;
 const cls=p=>p.human_result||p.features?.result||"Ambiguous";
 const groups={}; points.forEach(p=>{(groups[key(p)]??=[]).push(p)});
 const residue=points.filter(p=>cls(p)==="Residue").length;
 const non=points.filter(p=>cls(p)==="Non-residue").length;
 const review=points.length-residue-non;
 const verified=points.filter(p=>p.human_verified_at||p.human_result).length;
 const reps=[
   ["Residue",points.find(p=>cls(p)==="Residue"),"residue"],
   ["Non-residue",points.find(p=>cls(p)==="Non-residue"),"non"],
   ["Ambiguous",points.find(p=>cls(p)==="Ambiguous"||cls(p)==="Review"),"review"]
 ];
 const open=p=>{if(!p)return; const i=points.findIndex(x=>x.id===p.id); if(i>=0){go("Verification"); setTimeout(()=>window.dispatchEvent(new CustomEvent("uvtape:open-point",{detail:{index:i}})),0)}};
 return <div className="content">
   <div className="dashboardHeader"><div><span className="badge"><Activity size={13}/> SEM / EDS</span><h2>Analysis Dashboard</h2><p>현재 저장된 조건과 분석 결과의 요약입니다.</p></div><div className="dashboardActions"><button className="primary" onClick={()=>go("New Analysis")}><Upload size={14}/> New Analysis</button><button className="secondary" onClick={()=>go("Verification")}><Check size={14}/> Verification</button></div></div>
   <section className="panel conditionSection"><div className="panelHead"><b>Saved Conditions</b><small>{Object.keys(groups).length} condition(s) · {points.length} points</small></div><div className="conditionCards">{Object.entries(groups).map(([k,a])=>{const r=a.filter(p=>cls(p)==="Residue").length;const n=a.filter(p=>cls(p)==="Non-residue").length;const q=a.length-r-n;return <button className="conditionCard" key={k} onClick={()=>go("Condition Compare")}><b>{k}</b><span>{a.length} Points · Residue {r} · Non-residue {n} · Ambiguous {q}</span></button>})}{!points.length&&<div className="emptyInline">저장된 분석 조건이 없습니다.</div>}</div></section>
   <div className="dashboardMetrics"><Metric t="Points" v={points.length} s="current project"/><Metric t="Residue" v={residue} s={points.length?`${Math.round(residue/points.length*100)}%`:"—"}/><Metric t="Non-residue" v={non} s={points.length?`${Math.round(non/points.length*100)}%`:"—"}/><Metric t="Ambiguous" v={review} s="needs review"/><Metric t="Verified" v={verified} s="human reviewed"/><Metric t="Conditions" v={Object.keys(groups).length} s="Power / Time"/></div>
   {points.length>0&&<><div className="dashboardTwoCol"><section className="panel"><div className="panelHead"><b>Result Distribution</b><small>Current project</small></div><div className="distribution">{[["Residue",residue], ["Non-residue",non], ["Ambiguous",review]].map(([label,n])=><div className="distRow" key={label}><span>{label}</span><div className="distTrack"><i style={{width:`${Math.round(n/points.length*100)}%`}}/></div><strong>{n} · {Math.round(n/points.length*100)}%</strong></div>)}</div></section><section className="panel"><div className="panelHead"><b>Zone Summary</b><small>Center / Edge / Diamond</small></div><div className="dashTable"><div className="dashTr dashTh"><span>Zone</span><span>Points</span><span>Residue</span><span>Non</span><span>Ambiguous</span><span>Residue %</span></div>{["Center","Edge","Diamond","Unknown"].map(z=>{const a=points.filter(p=>(p.zone||"Unknown")===z);if(!a.length)return null;const rr=a.filter(p=>cls(p)==="Residue").length;const nn=a.filter(p=>cls(p)==="Non-residue").length;const aa=a.filter(p=>cls(p)==="Ambiguous"||cls(p)==="Review").length;return <div className="dashTr" key={z}><span>{z}</span><span>{a.length}</span><span>{rr}</span><span>{nn}</span><span>{aa}</span><strong>{Math.round(rr/a.length*100)}%</strong></div>})}</div></section></div>
   <section className="panel conditionSection"><div className="panelHead"><b>Power / Time Summary</b><small>Stored point results</small></div><div className="dashTable"><div className="dashTr dashTh"><span>Condition</span><span>Points</span><span>Residue</span><span>Non</span><span>Ambiguous</span><span>Residue %</span></div>{Object.entries(groups).map(([k,a])=>{const rr=a.filter(p=>cls(p)==="Residue").length;const nn=a.filter(p=>cls(p)==="Non-residue").length;const aa=a.filter(p=>cls(p)==="Ambiguous"||cls(p)==="Review").length;return <div className="dashTr" key={k}><span>{k}</span><span>{a.length}</span><span>{rr}</span><span>{nn}</span><span>{aa}</span><strong>{Math.round(rr/a.length*100)}%</strong></div>})}</div></section>
   <section className="panel representativePanel"><div className="panelHead"><b>Representative Results</b><small>One stored example per result state</small></div><div className="representativeGrid">{reps.map(([label,p,kind])=>p?<button className={`repCard ${kind}`} key={label} onClick={()=>open(p)}><div className="repImage"><ImageWithFallback sources={[p.assets?.sem_residue_overlay,p.assets?.sem]} alt={label}/></div><div className="repBody"><b>{p.power} · {p.time} · W{p.wafer} · P{p.point}</b><strong>{label}</strong><span>Score {displayScore(p)==null?"-":displayScore(p)} / 100 · {p.confidence||p.features?.confidence||"-"}</span></div></button>:<div className="repCard emptyRep" key={label}><strong>{label}</strong><span>현재 데이터에 해당 결과가 없습니다.</span></div>)}</div></section></>}
   {!points.length&&<section className="panel emptyPanel"><Database size={24}/><b>분석 데이터가 없습니다</b><small>EDS PDF를 업로드하면 조건과 Point 분석 결과가 이 대시보드에 표시됩니다.</small><button className="secondary" onClick={()=>go("New Analysis")}><Upload size={14}/> EDS 데이터 업로드</button></section>}
 </div>
}
function Metric({t,v,s}){return <div className="metric"><small>{t}</small><b>{v}</b><span>{s}</span></div>}
function UploadPage({input,files,setFiles,upload,conditions,updateCondition,addCondition,removeCondition,pagesPerPoint,setPagesPerPoint,dragging,setDragging,addFiles,expectedPoints,busy,substrateType,setSubstrateType,sampleCategory,setSampleCategory}){return <div className="content"><div className="intro"><div><h2>New Analysis</h2></div></div><section className="panel"><div className="panelHead"><b>1. EDS source upload</b><small>PDF · drag & drop supported</small></div><div className={`drop ${dragging?"dragging":""}`} onClick={()=>input?.current?.click()} onDragOver={e=>{e.preventDefault();setDragging(true)}} onDragLeave={()=>setDragging(false)} onDrop={e=>{e.preventDefault();setDragging(false);addFiles(e.dataTransfer.files)}}><FileUp size={30}/><b>{dragging?"여기에 PDF를 놓으세요":"EDS PDF를 끌어다 놓거나 클릭해서 선택하세요"}</b><small>여러 PDF를 선택하면 업로드 순서대로 이어서 매핑합니다.</small><input ref={input} hidden type="file" multiple accept=".pdf,application/pdf" onChange={e=>addFiles(e.target.files)}/></div>{files.map((f,i)=><div className="file" key={`${f.name}-${i}`}><FileText size={14}/><span>{f.name}</span><small>{(f.size/1024/1024).toFixed(1)} MB</small><button className="iconBtn" onClick={()=>setFiles(fs=>fs.filter((_,n)=>n!==i))}><Trash2 size={13}/></button></div>)}</section><section className="panel samplePanel"><div className="panelHead"><div><b>2. Sample / Substrate</b></div></div><div className="sampleControls"><label>Category<select value={sampleCategory} onChange={e=>{const v=e.target.value;setSampleCategory(v);if(v==="MAIN"){setSubstrateType("SiCN")}}}><option value="MAIN">MAIN</option><option value="ANOTHER">ANOTHER</option></select></label><label>Substrate<select value={substrateType} onChange={e=>setSubstrateType(e.target.value)}><option>SiCN</option><option>Si</option><option>Other</option></select></label></div></section><section className="panel conditionPanel"><div className="panelHead"><div className="conditionHead"><div><b>3. Analysis conditions</b></div><button className="secondary" onClick={addCondition}><Plus size={13}/> Add condition</button></div></div>{conditions.map((c,i)=><div className="conditionRow" key={i}><div className="conditionTitle">Condition {i+1}</div><label>Power (W)<input value={c.power} onChange={e=>updateCondition(i,"power",e.target.value)}/></label><label>Time (s)<input value={c.time} onChange={e=>updateCondition(i,"time",e.target.value)}/></label><div className="selectionGroup"><span className="selectionLabel">Wafer</span><div className="checks">{Array.from({length:9},(_,n)=>n+1).map(w=>{const a=parseSelection(c.wafers);return <label className="check" key={w}><input type="checkbox" checked={a.includes(w)} onChange={()=>{const next=a.includes(w)?a.filter(x=>x!==w):[...a,w].sort((x,y)=>x-y);updateCondition(i,"wafers",next.join(","))}}/><span>W{w}</span></label>})}</div></div><div className="selectionGroup"><span className="selectionLabel">Point</span><div className="checks">{Array.from({length:9},(_,n)=>n+1).map(pt=>{const a=parseSelection(c.points);return <label className="check" key={pt}><input type="checkbox" checked={a.includes(pt)} onChange={()=>{const next=a.includes(pt)?a.filter(x=>x!==pt):[...a,pt].sort((x,y)=>x-y);updateCondition(i,"points",next.join(","))}}/><span>P{pt}</span></label>})}</div></div>{conditions.length>1&&<button className="iconBtn" onClick={()=>removeCondition(i)}><Trash2 size={14}/></button>}</div>)}<div className="mappingSummary"><span>Pages / Point <input className="smallInput" type="number" min="1" value={pagesPerPoint} onChange={e=>setPagesPerPoint(Math.max(1,Number(e.target.value)||1))}/></span><b>Total Points: {expectedPoints()}</b><span>Total Pages: {expectedPoints()*pagesPerPoint}</span></div></section><div className="actions"><button className="primary" disabled={busy} onClick={upload}><Play size={14}/>{busy?"Uploading / Analyzing...":"Upload + Analyze"}</button></div></div>}
function displayScore(p){
 const f=p?.features||{};
 const raw=typeof f.residue_score==="number"?f.residue_score:(typeof p?.residue_score==="number"?p.residue_score:null);
 if(raw==null)return null;
 // v19: new backend stores the calibrated score; legacy records are shifted -15 points
 // so the previous 85-point level becomes the new 70-point level.
 if(f.score_calibrated===true || f.score_calibration_method) return Math.max(0,Math.min(100,Math.round(raw*100)));
 // Legacy records only: preserve the old display until they are re-analyzed.
 return Math.max(0,Math.min(100,Math.round(raw*100-15)));
}
function scoreClass(score){return score==null?"unknown":score>=70?"high":score>=60?"review":"low"}

function MetricBar({label,value,color}){
 const pct=typeof value==="number"?Math.max(0,Math.min(100,metricValue(value))):0;
 return <div className="barMetric"><div><span>{label}</span><b>{typeof value==="number"?metricValue(value):"-"}</b></div><i className={color}><em style={{width:pct+"%"}} /></i></div>;
}
function metricValue(v){return Math.round(v*100)}
function dynamicAsset(p,type){return p?.id?`/api/assets/${encodeURIComponent(p.id)}/${type}`:null}
function Review({p,idx,total,prev,next,human}){
 const f=p.features||{};
 const score=displayScore(p);
 const coverage=typeof f.candidate_coverage==='number'?Math.round(f.candidate_coverage):null;
 const maskQuality=typeof f.roi_quality==='number'?f.roi_quality:null;
 const autoResult=f.result||"Ambiguous";
 const resultLabel=autoResult==="Review"?"Ambiguous":autoResult;
 const confidence=f.confidence||"-";
 const maps={
   sem:[dynamicAsset(p,"sem_residue_overlay"),p.assets?.sem_residue_overlay,p.assets?.sem],
   eds:[dynamicAsset(p,"eds_map"),p.assets?.eds_map,p.assets?.full_element_maps_original],
   c:[dynamicAsset(p,"c_map_enhanced_overlay"),p.assets?.c_map_enhanced_overlay,p.assets?.c_map_roi_ring,p.assets?.c_map],
   o:[dynamicAsset(p,"o_map_enhanced_overlay"),p.assets?.o_map_enhanced_overlay,p.assets?.o_map_roi_ring,p.assets?.o_map]
 };
 const ratio=(roi,global)=>typeof roi==='number'&&typeof global==='number'&&global!==0?roi/global:null;
 const cRatio=ratio(f.c_roi_mean,f.c_global_mean), oRatio=ratio(f.o_roi_mean,f.o_global_mean);
 const fmtRatio=(x)=>x==null?"-":`${x.toFixed(2)}×`;
 const ratioClass=(x)=>x==null?"neutral":x>=1.50?"positive":x<=0.90?"negative":"neutral";
 const ratioText=(x)=>x==null?"-":fmtRatio(x);
 const metric=(v)=>typeof v==='number'?Math.round(v*100):null;
 return <div className="verificationPanel">
  <div className="verificationTop v21Top">
   <div><b>Point {idx+1} / {total}</b><span>{p.power} · {p.time} · W{p.wafer} · P{p.point}</span></div>
   <div className="verificationTopActions"><span className={`autoBadge ${resultLabel.toLowerCase().replace(/[^a-z]+/g,"-")}`}>{resultLabel} (Auto)</span><button className="iconBtn" onClick={prev}><ChevronLeft size={16}/></button><button className="iconBtn" onClick={next}><ChevronRight size={16}/></button></div>
  </div>
  <div className="verificationLayout">
   <div className="verificationVisualColumn">
    <div className="verificationImages"><Visual title="SEM / Residue ROI" sources={maps.sem}/><Visual title="Full EDS Map" sources={maps.eds}/></div>
    <div className="v21ElementStrip">
      <div className="focusPanel"><b>C Map (ROI)</b><ImageWithFallback sources={maps.c} alt="C map with ROI"/></div>
      <div className="focusPanel"><b>O Map (ROI)</b><ImageWithFallback sources={maps.o} alt="O map with ROI"/></div>
    </div>
   </div>
   <section className="analysisResultCard" aria-label="Analysis Result">
    <div className="analysisResultHead"><small>ANALYSIS RESULT</small><strong className={`resultTitle ${resultLabel.toLowerCase().replace(/[^a-z]+/g,"-")}`}>{resultLabel}</strong></div>
    <div className={`bigScore ${scoreClass(score)}`}>{score==null?"—":score}<span>/ 100</span></div>
    <div className="confidenceText">{confidence} confidence</div>
    <div className="metricBars">
      <MetricBar label="SEM Morphology" value={f.morphology_score} color="blue" />
      <MetricBar label="C Score" value={f.c_score} color="red" />
      <MetricBar label="O Score" value={f.o_score} color="green" />
      <MetricBar label="Spatial Overlap" value={typeof f.spatial_overlap === "number" ? f.spatial_overlap / 100 : null} color="purple" />
    </div>
    <div className="ratioList">
      <div><span>C ROI / Global</span><b>{typeof f.c_roi_mean==='number'&&typeof f.c_global_mean==='number'?`${f.c_roi_mean.toFixed(1)} / ${f.c_global_mean.toFixed(1)} `:''}<span className={`ratioMultiplier ${ratioClass(cRatio)}`} style={{color:cRatio==null?'#667386':cRatio>=1.50?'#159447':cRatio<=0.90?'#d12f3d':'#667386'}}>{`(${ratioText(cRatio)})`}</span></b></div>
      <div><span>O ROI / Global</span><b>{typeof f.o_roi_mean==='number'&&typeof f.o_global_mean==='number'?`${f.o_roi_mean.toFixed(1)} / ${f.o_global_mean.toFixed(1)} `:''}<span className={`ratioMultiplier ${ratioClass(oRatio)}`} style={{color:oRatio==null?'#667386':oRatio>=1.50?'#159447':oRatio<=0.90?'#d12f3d':'#667386'}}>{`(${ratioText(oRatio)})`}</span></b></div>
    </div>
    <div className="ratioRuleNote"><b>C + O 동시 증가 기준</b><span>둘 다 <strong>1.50× 이상</strong>이어야 Residue 후보입니다. 1.20× 미만은 배경 수준으로 취급합니다.</span></div>
    <div className="roiInfo"><b>ROI Information</b><div><span>ROI Area</span><strong>{f.roi_area_px?`${f.roi_area_px.toLocaleString()} px²`:'-'}</strong></div><div><span>ROI Coverage</span><strong className={coverage!=null&&coverage>=80?'good':''}>{coverage==null?'-':`${coverage}%${coverage>=80?'  (Good)':''}`}</strong></div><div><span>Mask Quality</span><strong>{maskQuality==null?'-':maskQuality.toFixed(2)}</strong></div><div><span>Detected as</span><strong>{f.roi_component_count!=null?`${f.roi_component_count} connected region${f.roi_component_count===1?'':'s'}`:'-'}</strong></div></div>
    <div className="scoreRule v21Rule"><b>RESIDUE ≥ 70</b><span>AMBIGUOUS 60–69 · NON-RESIDUE &lt; 60</span><small>0–100 calibrated score. Low ROI quality cannot force an automatic Residue decision.</small></div>
   </section>
  </div>
  <div className="verificationInfo v21Info"><b>ROI 표시</b><span>SEM에서 검출된 실제 residue mask를 기준으로 C/O EDS 영역에 동일한 contour를 표시합니다. N, Si는 Verification에서 표시하지 않습니다. 새 Analysis / Re-analysis에서는 Local Ring을 생성하거나 판정 기준으로 사용하지 않습니다.</span></div>
  <div className="verificationButtons"><button className="danger" onClick={()=>human("Non-residue")}>NON-RESIDUE (N)</button><button className="approve" onClick={()=>human("Residue")}>RESIDUE (R)</button><button className="secondary" onClick={()=>human("Skip")}><SkipForward size={14}/> SKIP (S)</button></div>
  <div className="verificationNav"><button className="secondary" onClick={prev}><ChevronLeft size={14}/> Previous</button><button className="secondary" onClick={next}>Next <ChevronRight size={14}/></button></div>
 </div>
}
function VerificationModal({p,idx,total,close,human,prev,next}){return <div className="verificationOverlay" onClick={close}><div className="verificationModal" onClick={e=>e.stopPropagation()}><div className="verificationModalHead"><b>Verification</b><button className="secondary" onClick={close}>Close</button></div><Review p={p} idx={idx} total={total} prev={prev} next={next} human={human}/></div></div>}

function ImageWithFallback({sources,alt,className="",...props}){
 const list=[...new Set((Array.isArray(sources)?sources:[sources]).filter(Boolean))];
 const [index,setIndex]=useState(0);
 useEffect(()=>{setIndex(0)},[list.join("|")]);
 const src=list[index]?imageUrl(list[index]):null;
 if(!src)return <div className="imageError">No image</div>;
 return <img {...props} className={className} src={src} alt={alt} onError={()=>setIndex(i=>i+1)}/>;
}
function Visual({title,sources}){
 const list=Array.isArray(sources)?sources:[sources];
 return <div className="visual panel"><div className="panelHead"><b>{title}</b><small>analysis image</small></div><ImageWithFallback sources={list} alt={title}/></div>;
}
function Gallery({points,q,setQ,result,setResult}){
 const [selected,setSelected]=useState(null);
 return <div className="content"><div className="intro"><div><h2>Image Gallery</h2><p>SEM / Element Map 시각화본을 Point별로 비교합니다. 카드를 눌러도 검증 화면으로 이동하지 않습니다.</p></div></div><section className="panel"><div className="filters"><div className="search"><Search size={14}/><input value={q} onChange={e=>setQ(e.target.value)} placeholder="Power / Wafer / Point / Position 검색"/></div>{["All","Residue","Ambiguous","Non-residue"].map(x=><button className={result===x?"sel":"secondary"} onClick={()=>setResult(x)} key={x}>{x}</button>)}</div></section><div className="gallery">{points.slice(0,120).map(p=><div className="tile" onClick={()=>setSelected(p)} key={p.id}><div className="tileImg"><ImageWithFallback sources={[p.assets?.sem_residue_overlay,p.assets?.sem]} alt="SEM"/><em>{p.human_result||p.features?.result||"Ambiguous"}</em></div><div className="tileBody"><b>{p.power} · {p.time}</b><span>W{p.wafer} · P{p.point} · {p.zone}</span></div></div>)}</div>{selected&&<ImageLightbox p={selected} close={()=>setSelected(null)}/>}</div>
}
function imageUrl(src){
 if(!src)return null;
 if(src.startsWith("data:")||src.startsWith("http://")||src.startsWith("https://"))return src;
 if(src.startsWith("/"))return `${API}${src}`;
 return `${API}/api/assets/${encodeURIComponent(src)}`;
}
function ImageLightbox({p,close}){
 const f=p.features||{};
 const scoreValue=displayScore(p);
 const sem=[dynamicAsset(p,"sem_residue_overlay"),p.assets?.sem_residue_overlay,p.assets?.sem];
 const eds=[dynamicAsset(p,"eds_map"),p.assets?.eds_map,p.assets?.full_element_maps_original];
 const maps=[["SE",[p.assets?.se_map_roi_ring,p.assets?.se_map]],["C",[dynamicAsset(p,"c_map_enhanced_overlay"),p.assets?.c_map_enhanced_overlay,p.assets?.c_map]],["O",[dynamicAsset(p,"o_map_enhanced_overlay"),p.assets?.o_map_enhanced_overlay,p.assets?.o_map]]];
 return <div className="lightbox" onClick={close}><div className="lightboxCard" onClick={e=>e.stopPropagation()}><div className="lightboxHead"><div><b>{p.power} · {p.time}</b><span>W{p.wafer} · P{p.point} · {p.zone}</span></div><button className="secondary" onClick={close}>Close</button></div><div className="lightboxGrid"><div><small>SEM / Residue ROI</small><ImageWithFallback sources={sem} alt="SEM overlay"/></div><div><small>Full EDS Map</small><ImageWithFallback sources={eds} alt="Full EDS Map"/></div></div><div className="elementDetailGrid">{maps.map(([label,sources])=><div key={label}><small>{label} / ROI</small><ImageWithFallback sources={sources} alt={`${label} map`}/></div>)}</div><div className="lightboxResult"><b>{p.human_result||p.features?.result||"Ambiguous"}</b><span>Score {typeof scoreValue==="number"?scoreValue:"-"} / 100 · {p.confidence||f.confidence||"-"}</span></div></div></div>
}
function AIAnalysis({points,analysis,busy,run}){const residue=points.filter(p=>(p.human_result||p.features?.result)==="Residue").length;const non=points.filter(p=>(p.human_result||p.features?.result)==="Non-residue").length;return <div className="content"><div className="intro"><div><label>RESEARCH INTERPRETATION</label><h2>AI Analysis</h2><p>OpenAI는 개별 Point의 분류기가 아니라, 이미 계산된 SEM/EDS 분석 결과를 연구 관점에서 해석합니다.</p></div><button className="primary" disabled={busy} onClick={run}><BrainCircuit size={14}/>{busy?"Analyzing...":"Run OpenAI Analysis"}</button></div><div className="metrics"><Metric t="Points" v={points.length} s="current dataset"/><Metric t="Residue" v={residue} s="classified"/><Metric t="Non-residue" v={non} s="classified"/><Metric t="Review" v={Math.max(0,points.length-residue-non)} s="ambiguous / needs review"/></div>{analysis?<div className="aiAnalysisGrid"><section className="panel"><div className="panelHead"><b>Summary</b><small>OpenAI research interpretation</small></div><div className="aiBody"><p>{analysis.summary}</p><h4>Key findings</h4><ul>{(analysis.key_findings||[]).map((x,i)=><li key={i}>{x}</li>)}</ul></div></section><section className="panel"><div className="panelHead"><b>Condition trends</b><small>Based on current dataset</small></div><div className="aiBody"><ul>{(analysis.condition_trends||[]).map((x,i)=><li key={i}>{x}</li>)}</ul><h4>Anomalies / points to inspect</h4><ul>{(analysis.anomalies||[]).map((x,i)=><li key={i}>{x}</li>)}</ul></div></section><section className="panel"><div className="panelHead"><b>Research next steps</b><small>Suggestions, not automated decisions</small></div><div className="aiBody"><ul>{(analysis.next_steps||[]).map((x,i)=><li key={i}>{x}</li>)}</ul><h4>Caveats</h4><ul>{(analysis.caveats||[]).map((x,i)=><li key={i}>{x}</li>)}</ul></div></section></div>:<section className="panel emptyPanel"><BrainCircuit size={26}/><b>AI Analysis를 실행하세요</b><small>Residue / Non-residue 분류 자체는 CV + Human Review 결과를 사용하고, OpenAI는 조건별 경향과 이상점, 연구 해석에 사용합니다.</small><button className="primary" disabled={busy} onClick={run}><BrainCircuit size={14}/> Run OpenAI Analysis</button></section>}</div>}

function Compare({points}){let g={};points.forEach(p=>{let k=`${p.power} / ${p.time}`;(g[k]??=[]).push(p)});return <div className="content"><div className="intro"><div><h2>Condition Comparison</h2><p>조건별 결과 분포.</p></div></div><section className="panel"><div className="table">{Object.entries(g).map(([k,a])=>{let r=a.filter(p=>(p.human_result||p.features?.result)==="Residue").length;return <div className="tr" key={k}><b>{k}</b><span>{a.length}</span><strong>{a.length?Math.round(r/a.length*100):0}%</strong><span>Avg score {a.length?(a.reduce((s,p)=>s+(displayScore(p)||0),0)/a.length).toFixed(1):"-"}</span></div>})}</div></section></div>}
function Reports({exportFile,project}){return <div className="content"><div className="intro"><div><h2>Reports & Export</h2><p>1 Point = 1 Page / Slide.</p></div></div><div className="reportGrid"><Report t="Point PPT" d="SEM / Spectrum / EDS Map / Element Maps / Data / Result" a={()=>exportFile("ppt")} disabled={!project}/><Report t="Point PDF" d="동일 레이아웃으로 1 Point = 1 Page" a={()=>exportFile("pdf")} disabled={!project}/><Report t="Analysis JSON" d="전체 분석 데이터 export" a={()=>exportFile("json")} disabled={!project}/></div></div>}
function Report({t,d,a,disabled}){return <div className="reportCard"><FileText size={20}/><b>{t}</b><p>{d}</p><button className="secondary" disabled={disabled} onClick={a}><Download size={14}/> Export</button></div>}
