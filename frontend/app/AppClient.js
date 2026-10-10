"use client";
import {Component,useEffect,useRef,useState} from "react";
import useDialog from "./useDialog";
import WorkspaceDashboard from "./WorkspaceDashboard";
import ResearchTools from "./ResearchTools";
import {DEFAULT_FILTERS} from "./researchModel.mjs";
import {Upload,LayoutDashboard,BrainCircuit,Images,GitCompare,FileText,Settings,FileUp,Play,Check,Download,ChevronLeft,ChevronRight,SkipForward,Search,Database,Activity,Plus,Trash2,MousePointer2,RotateCcw} from "lucide-react";

const API=process.env.NEXT_PUBLIC_API_BASE_URL||"https://uv-tape-residue-eds-backend.onrender.com";
const TERMINAL_JOBS=new Set(["completed","partial","failed","cancelled","interrupted"]);
const isActiveJob=x=>!TERMINAL_JOBS.has(x.status);
const DEFAULT_CONDITION={power:"150",time:"30",wafers:"1,4,5",points:"1-9",file:null};

function safeText(value,fallback="-") {
 if(value===null||value===undefined||value==="") return fallback;
 if(typeof value==="string"||typeof value==="number"||typeof value==="boolean") return String(value);
 if(typeof value==="object") {
   for(const key of ["label","name","value","text"]) {
     const v=value?.[key];
     if(typeof v==="string"||typeof v==="number") return String(v);
   }
 }
 return fallback;
}
function parseObjectLike(value){
 if(value&&typeof value==="object"&&!Array.isArray(value))return value;
 if(typeof value==="string"){try{const x=JSON.parse(value);return x&&typeof x==="object"&&!Array.isArray(x)?x:{}}catch{}}
 return {};
}
function normalizeAssetValue(value){
 if(typeof value==="string")return value;
 if(Array.isArray(value)){for(const v of value){const n=normalizeAssetValue(v);if(n)return n;}return null;}
 if(value&&typeof value==="object"){
   for(const key of ["url","src","path","storage_path","storagePath","key","asset_url"]) {
     if(typeof value[key]==="string"&&value[key])return value[key];
   }
 }
 return null;
}
function normalizePointRecord(raw){
 const p=raw&&typeof raw==="object"?{...raw}:{};
 const features=parseObjectLike(p.features);
 const assetsRaw=parseObjectLike(p.assets);
 const assets={};
 Object.entries(assetsRaw).forEach(([k,v])=>{const n=normalizeAssetValue(v);if(n)assets[k]=n});
 return {...p,id:safeText(p.id,`point-${Math.random().toString(36).slice(2)}`),power:safeText(p.power,"-"),time:safeText(p.time,"-"),substrate_type:safeText(p.substrate_type||features.substrate_type,"SiCN"),features,assets,human_result:typeof p.human_result==="string"?p.human_result:null};
}

class VerificationErrorBoundary extends Component{
 constructor(props){super(props);this.state={error:null}}
 static getDerivedStateFromError(error){return {error}}
 componentDidCatch(error,info){console.error("[Verification] render error",error,info)}
 componentDidUpdate(prevProps){if(prevProps.resetKey!==this.props.resetKey&&this.state.error)this.setState({error:null})}
 render(){
   if(!this.state.error)return this.props.children;
   const message=safeText(this.state.error?.message,"Unknown verification rendering error");
   return <div className="verificationOverlay" onClick={this.props.close}><div className="verificationModal verificationErrorModal" onClick={e=>e.stopPropagation()}><div className="verificationModalHead"><b>결과 검증</b><button className="secondary" onClick={this.props.close}>닫기</button></div><div className="verificationErrorState"><b>Verification 화면 데이터 형식을 복구하지 못했습니다.</b><p>{message}</p><span>다른 데이터는 유지됩니다. 이 오류 문구를 캡처하면 문제 Point를 바로 추적할 수 있습니다.</span></div></div></div>;
 }
}

function parseSelection(value){
 const out=[];
 String(value||"").split(",").forEach(part=>{
   const t=part.trim(); if(!t)return;
   if(t.includes("-")){const [a,b]=t.split("-").map(Number);if(Number.isFinite(a)&&Number.isFinite(b)){for(let n=Math.min(a,b);n<=Math.max(a,b);n++)out.push(n)}} else {const n=Number(t);if(Number.isFinite(n))out.push(n)}
 });
 return [...new Set(out)].sort((a,b)=>a-b);
}

const PAGE_LABELS={"Dashboard":"대시보드","New Analysis":"새 분석","Verification":"결과 검증","AI Analysis":"AI 해석","Image Gallery":"이미지 조회","Condition View":"조건별 데이터","Condition Compare":"조건·위치 비교","Reports":"보고서 출력","Delete Data":"데이터 관리"};
const NAV_GROUPS=[["작업",[["Dashboard",LayoutDashboard],["New Analysis",Upload],["Verification",Check]]],["데이터 탐색",[["Condition View",Activity],["Image Gallery",Images],["Condition Compare",GitCompare]]],["결과 활용",[["Reports",FileText],["AI Analysis",BrainCircuit]]]];
export default function App(){
 const [mounted,setMounted]=useState(false),[initialLoading,setInitialLoading]=useState(true),[page,setPage]=useState("Dashboard"),[points,setPoints]=useState([]),[project,setProject]=useState(null),[projectList,setProjectList]=useState([]),[idx,setIdx]=useState(0),[msg,setMsg]=useState(""),[aiAnalysis,setAiAnalysis]=useState(null),[aiBusy,setAiBusy]=useState(false),conditionInputs=useRef({});
 const [q,setQ]=useState(""),[result,setResult]=useState("All"),[conditionView,setConditionView]=useState("ALL"),[compareConditions,setCompareConditions]=useState([]),[conditions,setConditions]=useState([{...DEFAULT_CONDITION}]),[substrateType,setSubstrateType]=useState("SiCN"),[positionSubstrates,setPositionSubstrates]=useState(()=>({"1":"SiCN","4":"SiCN","5":"SiCN","9":"Si"})),[sampleCategory,setSampleCategory]=useState("MAIN"),[pagesPerPoint,setPagesPerPoint]=useState(3),[draggingCondition,setDraggingCondition]=useState(null),[busy,setBusy]=useState(false),[progress,setProgress]=useState(0),[progressPhase,setProgressPhase]=useState("idle"),[progressMessage,setProgressMessage]=useState(""),[verificationOpen,setVerificationOpen]=useState(false),[verificationCondition,setVerificationCondition]=useState("ALL"),[verificationWafer,setVerificationWafer]=useState("ALL"),[verificationResult,setVerificationResult]=useState("ALL"),[analysisQueue,setAnalysisQueue]=useState([]),[analysisModalOpen,setAnalysisModalOpen]=useState(false);
 const [dataState,setDataState]=useState("loading");
 const [researchFilters,setResearchFilters]=useState(DEFAULT_FILTERS);
 const [queueLoaded,setQueueLoaded]=useState(false);
 const uploadingRef=useRef(false);
 const activeAnalysis=analysisQueue.some(isActiveJob);
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
 async function loadData(preferredId=null, opts={}){
   const silent=!!opts.silent;
   setDataState("loading");
   // Full-screen loading is reserved for the initial mount only.
   // Background refreshes, queue completion, re-analysis, and navigation
   // must never replace an already rendered dashboard with the loading screen.
   const savedId=preferredId||(typeof window!=="undefined"?localStorage.getItem("uvtape:selectedProject"):null);
   let lastError=null;
   try{
     // During an active analysis queue, refresh data silently. Never replace a
     // working dashboard with the full-screen initial loader just because the
     // backend is waking up or a background job has just completed.
     for(let attempt=0;attempt<4;attempt++){
       try{
         const target=`${API}/api/workspace`;
         let r=await fetch(target,{cache:"no-store",signal:AbortSignal.timeout(20000)});
         if(!r.ok) throw new Error(`HTTP ${r.status}`);
         const j=await r.json();
         setDataState("ready");
         if(j?.project_id || j?.id){
           const pid=j.latest_project_id||j.project_id||j.id;
           const arr=(Array.isArray(j.points)?j.points:[]).map(normalizePointRecord);
           if(j.position_substrates)setPositionSubstrates(j.position_substrates);
           setProject(pid);setPoints(arr);
           const first=arr.findIndex(z=>!z.human_result);setIdx(first>=0?first:0);
           if(typeof window!=="undefined") localStorage.setItem("uvtape:selectedProject",pid);
           return arr;
         }
         // An empty workspace is valid. Do not erase currently displayed data
         // during a silent background refresh.
         if(!silent){setProject(null);setPoints([]);}
         return [];
       }catch(e){
         lastError=e;
         if(attempt<3) await new Promise(r=>setTimeout(r,[1200,2500,4500][attempt]));
       }
     }
     // Background refresh failures must not look like data loss. Keep the
     // current points and only surface a small toast for an explicit load.
     setDataState("error");
     if(!silent) notify(`프로젝트 데이터를 불러오지 못했습니다. ${lastError?.message||""}`.trim());
     return points;
   }finally{if(!silent)setInitialLoading(false);}
 }
 useEffect(()=>{setMounted(true);(async()=>{const list=await loadProjectList();await loadData(typeof window!=="undefined"?localStorage.getItem("uvtape:selectedProject"):null);if(!list.length)await loadProjectList()})()},[]);
 useEffect(()=>{
   try{const raw=JSON.parse(localStorage.getItem("uvtape:singleAnalysis")||localStorage.getItem("uvtape:analysisQueue")||"[]");if(Array.isArray(raw))setAnalysisQueue(raw.filter(x=>x?.job_id).slice(-1))}catch{}
   let cancelled=false;
   fetch(`${API}/api/analysis/current`,{cache:"no-store",signal:AbortSignal.timeout(20000)}).then(r=>r.ok?r.json():null).then(data=>{
     if(!cancelled&&data?.job)setAnalysisQueue([data.job]);
   }).catch(()=>{}).finally(()=>{if(!cancelled)setQueueLoaded(true)});
   return()=>{cancelled=true};
 },[]);
 useEffect(()=>{if(queueLoaded)try{localStorage.setItem("uvtape:singleAnalysis",JSON.stringify(analysisQueue))}catch{}},[analysisQueue,queueLoaded]);
 const pollingKey=analysisQueue.filter(isActiveJob).map(x=>x.job_id).join(",");
 useEffect(()=>{
   if(!queueLoaded||!pollingKey)return;
   let stopped=false,timer;
   const tick=async()=>{
     let terminal=false;
     for(const item of analysisQueue.filter(isActiveJob)){
       try{
         const r=await fetch(`${API}/api/jobs/${item.job_id}?project_id=${encodeURIComponent(item.project_id||"")}`,{cache:"no-store",signal:AbortSignal.timeout(20000)});
         if(stopped)return;
         if(r.status===404){
           setAnalysisQueue(q=>q.map(x=>x.job_id===item.job_id?{...x,status:"interrupted",message:"작업 상태를 찾을 수 없습니다. 서버 재시작 또는 이전 버전 작업일 수 있습니다. 저장된 결과를 확인하세요."}:x));
           terminal=true;continue;
         }
         if(!r.ok)throw new Error(`HTTP ${r.status}`);
         const st=await r.json();
         terminal=terminal||TERMINAL_JOBS.has(st.status);
         setAnalysisQueue(q=>q.map(x=>x.job_id===item.job_id?{...x,...st,job_id:item.job_id,files:item.files}:x));
       }catch(e){if(!stopped)setAnalysisQueue(q=>q.map(x=>x.job_id===item.job_id?{...x,message:"서버 연결 확인 중 · 연결 실패를 분석 완료로 처리하지 않습니다."}:x))}
     }
     if(stopped)return;
     if(!stopped&&!terminal)timer=setTimeout(tick,document.hidden?15000:5000);
   };
   tick();return()=>{stopped=true;clearTimeout(timer)};
 },[pollingKey,queueLoaded]);
 const terminalKey=analysisQueue.filter(x=>TERMINAL_JOBS.has(x.status)).map(x=>`${x.job_id}:${x.status}`).join(",");
 useEffect(()=>{if(queueLoaded&&terminalKey){loadProjectList();loadData(null,{silent:true});}},[terminalKey,queueLoaded]);
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
   if(uploadingRef.current||activeAnalysis)return notify("진행 중인 분석이 끝난 후 다음 PDF를 올려주세요.");
   const file=conditions[0]?.file;
   if(!file)return notify("PDF 하나를 먼저 선택하세요.");
   if(conditions.some(c=>!expectedPointsForCondition(c)||!Number(c.power)||!Number(c.time)))return notify("Power / Time / Wafer / Point 조건을 확인하세요.");
   uploadingRef.current=true;setBusy(true);setProgress(0);setProgressPhase("upload");setProgressMessage("PDF 업로드 중 · 업로드가 끝날 때까지 이 탭을 열어 두세요.");
   try{
     const fd=new FormData();fd.append("files",file);fd.append("conditions_json",JSON.stringify(conditions.map(({file,...c})=>c)));fd.append("pages_per_point",String(pagesPerPoint));fd.append("substrate_type",substrateType);fd.append("position_substrates_json",JSON.stringify(positionSubstrates));fd.append("sample_category",sampleCategory);
     // Do not retry POST automatically: a lost response may already have registered a job.
     const j=await new Promise((resolve,reject)=>{
       const xhr=new XMLHttpRequest();xhr.open("POST",`${API}/api/upload`);xhr.timeout=180000;
       xhr.upload.onprogress=e=>{if(e.lengthComputable){setProgress(Math.round(e.loaded/e.total*100));setProgressMessage(e.loaded===e.total?"서버에서 작업을 등록하고 있습니다.":"PDF 업로드 중 · 이 탭을 열어 두세요.")}};
       xhr.onerror=()=>reject(new Error("서버 연결 실패. 다시 올리기 전에 진행 중인 작업을 확인하세요."));xhr.ontimeout=()=>reject(new Error("등록 응답 시간 초과. 다시 올리기 전에 진행 중인 작업을 확인하세요."));
       xhr.onload=()=>{let data={};try{data=JSON.parse(xhr.responseText)}catch{}if(xhr.status<200||xhr.status>=300){reject(new Error(typeof data.detail==="string"?data.detail:`HTTP ${xhr.status}`));return}resolve(data)};xhr.send(fd);
     });
     setAnalysisQueue([{job_id:j.job_id,project_id:j.project_id,files:j.files||[file.name],total:j.expected_points,status:"queued",progress:5,completed:0,message:"서버에서 분석을 시작합니다."}]);
     setAnalysisModalOpen(true);setConditions([{...DEFAULT_CONDITION}]);notify("업로드 완료 · 서버에서 백그라운드 분석 중입니다.");
   }catch(e){
     notify(e.message||"업로드 실패");
     try{const r=await fetch(`${API}/api/analysis/current`,{cache:"no-store",signal:AbortSignal.timeout(20000)});if(r.ok){const data=await r.json();if(data.job){setAnalysisQueue([data.job]);setAnalysisModalOpen(true)}}}catch{}
   }
   finally{uploadingRef.current=false;setBusy(false);setProgressPhase("idle")}
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
     const normalized=normalizePointRecord(j); const updated=points.map(z=>z.id===p.id?normalized:z); setPoints(updated);if(typeof window!=="undefined")localStorage.setItem("uvtape:lastPointId", p.id);
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
     const normalized=normalizePointRecord(j); setPoints(points.map(z=>z.id===p.id?normalized:z));
     notify("Human ROI 저장 완료 · C/O 재계산 및 Ground Truth 데이터 저장");
     return j;
   }catch(e){notify(e.message||"ROI 저장 실패");throw e;}
 }
 async function runProjectAI(){if(!project)return notify("먼저 분석 데이터를 불러오세요.");setAiBusy(true);notify("OpenAI가 전체 실험 결과를 분석하고 있습니다...");try{const r=await fetch(`${API}/api/projects/${project}/ai-analysis`,{method:"POST"});const j=await r.json();if(!r.ok)throw new Error(j.detail||j.message||"AI analysis failed");setAiAnalysis(j);setPage("AI Analysis");notify("AI 연구 분석 완료")}catch(e){notify("AI 분석 오류: "+e.message)}finally{setAiBusy(false)}}
