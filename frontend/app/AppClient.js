"use client";
import {useEffect,useRef,useState} from "react";
import {Upload,LayoutDashboard,BrainCircuit,Images,GitCompare,FileText,Settings,FileUp,Play,Check,Download,ChevronLeft,ChevronRight,SkipForward,Search,Database,Activity,Plus,Trash2,MousePointer2,RotateCcw} from "lucide-react";

const API=process.env.NEXT_PUBLIC_API_BASE_URL||"https://uv-tape-residue-eds-backend.onrender.com";
const DEFAULT_CONDITION={power:"150",time:"30",wafers:"1,4,5",points:"1-9",file:null};

function parseSelection(value){
 const out=[];
 String(value||"").split(",").forEach(part=>{
   const t=part.trim(); if(!t)return;
   if(t.includes("-")){const [a,b]=t.split("-").map(Number);if(Number.isFinite(a)&&Number.isFinite(b)){for(let n=Math.min(a,b);n<=Math.max(a,b);n++)out.push(n)}} else {const n=Number(t);if(Number.isFinite(n))out.push(n)}
 });
 return [...new Set(out)].sort((a,b)=>a-b);
}

export default function App(){
 const [mounted,setMounted]=useState(false),[initialLoading,setInitialLoading]=useState(true),[page,setPage]=useState("Dashboard"),[points,setPoints]=useState([]),[project,setProject]=useState(null),[projectList,setProjectList]=useState([]),[idx,setIdx]=useState(0),[msg,setMsg]=useState(""),[aiAnalysis,setAiAnalysis]=useState(null),[aiBusy,setAiBusy]=useState(false),conditionInputs=useRef({});
 const [q,setQ]=useState(""),[result,setResult]=useState("All"),[conditionView,setConditionView]=useState("ALL"),[compareConditions,setCompareConditions]=useState([]),[conditions,setConditions]=useState([{...DEFAULT_CONDITION}]),[substrateType,setSubstrateType]=useState("SiCN"),[positionSubstrates,setPositionSubstrates]=useState(()=>Object.fromEntries(Array.from({length:9},(_,i)=>[String(i+1),"SiCN"]))),[sampleCategory,setSampleCategory]=useState("MAIN"),[pagesPerPoint,setPagesPerPoint]=useState(3),[draggingCondition,setDraggingCondition]=useState(null),[busy,setBusy]=useState(false),[progress,setProgress]=useState(0),[progressPhase,setProgressPhase]=useState("idle"),[progressMessage,setProgressMessage]=useState(""),[verificationOpen,setVerificationOpen]=useState(false),[verificationCondition,setVerificationCondition]=useState("ALL"),[verificationWafer,setVerificationWafer]=useState("ALL"),[verificationResult,setVerificationResult]=useState("ALL"),[analysisQueue,setAnalysisQueue]=useState([]);
 const notify=x=>{setMsg(x);setTimeout(()=>setMsg(""),3200)};
 async function loadProjectList(){
   try{
     const r=await fetch(`${API}/api/projects`,{cache:"no-store"});
     if(!r.ok)throw new Error(`HTTP ${r.status}`);
     const j=await r.json();
     const list=Array.isArray(j.projects)?j.projects:[];
     setProjectList(list);
     return list;
   }catch(e){return []}
 }
 async function loadData(preferredId=null){
   setInitialLoading(true);
   const savedId=preferredId||(typeof window!=="undefined"?localStorage.getItem("uvtape:selectedProject"):null);
   let lastError=null;
   try{
     // Render can take a little time to wake from sleep. Retry before showing
     // an error so a transient cold-start does not look like lost project data.
     for(let attempt=0;attempt<4;attempt++){
       try{
         // v23.7.29: the user-facing dataset is cumulative across uploads.
         // Project IDs remain internal for queue/re-analysis/delete bookkeeping.
         const target=`${API}/api/workspace`;
         let r=await fetch(target,{cache:"no-store"});
         if(!r.ok) throw new Error(`HTTP ${r.status}`);
         const j=await r.json();
         if(j?.project_id || j?.id){
           const pid=j.latest_project_id||j.project_id||j.id;
           const arr=(j.points||[]).map(x=>({...x,human_result:x.human_result||null}));
           if(j.position_substrates)setPositionSubstrates(j.position_substrates);
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
 useEffect(()=>{setMounted(true);(async()=>{const list=await loadProjectList();await loadData(typeof window!=="undefined"?localStorage.getItem("uvtape:selectedProject"):null);if(!list.length)await loadProjectList()})()},[]);
 useEffect(()=>{
   try{const raw=localStorage.getItem("uvtape:analysisQueue");if(raw)setAnalysisQueue(JSON.parse(raw)||[])}catch{}
 },[]);
 useEffect(()=>{try{localStorage.setItem("uvtape:analysisQueue",JSON.stringify(analysisQueue))}catch{}},[analysisQueue]);
 useEffect(()=>{
   if(!analysisQueue.length)return;
   let stopped=false;
   const tick=async()=>{
     const next=[];
     for(const item of analysisQueue){
       try{
         const r=await fetch(`${API}/api/jobs/${item.job_id}`,{cache:"no-store"});
         if(r.ok){const st=await r.json(); next.push({...item,status:st.status||item.status,progress:st.progress||0,message:st.message||item.message||"" ,completed:st.completed||0,total:st.total||item.total||0}); continue;}
       }catch{}
       try{
         const pr=await fetch(`${API}/api/projects/${item.project_id}`,{cache:"no-store"});
         if(pr.ok){next.push({...item,status:"completed",progress:100,message:"분석 완료",completed:item.total||0,total:item.total||0});continue;}
       }catch{}
       next.push(item);
     }
     const hadCompleted=next.some((x,i)=>x.status==="completed" && analysisQueue[i]?.status!=="completed");
     if(!stopped)setAnalysisQueue(next);
     if(!stopped){
       await loadProjectList();
       if(hadCompleted) await loadData();
     }
   };
   tick(); const timer=setInterval(tick,2500); return()=>{stopped=true;clearInterval(timer)};
 },[analysisQueue.length]);
 useEffect(()=>{const h=e=>{if(typeof e.detail?.index!=="number")return;setIdx(e.detail.index);setVerificationOpen(true)};window.addEventListener("uvtape:open-point",h);return()=>window.removeEventListener("uvtape:open-point",h)},[]);
function nextUnverified(start=0){
 for(let i=Math.max(0,start);i<points.length;i++) if(!points[i].human_result) return i;
 return -1;
}
function updateCondition(i,key,value){setConditions(cs=>cs.map((c,n)=>n===i?{...c,[key]:value}:c));}
 function setConditionFile(i,file){if(!file)return;if(!file.name.toLowerCase().endsWith(".pdf"))return notify("PDF 파일만 추가할 수 있습니다.");setConditions(cs=>cs.map((c,n)=>n===i?{...c,file}:c));}
 function addCondition(){setConditions(cs=>[...cs,{...DEFAULT_CONDITION,file:null}]);}
 function removeCondition(i){setConditions(cs=>cs.length===1?cs:cs.filter((_,n)=>n!==i));}
 function expectedPointsForCondition(c){const wafers=parseSelection(c.wafers),pts=parseSelection(c.points);return new Set(wafers).size*new Set(pts).size;}
 function expectedPoints(){return conditions.reduce((sum,c)=>sum+expectedPointsForCondition(c),0)}
 async function upload(){
   const missing=conditions.findIndex(c=>!c.file);
   if(missing>=0)return notify(`Condition ${missing+1}에 PDF를 먼저 추가하세요.`);
   const invalid=conditions.findIndex(c=>!expectedPointsForCondition(c));
   if(invalid>=0)return notify(`Condition ${invalid+1}의 Power / Time / Wafer / Point 조건을 확인하세요.`);
   const batch=[...conditions];
   setBusy(true);setProgressPhase("queued");setProgress(0);setProgressMessage(`${batch.length}개 조건을 순차적으로 분석 대기열에 등록합니다.`);
   let added=0,failed=0;
   try{
     for(let i=0;i<batch.length;i++){
       const itemConfig=batch[i],file=itemConfig.file,n=expectedPointsForCondition(itemConfig);
       setProgressPhase("upload");setProgress(0);setProgressMessage(`분석 대기열 등록 중 · ${i+1}/${batch.length} · ${file.name}`);
       const fd=new FormData();fd.append("files",file);fd.append("conditions_json",JSON.stringify([{power:itemConfig.power,time:itemConfig.time,wafers:itemConfig.wafers,points:itemConfig.points}]));fd.append("pages_per_point",String(pagesPerPoint));fd.append("substrate_type",substrateType);fd.append("position_substrates_json",JSON.stringify(positionSubstrates));fd.append("sample_category",sampleCategory);
       try{
         const j=await new Promise((resolve,reject)=>{
           const xhr=new XMLHttpRequest();xhr.open("POST",`${API}/api/upload`);
           xhr.upload.onprogress=e=>{if(e.lengthComputable){const pct=Math.round((e.loaded/e.total)*100);setProgress(pct);setProgressMessage(`업로드 중 · ${i+1}/${batch.length} · ${file.name} · ${pct}%`);}};
           xhr.onerror=()=>reject(new Error("서버 연결에 실패했습니다."));xhr.ontimeout=()=>reject(new Error("업로드 시간이 초과되었습니다."));
           xhr.onload=()=>{let body=xhr.responseText,data={};try{data=JSON.parse(body)}catch{}if(xhr.status<200||xhr.status>=300){reject(new Error(`HTTP ${xhr.status}: ${data.detail||body||"server error"}`));return}resolve(data)};xhr.send(fd);
         });
         const queueItem={job_id:j.job_id,project_id:j.project_id,files:j.files||[file.name],total:j.expected_points||n,status:"queued",progress:5,message:"분석 대기 중"};
         setAnalysisQueue(q=>[...q.filter(x=>x.job_id!==queueItem.job_id),queueItem]);added++;
       }catch(e){failed++;setAnalysisQueue(q=>[...q,{job_id:`local-failed-${Date.now()}-${i}`,project_id:null,files:[file.name],total:n,status:"failed",progress:0,message:`등록 실패: ${e?.message||"server error"}`}]);notify(`${file.name} 등록 실패 · 다음 조건을 계속 등록합니다.`);}
     }
     setProgressPhase("queued");setProgress(100);setProgressMessage(`${added}개 PDF가 순차 분석 대기열에 등록되었습니다.${failed?` ${failed}개는 등록 실패했습니다.`:""}`);await loadProjectList();notify(`${added}개 PDF를 순차 분석 대기열에 추가했습니다.${failed?` (${failed}개 실패)":""}`);
     if(added>0)setConditions([{...DEFAULT_CONDITION,file:null}]);
   }finally{setBusy(false);setTimeout(()=>{setProgressPhase("idle");setProgressMessage("");setProgress(0)},900);}
 }
 async function deleteProjectById(projectId,meta){
   if(!projectId)return;
   const label=meta?`${meta.point_count||0} points · ${meta.files?.join(", ")||"project"}`:"선택한 project";
   if(!window.confirm(`정말 이 데이터를 삭제할까요?\n\n${label}\n\nProject의 Point, 분석 결과, Human ROI, 이미지/R2 원본까지 삭제됩니다. 이 작업은 되돌릴 수 없습니다.`))return;
   try{
     setBusy(true);
     const r=await fetch(`${API}/api/projects/${projectId}`,{method:"DELETE"});
     const j=await r.json(); if(!r.ok)throw new Error(j.detail||"데이터 삭제 실패");
     setAnalysisQueue(q=>q.filter(x=>x.project_id!==projectId));
     const deletingCurrent=projectId===project;
     if(deletingCurrent){
       localStorage.removeItem("uvtape:selectedProject");
       setProject(null);setPoints([]);setVerificationOpen(false);
     }
     const list=await loadProjectList();
     if(deletingCurrent){
       const next=list.find(x=>x.status!=="Deleted");
       if(next){await loadData(next.id)}
     }
     notify("선택한 데이터와 관련 원본/분석 파일이 삭제되었습니다.");
   }catch(e){notify(e.message||"데이터 삭제 실패")}finally{setBusy(false)}
 }
 async function deleteCurrentProject(){
   if(!project)return;
   const meta=projectList.find(x=>x.id===project);
   await deleteProjectById(project,meta);
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
 async function saveHumanRoi(polygon){
   const p=points[idx]; if(!p)return;
   try{
     const r=await fetch(`${API}/api/points/${p.id}/human-roi`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({polygons:polygon})});
     const j=await r.json(); if(!r.ok)throw new Error(j.detail||"ROI 저장 실패");
     setPoints(points.map(z=>z.id===p.id?j:z));
     notify("Human ROI 저장 완료 · C/O 재계산 및 Ground Truth 데이터 저장");
     return j;
   }catch(e){notify(e.message||"ROI 저장 실패");throw e;}
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
 return <div className="app"><aside><div className="logo"><b>EDS</b><span>Insight Lab</span></div>{[["Dashboard",LayoutDashboard],["New Analysis",Upload],["Verification",Check],["AI Analysis",BrainCircuit],["Image Gallery",Images],["Condition View",Activity],["Condition Compare",GitCompare],["Reports",FileText],["Delete Data",Trash2]].map(([n,I])=><button className={page===n?"nav active":"nav"} key={n} onClick={()=>goNav(n)}><I size={16}/>{n}</button>)}<div className="sideBottom"><button className="nav"><Settings size={16}/>Settings</button></div></aside><main><header><div><small>Projects / UV Tape Residue / {page}</small><h1>{page}</h1></div><div className="headerActions">{project&&<button className="secondary reanalyzeBtn" onClick={reanalyzeProject} disabled={busy}><Database size={13}/>{busy?"Working...":"Re-analyze"}</button>}<span className="ready">● {points.length?"Data loaded":"Ready"}</span></div></header>
 {page==="Dashboard"&&<Dashboard points={points} go={setPage}/>}
 {page==="New Analysis"&&<UploadPage conditionInputs={conditionInputs} setConditionFile={setConditionFile} upload={upload} conditions={conditions} updateCondition={updateCondition} addCondition={addCondition} removeCondition={removeCondition} pagesPerPoint={pagesPerPoint} setPagesPerPoint={setPagesPerPoint} draggingCondition={draggingCondition} setDraggingCondition={setDraggingCondition} expectedPointsForCondition={expectedPointsForCondition} expectedPoints={expectedPoints} busy={busy} analysisQueue={analysisQueue} substrateType={substrateType} setSubstrateType={setSubstrateType} sampleCategory={sampleCategory} setSampleCategory={setSampleCategory} positionSubstrates={positionSubstrates} setPositionSubstrates={setPositionSubstrates}/>} 
 {page==="Verification"&&(p?<Review p={p} idx={idx} total={points.length} prev={()=>setIdx(Math.max(0,idx-1))} next={()=>setIdx(Math.min(points.length-1,idx+1))} human={human} saveHumanRoi={saveHumanRoi}/>:<Empty title="분석 데이터가 없습니다" text="EDS PDF를 업로드하면 Point별 분석 결과가 이 화면에 표시됩니다." go={()=>setPage("New Analysis")}/>)}
 {page==="AI Analysis"&&<AIAnalysis points={points} analysis={aiAnalysis} busy={aiBusy} run={runProjectAI}/>}
 {page==="Image Gallery"&&<Gallery points={filtered} q={q} setQ={setQ} result={result} setResult={setResult}/>}
 {page==="Condition View"&&<ConditionView points={points} selected={conditionView} setSelected={setConditionView} go={setPage}/>}
 {page==="Condition Compare"&&<Compare points={points} selected={compareConditions} setSelected={setCompareConditions}/>} {page==="Reports"&&<Reports exportFile={exportFile} project={project}/>} {page==="Delete Data"&&<DeleteData projectList={projectList} currentProject={project} deleteProject={deleteProjectById} busy={busy}/>} </main>{verificationOpen&&p&&<VerificationModal p={p} idx={idx} total={points.length} points={points} close={()=>setVerificationOpen(false)} setIdx={setIdx} condition={verificationCondition} setCondition={setVerificationCondition} wafer={verificationWafer} setWafer={setVerificationWafer} resultFilter={verificationResult} setResultFilter={setVerificationResult} human={human} saveHumanRoi={saveHumanRoi}/>} {msg&&<div className="toast"><Check size={14}/>{msg}</div>}</div>
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
   <div className="dashboardMetrics"><Metric t="Points" v={points.length} s="cumulative workspace"/><Metric t="Residue" v={residue} s={points.length?`${Math.round(residue/points.length*100)}%`:"—"}/><Metric t="Non-residue" v={non} s={points.length?`${Math.round(non/points.length*100)}%`:"—"}/><Metric t="Ambiguous" v={review} s="needs review"/><Metric t="Verified" v={verified} s="human reviewed"/><Metric t="Conditions" v={Object.keys(groups).length} s="Power / Time"/></div>
   {points.length>0&&<><div className="dashboardTwoCol"><section className="panel"><div className="panelHead"><b>Result Distribution</b><small>Cumulative workspace</small></div><div className="distribution">{[["Residue",residue], ["Non-residue",non], ["Ambiguous",review]].map(([label,n])=><div className="distRow" key={label}><span>{label}</span><div className="distTrack"><i style={{width:`${Math.round(n/points.length*100)}%`}}/></div><strong>{n} · {Math.round(n/points.length*100)}%</strong></div>)}</div></section><section className="panel"><div className="panelHead"><b>Zone Summary</b><small>Center / Edge / Diamond</small></div><div className="dashTable"><div className="dashTr dashTh"><span>Zone</span><span>Points</span><span>Residue</span><span>Non</span><span>Ambiguous</span><span>Residue %</span></div>{["Center","Edge","Diamond","Unknown"].map(z=>{const a=points.filter(p=>(p.zone||"Unknown")===z);if(!a.length)return null;const rr=a.filter(p=>cls(p)==="Residue").length;const nn=a.filter(p=>cls(p)==="Non-residue").length;const aa=a.filter(p=>cls(p)==="Ambiguous"||cls(p)==="Review").length;return <div className="dashTr" key={z}><span>{z}</span><span>{a.length}</span><span>{rr}</span><span>{nn}</span><span>{aa}</span><strong>{Math.round(rr/a.length*100)}%</strong></div>})}</div></section></div>
   <section className="panel conditionSection"><div className="panelHead"><b>Power / Time Summary</b><small>All stored point results</small></div><div className="dashTable"><div className="dashTr dashTh"><span>Condition</span><span>Points</span><span>Residue</span><span>Non</span><span>Ambiguous</span><span>Residue %</span></div>{Object.entries(groups).map(([k,a])=>{const rr=a.filter(p=>cls(p)==="Residue").length;const nn=a.filter(p=>cls(p)==="Non-residue").length;const aa=a.filter(p=>cls(p)==="Ambiguous"||cls(p)==="Review").length;return <div className="dashTr" key={k}><span>{k}</span><span>{a.length}</span><span>{rr}</span><span>{nn}</span><span>{aa}</span><strong>{Math.round(rr/a.length*100)}%</strong></div>})}</div></section>
   <section className="panel representativePanel"><div className="panelHead"><b>Representative Results</b><small>One stored example per result state</small></div><div className="representativeGrid">{reps.map(([label,p,kind])=>p?<button className={`repCard ${kind}`} key={label} onClick={()=>open(p)}><div className="repImage"><ImageWithFallback sources={[p.assets?.sem_residue_overlay,p.assets?.sem]} alt={label}/></div><div className="repBody"><b>{p.power} · {p.time} · W{p.wafer} · P{p.point}</b><strong>{label}</strong><span>Score {displayScore(p)==null?"-":displayScore(p)} / 100 · {p.confidence||p.features?.confidence||"-"}</span></div></button>:<div className="repCard emptyRep" key={label}><strong>{label}</strong><span>현재 데이터에 해당 결과가 없습니다.</span></div>)}</div></section></>}
   {!points.length&&<section className="panel emptyPanel"><Database size={24}/><b>분석 데이터가 없습니다</b><small>EDS PDF를 업로드하면 조건과 Point 분석 결과가 이 대시보드에 표시됩니다.</small><button className="secondary" onClick={()=>go("New Analysis")}><Upload size={14}/> EDS 데이터 업로드</button></section>}
 </div>
}
function Metric({t,v,s}){return <div className="metric"><small>{t}</small><b>{v}</b><span>{s}</span></div>}
function UploadPage({conditionInputs,setConditionFile,upload,conditions,updateCondition,addCondition,removeCondition,pagesPerPoint,setPagesPerPoint,draggingCondition,setDraggingCondition,expectedPointsForCondition,expectedPoints,busy,analysisQueue,substrateType,setSubstrateType,sampleCategory,setSampleCategory,positionSubstrates,setPositionSubstrates}){
 return <div className="content">
  <div className="intro"><div><h2>New Analysis</h2><p>PDF와 분석 조건을 1:1로 등록한 뒤 <b>Analysis</b>를 누르면 등록된 순서대로 서버가 하나씩 분석합니다.</p></div><span>{conditions.length} conditions</span></div>
  <section className="panel conditionPanel"><div className="panelHead"><div className="conditionHead"><div><b>1. PDF / Analysis conditions</b><small>각 Condition에 PDF 1개와 해당 Power / Time / Wafer / Point를 지정하세요. Add condition으로 원하는 만큼 추가할 수 있습니다.</small></div><button className="secondary" type="button" onClick={addCondition}><Plus size={13}/> Add condition</button></div></div>
   {conditions.map((c,i)=><div className="conditionCard" key={i}>
    <div className="conditionCardTop"><div className="conditionTitle">Condition {i+1}</div>{conditions.length>1&&<button className="iconBtn" type="button" onClick={()=>removeCondition(i)} title="Remove condition"><Trash2 size={14}/></button>}</div>
    <div className={`conditionDrop ${draggingCondition===i?"dragging":""}`} onClick={()=>conditionInputs.current[i]?.click()} onDragOver={e=>{e.preventDefault();setDraggingCondition(i)}} onDragLeave={()=>setDraggingCondition(null)} onDrop={e=>{e.preventDefault();setDraggingCondition(null);setConditionFile(i,e.dataTransfer.files?.[0])}}>
      <input ref={el=>{conditionInputs.current[i]=el}} hidden type="file" accept=".pdf,application/pdf" onChange={e=>{setConditionFile(i,e.target.files?.[0]);e.target.value=""}}/>
      <FileUp size={24}/><b>{c.file?c.file.name:"EDS PDF를 선택하거나 여기에 끌어다 놓으세요"}</b><small>{c.file?`${(c.file.size/1024/1024).toFixed(1)} MB · 이 Condition에 연결됨`:"PDF 1개 = Condition 1개"}</small>
    </div>
    <div className="conditionFields"><label>Power (W)<input value={c.power} onChange={e=>updateCondition(i,"power",e.target.value)}/></label><label>Time (s)<input value={c.time} onChange={e=>updateCondition(i,"time",e.target.value)}/></label></div>
    <div className="selectionGroup"><span className="selectionLabel">Wafer</span><div className="checks">{Array.from({length:9},(_,n)=>n+1).map(w=>{const a=parseSelection(c.wafers);return <label className="check" key={w}><input type="checkbox" checked={a.includes(w)} onChange={()=>{const next=a.includes(w)?a.filter(x=>x!==w):[...a,w].sort((x,y)=>x-y);updateCondition(i,"wafers",next.join(","))}}/><span>W{w}</span></label>})}</div></div>
    <div className="selectionGroup"><span className="selectionLabel">Point</span><div className="checks">{Array.from({length:9},(_,n)=>n+1).map(pt=>{const a=parseSelection(c.points);return <label className="check" key={pt}><input type="checkbox" checked={a.includes(pt)} onChange={()=>{const next=a.includes(pt)?a.filter(x=>x!==pt):[...a,pt].sort((x,y)=>x-y);updateCondition(i,"points",next.join(","))}}/><span>P{pt}</span></label>})}</div></div>
    <div className="conditionSummary"><span>Pages / Point <input className="smallInput" type="number" min="1" value={pagesPerPoint} onChange={e=>setPagesPerPoint(Math.max(1,Number(e.target.value)||1))}/></span><b>{expectedPointsForCondition(c)} points</b><span>{expectedPointsForCondition(c)*pagesPerPoint} pages expected</span></div>
   </div>)}
   <div className="batchSummary"><b>Total: {conditions.length} PDFs · {expectedPoints()} points · {expectedPoints()*pagesPerPoint} pages expected</b><span>PDF가 없는 Condition은 Analysis를 시작할 수 없습니다.</span></div>
  </section>
  <section className="panel samplePanel"><div className="panelHead"><div><b>2. Sample / Substrate</b><small>전체 배치에 적용됩니다. 위치별 기판을 지정할 수 있습니다.</small></div></div><div className="sampleControls"><label>Category<select value={sampleCategory} onChange={e=>{const v=e.target.value;setSampleCategory(v);if(v==="MAIN")setSubstrateType("SiCN")}}><option value="MAIN">MAIN</option><option value="ANOTHER">ANOTHER</option></select></label><label>Default Substrate<select value={substrateType} onChange={e=>setSubstrateType(e.target.value)}><option>SiCN</option><option>Si</option><option>SiN</option><option>SiO2</option></select></label></div><div className="positionSubstrateGrid"><div className="positionSubstrateTitle">Position / Substrate</div>{Array.from({length:9},(_,i)=>i+1).map(pos=><label key={pos}>P{pos}<select value={positionSubstrates[String(pos)]||"SiCN"} onChange={e=>setPositionSubstrates(m=>({...m,[String(pos)]:e.target.value}))}><option>SiCN</option><option>Si</option><option>SiN</option><option>SiO2</option></select></label>)}</div></section>
  <div className="actions"><button className="primary analysisStartBtn" disabled={busy} onClick={upload}><Play size={14}/>{busy?"Analysis 준비 중...":`Analysis 시작 (${conditions.length}개 PDF)`}</button></div>
  <section className="panel queuePanel"><div className="panelHead"><div><b>Analysis Queue</b><small>Analysis를 누르면 등록된 Condition 순서대로 PDF가 서버 대기열에 들어갑니다. 한 PDF가 실패해도 다음 PDF는 계속 진행합니다.</small></div></div>{!analysisQueue?.length?<div className="queueEmpty">아직 시작한 분석 작업이 없습니다.</div>:<div className="analysisQueueList">{analysisQueue.map((q,i)=><div className="analysisQueueItem" key={q.job_id}><div><b>{q.files?.join(", ")||`Project ${i+1}`}</b><span>{q.completed||0}/{q.total||0} points · {q.status}{q.message?` · ${q.message}`:""}</span></div><strong>{Math.round(q.progress||0)}%</strong></div>)}</div>}</section>
 </div>
}
function coRatioDisplayScore(limiting){
 const x=Number(limiting);
 if(!Number.isFinite(x))return null;
 if(x<2)return Math.max(0,Math.min(59,Math.round((x/2)*59)));
 if(x<3)return Math.round(60+(x-2)*10);
 const k=0.23, denom=1-Math.exp(-k*17);
 const normalized=(1-Math.exp(-k*(x-3)))/denom;
 return Math.round(Math.min(100,70+30*Math.max(0,normalized)));
}
function verificationResult(x){
 const f=x?.features||{};
 if(x?.human_result)return x.human_result;
 if(f.human_roi_rule_result)return f.human_roi_rule_result;
 if(x?.result)return x.result;
 if(f.result)return f.result;
 return "Ambiguous";
}

function displayScore(p){
 const f=p?.features||{};
 const raw=typeof f.residue_score==="number"?f.residue_score:(typeof p?.residue_score==="number"?p.residue_score:null);
 if(typeof f.human_residue_score==='number' && ((Array.isArray(f.human_roi_polygons)&&f.human_roi_polygons.some(r=>Array.isArray(r)&&r.length>=3))||(Array.isArray(f.human_roi_polygon)&&f.human_roi_polygon.length>=3))) return Math.max(0,Math.min(100,Math.round(f.human_residue_score)));
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
function dynamicAsset(p,type){
 if(!p?.id)return null;
 const f=p.features||{};
 const humanSaved=(f.human_roi_saved_at||f.human_roi_polygons||f.human_roi_polygon);
 const needsHumanRefresh=humanSaved && ["sem_residue_overlay","c_map_enhanced_overlay","o_map_enhanced_overlay","eds_co_overlay"].includes(type);
 const v=needsHumanRefresh?encodeURIComponent(String(f.human_roi_saved_at||"human")):"";
 return `/api/assets/${encodeURIComponent(p.id)}/${type}${v?`?v=${v}`:""}`;
}
function Review({p,idx,total,prev,next,human,saveHumanRoi}){
 const f=p.features||{};
 const ratio=(roi,global)=>typeof roi==='number'&&typeof global==='number'&&global!==0?roi/global:null;
 const hasHumanROI=(Array.isArray(f.human_roi_polygons)&&f.human_roi_polygons.some(r=>Array.isArray(r)&&r.length>=3))||(Array.isArray(f.human_roi_polygon)&&f.human_roi_polygon.length>=3);
 const cRatio=hasHumanROI?f.human_c_ratio:ratio(f.c_roi_mean,f.c_global_mean), oRatio=hasHumanROI?f.human_o_ratio:ratio(f.o_roi_mean,f.o_global_mean);
 const score=hasHumanROI&&typeof cRatio==='number'&&typeof oRatio==='number'?coRatioDisplayScore(Math.min(cRatio,oRatio)):(hasHumanROI&&typeof f.human_residue_score==='number'?Math.round(f.human_residue_score):displayScore(p));
 const coverage=hasHumanROI&&typeof f.human_roi_area_px==='number'?Math.round((f.human_roi_area_px/Math.max(1,(f.human_roi_global_area_px||f.roi_area_px||1)))*100):typeof f.candidate_coverage==='number'?Math.round(f.candidate_coverage):null;
 const maskQuality=hasHumanROI&&typeof f.human_roi_quality==='number'?f.human_roi_quality:(typeof f.roi_quality==='number'?f.roi_quality:null);
 const autoResult=f.result||"Ambiguous";
 const resultLabel=hasHumanROI?(f.human_roi_rule_result||"Ambiguous"):(autoResult==="Review"?"Ambiguous":autoResult);
 const confidence=hasHumanROI?"Human ROI":(f.confidence||"-");
 const maps={
   // If Human ROI is already saved, never fall back to the old AI/CV overlay.
   // While the fresh Human ROI overlay is loading, show the original image/map
   // instead of briefly displaying the stale AI ROI.
   sem:hasHumanROI?[dynamicAsset(p,"sem_residue_overlay"),p.assets?.sem]:[dynamicAsset(p,"sem_residue_overlay"),p.assets?.sem_residue_overlay,p.assets?.sem],
   eds:[dynamicAsset(p,"eds_map"),p.assets?.eds_map,p.assets?.full_element_maps_original],
   c:hasHumanROI?[dynamicAsset(p,"c_map_enhanced_overlay"),p.assets?.c_map]:[dynamicAsset(p,"c_map_enhanced_overlay"),p.assets?.c_map_enhanced_overlay,p.assets?.c_map_roi_ring,p.assets?.c_map],
   o:hasHumanROI?[dynamicAsset(p,"o_map_enhanced_overlay"),p.assets?.o_map]:[dynamicAsset(p,"o_map_enhanced_overlay"),p.assets?.o_map_enhanced_overlay,p.assets?.o_map_roi_ring,p.assets?.o_map]
 };
 const fmtRatio=(x)=>x==null?"-":`${x.toFixed(2)}×`;
 const ratioClass=(x)=>x==null?"neutral":x>=3.00?"strong":x>=2.00?"positive":x<=0.90?"negative":"neutral";
 const ratioText=(x)=>x==null?"-":fmtRatio(x);
 const metric=(v)=>typeof v==='number'?Math.round(v*100):null;
 return <div className="verificationPanel">
  <div className="verificationTop v21Top">
   <div><b>Point {idx+1} / {total}</b><span>{p.power} · {p.time} · W{p.wafer} · P{p.point} · {p.substrate_type||f.substrate_type||"SiCN"}</span></div>
   <div className="verificationTopActions"><span className={`autoBadge ${resultLabel.toLowerCase().replace(/[^a-z]+/g,"-")}`}>{resultLabel} (Auto)</span><button className="iconBtn" onClick={prev}><ChevronLeft size={16}/></button><button className="iconBtn" onClick={next}><ChevronRight size={16}/></button></div>
  </div>
  <div className="verificationLayout">
   <div className="verificationVisualColumn">
    <div className="verificationImages"><Visual title={hasHumanROI?"SEM / Human ROI":"SEM / Residue ROI"} sources={maps.sem}/><Visual title="Full EDS Map" sources={maps.eds}/></div>
    <div className="v21ElementStrip">
      <div className="focusPanel"><b>{hasHumanROI?"C Map (Human ROI)":"C Map (ROI)"}</b><ImageWithFallback sources={maps.c} alt={hasHumanROI?"C map with Human ROI":"C map with ROI"}/></div>
      <div className="focusPanel"><b>{hasHumanROI?"O Map (Human ROI)":"O Map (ROI)"}</b><ImageWithFallback sources={maps.o} alt={hasHumanROI?"O map with Human ROI":"O map with ROI"}/></div>
    </div>
    <HumanRoiEditor p={p} saveHumanRoi={saveHumanRoi}/>
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
      <div><span>C ROI / Global</span><b>{hasHumanROI&&typeof f.human_c_roi_mean==='number'&&typeof f.human_c_global_mean==='number'?`${f.human_c_roi_mean.toFixed(1)} / ${f.human_c_global_mean.toFixed(1)} `:typeof f.c_roi_mean==='number'&&typeof f.c_global_mean==='number'?`${f.c_roi_mean.toFixed(1)} / ${f.c_global_mean.toFixed(1)} `:''}<span className={`ratioMultiplier ${ratioClass(cRatio)}`} style={{color:cRatio==null?'#667386':cRatio>=3.00?'#159447':cRatio>=2.00?'#b77900':cRatio<=0.90?'#d12f3d':'#667386'}}>{`(${ratioText(cRatio)})`}</span></b></div>
      <div><span>O ROI / Global</span><b>{hasHumanROI&&typeof f.human_o_roi_mean==='number'&&typeof f.human_o_global_mean==='number'?`${f.human_o_roi_mean.toFixed(1)} / ${f.human_o_global_mean.toFixed(1)} `:typeof f.o_roi_mean==='number'&&typeof f.o_global_mean==='number'?`${f.o_roi_mean.toFixed(1)} / ${f.o_global_mean.toFixed(1)} `:''}<span className={`ratioMultiplier ${ratioClass(oRatio)}`} style={{color:oRatio==null?'#667386':oRatio>=3.00?'#159447':oRatio>=2.00?'#b77900':oRatio<=0.90?'#d12f3d':'#667386'}}>{`(${ratioText(oRatio)})`}</span></b></div>
    </div>
    <div className="ratioRuleNote"><b>C + O 동시 증가 기준</b><span>Human ROI는 <strong>C와 O가 모두 3.00× 이상</strong>이면 Residue, 둘 다 2.00× 이상이면서 하나라도 3.00× 미만이면 Ambiguous로 계산합니다.</span></div>
    <div className="humanRoiStatus"><MousePointer2 size={14}/><span>{hasHumanROI?"Human ROI가 저장되어 현재 C/O 계산에 적용되었습니다.":"AI/CV ROI가 표시됩니다. 잘못 잡혔으면 아래에서 직접 Human ROI를 지정하세요."}</span></div>
    <div className="roiInfo"><b>ROI Information</b><div><span>ROI Area</span><strong>{(hasHumanROI?f.human_roi_area_px:f.roi_area_px)?`${(hasHumanROI?f.human_roi_area_px:f.roi_area_px).toLocaleString()} px²`:'-'}</strong></div><div><span>ROI Coverage</span><strong>{hasHumanROI&&typeof f.human_roi_fill_ratio==='number'?`${Math.round(f.human_roi_fill_ratio*100)}%`:coverage==null?'-':`${coverage}%`}</strong></div><div><span>Mask Quality</span><strong>{maskQuality==null?'-':maskQuality.toFixed(2)}</strong></div><div><span>Detected as</span><strong>{hasHumanROI&&f.human_roi_component_count!=null?`${f.human_roi_component_count} connected region${f.human_roi_component_count===1?'':'s'}`:f.roi_component_count!=null?`${f.roi_component_count} connected region${f.roi_component_count===1?'':'s'}`:'-'}</strong></div></div>
    <div className="scoreRule v21Rule"><b>RESIDUE ≥ 70</b><span>AMBIGUOUS 60–69 · NON-RESIDUE &lt; 60</span><small>C/O 기준 점수: &lt;2.00× = Non-residue, 2.00–2.99× = Ambiguous, ≥3.00× = Residue. SEM Morphology/Spatial Overlap은 보조 지표입니다.</small></div>
   </section>
  </div>
  <div className="verificationInfo v21Info"><b>ROI 표시</b><span>AI/CV ROI를 기본으로 표시하고, Verification에서 직접 지정한 Human ROI가 있으면 그 ROI를 SEM/C/O에 반영합니다. Human ROI는 C/O 재계산과 Ground Truth 데이터로 저장됩니다. Local Ring은 판정 기준으로 사용하지 않습니다.</span></div>
  <div className="verificationButtons"><button className="danger" onClick={()=>human("Non-residue")}>NON-RESIDUE (N)</button><button className="approve" onClick={()=>human("Residue")}>RESIDUE (R)</button><button className="secondary" onClick={()=>human("Skip")}><SkipForward size={14}/> SKIP (S)</button></div>
  <div className="verificationNav"><button className="secondary" onClick={prev}><ChevronLeft size={14}/> Previous</button><button className="secondary" onClick={next}>Next <ChevronRight size={14}/></button></div>
 </div>
}
function HumanRoiEditor({p,saveHumanRoi}){
 const [open,setOpen]=useState(false), [regions,setRegions]=useState([]), [current,setCurrent]=useState([]), [drawing,setDrawing]=useState(false), [saving,setSaving]=useState(false), [src,setSrc]=useState(null);
 const canvasRef=useRef(); const wrapRef=useRef();
 useEffect(()=>{if(!open)return; const u=dynamicAsset(p,"sem")||p.assets?.sem_residue_overlay||p.assets?.sem; setSrc(imageUrl(u)); const stored=Array.isArray(p.features?.human_roi_polygons)?p.features.human_roi_polygons:(Array.isArray(p.features?.human_roi_polygon)&&p.features.human_roi_polygon.length>=3?[p.features.human_roi_polygon]:[]); setRegions(stored); setCurrent([]); setDrawing(false);},[open,p]);
 useEffect(()=>{if(!open||!src)return; const c=canvasRef.current,w=wrapRef.current;if(!c||!w)return; const img=new Image();img.onload=()=>{const maxW=Math.max(320,w.clientWidth);const maxH=430;const scale=Math.min(maxW/img.naturalWidth,maxH/img.naturalHeight);c.width=Math.max(1,Math.round(img.naturalWidth*scale));c.height=Math.max(1,Math.round(img.naturalHeight*scale));const ctx=c.getContext("2d");ctx.clearRect(0,0,c.width,c.height);ctx.drawImage(img,0,0,c.width,c.height);
   const drawPoly=(poly,active=false)=>{if(!poly?.length)return;ctx.beginPath();poly.forEach((q,i)=>{const x=q.x*c.width,y=q.y*c.height;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});if(poly.length>1){ctx.strokeStyle="#ff2525";ctx.lineWidth=active?4:4;ctx.lineJoin="round";ctx.lineCap="round";ctx.stroke();if(!active&&poly.length>=3){ctx.beginPath();poly.forEach((q,i)=>{const x=q.x*c.width,y=q.y*c.height;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.closePath();}}};
   regions.forEach(r=>drawPoly(r,false)); drawPoly(current,true);
 };img.src=src;},[open,src,regions,current]);
 if(!open)return <div className="humanRoiBar"><button className="secondary" onClick={()=>setOpen(true)}><MousePointer2 size={14}/>{Array.isArray(p.features?.human_roi_polygon)||Array.isArray(p.features?.human_roi_polygons)?"Edit Human ROI":"Set Human ROI"}</button><span>{(p.features?.human_roi_polygons?.length||0)>1?`${p.features.human_roi_polygons.length}개 Human ROI 저장됨`:Array.isArray(p.features?.human_roi_polygon)?"저장된 수동 ROI 있음":"AI/CV ROI가 틀리면 직접 지정"}</span></div>;
 const pointFromEvent=e=>{const c=canvasRef.current;if(!c)return null;const r=c.getBoundingClientRect();return {x:Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),y:Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))};};
 const startDraw=e=>{e.preventDefault();const q=pointFromEvent(e);if(!q)return;setDrawing(true);setCurrent([q]);try{canvasRef.current.setPointerCapture(e.pointerId)}catch{}};
 const draw=e=>{if(!drawing)return;e.preventDefault();const q=pointFromEvent(e);if(!q)return;setCurrent(v=>{const last=v[v.length-1];if(last&&Math.hypot((q.x-last.x)*canvasRef.current.width,(q.y-last.y)*canvasRef.current.height)<2)return v;return [...v,q]});};
 const endDraw=e=>{if(!drawing)return;e?.preventDefault?.();setDrawing(false);try{if(e?.pointerId!=null)canvasRef.current.releasePointerCapture(e.pointerId)}catch{};if(current.length>=3){setRegions(v=>[...v,smoothClosedPath(current)]);setCurrent([])}};
 const smoothClosedPath=poly=>{
   if(!Array.isArray(poly)||poly.length<6)return poly||[];
   const pts=[];
   const minStep=0.0018;
   for(const q of poly){
     const last=pts[pts.length-1];
     if(!last||Math.hypot(q.x-last.x,q.y-last.y)>=minStep)pts.push(q);
   }
   if(pts.length<6)return pts;
   const n=pts.length;
   return pts.map((q,i)=>{
     const a=pts[(i-1+n)%n],b=pts[(i+1)%n];
     return {
       x:Math.max(0,Math.min(1,q.x*0.70+(a.x+b.x)*0.15)),
       y:Math.max(0,Math.min(1,q.y*0.70+(a.y+b.y)*0.15))
     };
   });
 };
 const finishRegion=()=>{if(current.length<3)return;setRegions(v=>[...v,smoothClosedPath(current)]);setCurrent([]);setDrawing(false)};
 const undo=()=>setCurrent(v=>v.slice(0,-Math.min(12,v.length||0)));
 const clear=()=>{setRegions([]);setCurrent([]);setDrawing(false)};
 const removeLastRegion=()=>setRegions(v=>v.slice(0,-1));
 const save=async()=>{const all=current.length>=3?[...regions,smoothClosedPath(current)]:regions;if(!all.length)return;setSaving(true);try{await saveHumanRoi(all)}finally{setSaving(false)}};
 return <div className="humanRoiEditor"><div className="humanRoiEditorHead"><b>Human ROI 직접 지정</b><span><b>그림판처럼 마우스로 residue 외곽을 따라 그리세요.</b> 마우스를 놓는 순간 선이 자동으로 닫히고 smoothing되어 하나의 ROI로 완료됩니다. 떨어진 residue가 여러 개면 다시 그려서 여러 영역을 추가할 수 있습니다.</span></div><div className="humanRoiCanvasWrap" ref={wrapRef}><canvas ref={canvasRef} className="humanRoiDrawCanvas" onPointerDown={startDraw} onPointerMove={draw} onPointerUp={endDraw} onPointerCancel={endDraw} /></div><div className="humanRoiEditorHint"><span>현재 선: {current.length}점 · 저장할 영역: {regions.length}개</span><span>빨간색=Human ROI 작성/저장 · 마우스 놓으면 자동 영역 완료 · 자동 smoothing: 약하게</span></div><div className="humanRoiActions"><button className="secondary" onClick={undo} disabled={!current.length}>↶ 마지막 선 되돌리기</button><button className="secondary" onClick={finishRegion} disabled={current.length<3}>✓ 영역 완료</button><button className="secondary" onClick={removeLastRegion} disabled={!regions.length}>− 마지막 영역 삭제</button><button className="secondary" onClick={clear}><RotateCcw size={14}/>전체 삭제</button><button className="secondary" onClick={()=>setOpen(false)}>Cancel</button><button className="primary" disabled={saving||(!regions.length&&current.length<3)} onClick={save}>{saving?"Saving...":"Save Human ROI"}</button></div></div>
}

function VerificationModal({p,idx,total,points,close,setIdx,condition,setCondition,wafer,setWafer,resultFilter,setResultFilter,human,saveHumanRoi}){
 const visible=points.filter(x=>{
   const c=condition==="ALL"||`${x.power} · ${x.time}`===condition;
   const w=wafer==="ALL"||String(x.wafer??"")===String(wafer);
   const r=resultFilter==="ALL"||(verificationResult(x)===resultFilter);
   return c&&w&&r;
 });
 const currentPos=Math.max(0,visible.findIndex(x=>x.id===p?.id));
 useEffect(()=>{if(visible.length&&!visible.some(x=>x.id===p?.id))setIdx(points.findIndex(x=>x.id===visible[0].id));},[condition,wafer,resultFilter,visible.length,p?.id]);
 const goVisible=delta=>{if(!visible.length)return;const n=Math.min(visible.length-1,Math.max(0,currentPos+delta));const ni=points.findIndex(x=>x.id===visible[n].id);if(ni>=0)setIdx(ni)};
 const conditions=[...new Set(points.map(x=>`${x.power} · ${x.time}`).filter(Boolean))];
 const wafers=[...new Set(points.map(x=>x.wafer).filter(v=>v!==undefined&&v!==null&&String(v)!==""))].sort((a,b)=>Number(a)-Number(b));
 return <div className="verificationOverlay" onClick={close}><div className="verificationModal" onClick={e=>e.stopPropagation()}><div className="verificationModalHead"><b>Verification</b><div className="verificationHeadTools"><span>{visible.length} points shown</span><button className="secondary" onClick={close}>Close</button></div></div><div className="verificationWorkspace"><aside className="verificationQueue"><div className="verificationQueueTitle"><b>Point Navigator</b><small>조건 / 웨이퍼 / 결과별 Point 선택</small></div><label>Condition<select value={condition} onChange={e=>setCondition(e.target.value)}><option value="ALL">All Conditions</option>{conditions.map(c=><option key={c} value={c}>{c}</option>)}</select></label><label>Wafer<select value={wafer} onChange={e=>setWafer(e.target.value)}><option value="ALL">All Wafers</option>{wafers.map(w=><option key={w} value={String(w)}>W{w}</option>)}</select></label><label>Result<select value={resultFilter} onChange={e=>setResultFilter(e.target.value)}><option value="ALL">All Results</option><option value="Residue">Residue</option><option value="Ambiguous">Ambiguous</option><option value="Non-residue">Non-residue</option></select></label><div className="verificationQueueCount">{visible.length} / {points.length} points</div><div className="verificationPointList">{visible.map((x,i)=>{const gi=points.findIndex(y=>y.id===x.id);const r=verificationResult(x);return <button key={x.id} className={`verificationPointItem ${gi===idx?"active":""}`} onClick={()=>setIdx(gi)}><span>P{Number(x.point)||i+1}</span><em>{x.power}W · {x.time}s · W{x.wafer} · {x.substrate_type||"SiCN"}</em><strong className={r.toLowerCase().replace(/[^a-z]+/g,"-")}>{r}</strong></button>})}</div></aside><div className="verificationMain"><Review p={p} idx={idx} total={visible.length||total} prev={()=>goVisible(-1)} next={()=>goVisible(1)} human={human} saveHumanRoi={saveHumanRoi}/></div></div></div></div>}

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

function conditionKey(p){return `${p.power||"-"} / ${p.time||"-"}`}
function pointClass(p){return p.human_result||p.features?.result||"Ambiguous"}
function conditionGroups(points){const g={};(points||[]).forEach(p=>{const k=conditionKey(p);(g[k]??=[]).push(p)});return g}
function ConditionView({points,selected,setSelected,go}){
 const groups=conditionGroups(points); const keys=Object.keys(groups); const active=selected&&selected!=="ALL"?groups[selected]||[]:points;
 const residue=active.filter(p=>pointClass(p)==="Residue").length, non=active.filter(p=>pointClass(p)==="Non-residue").length, amb=active.length-residue-non;
 const wafers=[...new Set(active.map(p=>p.wafer).filter(x=>x!=null))].sort((a,b)=>Number(a)-Number(b));
 return <div className="content"><div className="intro"><div><span className="badge"><Activity size={13}/> CONDITION VIEW</span><h2>Condition View</h2><p>누적된 전체 데이터를 Power / Time 조건별로 확인합니다.</p></div><span>{active.length} Points</span></div>
 <section className="panel conditionViewToolbar"><div className="conditionViewSelect"><label>Condition</label><select value={selected} onChange={e=>setSelected(e.target.value)}><option value="ALL">All Conditions · {points.length} Points</option>{keys.map(k=><option key={k} value={k}>{k} · {groups[k].length} Points</option>)}</select></div><button className="secondary" onClick={()=>go("Condition Compare")}><GitCompare size={14}/> Compare Conditions</button></section>
 <div className="dashboardMetrics conditionMetrics"><Metric t="Points" v={active.length} s={selected==="ALL"?"cumulative":"selected condition"}/><Metric t="Residue" v={residue} s={active.length?`${Math.round(residue/active.length*100)}%`:"—"}/><Metric t="Non-residue" v={non} s={active.length?`${Math.round(non/active.length*100)}%`:"—"}/><Metric t="Ambiguous" v={amb} s="needs review"/><Metric t="Avg Score" v={active.length?(active.reduce((s,p)=>s+(displayScore(p)||0),0)/active.length).toFixed(1):"—"} s="selected points"/><Metric t="Wafers" v={wafers.length} s={wafers.map(w=>`W${w}`).join(", ")||"—"}/></div>
 <section className="panel"><div className="panelHead"><b>{selected==="ALL"?"All Conditions":"Condition · "+selected}</b><small>Result distribution and wafer summary</small></div><div className="dashTable"><div className="dashTr dashTh"><span>Condition</span><span>Points</span><span>Residue</span><span>Non</span><span>Ambiguous</span><span>Residue %</span></div>{keys.map(k=>{const a=groups[k],r=a.filter(p=>pointClass(p)==="Residue").length,n=a.filter(p=>pointClass(p)==="Non-residue").length,q=a.length-r-n;return <button className={`dashTr conditionRowBtn ${selected===k?"selected": ""}`} key={k} onClick={()=>setSelected(k)}><span>{k}</span><span>{a.length}</span><span>{r}</span><span>{n}</span><span>{q}</span><strong>{a.length?Math.round(r/a.length*100):0}%</strong></button>})}</div></section>
 {selected!=="ALL"&&<section className="panel conditionPointPanel"><div className="panelHead"><b>{selected} · Point List</b><small>Point를 클릭하면 Verification에서 해당 결과를 확인합니다.</small></div><div className="conditionPointGrid">{active.map(p=>{const c=pointClass(p);return <button className={`conditionPointCard ${c.toLowerCase().replace("-","")}`} key={p.id} onClick={()=>{const i=points.findIndex(x=>x.id===p.id);if(i>=0){go("Verification");setTimeout(()=>window.dispatchEvent(new CustomEvent("uvtape:open-point",{detail:{index:i}})),0)}}}><b>P{p.point}</b><span>W{p.wafer} · {p.power} · {p.time}</span><strong>{c}</strong><small>Score {displayScore(p)==null?"-":displayScore(p)}</small></button>})}</div></section>}
 </div>
}
function compareRatio(p,kind){
 const f=p?.features||{};
 const direct=kind==="C"?f.human_c_ratio:f.human_o_ratio;
 if(typeof direct==="number"&&Number.isFinite(direct))return direct;
 const roi=kind==="C"?f.c_roi_mean:f.o_roi_mean;
 const global=kind==="C"?f.c_global_mean:f.o_global_mean;
 return typeof roi==="number"&&typeof global==="number"&&global!==0?roi/global:null;
}
function comparePointKey(p){return `W${p?.wafer??"-"}|P${p?.point??"-"}`}
function compareEsc(v){return String(v??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]))}
function compareRate(a,b){return b?Math.round(a/b*1000)/10:0}
function Compare({points,selected,setSelected}){
 const g=conditionGroups(points),keys=Object.keys(g);
 const active=Array.isArray(selected)?selected:[];
 const effectiveKeys=active.length?active:keys.slice(0,2);
 const rows=effectiveKeys.map(k=>{
   const a=g[k]||[], r=a.filter(p=>pointClass(p)==="Residue").length, n=a.filter(p=>pointClass(p)==="Non-residue").length;
   const q=a.length-r-n, scores=a.map(displayScore).filter(v=>typeof v==="number"&&Number.isFinite(v));
   const cr=a.map(p=>compareRatio(p,"C")).filter(v=>typeof v==="number"&&Number.isFinite(v));
   const or=a.map(p=>compareRatio(p,"O")).filter(v=>typeof v==="number"&&Number.isFinite(v));
   const lim=a.map(p=>{const c=compareRatio(p,"C"),o=compareRatio(p,"O");return Number.isFinite(c)&&Number.isFinite(o)?Math.min(c,o):null}).filter(v=>v!=null);
   const waf=[...new Set(a.map(p=>p.wafer).filter(v=>v!=null))].sort((x,y)=>Number(x)-Number(y));
   const subs=[...new Set(a.map(p=>p.substrate_type||p.features?.substrate_type||"SiCN"))];
   return {k,a,r,n,q,avg:scores.length?scores.reduce((s,v)=>s+v,0)/scores.length:null,max:scores.length?Math.max(...scores):null,avgC:cr.length?cr.reduce((s,v)=>s+v,0)/cr.length:null,avgO:or.length?or.reduce((s,v)=>s+v,0)/or.length:null,avgLim:lim.length?lim.reduce((s,v)=>s+v,0)/lim.length:null,waf,subs};
 });
 const toggle=k=>setSelected(active.includes(k)?active.filter(x=>x!==k):[...active,k]);
 const pair=rows.length===2?rows:null;
 const pairTransition=()=>{
   if(!pair)return null;
   const [a,b]=pair, bm=new Map(b.a.map(p=>[comparePointKey(p),p]));
   const trans={};
   const matched=[];
   a.a.forEach(pa=>{const pb=bm.get(comparePointKey(pa));if(!pb)return;const ar=pointClass(pa),br=pointClass(pb);const key=`${ar} → ${br}`;trans[key]=(trans[key]||0)+1; if(ar!==br)matched.push({key:comparePointKey(pa),wafer:pa.wafer,point:pa.point,from:ar,to:br});});
   return {trans,matched};
 };
 const transition=pairTransition();
 const findings=[];
 if(pair){
   const [a,b]=pair;
   const dr=compareRate(b.r,b.a.length)-compareRate(a.r,a.a.length);
   const ds=(b.avg??0)-(a.avg??0);
   const da=compareRate(b.q,b.a.length)-compareRate(a.q,a.a.length);
   findings.push(`${b.k}의 Residue율은 ${dr>=0?"+":""}${dr.toFixed(1)}%p (${compareRate(a.r,a.a.length).toFixed(1)}% → ${compareRate(b.r,b.a.length).toFixed(1)}%)입니다.`);
   findings.push(`평균 Score는 ${ds>=0?"+":""}${ds.toFixed(1)}점, Ambiguous율은 ${da>=0?"+":""}${da.toFixed(1)}%p 변화했습니다.`);
   if(transition) findings.push(`동일 W/P가 모두 존재하는 Point 중 ${transition.matched.length}개에서 판정이 변경되었습니다.`);
 }
 const printReport=()=>{
   const title=pair?`${pair[0].k} vs ${pair[1].k}`:"Selected Condition Comparison";
   const html=`<html><head><title>${compareEsc(title)}</title><style>
   @page{size:A4 landscape;margin:10mm}body{font-family:Arial,"Malgun Gothic",sans-serif;color:#172236;margin:0}
   h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:18px 0 8px}.muted{color:#68768a;font-size:11px}
   table{width:100%;border-collapse:collapse;font-size:10px}th,td{border:1px solid #d7e0e8;padding:6px;text-align:center}th{background:#eef3f7}
   .cards{display:grid;grid-template-columns:repeat(${Math.min(3,Math.max(1,rows.length))},1fr);gap:10px}.card{border:1px solid #d7e0e8;border-radius:8px;padding:10px}.big{font-size:24px;font-weight:800}.note{font-size:10px;line-height:1.5}
   </style></head><body><h1>UV Tape Residue — Condition Comparison</h1><div class="muted">${compareEsc(title)} · cumulative workspace</div>
   <div class="cards">${rows.map(x=>`<div class="card"><b>${compareEsc(x.k)}</b><div class="big">${x.a.length} <span style="font-size:11px">Points</span></div><div>Residue ${compareRate(x.r,x.a.length).toFixed(1)}% · Avg Score ${x.avg==null?"-":x.avg.toFixed(1)}</div><div>Ambiguous ${compareRate(x.q,x.a.length).toFixed(1)}% · Avg C ${x.avgC==null?"-":x.avgC.toFixed(2)}× · Avg O ${x.avgO==null?"-":x.avgO.toFixed(2)}×</div></div>`).join("")}</div>
   <h2>Direct Comparison</h2><table><tr><th>Condition</th><th>Points</th><th>Residue %</th><th>Non-residue %</th><th>Ambiguous %</th><th>Avg Score</th><th>Avg C Ratio</th><th>Avg O Ratio</th></tr>${rows.map(x=>`<tr><td>${compareEsc(x.k)}</td><td>${x.a.length}</td><td>${compareRate(x.r,x.a.length).toFixed(1)}%</td><td>${compareRate(x.n,x.a.length).toFixed(1)}%</td><td>${compareRate(x.q,x.a.length).toFixed(1)}%</td><td>${x.avg==null?"-":x.avg.toFixed(1)}</td><td>${x.avgC==null?"-":x.avgC.toFixed(2)}×</td><td>${x.avgO==null?"-":x.avgO.toFixed(2)}×</td></tr>`).join("")}</table>
   ${pair&&transition?`<h2>Same Wafer / Position Result Transition</h2><table><tr><th>Transition</th><th>Count</th></tr>${Object.entries(transition.trans).map(([k,v])=>`<tr><td>${compareEsc(k)}</td><td>${v}</td></tr>`).join("")}</table>`:""}
   <div class="note" style="margin-top:14px">해석은 관측된 데이터 변화만 요약하며 공정 원인/인과관계를 단정하지 않습니다.</div>
   </body></html>`;
   const w=window.open("","_blank");if(w){w.document.write(html);w.document.close();setTimeout(()=>w.print(),500);}
 };
 return <div className="content">
   <div className="intro"><div><span className="badge"><GitCompare size={13}/> CONDITION COMPARE</span><h2>Condition Comparison</h2><p>HTML Viewer처럼 조건을 선택하고 Power / Time / Wafer / Position / Zone / Substrate별로 비교합니다.</p></div><span>{effectiveKeys.length} conditions</span></div>
   <section className="panel comparePicker"><div className="panelHead"><div><b>Compare Conditions</b><small>2개 선택하면 동일 Wafer + Position의 판정 변화까지 분석합니다. 여러 조건 선택도 가능합니다.</small></div><div style={{display:"flex",gap:7}}><button className="secondary" onClick={()=>setSelected([])}>Reset</button><button className="secondary" onClick={printReport}><FileText size={13}/> 비교 리포트</button></div></div>
     <div className="compareChoices">{keys.map(k=><button key={k} className={`compareChoice ${active.includes(k)?"selected":""}`} onClick={()=>toggle(k)}><b>{k}</b><span>{g[k].length} Points · Residue {compareRate(g[k].filter(p=>pointClass(p)==="Residue").length,g[k].length).toFixed(1)}%</span><i>{active.includes(k)?"✓":"+"}</i></button>)}</div>
     <div className="compareActions"><span>{active.length?`${active.length}개 선택`:`선택이 없어서 최근 2개 조건을 기본 비교합니다.`}</span><button className="secondary" onClick={()=>setSelected(keys)}>전체 조건 선택</button></div>
   </section>
   <section className="panel"><div className="panelHead"><b>Comparison Summary</b><small>누적 workspace 기준 · Human ROI 판정이 있으면 해당 결과를 사용합니다.</small></div>
     <div className="compareGrid">{rows.map(x=><div className="compareCard" key={x.k}><h3>{x.k}</h3><div className="compareBig">{x.a.length}<small> Points</small></div><div className="compareStats"><span><b>{x.r}</b> Residue</span><span><b>{x.n}</b> Non-residue</span><span><b>{x.q}</b> Ambiguous</span></div><div className="compareBar"><i style={{width:`${compareRate(x.r,x.a.length)}%`}}/></div><div className="compareFoot"><span>Residue Rate</span><strong>{compareRate(x.r,x.a.length).toFixed(1)}%</strong><span>Avg Score</span><strong>{x.avg==null?"-":x.avg.toFixed(1)}</strong><span>Max Score</span><strong>{x.max==null?"-":x.max.toFixed(1)}</strong><span>Avg C / O</span><strong>{x.avgC==null?"-":x.avgC.toFixed(2)}× / {x.avgO==null?"-":x.avgO.toFixed(2)}×</strong></div></div>)}</div>
   </section>
   {pair&&<section className="panel"><div className="panelHead"><b>A ↔ B Direct Comparison</b><small>첫 번째 선택 조건을 A, 두 번째 선택 조건을 B로 봅니다.</small></div>
     <div className="compareDeltaGrid"><div><small>Residue Rate Δ</small><b>{(compareRate(pair[1].r,pair[1].a.length)-compareRate(pair[0].r,pair[0].a.length)).toFixed(1)}%p</b></div><div><small>Avg Score Δ</small><b>{((pair[1].avg??0)-(pair[0].avg??0)).toFixed(1)}</b></div><div><small>Ambiguous Rate Δ</small><b>{(compareRate(pair[1].q,pair[1].a.length)-compareRate(pair[0].q,pair[0].a.length)).toFixed(1)}%p</b></div><div><small>Avg Limiting C/O Δ</small><b>{pair[0].avgLim!=null&&pair[1].avgLim!=null?((pair[1].avgLim-pair[0].avgLim).toFixed(2)+"×"):"-"}</b></div></div>
     <div className="dashTable"><div className="dashTr dashTh"><span>Condition</span><span>Points</span><span>Residue %</span><span>Avg Score</span><span>Ambiguous %</span><span>Wafers</span></div>{pair.map(x=><div className="dashTr" key={x.k}><span>{x.k}</span><span>{x.a.length}</span><strong>{compareRate(x.r,x.a.length).toFixed(1)}%</strong><span>{x.avg==null?"-":x.avg.toFixed(1)}</span><span>{compareRate(x.q,x.a.length).toFixed(1)}%</span><span>{x.waf.map(w=>`W${w}`).join(", ")||"-"}</span></div>)}</div>
   </section>}
   <section className="grid2">
     <section className="panel"><div className="panelHead"><b>Residue Rate Ranking</b><small>선택 조건 중 Residue 비율</small></div>{[...rows].sort((a,b)=>compareRate(b.r,b.a.length)-compareRate(a.r,a.a.length)).map(x=><div className="compareRank" key={x.k}><span>{x.k}</span><div><i style={{width:`${compareRate(x.r,x.a.length)}%`}}/></div><strong>{compareRate(x.r,x.a.length).toFixed(1)}%</strong></div>)}</section>
     <section className="panel"><div className="panelHead"><b>Score Distribution</b><small>60 미만 / 60–69 / 70–79 / 80–89 / 90+</small></div>{rows.map(x=>{const bins=[[0,60],[60,70],[70,80],[80,90],[90,101]].map(([lo,hi])=>x.a.filter(p=>{const s=displayScore(p);return s!=null&&s>=lo&&s<hi}).length);return <div key={x.k} className="scoreDistBlock"><b>{x.k}</b>{bins.map((v,i)=><div className="scoreDistRow" key={i}><span>{["<60","60–69","70–79","80–89","90+"][i]}</span><div><i style={{width:`${compareRate(v,x.a.length)}%`}}/></div><strong>{v} ({compareRate(v,x.a.length).toFixed(1)}%)</strong></div>)}</div>})}</section>
   </section>
   <section className="grid2">
     <section className="panel"><div className="panelHead"><b>Time 변화 — 같은 Power</b><small>조건을 표 형태로 확인</small></div><div className="compareMatrix">{[...new Set(rows.map(x=>x.k.split(" / ")[1]))].map(t=>null)}<table><thead><tr><th>Power</th>{[...new Set(rows.map(x=>x.k.split(" / ")[1]))].map(t=><th key={t}>{t}</th>)}</tr></thead><tbody>{[...new Set(rows.map(x=>x.k.split(" / ")[0]))].map(p=><tr key={p}><th>{p}</th>{[...new Set(rows.map(x=>x.k.split(" / ")[1]))].map(t=>{const x=rows.find(r=>r.k===`${p} / ${t}`);return <td key={t}>{x?`${compareRate(x.r,x.a.length).toFixed(1)}% (${x.a.length})`:"—"}</td>})}</tr>)}</tbody></table></div></section>
     <section className="panel"><div className="panelHead"><b>Power 변화 — 같은 Time</b><small>조건을 표 형태로 확인</small></div><table><thead><tr><th>Time</th>{[...new Set(rows.map(x=>x.k.split(" / ")[0]))].map(p=><th key={p}>{p}</th>)}</tr></thead><tbody>{[...new Set(rows.map(x=>x.k.split(" / ")[1]))].map(t=><tr key={t}><th>{t}</th>{[...new Set(rows.map(x=>x.k.split(" / ")[0]))].map(p=>{const x=rows.find(r=>r.k===`${p} / ${t}`);return <td key={p}>{x?`${compareRate(x.r,x.a.length).toFixed(1)}% (${x.a.length})`:"—"}</td>})}</tr>)}</tbody></table></section>
   </section>
   <section className="grid2">
     <section className="panel"><div className="panelHead"><b>Zone별 조건 비교</b><small>Corner / Edge / Middle별 Residue율</small></div><table><thead><tr><th>Condition</th>{["Corner","Edge","Middle"].map(z=><th key={z}>{z}</th>)}</tr></thead><tbody>{rows.map(x=><tr key={x.k}><th>{x.k}</th>{["Corner","Edge","Middle"].map(z=>{const a=x.a.filter(p=>p.zone===z),r=a.filter(p=>pointClass(p)==="Residue").length;return <td key={z}>{a.length?`${compareRate(r,a.length).toFixed(1)}% (${a.length})`:"—"}</td>})}</tr>)}</tbody></table></section>
     <section className="panel"><div className="panelHead"><b>Wafer별 조건 비교</b><small>W1 / W4 / W5 / W9 등 실제 데이터 기준</small></div><table><thead><tr><th>Condition</th>{[...new Set(rows.flatMap(x=>x.waf))].sort((a,b)=>Number(a)-Number(b)).map(w=><th key={w}>W{w}</th>)}</tr></thead><tbody>{rows.map(x=><tr key={x.k}><th>{x.k}</th>{[...new Set(rows.flatMap(y=>y.waf))].sort((a,b)=>Number(a)-Number(b)).map(w=>{const a=x.a.filter(p=>Number(p.wafer)===Number(w)),r=a.filter(p=>pointClass(p)==="Residue").length;return <td key={w}>{a.length?`${compareRate(r,a.length).toFixed(1)}% (${a.length})`:"—"}</td>})}</tr>)}</tbody></table></section>
   </section>
   <section className="grid2">
     <section className="panel"><div className="panelHead"><b>Position별 조건 비교</b><small>P1–P9 · 같은 위치의 Residue율</small></div><table><thead><tr><th>Condition</th>{Array.from({length:9},(_,i)=>i+1).map(p=><th key={p}>P{p}</th>)}</tr></thead><tbody>{rows.map(x=><tr key={x.k}><th>{x.k}</th>{Array.from({length:9},(_,i)=>i+1).map(pt=>{const a=x.a.filter(p=>Number(p.point)===pt),r=a.filter(p=>pointClass(p)==="Residue").length;return <td key={pt}>{a.length?`${compareRate(r,a.length).toFixed(0)}%`:"—"}</td>})}</tr>)}</tbody></table></section>
     <section className="panel"><div className="panelHead"><b>Substrate별 조건 비교</b><small>SiCN / Si / SiN / SiO2</small></div><table><thead><tr><th>Condition</th>{["SiCN","Si","SiN","SiO2"].map(s=><th key={s}>{s}</th>)}</tr></thead><tbody>{rows.map(x=><tr key={x.k}><th>{x.k}</th>{["SiCN","Si","SiN","SiO2"].map(s=>{const a=x.a.filter(p=>(p.substrate_type||p.features?.substrate_type||"SiCN")===s),r=a.filter(p=>pointClass(p)==="Residue").length;return <td key={s}>{a.length?`${compareRate(r,a.length).toFixed(1)}% (${a.length})`:"—"}</td>})}</tr>)}</tbody></table></section>
   </section>
   {pair&&transition&&<section className="panel"><div className="panelHead"><div><b>Same Wafer + Position Transition</b><small>{pair[0].k} → {pair[1].k} · 동일한 W/P만 비교</small></div></div>
     <div className="transitionGrid">{Object.entries(transition.trans).sort((a,b)=>b[1]-a[1]).map(([k,v])=><div className="transitionCard" key={k}><b>{k}</b><strong>{v}</strong><span>{compareRate(v,transition.matched.length).toFixed(1)}%</span></div>)}</div>
     {transition.matched.length>0&&<div className="dashTable"><div className="dashTr dashTh"><span>Wafer / Position</span><span>From</span><span>To</span><span>Type</span><span></span><span></span></div>{transition.matched.slice(0,40).map((x,i)=><div className="dashTr" key={i}><span>W{x.wafer} / P{x.point}</span><span>{x.from}</span><strong>{x.to}</strong><span>{x.from==="Ambiguous"?"Ambiguous resolution":x.to==="Ambiguous"?"Ambiguous appearance":"Result changed"}</span><span></span><span></span></div>)}</div>}</section>}
   {pair&&<section className="panel"><div className="panelHead"><b>Data-supported Findings</b><small>자동 생성 · 인과관계가 아닌 관측값만 표시</small></div><ul className="compareFindings">{findings.map((f,i)=><li key={i}>{f}</li>)}</ul></section>}
 </div>
}

function DeleteData({projectList,currentProject,deleteProject,busy}){
 const list=Array.isArray(projectList)?projectList:[];
 return <div className="content"><div className="intro"><div><h2>Delete Data</h2><p>필요하지 않은 Project와 해당 Point/분석 결과/원본 파일을 삭제합니다. 삭제 후 복구할 수 없습니다.</p></div></div><section className="panel deleteDataPanel"><div className="panelHead"><b>Stored Projects</b><small>업로드 배치는 내부적으로 구분되지만 Dashboard/Verification에서는 모든 데이터가 누적되어 표시됩니다.</small></div>{!list.length?<div className="deleteEmpty">삭제할 Project가 없습니다.</div>:<div className="deleteList">{list.map((x,i)=><div className={`deleteRow ${x.id===currentProject?"current":""}`} key={x.id}><div className="deleteMeta"><b>{x.name||"UV Tape Residue"}{x.id===currentProject&&<span className="currentTag">CURRENT</span>}</b><span>{x.created_at?new Date(x.created_at).toLocaleString("ko-KR"):"-"} · {x.point_count||0} points</span><small>{(x.files||[]).join(", ")||"source PDF"}</small></div><button className="danger" disabled={busy} onClick={()=>deleteProject(x.id,x)}><Trash2 size={13}/> Delete</button></div>)}</div>}</section></div>
}
function Reports({exportFile,project}){return <div className="content"><div className="intro"><div><h2>Reports & Export</h2><p>1 Point = 1 Page / Slide.</p></div></div><div className="reportGrid"><Report t="Point PPT" d="SEM / Spectrum / EDS Map / Element Maps / Data / Result" a={()=>exportFile("ppt")} disabled={!project}/><Report t="Point PDF" d="동일 레이아웃으로 1 Point = 1 Page" a={()=>exportFile("pdf")} disabled={!project}/><Report t="Analysis JSON" d="전체 분석 데이터 export" a={()=>exportFile("json")} disabled={!project}/></div></div>}
function Report({t,d,a,disabled}){return <div className="reportCard"><FileText size={20}/><b>{t}</b><p>{d}</p><button className="secondary" disabled={disabled} onClick={a}><Download size={14}/> Export</button></div>}