async function reanalyzeProject(){if(!project)return notify("현재 프로젝트가 없습니다.");if(!window.confirm("R2에 저장된 원본 PDF로 현재 프로젝트를 다시 분석할까요? 기존 분석 결과가 새 분석 결과로 갱신됩니다."))return;setBusy(true);try{const r=await fetch(`${API}/api/projects/${project}/reanalyze`,{method:"POST"});const j=await r.json();if(!r.ok)throw new Error(j.detail||j.message||"re-analysis failed");const poll=async()=>{const pr=await fetch(`${API}/api/jobs/${j.job_id}`);const st=await pr.json();setProgress(st.progress||0);setProgressPhase(st.phase||"reanalysis");setProgressMessage(st.message||"재분석 중...");if(st.status==="completed"||st.status==="partial"){setBusy(false);const loaded=await loadData(project);const ni=loaded.findIndex(z=>!z.human_result);if(ni>=0)setIdx(ni);setVerificationOpen(loaded.length>0);notify(st.status==="partial"?`재분석 부분 완료 · 실패 ${st.failed_count||0} Point`:"기존 데이터 재분석 완료");setTimeout(()=>{setProgressPhase("idle");setProgressMessage("");setProgress(0)},700);return;}if(st.status==="failed"){setBusy(false);throw new Error(st.error||"재분석 실패");}setTimeout(poll,1200)};await poll()}catch(e){setBusy(false);notify("재분석 오류: "+e.message)}}

 async function openVerification(){
 if(!points.length){notify("분석 데이터가 없습니다.");return;}
 const ni=points.findIndex(z=>!z.human_result);
 setIdx(ni>=0?ni:0);setPage("Dashboard");setVerificationOpen(true);
 if(ni<0)notify("모든 Point가 검증되어 첫 Point부터 열었습니다.");
}
function goNav(n){if(n==="Verification"){openVerification();return}setVerificationOpen(false);setPage(n)}
 async function exportFile(kind){if(!project)return notify("먼저 실제 PDF를 업로드해 project를 생성하세요.");const r=await fetch(`${API}/api/projects/${project}/export/${kind}`,{method:"POST"});if(!r.ok)return notify("Export 실패");const blob=await r.blob(),u=URL.createObjectURL(blob),a=document.createElement("a");a.href=u;a.download=`point_report.${kind==="ppt"?"pptx":kind}`;a.click();URL.revokeObjectURL(u);notify(`${kind.toUpperCase()} export 완료`)}
 async function exportWorkspaceFile(kind){if(!points.length)return notify("먼저 분석 데이터를 추가하세요.");const r=await fetch(`${API}/api/workspace/export/${kind}`,{method:"POST"});if(!r.ok)return notify("Engineering Summary export 실패");const blob=await r.blob(),u=URL.createObjectURL(blob),a=document.createElement("a");a.href=u;a.download=kind==="summary-ppt"?"UV_Tape_Engineering_Summary.pptx":"UV_Tape_Engineering_Summary.pdf";a.click();URL.revokeObjectURL(u);notify("Engineering Summary export 완료")}
 if(!mounted)return <div className="app"><main><InitialLoading/></main></div>;
 if(initialLoading)return <div className="app"><main><InitialLoading/></main></div>;
 const p=points[idx],filtered=points.filter(x=>(result==="All"||pointClass(x)===result)&&Object.values(x).join(" ").toLowerCase().includes(q.toLowerCase()));
 return <div className="app"><a className="skipLink" href="#workspace-main">본문으로 이동</a><aside className="workspaceSidebar"><div className="logo"><b>EDS</b><div><span>Insight Lab</span><small>UV Tape Residue</small></div></div><nav aria-label="주 메뉴">{NAV_GROUPS.map(([label,items])=><div className="navGroup" key={label}><div className="navGroupTitle">{label}</div>{items.map(([n,I])=><button title={PAGE_LABELS[n]} aria-label={PAGE_LABELS[n]} aria-current={(verificationOpen?n==="Verification":page===n)?"page":undefined} className={(verificationOpen?n==="Verification":page===n)?"nav active":"nav"} key={n} onClick={()=>goNav(n)}><I size={18}/><span>{PAGE_LABELS[n]}</span></button>)}</div>)}</nav><div className="sideBottom"><button className={page==="Delete Data"?"nav active":"nav"} onClick={()=>goNav("Delete Data")} title="데이터 관리"><Database size={18}/><span>데이터 관리</span></button><div className="workspaceVersion">SEM / EDS Workspace <span>v23.8.1</span></div></div></aside><main id="workspace-main" tabIndex={-1}><header><div className="workspaceBreadcrumb"><span>UV Tape Residue</span><ChevronRight size={13}/><h1>{PAGE_LABELS[page]}</h1></div><div className="headerActions">{analysisQueue.length>0&&<button className="secondary" onClick={()=>setAnalysisModalOpen(true)}><Activity size={15}/>{activeAnalysis?`분석 중 · ${Math.round(analysisQueue[0]?.progress||0)}%`:"최근 분석 상태"}</button>}<span className={`ready ${dataState}`} role="status"><i/>{dataState==="error"?"연결 확인 필요":dataState==="loading"?"데이터 확인 중":points.length?"데이터 연결됨":"연결됨 · 저장 데이터 없음"}</span></div></header>
 {dataState==="error"&&<div className="workspaceAlert" role="alert"><div><b>데이터를 불러오지 못했습니다.</b><span>{points.length?"마지막으로 불러온 데이터를 표시하고 있습니다.":"저장된 Point 수를 확인할 수 없습니다. 다시 불러와 주세요."}</span></div><button className="secondary" onClick={()=>loadData(null,{silent:true})}>다시 불러오기</button></div>}
 {page==="Dashboard"&&<WorkspaceDashboard points={points} classify={pointClass} go={goNav} dataState={dataState} jobs={analysisQueue} openJob={()=>setAnalysisModalOpen(true)} onCondition={key=>{setConditionView(key);goNav("Condition View")}} onPoint={point=>{setIdx(points.findIndex(x=>x.id===point.id));setVerificationOpen(true)}}/>}
 {page==="New Analysis"&&<UploadPage conditionInputs={conditionInputs} setConditionFile={setConditionFile} upload={upload} conditions={conditions} updateCondition={updateCondition} addCondition={addCondition} removeCondition={removeCondition} pagesPerPoint={pagesPerPoint} setPagesPerPoint={setPagesPerPoint} draggingCondition={draggingCondition} setDraggingCondition={setDraggingCondition} expectedPointsForCondition={expectedPointsForCondition} expectedPoints={expectedPoints} busy={busy||activeAnalysis||!queueLoaded} analysisQueue={analysisQueue} substrateType={substrateType} setSubstrateType={setSubstrateType} sampleCategory={sampleCategory} setSampleCategory={setSampleCategory} positionSubstrates={positionSubstrates} setPositionSubstrates={setPositionSubstrates}/>} 
 {page==="Verification"&&<section className="panel emptyPanel"><Check size={24}/><b>결과 검증 창을 열어 주세요.</b><small>Point 목록에서 측정 결과와 원소 맵을 확인할 수 있습니다.</small><button className="secondary" onClick={openVerification}>검증 열기</button></section>}
 {page==="AI Analysis"&&<AIAnalysis points={points} analysis={aiAnalysis} busy={aiBusy} run={runProjectAI}/>}
 {page==="Image Gallery"&&<Gallery points={filtered} q={q} setQ={setQ} result={result} setResult={setResult}/>}
 {page==="Condition View"&&<ConditionView points={points} selected={conditionView} setSelected={setConditionView} go={goNav}/>}
 {page==="Condition Compare"&&<ResearchTools mode="compare" points={points} filters={researchFilters} setFilters={setResearchFilters} classify={pointClass} autoClass={autoPointClass} scoreFn={displayScore} renderEvidence={(p,close)=><ImageLightbox p={p} close={close}/>} api={API} go={goNav} projectList={projectList} analysisActive={activeAnalysis}/>} {page==="Reports"&&<ResearchTools mode="reports" points={points} filters={researchFilters} setFilters={setResearchFilters} classify={pointClass} autoClass={autoPointClass} scoreFn={displayScore} renderEvidence={(p,close)=><ImageLightbox p={p} close={close}/>} api={API} go={goNav} projectList={projectList} analysisActive={activeAnalysis}/>} {page==="Delete Data"&&<DeleteData projectList={projectList} currentProject={project} deleteProject={deleteProjectById} busy={busy||activeAnalysis} reanalyze={reanalyzeProject}/>} </main>{busy&&progressPhase==="upload"&&<AnalysisProgress progress={progress} phase={progressPhase} message={progressMessage}/>} {analysisModalOpen&&<AnalysisBatchModal items={analysisQueue} open={analysisModalOpen} close={()=>setAnalysisModalOpen(false)}/>} {verificationOpen&&p&&<VerificationErrorBoundary resetKey={`${safeText(p?.id,"-")}-${points.length}`} close={()=>setVerificationOpen(false)}><VerificationModal p={p} idx={idx} total={points.length} points={points} close={()=>setVerificationOpen(false)} setIdx={setIdx} condition={verificationCondition} setCondition={setVerificationCondition} wafer={verificationWafer} setWafer={setVerificationWafer} resultFilter={verificationResult} setResultFilter={setVerificationResult} human={human} saveHumanRoi={saveHumanRoi}/></VerificationErrorBoundary>} {msg&&<div className="toast" role="status">{msg}</div>}</div>
}
function InitialLoading(){return <div className="initialLoading"><div className="initialLoadingCard"><div className="initialLoadingBrand"><b>EDS</b><span>Insight Lab</span></div><div className="initialLoadingTitle">저장 데이터 불러오는 중</div><p>기존 분석 데이터와 Point 정보를 불러오는 중입니다.</p><div className="initialLoadingTrack"><div className="initialLoadingBar"/></div><div className="initialLoadingMeta"><span>프로젝트 연결 확인</span><span>잠시만 기다려주세요</span></div></div></div>}
function AnalysisProgress({progress,phase,message}){
 const label=phase==="upload"?"파일 업로드":phase==="queued"?"분석 준비":phase==="analysis"?"Point 분석":phase==="database"?"결과 저장":"분석 진행";
 return <div className="progressOverlay"><div className="progressModal"><div className="progressTop"><div><span className="badge"><Activity size={13}/> ANALYSIS IN PROGRESS</span><h3>{label}</h3></div><b>{Math.round(progress)}%</b></div><div className="progressTrack"><div className="progressBar" style={{width:`${Math.max(2,Math.min(100,progress))}%`}}/></div><p>{message||"분석 중입니다. 잠시만 기다려주세요."}</p><small>창을 닫거나 새로고침하지 마세요.</small></div></div>
}
function AnalysisBatchModal({items,open,close}){
 if(!open)return null;
 const item=items[0],active=item&&isActiveJob(item),pct=Math.round(item?.progress||0);
 const labels={completed:"분석 완료",partial:"일부 Point만 저장됨",failed:"분석 실패",interrupted:"분석 중단",cancelled:"분석 취소",queued:"분석 대기",processing:"분석 중"};
 const failures=Array.isArray(item?.failed_points)?item.failed_points:[];
 const missing=Array.isArray(item?.missing_points)?item.missing_points:[];
 const incomplete=Array.isArray(item?.incomplete_points)?item.incomplete_points:[];
 const recovered=Array.isArray(item?.recovered_points)?item.recovered_points:[];
 const detected=Number.isFinite(Number(item?.detected_points))?Number(item.detected_points):null;
 const total=Number(item?.total||0);
 const mode=item?.recognition_mode==="point_label"?"PDF Point label 기준":item?.recognition_mode==="page_order"?"3-page 순서 기준":"";
 return <div className="progressOverlay"><div className="progressModal batchProgressModal">
  <div className="progressTop"><div><span className="badge"><Activity size={13}/> PDF ANALYSIS</span><h3>{labels[item?.status]||"작업 상태 확인"}</h3></div><b>{pct}%</b></div>
  <p>{item?.files?.join(", ")}</p><div className="progressTrack"><div className="progressBar" style={{width:`${pct}%`}}/></div>
  <p>{item?.message}</p>
  <div className="analysisStatusGrid">
   <div><small>저장 완료</small><b>{item?.completed||0} / {total||0}</b></div>
   <div><small>PDF Point 인식</small><b>{detected==null?"확인 중":`${detected} / ${total||detected}`}</b>{mode&&<span>{mode}</span>}</div>
   <div><small>미완성 / 오류</small><b>{item?.failed_count||incomplete.length||0}</b></div>
  </div>
  {recovered.length>0&&<div className="analysisRecovered"><b>자동 복구 {recovered.length} Point</b><span>{recovered.join(", ")}</span></div>}
  {incomplete.length>0&&<div className="analysisIssueBox"><b>최종 저장 검증 미완성 {incomplete.length}개</b><p>{incomplete.map(x=>`${x.id}${x.missing?.length?` (${x.missing.join(" / ")})`:""}`).join(", ")}</p></div>}
  {missing.length>0&&<div className="analysisIssueBox"><b>PDF에서 매칭되지 않은 Point {missing.length}개</b><p>{missing.map(x=>x.id||`W${x.wafer}-P${x.point}`).join(", ")}</p></div>}
  {failures.length>0&&<div className="analysisFailureList"><b>실패 Point / 원인</b>{failures.map((f,i)=><div className="analysisFailureItem" key={`${f.id||i}-${i}`}><div><strong>{f.id||`W${f.wafer}-P${f.point}`}</strong><span>{f.stage||"analysis"}</span></div><p>{f.error||"Unknown error"}</p></div>)}</div>}
  {item?.storage_warning&&<p className="analysisWarning">{item.storage_warning}</p>}
  <div className="batchProgressBottom"><span>{active?"다른 화면에서 작업해도 분석은 계속됩니다. 분석이 끝나면 Point와 이미지의 저장 상태를 확인합니다.":"완료 수치는 최종 저장 검증 결과입니다. 자동 복구/미완성 Point도 여기서 확인할 수 있습니다."}</span><button className="secondary" onClick={close}>{active?"백그라운드로 보내기":"닫기"}</button></div>
 </div></div>
}
function Metric({t,v,s}){return <div className="metric"><small>{t}</small><b>{v}</b><span>{s}</span></div>}
function UploadPage({conditionInputs,setConditionFile,upload,conditions,updateCondition,addCondition,removeCondition,pagesPerPoint,setPagesPerPoint,draggingCondition,setDraggingCondition,expectedPoints,busy,substrateType,setSubstrateType,sampleCategory,setSampleCategory,positionSubstrates,setPositionSubstrates}){
 const files=conditions[0]?.file?[conditions[0].file]:[];
 const dragging=draggingCondition===0;
 const setDragging=v=>setDraggingCondition(v?0:null);
 const addFiles=fs=>{if(fs?.length===1)setConditionFile(0,fs[0]);else if(fs?.length>1)window.alert("PDF는 한 번에 하나만 선택하세요.")};
 return <div className="content"><div className="intro"><div><h2>새 분석</h2><p>PDF 하나를 업로드하고 백그라운드로 분석합니다. 다음 PDF는 현재 작업이 끝난 뒤 등록하세요.</p></div></div><section className="panel"><div className="panelHead"><b>01 · 원본 PDF</b><small>파일 선택 또는 끌어다 놓기</small></div><div className={`drop ${dragging?"dragging":""}`} role="button" tabIndex={0} aria-label="EDS PDF 선택" onKeyDown={e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();conditionInputs.current[0]?.click()}}} onClick={()=>conditionInputs.current[0]?.click()} onDragOver={e=>{e.preventDefault();setDragging(true)}} onDragLeave={()=>setDragging(false)} onDrop={e=>{e.preventDefault();setDragging(false);addFiles(e.dataTransfer.files)}}><FileUp size={30}/><b>{dragging?"여기에 PDF를 놓으세요":"EDS PDF를 끌어다 놓거나 클릭해서 선택하세요"}</b><small>PDF 1개 · 업로드 완료 후 다른 메뉴로 이동할 수 있습니다.</small><input ref={el=>{conditionInputs.current[0]=el}} hidden type="file" accept=".pdf,application/pdf" onChange={e=>addFiles(e.target.files)}/></div>{files.map((f,i)=><div className="file" key={`${f.name}-${i}`}><FileText size={14}/><span>{f.name}</span><small>{(f.size/1024/1024).toFixed(1)} MB</small><button className="iconBtn" onClick={()=>updateCondition(0,"file",null)}><Trash2 size={13}/></button></div>)}</section><section className="panel samplePanel"><div className="panelHead"><div><b>02 · 시료와 기판</b><small>선택한 PDF에 적용됩니다. Wafer별 기판을 지정합니다. W1/W4/W5는 main 위치, W9는 reference/other로 분리 집계합니다.</small></div></div><div className="sampleControls"><label>시료 구분<select value={sampleCategory} onChange={e=>{const v=e.target.value;setSampleCategory(v);if(v==="MAIN")setSubstrateType("SiCN")}}><option value="MAIN">MAIN</option><option value="ANOTHER">ANOTHER</option></select></label><label>기본 기판<select value={substrateType} onChange={e=>setSubstrateType(e.target.value)}><option>SiCN</option><option>Si</option><option>SiN</option><option>SiO2</option></select></label></div><div className="positionSubstrateGrid"><div className="positionSubstrateTitle">위치별 기판</div>{Array.from({length:9},(_,i)=>i+1).map(pos=><label key={pos}>W{pos}<select value={positionSubstrates[String(pos)]||substrateType||"SiCN"} onChange={e=>setPositionSubstrates(m=>({...m,[String(pos)]:e.target.value}))}><option>SiCN</option><option>Si</option><option>SiN</option><option>SiO2</option></select></label>)}</div></section><section className="panel conditionPanel"><div className="panelHead"><div className="conditionHead"><div><b>03 · 측정 조건</b><small>한 PDF에 여러 조건이 있으면 PDF에 나오는 순서대로 추가하세요.</small></div><button className="secondary" onClick={addCondition}><Plus size={13}/> 조건 추가</button></div></div>{conditions.map((c,i)=><div className="conditionRow" key={i}><div className="conditionTitle">조건 {i+1}</div><label>Power (W)<input value={c.power} onChange={e=>updateCondition(i,"power",e.target.value)}/></label><label>Time (s)<input value={c.time} onChange={e=>updateCondition(i,"time",e.target.value)}/></label><div className="selectionGroup"><span className="selectionLabel">Wafer</span><div className="checks">{Array.from({length:9},(_,n)=>n+1).map(w=>{const a=parseSelection(c.wafers);return <label className="check" key={w}><input type="checkbox" checked={a.includes(w)} onChange={()=>{const next=a.includes(w)?a.filter(x=>x!==w):[...a,w].sort((x,y)=>x-y);updateCondition(i,"wafers",next.join(","))}}/><span>W{w}</span></label>})}</div></div><div className="selectionGroup"><span className="selectionLabel">Point</span><div className="checks">{Array.from({length:9},(_,n)=>n+1).map(pt=>{const a=parseSelection(c.points);return <label className="check" key={pt}><input type="checkbox" checked={a.includes(pt)} onChange={()=>{const next=a.includes(pt)?a.filter(x=>x!==pt):[...a,pt].sort((x,y)=>x-y);updateCondition(i,"points",next.join(","))}}/><span>P{pt}</span></label>})}</div></div>{conditions.length>1&&<button className="iconBtn" onClick={()=>{if(i===0&&conditions[0].file)updateCondition(1,"file",conditions[0].file);removeCondition(i)}}><Trash2 size={14}/></button>}</div>)}<div className="mappingSummary"><span>Point당 페이지 <input className="smallInput" type="number" min="1" value={pagesPerPoint} onChange={e=>setPagesPerPoint(Math.max(1,Number(e.target.value)||1))}/></span><b>예상 Point: {expectedPoints()}</b><span>예상 페이지: {expectedPoints()*pagesPerPoint}</span></div></section><div className="actions"><button className="primary" disabled={busy} onClick={upload}><Play size={14}/>{busy?"현재 작업 처리 중...":"분석 시작"}</button></div></div>}
function elementRatioDisplayScore(ratio,strong){
 const x=finiteNumber(ratio);
 if(x==null)return null;
 if(x<2)return Math.max(0,Math.min(59,(x/2)*59));
 if(x<strong)return 60+((x-2)/(strong-2))*10;
 const equivalent=x*(3/strong);
 const k=0.23, denom=1-Math.exp(-k*17);
 const normalized=(1-Math.exp(-k*(equivalent-3)))/denom;
 return Math.min(100,70+30*Math.max(0,normalized));
}
function coRatioDisplayScore(cRatio,oRatio){
 const c=elementRatioDisplayScore(cRatio,2.40), o=elementRatioDisplayScore(oRatio,3.00);
 if(c==null||o==null)return null;
 return Math.round(Math.min(c,o)*10)/10;
}
function coRatioRuleResult(cRatio,oRatio){
 const c=finiteNumber(cRatio), o=finiteNumber(oRatio);
 if(c==null||o==null)return null;
 if(c>=2.40 && o>=3.00)return "Residue";
 if(c>=2.00 && o>=2.00)return "Ambiguous";
 return "Non-residue";
}
function hasHumanROI(f={}){
 return (Array.isArray(f.human_roi_polygons)&&f.human_roi_polygons.some(r=>Array.isArray(r)&&r.length>=3))||(Array.isArray(f.human_roi_polygon)&&f.human_roi_polygon.length>=3);
}
function finiteNumber(v){
 if(v===null||v===undefined||v==="")return null;
 const n=Number(v);
 return Number.isFinite(n)?n:null;
}
function normalizeResultLabel(v){
 // v23.7.51: legacy rows can contain stale/non-string result payloads.
 // Never allow one malformed Point to crash Verification or other aggregate views.
 if(typeof v!=="string")return null;
 const raw=v.trim();
 const k=raw.toLowerCase().replace(/[\s_]+/g,"-");
 if(["residue","r"].includes(k))return "Residue";
 if(["non-residue","nonresidue","non-res","n"].includes(k))return "Non-residue";
 if(["ambiguous","review","unknown","a"].includes(k))return "Ambiguous";
 return null;
}
function currentRatios(p){
 const f=p?.features||{};
 const ratio=(roi,global)=>{const a=finiteNumber(roi),b=finiteNumber(global);return a!=null&&b!=null&&b!==0?a/b:null};
 const hc=finiteNumber(f.human_c_ratio),ho=finiteNumber(f.human_o_ratio);
 if(hasHumanROI(f) && hc!=null && ho!=null) return {c:hc,o:ho,source:"Human ROI"};
 const storedC=finiteNumber(f.c_roi_global_ratio),storedO=finiteNumber(f.o_roi_global_ratio);
 const c=storedC!=null?storedC:ratio(f.c_roi_mean,f.c_global_mean);
 const o=storedO!=null?storedO:ratio(f.o_roi_mean,f.o_global_mean);
 return {c,o,source:"Auto ROI"};
}
function autoPointClass(p){
 const f=p?.features||{},r=currentRatios(p),live=coRatioRuleResult(r.c,r.o);
 if(live)return live;
 const candidates=[p?.cv_result,p?.result,f.result,f.human_roi_rule_result];
 for(const v of candidates){const normalized=normalizeResultLabel(v);if(normalized)return normalized;}
 return "Ambiguous";
}
function pointClass(p){
 const human=normalizeResultLabel(p?.human_result);
 return human||autoPointClass(p);
}
function verificationResult(x){return pointClass(x)}
function resultCssClass(v){return pointClass({human_result:v,features:{}}).toLowerCase().replace(/[^a-z]+/g,"-")}

function displayScore(p){
 if(Object.prototype.hasOwnProperty.call(p||{},"evaluation_score"))return p.evaluation_score;
 const ratios=currentRatios(p),live=coRatioDisplayScore(ratios.c,ratios.o);
 if(live!=null)return live;
 const f=p?.features||{},raw=typeof f.residue_score==="number"?f.residue_score:p?.residue_score;
 return typeof raw==="number"&&Number.isFinite(raw)?Math.round((raw<=1.000001?raw*100:raw)*10)/10:null;
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
 const hasHumanROIFlag=hasHumanROI(f), liveRatios=currentRatios(p);
 const humanROIActive=hasHumanROIFlag;
 const cRatio=liveRatios.c, oRatio=liveRatios.o;
 const score=humanROIActive&&typeof cRatio==='number'&&typeof oRatio==='number'?coRatioDisplayScore(cRatio,oRatio):(humanROIActive&&typeof f.human_residue_score==='number'?Math.round(f.human_residue_score):displayScore(p));
 const coverage=humanROIActive&&typeof f.human_roi_area_px==='number'?Math.round((f.human_roi_area_px/Math.max(1,(f.human_roi_global_area_px||f.roi_area_px||1)))*100):typeof f.candidate_coverage==='number'?Math.round(f.candidate_coverage):null;
 const maskQuality=humanROIActive&&typeof f.human_roi_quality==='number'?f.human_roi_quality:(typeof f.roi_quality==='number'?f.roi_quality:null);
 const resultLabel=pointClass(p);
 const confidence=humanROIActive?"Human ROI":safeText(f.confidence||p?.confidence,"-");
 const maps={
   // If Human ROI is already saved, never fall back to the old AI/CV overlay.
   // While the fresh Human ROI overlay is loading, show the original image/map
   // instead of briefly displaying the stale AI ROI.
   sem:humanROIActive?[dynamicAsset(p,"sem_residue_overlay"),p.assets?.sem]:[dynamicAsset(p,"sem_residue_overlay"),p.assets?.sem_residue_overlay,p.assets?.sem],
   eds:[dynamicAsset(p,"eds_map"),p.assets?.eds_map,p.assets?.full_element_maps_original],
   c:humanROIActive?[dynamicAsset(p,"c_map_enhanced_overlay"),p.assets?.c_map]:[dynamicAsset(p,"c_map_enhanced_overlay"),p.assets?.c_map_enhanced_overlay,p.assets?.c_map_roi_ring,p.assets?.c_map],
   o:humanROIActive?[dynamicAsset(p,"o_map_enhanced_overlay"),p.assets?.o_map]:[dynamicAsset(p,"o_map_enhanced_overlay"),p.assets?.o_map_enhanced_overlay,p.assets?.o_map_roi_ring,p.assets?.o_map]
 };
 const fmtRatio=(x)=>x==null?"-":`${x.toFixed(2)}×`;
 const ratioClass=(x,strong)=>x==null?"neutral":x>=strong?"strong":x>=2.00?"positive":x<=0.90?"negative":"neutral";
 const ratioText=(x)=>x==null?"-":fmtRatio(x);
 const metric=(v)=>typeof v==='number'?Math.round(v*100):null;
 return <div className="verificationPanel">
  <div className="verificationTop v21Top">
   <div><b>Point {idx+1} / {total}</b><span>{safeText(p.power)} · {safeText(p.time)} · W{safeText(p.wafer)} · P{safeText(p.point)} · {safeText(p.substrate_type||f.substrate_type,"SiCN")}</span></div>
   <div className="verificationTopActions"><span className={`autoBadge ${resultCssClass(resultLabel)}`}>{resultLabel} · {p.human_result?"검증 판정":"자동 판정"}</span><button className="iconBtn" aria-label="이전 Point" onClick={prev}><ChevronLeft size={16}/></button><button className="iconBtn" aria-label="다음 Point" onClick={next}><ChevronRight size={16}/></button></div>
  </div>
  <div className="verificationLayout">
   <div className="verificationVisualColumn">
    <div className="verificationImages"><Visual title={humanROIActive?"SEM / Human ROI":"SEM / Residue ROI"} sources={maps.sem}/><Visual title="Full EDS Map" sources={maps.eds}/></div>
    <div className="v21ElementStrip">
      <div className="focusPanel"><b>{humanROIActive?"C Map (Human ROI)":"C Map (ROI)"}</b><ImageWithFallback sources={maps.c} alt={humanROIActive?"C map with Human ROI":"C map with ROI"}/></div>
      <div className="focusPanel"><b>{humanROIActive?"O Map (Human ROI)":"O Map (ROI)"}</b><ImageWithFallback sources={maps.o} alt={humanROIActive?"O map with Human ROI":"O map with ROI"}/></div>
    </div>
    <HumanRoiEditor p={p} saveHumanRoi={saveHumanRoi}/>
   </div>
   <section className="analysisResultCard" aria-label="판정 근거">
    <div className="analysisResultHead"><small>현재 판정</small><strong className={`resultTitle ${resultCssClass(resultLabel)}`}>{resultLabel}</strong></div>
    <p className="decisionBasis">{p.human_result?"사용자가 검증한 판정을 표시합니다.":"자동 분석 결과를 표시합니다."}</p>
    <div className="ratioFocus"><div><span>C ROI / Global</span><b>{fmtRatio(cRatio)}</b><small>Residue 기준 ≥ 2.40×</small></div><div><span>O ROI / Global</span><b>{fmtRatio(oRatio)}</b><small>Residue 기준 ≥ 3.00×</small></div></div>
    <div className="ratioRuleNote"><b>C와 O가 모두 기준 이상일 때 Residue</b><span>둘 다 2.00× 이상이면서 위 기준에 못 미치면 Ambiguous입니다. C/O 증가만으로 UV tape 유래를 확정하지 않습니다.</span></div>
    <div className="screeningScore"><div><span>C/O 스크리닝 점수</span><small>판정 보조 지표 · 확률 아님</small></div><b>{score==null?"—":score}<small> / 100</small></b></div>
    <div className="humanRoiStatus"><MousePointer2 size={16}/><span>{humanROIActive?"저장된 수동 ROI를 C/O 계산에 적용했습니다.":"자동 ROI를 표시합니다. 영역이 맞지 않으면 이미지 아래에서 수동 ROI를 지정하세요."}</span></div>
    <details className="verificationDetails"><summary>보조 지표와 ROI 정보</summary><div className="metricBars"><MetricBar label="SEM 형상" value={f.morphology_score} color="blue"/><MetricBar label="C 점수" value={f.c_score} color="red"/><MetricBar label="O 점수" value={f.o_score} color="green"/><MetricBar label="공간 겹침" value={typeof f.spatial_overlap==="number"?f.spatial_overlap/100:null} color="purple"/></div>
    <div className="roiInfo"><div><span>ROI 면적</span><strong>{(humanROIActive?f.human_roi_area_px:f.roi_area_px)?`${(humanROIActive?f.human_roi_area_px:f.roi_area_px).toLocaleString()} px²`:'—'}</strong></div><div><span>ROI Coverage</span><strong>{humanROIActive&&typeof f.human_roi_fill_ratio==='number'?`${Math.round(f.human_roi_fill_ratio*100)}%`:coverage==null?'—':`${coverage}%`}</strong></div><div><span>Mask Quality</span><strong>{maskQuality==null?'—':maskQuality.toFixed(2)}</strong></div><div><span>신뢰도 표시</span><strong>{confidence}</strong></div></div><p>형상과 공간 겹침은 보조 지표입니다. 사용자 판정은 C/O 점수와 다를 수 있습니다.</p></details>
   </section>
  </div>
  <div className="verificationInfo v21Info"><b>ROI 표시</b><span>AI/CV ROI를 기본으로 표시하고, Verification에서 직접 지정한 Human ROI가 있으면 그 ROI를 SEM/C/O에 반영합니다. Human ROI는 C/O 재계산과 사용자 검증 데이터로 저장됩니다. Local Ring은 판정 기준으로 사용하지 않습니다.</span></div>
  <div className="verificationButtons"><button className="danger" onClick={()=>human("Non-residue")}>Non-residue로 저장</button><button className="approve" onClick={()=>human("Residue")}>Residue로 저장</button><button className="secondary" onClick={()=>human("Skip")}><SkipForward size={14}/> 건너뛰기</button></div>
  <div className="verificationNav"><button className="secondary" onClick={prev}><ChevronLeft size={14}/> 이전</button><button className="secondary" onClick={next}>다음 <ChevronRight size={14}/></button></div>
 </div>
}
function HumanRoiEditor({p,saveHumanRoi}){
 const [open,setOpen]=useState(false), [regions,setRegions]=useState([]), [current,setCurrent]=useState([]), [drawing,setDrawing]=useState(false), [saving,setSaving]=useState(false), [src,setSrc]=useState(null);
 const canvasRef=useRef(); const wrapRef=useRef();
 useEffect(()=>{if(!open)return; const u=dynamicAsset(p,"sem")||p.assets?.sem_residue_overlay||p.assets?.sem; setSrc(imageUrl(u)); const raw=Array.isArray(p.features?.human_roi_polygons)?p.features.human_roi_polygons:(Array.isArray(p.features?.human_roi_polygon)&&p.features.human_roi_polygon.length>=3?[p.features.human_roi_polygon]:[]); const stored=raw.map(poly=>Array.isArray(poly)?poly.filter(q=>q&&Number.isFinite(Number(q.x))&&Number.isFinite(Number(q.y))).map(q=>({x:Number(q.x),y:Number(q.y)})):[]).filter(poly=>poly.length>=3); setRegions(stored); setCurrent([]); setDrawing(false);},[open,p]);
 useEffect(()=>{if(!open||!src)return; const c=canvasRef.current,w=wrapRef.current;if(!c||!w)return; const img=new Image();img.onload=()=>{const maxW=Math.max(320,w.clientWidth);const maxH=430;const scale=Math.min(maxW/img.naturalWidth,maxH/img.naturalHeight);c.width=Math.max(1,Math.round(img.naturalWidth*scale));c.height=Math.max(1,Math.round(img.naturalHeight*scale));const ctx=c.getContext("2d");ctx.clearRect(0,0,c.width,c.height);ctx.drawImage(img,0,0,c.width,c.height);
   const drawPoly=(poly,active=false)=>{if(!poly?.length)return;ctx.beginPath();poly.forEach((q,i)=>{const x=q.x*c.width,y=q.y*c.height;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});if(poly.length>1){ctx.strokeStyle="#ff2525";ctx.lineWidth=active?4:4;ctx.lineJoin="round";ctx.lineCap="round";ctx.stroke();if(!active&&poly.length>=3){ctx.beginPath();poly.forEach((q,i)=>{const x=q.x*c.width,y=q.y*c.height;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.closePath();}}};
   regions.forEach(r=>drawPoly(r,false)); drawPoly(current,true);
 };img.src=src;},[open,src,regions,current]);
 if(!open)return <div className="humanRoiBar"><button className="secondary" onClick={()=>setOpen(true)}><MousePointer2 size={14}/>{Array.isArray(p.features?.human_roi_polygon)||Array.isArray(p.features?.human_roi_polygons)?"수동 ROI 수정":"수동 ROI 지정"}</button><span>{(p.features?.human_roi_polygons?.length||0)>1?`${p.features.human_roi_polygons.length}개 Human ROI 저장됨`:Array.isArray(p.features?.human_roi_polygon)?"저장된 수동 ROI 있음":"AI/CV ROI가 틀리면 직접 지정"}</span></div>;
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
 return <div className="humanRoiEditor"><div className="humanRoiEditorHead"><b>Human ROI 직접 지정</b><span><b>그림판처럼 마우스로 residue 외곽을 따라 그리세요.</b> 마우스를 놓는 순간 선이 자동으로 닫히고 smoothing되어 하나의 ROI로 완료됩니다. 떨어진 residue가 여러 개면 다시 그려서 여러 영역을 추가할 수 있습니다.</span></div><div className="humanRoiCanvasWrap" ref={wrapRef}><canvas ref={canvasRef} className="humanRoiDrawCanvas" onPointerDown={startDraw} onPointerMove={draw} onPointerUp={endDraw} onPointerCancel={endDraw} /></div><div className="humanRoiEditorHint"><span>현재 선: {current.length}점 · 저장할 영역: {regions.length}개</span><span>빨간색=Human ROI 작성/저장 · 마우스 놓으면 자동 영역 완료 · 자동 smoothing: 약하게</span></div><div className="humanRoiActions"><button className="secondary" onClick={undo} disabled={!current.length}>↶ 마지막 선 되돌리기</button><button className="secondary" onClick={finishRegion} disabled={current.length<3}>✓ 영역 완료</button><button className="secondary" onClick={removeLastRegion} disabled={!regions.length}>− 마지막 영역 삭제</button><button className="secondary" onClick={clear}><RotateCcw size={14}/>전체 삭제</button><button className="secondary" onClick={()=>setOpen(false)}>취소</button><button className="primary" disabled={saving||(!regions.length&&current.length<3)} onClick={save}>{saving?"저장 중…":"ROI 저장"}</button></div></div>
}

function VerificationModal({p,idx,total,points,close,setIdx,condition,setCondition,wafer,setWafer,resultFilter,setResultFilter,human,saveHumanRoi}){
 const dialogRef=useDialog(close);
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
 return <div className="verificationOverlay" onClick={close}><div ref={dialogRef} className="verificationModal" role="dialog" aria-modal="true" aria-label="결과 검증" onClick={e=>e.stopPropagation()}><div className="verificationModalHead"><b>결과 검증</b><div className="verificationHeadTools"><span>{visible.length} Point 선택됨</span><button className="secondary" onClick={close}>닫기</button></div></div><div className="verificationWorkspace"><aside className="verificationQueue"><div className="verificationQueueTitle"><b>Point 탐색</b><small>조건 / 웨이퍼 / 결과별 Point 선택</small></div><label>플라즈마 조건<select value={condition} onChange={e=>setCondition(e.target.value)}><option value="ALL">전체 조건</option>{conditions.map(c=><option key={c} value={c}>{c}</option>)}</select></label><label>웨이퍼 위치<select value={wafer} onChange={e=>setWafer(e.target.value)}><option value="ALL">전체 위치</option>{wafers.map(w=><option key={w} value={String(w)}>W{w}</option>)}</select></label><label>판정<select value={resultFilter} onChange={e=>setResultFilter(e.target.value)}><option value="ALL">전체 판정</option><option value="Residue">Residue</option><option value="Ambiguous">Ambiguous</option><option value="Non-residue">Non-residue</option></select></label><div className="verificationQueueCount">{visible.length} / {points.length} points</div><div className="verificationPointList">{visible.map((x,i)=>{const gi=points.findIndex(y=>y.id===x.id);const r=verificationResult(x);return <button key={x.id} className={`verificationPointItem ${gi===idx?"active":""}`} onClick={()=>setIdx(gi)}><span>P{Number(x.point)||i+1}</span><em>{safeText(x.power)} · {safeText(x.time)} · W{safeText(x.wafer)} · {safeText(x.substrate_type,"SiCN")}</em><strong className={resultCssClass(r)}>{r}</strong></button>})}</div></aside><div className="verificationMain">{visible.length?<Review p={p} idx={currentPos} total={visible.length||total} prev={()=>goVisible(-1)} next={()=>goVisible(1)} human={human} saveHumanRoi={saveHumanRoi}/>:<div className="emptyPanel"><b>조건에 맞는 Point가 없습니다</b><small>왼쪽 조건·위치·판정 필터를 변경하세요.</small></div>}</div></div></div></div>}

function ImageWithFallback({sources,alt,className="",...props}){
 const list=[...new Set((Array.isArray(sources)?sources:[sources]).map(normalizeAssetValue).filter(Boolean))];
 const signature=list.join("|");
 const [index,setIndex]=useState(0);
 useEffect(()=>{setIndex(0)},[signature]);
 const src=index<list.length?imageUrl(list[index]):null;
 if(!src)return <div className="imageError">이미지 없음</div>;
 return <img {...props} className={className} src={src} alt={safeText(alt,"analysis image")} onError={()=>setIndex(i=>Math.min(i+1,list.length))}/>;
}
function Visual({title,sources}){
 const list=Array.isArray(sources)?sources:[sources];
 return <div className="visual panel"><div className="panelHead"><b>{title}</b><small>분석 근거 이미지</small></div><ImageWithFallback sources={list} alt={title}/></div>;
}
function Gallery({points,q,setQ,result,setResult}){
 const [selected,setSelected]=useState(null),[page,setPage]=useState(0),[view,setView]=useState("roi");
 useEffect(()=>setPage(0),[q,result]);
 const last=Math.max(0,Math.ceil(points.length/24)-1),current=Math.min(page,last);
 return <div className="content"><div className="intro"><div><h2>이미지 조회</h2><p>Point별 SEM과 원소 맵을 확인합니다. 이미지를 선택하면 상세 근거가 열립니다.</p></div><span>{points.length.toLocaleString()} Point</span></div><section className="panel"><div className="filters"><label className="search"><Search size={17}/><input aria-label="이미지 검색" value={q} onChange={e=>setQ(e.target.value)} placeholder="조건, Wafer, Point 검색"/></label><div className="galleryResultFilters">{["All","Residue","Ambiguous","Non-residue"].map(x=><button aria-pressed={result===x} className={result===x?"sel":"secondary"} onClick={()=>setResult(x)} key={x}>{x==="All"?"전체":x}</button>)}</div></div><div className="galleryToolbar"><span>Residue · 잔사 의심 / Non-residue · 기준 미충족 / Ambiguous · 판정 보류</span><label>SEM 표시<select value={view} onChange={e=>setView(e.target.value)}><option value="roi">ROI 표시</option><option value="raw">저장 원본</option></select></label></div></section>{!points.length?<section className="panel emptyPanel"><Images size={28}/><b>조건에 맞는 이미지가 없습니다</b><small>검색어 또는 판정 필터를 변경해 주세요.</small></section>:<><div className="gallery">{points.slice(current*24,(current+1)*24).map(p=><button className="tile" onClick={()=>setSelected(p)} key={p.id}><div className="tileImg"><ImageWithFallback loading="lazy" sources={view==="raw"?[p.assets?.sem]:[dynamicAsset(p,"sem_residue_overlay"),p.assets?.sem_residue_overlay,p.assets?.sem]} alt={`SEM ${p.power} ${p.time} W${p.wafer} P${p.point}`}/><em className={resultCssClass(pointClass(p))}>{pointClass(p)}</em></div><div className="tileBody"><b>{p.power} · {p.time}</b><span>W{p.wafer} · P{p.point} · {p.substrate_type||"기판 미지정"}</span></div></button>)}</div><div className="workspacePagination"><span>{current*24+1}–{Math.min((current+1)*24,points.length)} / {points.length} Point</span><div><button className="secondary" disabled={!current} onClick={()=>setPage(current-1)}><ChevronLeft size={16}/>이전</button><b>{current+1} / {last+1}</b><button className="secondary" disabled={current>=last} onClick={()=>setPage(current+1)}>다음<ChevronRight size={16}/></button></div></div></>}{selected&&<ImageLightbox p={selected} close={()=>setSelected(null)}/>}</div>
}
function imageUrl(src){
 const value=normalizeAssetValue(src);
 if(!value)return null;
 if(value.startsWith("data:")||value.startsWith("blob:")||value.startsWith("http://")||value.startsWith("https://"))return value;
 if(value.startsWith("/"))return `${API}${value}`;
 return `${API}/api/assets/${encodeURIComponent(value)}`;
}
function ImageLightbox({p,close}){
 const dialogRef=useDialog(close);
 const f=p.features||{};
 const scoreValue=displayScore(p);
 const sem=[dynamicAsset(p,"sem_residue_overlay"),p.assets?.sem_residue_overlay,p.assets?.sem];
 const eds=[dynamicAsset(p,"eds_map"),p.assets?.eds_map,p.assets?.full_element_maps_original];
 const maps=[["SE",[p.assets?.se_map_roi_ring,p.assets?.se_map]],["C",[dynamicAsset(p,"c_map_enhanced_overlay"),p.assets?.c_map_enhanced_overlay,p.assets?.c_map]],["O",[dynamicAsset(p,"o_map_enhanced_overlay"),p.assets?.o_map_enhanced_overlay,p.assets?.o_map]]];
 return <div className="lightbox" onClick={close}><div ref={dialogRef} className="lightboxCard" role="dialog" aria-modal="true" aria-label="Point 근거 이미지" onClick={e=>e.stopPropagation()}><div className="lightboxHead"><div><b>{p.power} · {p.time}</b><span>W{p.wafer} · P{p.point} · {p.zone}</span></div><button className="secondary" onClick={close}>닫기</button></div><div className="lightboxGrid"><div><small>SEM / Residue ROI</small><ImageWithFallback sources={sem} alt="SEM overlay"/></div><div><small>Full EDS Map</small><ImageWithFallback sources={eds} alt="Full EDS Map"/></div></div><div className="elementDetailGrid">{maps.map(([label,sources])=><div key={label}><small>{label} / ROI</small><ImageWithFallback sources={sources} alt={`${label} map`}/></div>)}</div><div className="lightboxResult"><b>{pointClass(p)}</b><span>Score {typeof scoreValue==="number"?scoreValue:"-"} / 100 · {p.confidence||f.confidence||"-"}</span></div></div></div>
}
function AIAnalysis({points,analysis,busy,run}){const residue=points.filter(p=>pointClass(p)==="Residue").length;const non=points.filter(p=>pointClass(p)==="Non-residue").length;return <div className="content"><div className="intro"><div><label>RESEARCH INTERPRETATION</label><h2>AI 해석</h2><p>OpenAI는 개별 Point의 분류기가 아니라, 이미 계산된 SEM/EDS 분석 결과를 연구 관점에서 해석합니다.</p></div><button className="primary" disabled={busy} onClick={run}><BrainCircuit size={14}/>{busy?"해석 중…":"AI 해석 실행"}</button></div><div className="metrics"><Metric t="Points" v={points.length} s="현재 데이터"/><Metric t="Residue" v={residue} s="판정 수"/><Metric t="Non-residue" v={non} s="판정 수"/><Metric t="Review" v={Math.max(0,points.length-residue-non)} s="판정 보류"/></div>{analysis?<div className="aiAnalysisGrid"><section className="panel"><div className="panelHead"><b>요약</b><small>저장된 결과를 바탕으로 한 해석</small></div><div className="aiBody"><p>{analysis.summary}</p><h4>주요 관찰</h4><ul>{(analysis.key_findings||[]).map((x,i)=><li key={i}>{x}</li>)}</ul></div></section><section className="panel"><div className="panelHead"><b>조건별 경향</b><small>현재 데이터 기준</small></div><div className="aiBody"><ul>{(analysis.condition_trends||[]).map((x,i)=><li key={i}>{x}</li>)}</ul><h4>추가 확인 항목</h4><ul>{(analysis.anomalies||[]).map((x,i)=><li key={i}>{x}</li>)}</ul></div></section><section className="panel"><div className="panelHead"><b>후속 검토 제안</b><small>검토를 위한 참고 제안</small></div><div className="aiBody"><ul>{(analysis.next_steps||[]).map((x,i)=><li key={i}>{x}</li>)}</ul><h4>해석 시 유의사항</h4><ul>{(analysis.caveats||[]).map((x,i)=><li key={i}>{x}</li>)}</ul></div></section></div>:<section className="panel emptyPanel"><BrainCircuit size={26}/><b>AI Analysis를 실행하세요</b><small>Residue / Non-residue 분류 자체는 CV + Human Review 결과를 사용하고, OpenAI는 조건별 경향과 이상점, 연구 해석에 사용합니다.</small><button className="primary" disabled={busy} onClick={run}><BrainCircuit size={14}/> AI 해석 실행</button></section>}</div>}

function conditionKey(p){return `${p.power||"-"} / ${p.time||"-"}`}
function conditionGroups(points){const g={};(points||[]).forEach(p=>{const k=conditionKey(p);(g[k]??=[]).push(p)});return g}
function ConditionView({points,selected,setSelected,go}){
 const groups=conditionGroups(points); const keys=Object.keys(groups); const active=selected&&selected!=="ALL"?groups[selected]||[]:points;
 const residue=active.filter(p=>pointClass(p)==="Residue").length, non=active.filter(p=>pointClass(p)==="Non-residue").length, amb=active.length-residue-non;
 const wafers=[...new Set(active.map(p=>p.wafer).filter(x=>x!=null))].sort((a,b)=>Number(a)-Number(b));
 return <div className="content"><div className="intro"><div><span className="badge"><Activity size={13}/> CONDITION VIEW</span><h2>조건별 데이터</h2><p>누적된 전체 데이터를 Power / Time 조건별로 확인합니다.</p></div><span>{active.length} Points</span></div>
 <section className="panel conditionViewToolbar"><div className="conditionViewSelect"><label>플라즈마 조건</label><select value={selected} onChange={e=>setSelected(e.target.value)}><option value="ALL">전체 조건 · {points.length} Points</option>{keys.map(k=><option key={k} value={k}>{k} · {groups[k].length} Points</option>)}</select></div><button className="secondary" onClick={()=>go("Condition Compare")}><GitCompare size={14}/> 조건·위치 비교</button></section>
 <div className="dashboardMetrics conditionMetrics"><Metric t="Points" v={active.length} s={selected==="ALL"?"전체 데이터":"선택 조건"}/><Metric t="Residue" v={residue} s={active.length?`${Math.round(residue/active.length*100)}%`:"—"}/><Metric t="Non-residue" v={non} s={active.length?`${Math.round(non/active.length*100)}%`:"—"}/><Metric t="Ambiguous" v={amb} s="추가 검토 대상"/><Metric t="Human Verified" v={active.filter(p=>!!p.human_result).length} s="사용자 검증 완료"/><Metric t="Wafers" v={wafers.length} s={wafers.map(w=>`W${w}`).join(", ")||"—"}/></div>
 <section className="panel"><div className="panelHead"><b>{selected==="ALL"?"전체 조건":"조건 · "+selected}</b><small>저장된 조건별 판정 현황 · 전체 위치 포함</small></div><div className="dashTable"><div className="dashTr dashTh"><span>플라즈마 조건</span><span>Point</span><span>Residue</span><span>Non-residue</span><span>Ambiguous</span><span>Residue 비율</span></div>{keys.map(k=>{const a=groups[k],r=a.filter(p=>pointClass(p)==="Residue").length,n=a.filter(p=>pointClass(p)==="Non-residue").length,q=a.length-r-n;return <button className={`dashTr conditionRowBtn ${selected===k?"selected": ""}`} key={k} onClick={()=>setSelected(k)}><span>{k}</span><span>{a.length}</span><span>{r}</span><span>{n}</span><span>{q}</span><strong>{a.length?Math.round(r/a.length*100):0}%</strong></button>})}</div></section>
 {selected!=="ALL"&&<section className="panel conditionPointPanel"><div className="panelHead"><b>{selected} · Point List</b><small>Point를 클릭하면 Verification에서 해당 결과를 확인합니다.</small></div><div className="conditionPointGrid">{active.map(p=>{const c=pointClass(p);return <button className={`conditionPointCard ${resultCssClass(c).replace(/-/g,"")}`} key={p.id} onClick={()=>{const i=points.findIndex(x=>x.id===p.id);if(i>=0){go("Dashboard");setTimeout(()=>window.dispatchEvent(new CustomEvent("uvtape:open-point",{detail:{index:i}})),0)}}}><b>P{p.point}</b><span>W{p.wafer} · {p.power} · {p.time}</span><strong>{c}</strong><small>Score {displayScore(p)==null?"-":displayScore(p)}</small></button>})}</div></section>}
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
 const groups=conditionGroups(points), keys=Object.keys(groups);
 const active=Array.isArray(selected)?selected:[];
 const effectiveKeys=active.length?active:keys;
 const locationDefs=[{wafer:1,label:"Corner"},{wafer:4,label:"Edge"},{wafer:5,label:"Middle"}];
 const isMain=p=>[1,4,5].includes(Number(p?.wafer));
 const substrateOf=p=>safeText(p?.substrate_type||p?.features?.substrate_type,"SiCN");
 const makeRow=k=>{
   const all=groups[k]||[],a=all.filter(isMain);
   const r=a.filter(p=>pointClass(p)==="Residue").length,n=a.filter(p=>pointClass(p)==="Non-residue").length,q=a.length-r-n;
   const locations=locationDefs.map(loc=>{const pts=a.filter(p=>Number(p.wafer)===loc.wafer),rr=pts.filter(p=>pointClass(p)==="Residue").length,nn=pts.filter(p=>pointClass(p)==="Non-residue").length,qq=pts.length-rr-nn;return {...loc,pts,r:rr,n:nn,q:qq,rate:pts.length?compareRate(rr,pts.length):null}});
   const valid=locations.filter(x=>x.rate!=null),spread=valid.length>1?Math.max(...valid.map(x=>x.rate))-Math.min(...valid.map(x=>x.rate)):null;
   const worst=valid.length?[...valid].sort((x,y)=>y.rate-x.rate)[0]:null;
   const verified=a.filter(p=>!!p.human_result).length;
   const ref9=all.filter(p=>Number(p.wafer)===9);
   const other=all.filter(p=>![1,4,5,9].includes(Number(p.wafer)));
   return {k,all,a,r,n,q,rate:compareRate(r,a.length),ambRate:compareRate(q,a.length),verified,coverage:Math.min(100,compareRate(a.length,27)),locations,spread,worst,ref9,other,excluded:all.length-a.length,waf:[...new Set(a.map(p=>Number(p.wafer)))].sort((x,y)=>x-y)};
 };
 const rows=effectiveKeys.map(makeRow);
 const ranked=[...rows].filter(x=>x.a.length).sort((a,b)=>a.rate-b.rate||a.ambRate-b.ambRate||a.k.localeCompare(b.k));
 const lowest=ranked[0]||null;
 const mostUniform=[...rows].filter(x=>x.spread!=null).sort((a,b)=>a.spread-b.spread)[0]||null;
 const mostSensitive=[...rows].filter(x=>x.spread!=null).sort((a,b)=>b.spread-a.spread)[0]||null;
 const toggle=k=>setSelected(active.includes(k)?active.filter(x=>x!==k):[...active,k]);
 const pair=rows.length===2?rows:null;
 const transition=pair?(()=>{const bm=new Map(pair[1].a.map(p=>[comparePointKey(p),p])),trans={},changed=[];pair[0].a.forEach(pa=>{const pb=bm.get(comparePointKey(pa));if(!pb)return;const from=pointClass(pa),to=pointClass(pb),key=`${from} → ${to}`;trans[key]=(trans[key]||0)+1;if(from!==to)changed.push({wafer:pa.wafer,point:pa.point,from,to})});return {trans,changed,matched:Object.values(trans).reduce((sum,v)=>sum+v,0)}})():null;
 const powers=[...new Set(rows.map(x=>x.k.split(" / ")[0]))].sort((a,b)=>Number(a.replace(/\D/g,""))-Number(b.replace(/\D/g,"")));
 const times=[...new Set(rows.map(x=>x.k.split(" / ")[1]))].sort((a,b)=>Number(a.replace(/\D/g,""))-Number(b.replace(/\D/g,"")));
 const w9Points=effectiveKeys.flatMap(k=>(groups[k]||[]).filter(p=>Number(p.wafer)===9));
 const w9Map=new Map();
 w9Points.forEach(p=>{const key=`${conditionKey(p)}|||${substrateOf(p)}`;(w9Map.get(key)||w9Map.set(key,[]).get(key)).push(p)});
 const w9Rows=[...w9Map.entries()].map(([key,a])=>{const [condition,substrate]=key.split("|||");const r=a.filter(p=>pointClass(p)==="Residue").length,n=a.filter(p=>pointClass(p)==="Non-residue").length,q=a.length-r-n;return {condition,substrate,a,r,n,q,rate:compareRate(r,a.length),ambRate:compareRate(q,a.length),verified:a.filter(p=>!!p.human_result).length}}).sort((a,b)=>a.substrate.localeCompare(b.substrate)||a.condition.localeCompare(b.condition));
 const w9Substrates=[...new Set(w9Rows.map(x=>x.substrate))];
 const otherPoints=effectiveKeys.flatMap(k=>(groups[k]||[]).filter(p=>![1,4,5,9].includes(Number(p.wafer))));
 const printReport=()=>{
   const refRows=w9Rows.length?`<h2>W9 Reference / Other (excluded from main ranking)</h2><table><tr><th>Condition</th><th>Substrate</th><th>N</th><th>Residue</th><th>Non</th><th>Ambiguous</th><th>Residue %</th></tr>${w9Rows.map(x=>`<tr><td>${compareEsc(x.condition)}</td><td>${compareEsc(x.substrate)}</td><td>${x.a.length}</td><td>${x.r}</td><td>${x.n}</td><td>${x.q}</td><td>${x.rate.toFixed(1)}%</td></tr>`).join("")}</table>`:"";
   const html=`<html><head><title>Engineering Condition Comparison</title><style>@page{size:A4 landscape;margin:9mm}body{font-family:Arial,"Malgun Gothic",sans-serif;color:#172236}h1{font-size:22px;margin:0 0 4px}h2{font-size:14px;margin:18px 0 8px}.muted{font-size:10px;color:#68768a}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.card{border:1px solid #d7e0e8;border-radius:8px;padding:9px}.big{font-size:18px;font-weight:800}table{width:100%;border-collapse:collapse;font-size:9px}th,td{border:1px solid #d7e0e8;padding:5px;text-align:center}th{background:#eef3f7}.note{font-size:9px;line-height:1.5;color:#59687a}</style></head><body><h1>UV Tape Residue - Engineering Condition Comparison</h1><div class="muted">MAIN ranking: W1 Corner / W4 Edge / W5 Middle only. W9 is reference/other and is never mixed into the MAIN rate.</div><div class="cards"><div class="card"><b>Lowest observed residue</b><div class="big">${lowest?compareEsc(lowest.k):"-"}</div><div>${lowest?`${lowest.r}/${lowest.a.length} (${lowest.rate.toFixed(1)}%)`:"-"}</div></div><div class="card"><b>Smallest location spread</b><div class="big">${mostUniform?compareEsc(mostUniform.k):"-"}</div><div>${mostUniform&&mostUniform.spread!=null?mostUniform.spread.toFixed(1)+"%p":"-"}</div></div><div class="card"><b>Classification rule</b><div class="big">C 2.40x / O 3.00x</div><div>Human Verified result has priority</div></div></div><h2>MAIN condition summary</h2><table><tr><th>Condition</th><th>N</th><th>Residue</th><th>Non-residue</th><th>Ambiguous</th><th>W1 Corner</th><th>W4 Edge</th><th>W5 Middle</th><th>Spread</th><th>Coverage</th></tr>${rows.map(x=>`<tr><td>${compareEsc(x.k)}</td><td>${x.a.length}</td><td>${x.r} (${x.rate.toFixed(1)}%)</td><td>${x.n} (${compareRate(x.n,x.a.length).toFixed(1)}%)</td><td>${x.q} (${x.ambRate.toFixed(1)}%)</td>${x.locations.map(z=>`<td>${z.pts.length?`${z.r}/${z.pts.length} (${z.rate.toFixed(1)}%)`:"-"}</td>`).join("")}<td>${x.spread==null?"-":x.spread.toFixed(1)+"%p"}</td><td>${x.coverage.toFixed(0)}%</td></tr>`).join("")}</table>${refRows}<div class="note" style="margin-top:12px">Average score is intentionally excluded. MAIN coverage target is 27 points per condition (W1/W4/W5 x P1-P9). W9 Si/other substrates are reference datasets and remain separate.</div></body></html>`;
   const w=window.open("","_blank");if(w){w.document.write(html);w.document.close();setTimeout(()=>w.print(),400)};
 };
 const locationCell=(x,wafer)=>{const z=x.locations.find(v=>v.wafer===wafer);return z?.pts.length?`${z.r}/${z.pts.length} · ${z.rate.toFixed(1)}%`:"—"};
 return <div className="content">
   <div className="intro"><div><span className="badge"><GitCompare size={13}/> ENGINEERING COMPARE</span><h2>Plasma Condition & Wafer Position</h2><p>MAIN 평가는 W1 Corner / W4 Edge / W5 Middle만 사용합니다. W9 및 다른 wafer는 Reference / Other로 완전히 분리합니다.</p></div><span>{rows.length} conditions</span></div>
   <section className="panel comparePicker"><div className="panelHead"><div><b>Analysis Scope</b><small>선택이 없으면 모든 조건을 비교합니다. MAIN ranking에는 W1/W4/W5만 포함되고 W9는 별도 Reference 표에서만 비교됩니다.</small></div><div style={{display:"flex",gap:7}}><button className="secondary" onClick={()=>setSelected([])}>전체 조건</button><button className="secondary" onClick={printReport}><FileText size={13}/> Quick PDF / Print</button></div></div><div className="compareChoices">{keys.map(k=>{const main=(groups[k]||[]).filter(isMain).length,ref=(groups[k]||[]).filter(p=>Number(p.wafer)===9).length;return <button className={`compareChoice ${active.includes(k)?"selected":""}`} key={k} onClick={()=>toggle(k)}><b>{k}</b><span>MAIN {main} · W9 Ref {ref}</span><i>{active.includes(k)?"✓":"+"}</i></button>})}</div></section>
   <div className="engineeringKpis">
     <div className="engineeringKpi"><small>Lowest MAIN residue</small><b>{lowest?.k||"—"}</b><span>{lowest?`${lowest.r}/${lowest.a.length} · ${lowest.rate.toFixed(1)}%`:"No MAIN data"}</span></div>
     <div className="engineeringKpi"><small>Smallest W1/W4/W5 spread</small><b>{mostUniform?.k||"—"}</b><span>{mostUniform&&mostUniform.spread!=null?`${mostUniform.spread.toFixed(1)}%p max-min`:"Need 2+ locations"}</span></div>
     <div className="engineeringKpi"><small>W9 reference datasets</small><b>{w9Points.length} points</b><span>{w9Substrates.length?w9Substrates.join(" / "):"No W9 data"}</span></div>
     <div className="engineeringKpi"><small>Evaluation rule</small><b>C 2.40× + O 3.00×</b><span>Human Verified takes priority</span></div>
   </div>
   <section className="panel"><div className="panelHead"><b>1. Plasma Condition Comparison · MAIN</b><small>W1/W4/W5 only. Primary KPI = Residue incidence; lower is better. W9 is excluded.</small></div><div className="engineeringTableWrap"><table className="engineeringTable"><thead><tr><th>Rank</th><th>Condition</th><th>Measured</th><th>Residue</th><th>Non-residue</th><th>Ambiguous</th><th>Coverage</th><th>Human Verified</th></tr></thead><tbody>{ranked.map((x,i)=><tr key={x.k}><td>{i+1}</td><th>{x.k}</th><td>{x.a.length}/27</td><td className="residueCell"><b>{x.r}/{x.a.length}</b><span>{x.rate.toFixed(1)}%</span></td><td><b>{x.n}/{x.a.length}</b><span>{compareRate(x.n,x.a.length).toFixed(1)}%</span></td><td className="ambCell"><b>{x.q}/{x.a.length}</b><span>{x.ambRate.toFixed(1)}%</span></td><td>{x.coverage.toFixed(0)}%</td><td>{x.verified}/{x.a.length}</td></tr>)}</tbody></table></div></section>
   <section className="panel"><div className="panelHead"><b>Power × Time Matrix · MAIN</b><small>W1/W4/W5 데이터만 사용. 각 셀은 Residue count / measured count · Residue rate입니다.</small></div><div className="engineeringTableWrap"><table className="engineeringTable matrixTable"><thead><tr><th>Power \ Time</th>{times.map(t=><th key={t}>{t}</th>)}</tr></thead><tbody>{powers.map(pwr=><tr key={pwr}><th>{pwr}</th>{times.map(t=>{const x=rows.find(r=>r.k===`${pwr} / ${t}`);return <td key={t}>{x&&x.a.length?<><b>{x.r}/{x.a.length}</b><span>{x.rate.toFixed(1)}%</span></>:"—"}</td>})}</tr>)}</tbody></table></div></section>
   <section className="panel"><div className="panelHead"><b>2. Wafer Position Comparison · MAIN</b><small>W1 = Corner · W4 = Edge · W5 = Middle. W9는 이 표에 포함하지 않습니다.</small></div><div className="engineeringTableWrap"><table className="engineeringTable"><thead><tr><th>Condition</th><th>W1 · Corner</th><th>W4 · Edge</th><th>W5 · Middle</th><th>Location Spread</th><th>Worst Location</th></tr></thead><tbody>{rows.map(x=><tr key={x.k}><th>{x.k}</th><td>{locationCell(x,1)}</td><td>{locationCell(x,4)}</td><td>{locationCell(x,5)}</td><td><b>{x.spread==null?"—":`${x.spread.toFixed(1)}%p`}</b></td><td>{x.worst?`${x.worst.label} · ${x.worst.rate.toFixed(1)}%`:"—"}</td></tr>)}</tbody></table></div></section>
   <section className="engineeringLocationGrid">{locationDefs.map(loc=><section className="panel" key={loc.wafer}><div className="panelHead"><b>W{loc.wafer} · {loc.label}</b><small>조건별 MAIN Residue incidence</small></div><div className="locationRankList">{[...rows].map(x=>({x,z:x.locations.find(v=>v.wafer===loc.wafer)})).sort((a,b)=>(a.z?.rate??999)-(b.z?.rate??999)).map(({x,z})=><div className="locationRank" key={x.k}><span>{x.k}</span><div><i style={{width:`${Math.min(100,z?.rate||0)}%`}}/></div><strong>{z?.pts.length?`${z.r}/${z.pts.length} · ${z.rate.toFixed(1)}%`:"—"}</strong></div>)}</div></section>)}</section>
   <section className="panel referencePanel"><div className="panelHead"><div><b>3. W9 Reference / Other</b><small>MAIN 조건 ranking과 완전히 분리합니다. W9는 substrate별로만 서로 비교합니다.</small></div><span>{w9Points.length} W9 points</span></div>{w9Rows.length?<div className="engineeringTableWrap"><table className="engineeringTable"><thead><tr><th>Condition</th><th>W9 Substrate</th><th>Measured</th><th>Residue</th><th>Non-residue</th><th>Ambiguous</th><th>Residue %</th><th>Human Verified</th></tr></thead><tbody>{w9Rows.map(x=><tr key={`${x.condition}-${x.substrate}`}><th>{x.condition}</th><td><b>{x.substrate}</b></td><td>{x.a.length}</td><td className="residueCell"><b>{x.r}/{x.a.length}</b></td><td>{x.n}/{x.a.length}</td><td className="ambCell">{x.q}/{x.a.length}</td><td><b>{x.rate.toFixed(1)}%</b></td><td>{x.verified}/{x.a.length}</td></tr>)}</tbody></table></div>:<div className="emptyInline">아직 W9 Reference 데이터가 없습니다. 앞으로 W9에 Si substrate를 지정해서 업로드하면 이 영역에 Si 데이터끼리만 모입니다.</div>}<div className="referenceFoot"><span>W9 substrate groups: <b>{w9Substrates.length?w9Substrates.join(", "):"—"}</b></span><span>W2/W3/W6/W7/W8 등 기타 wafer: <b>{otherPoints.length} points</b></span></div></section>
   <section className="panel"><div className="panelHead"><b>Data Completeness / Review Status · MAIN</b><small>조건별 27 Point(W1/W4/W5 × P1-P9) 기준. W9/Other는 coverage에 포함하지 않습니다.</small></div><div className="engineeringTableWrap"><table className="engineeringTable"><thead><tr><th>Condition</th><th>MAIN Coverage</th><th>W1</th><th>W4</th><th>W5</th><th>Ambiguous</th><th>W9 Ref</th><th>Other</th></tr></thead><tbody>{rows.map(x=><tr key={x.k}><th>{x.k}</th><td>{x.a.length}/27 · {x.coverage.toFixed(0)}%</td>{[1,4,5].map(w=>{const z=x.locations.find(v=>v.wafer===w);return <td key={w}>{z?.pts.length||0}/9</td>})}<td>{x.q} · {x.ambRate.toFixed(1)}%</td><td>{x.ref9.length}</td><td>{x.other.length}</td></tr>)}</tbody></table></div></section>
   {pair&&transition&&<section className="panel"><div className="panelHead"><b>Selected Pair · Same W/P Transition</b><small>{pair[0].k} → {pair[1].k} · 동일 W1/W4/W5 + P 위치만 직접 비교</small></div><div className="transitionGrid">{Object.entries(transition.trans).sort((a,b)=>b[1]-a[1]).map(([k,v])=><div className="transitionCard" key={k}><b>{k}</b><strong>{v}</strong><span>{transition.matched?compareRate(v,transition.matched).toFixed(1):0}%</span></div>)}</div>{transition.changed.length>0&&<div className="engineeringTableWrap"><table className="engineeringTable"><thead><tr><th>Wafer</th><th>Point</th><th>From</th><th>To</th></tr></thead><tbody>{transition.changed.map((x,i)=><tr key={i}><td>W{x.wafer}</td><td>P{x.point}</td><td>{x.from}</td><td>{x.to}</td></tr>)}</tbody></table></div>}</section>}
   <section className="panel engineeringNote"><div className="panelHead"><b>Engineering Interpretation Guide</b><small>공정 조건 선정 시 우선 볼 항목</small></div><div className="engineeringGuide"><div><b>① MAIN Residue incidence</b><span>W1/W4/W5의 실제 Residue 개수/측정 개수를 조건 선정의 1순위로 봅니다.</span></div><div><b>② Spatial robustness</b><span>Corner / Edge / Middle 차이가 작은 조건인지 확인합니다. Spread가 크면 위치 민감도가 큽니다.</span></div><div><b>③ W9 reference</b><span>Si 등 W9 reference substrate는 MAIN과 섞지 않고 같은 substrate끼리 조건별로 비교합니다.</span></div></div></section>
 </div>
}

function DeleteData({projectList,currentProject,deleteProject,busy,reanalyze}){
 const list=Array.isArray(projectList)?projectList:[];
 return <div className="content"><div className="intro"><div><h2>데이터 관리</h2><p>업로드 배치와 저장 파일을 관리합니다. 삭제한 데이터는 복구할 수 없습니다.</p></div></div>{currentProject&&<section className="panel maintenancePanel"><div><b>현재 배치 재분석</b><p>선택된 최근 배치의 저장 자료로 분석을 다시 실행합니다.</p></div><button className="secondary" onClick={reanalyze} disabled={busy}><RotateCcw size={15}/>재분석</button></section>}<section className="panel deleteDataPanel"><div className="panelHead"><b>저장된 업로드 배치</b><small>업로드한 PDF별 저장 내역입니다. 대시보드와 결과 검증은 모든 배치를 함께 표시합니다.</small></div>{!list.length?<div className="deleteEmpty">저장된 업로드 배치가 없습니다.</div>:<div className="deleteList">{list.map((x,i)=><div className={`deleteRow ${x.id===currentProject?"current":""}`} key={x.id}><div className="deleteMeta"><b>{x.name||"UV Tape Residue"}{x.id===currentProject&&<span className="currentTag">현재 배치</span>}</b><span>{x.created_at?new Date(x.created_at).toLocaleString("ko-KR"):"-"} · {x.point_count||0} points</span><small>{(x.files||[]).join(", ")||"source PDF"}</small></div><button className="danger" disabled={busy} onClick={()=>deleteProject(x.id,x)}><Trash2 size={13}/> 배치 삭제</button></div>)}</div>}</section></div>
}
function Reports({exportFile,exportWorkspaceFile,project,points}){return <div className="content"><div className="intro"><div><span className="badge"><FileText size={13}/> ENGINEERING REPORT</span><h2>Reports & Export</h2><p>조건 비교용 요약 리포트와 Point 상세 결과를 분리해서 출력합니다.</p></div></div><div className="reportSectionTitle"><b>Condition / Wafer Position Summary</b><span>MAIN: W1 Corner / W4 Edge / W5 Middle · W9 Reference는 substrate별 별도 집계</span></div><div className="reportGrid engineeringReports"><Report t="Engineering Summary PPT" d="MAIN 조건별 Residue incidence, Power×Time matrix, W1/W4/W5 위치 비교 + W9 substrate별 Reference" a={()=>exportWorkspaceFile("summary-ppt")} disabled={!points.length}/><Report t="Engineering Summary PDF" d="회사 보고용 5-page summary. MAIN 조건/위치 + W9 Reference 분리" a={()=>exportWorkspaceFile("summary-pdf")} disabled={!points.length}/></div><div className="reportSectionTitle secondaryReportTitle"><b>Point Detail</b><span>현재 Project의 SEM / EDS 상세 원본 결과</span></div><div className="reportGrid"><Report t="Point PPT" d="SEM / Spectrum / EDS Map / Element Maps / Data / Result" a={()=>exportFile("ppt")} disabled={!project}/><Report t="Point PDF" d="동일 레이아웃으로 1 Point = 1 Page" a={()=>exportFile("pdf")} disabled={!project}/><Report t="Analysis JSON" d="현재 Project 분석 데이터 export" a={()=>exportFile("json")} disabled={!project}/></div></div>}
function Report({t,d,a,disabled}){return <div className="reportCard"><FileText size={20}/><b>{t}</b><p>{d}</p><button className="secondary" disabled={disabled} onClick={a}><Download size={14}/> Export</button></div>}
