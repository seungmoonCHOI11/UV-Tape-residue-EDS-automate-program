"use client";
import {useEffect,useRef,useState} from "react";
import {Upload,LayoutDashboard,BrainCircuit,Images,GitCompare,FileText,Settings,FileUp,Play,Check,Download,ChevronLeft,ChevronRight,SkipForward,Search,Database,Activity} from "lucide-react";

const API=process.env.NEXT_PUBLIC_API_BASE_URL||"https://uv-tape-residue-eds-backend.onrender.com";
export default function App(){
 const [page,setPage]=useState("Dashboard"),[points,setPoints]=useState([]),[project,setProject]=useState(null),[files,setFiles]=useState([]),[idx,setIdx]=useState(0),[msg,setMsg]=useState(""),input=useRef();
 const [q,setQ]=useState(""),[result,setResult]=useState("All");
 const notify=x=>{setMsg(x);setTimeout(()=>setMsg(""),2200)};
 useEffect(()=>{(async()=>{try{const r=await fetch(`${API}/api/projects/latest`);if(!r.ok)return;const j=await r.json();if(j?.project_id){setProject(j.project_id);setPoints(j.points||[]);}}catch{}})()},[]);
 async function upload(){if(!files.length)return notify("EDS 파일을 먼저 선택하세요.");const fd=new FormData();files.forEach(f=>fd.append("files",f));fd.append("condition_count","7");try{const r=await fetch(`${API}/api/upload`,{method:"POST",body:fd});if(!r.ok)throw Error(await r.text());const j=await r.json();setProject(j.project_id);setPoints(j.points.map(x=>({...x,ai_result:x.features?.result,human_result:null})));notify(`${j.count} points 분석 완료`);setPage("AI Review")}catch(e){notify("Backend 연결 실패: "+e.message)}}
 async function human(v){const p=points[idx];try{const r=await fetch(`${API}/api/points/${p.id}/human?result=${encodeURIComponent(v)}`,{method:"POST"});if(r.ok){const j=await r.json();setPoints(x=>x.map(z=>z.id===p.id?j:z));}}catch{}setIdx(Math.min(points.length-1,idx+1))}
 async function runAI(){const p=points[idx];notify("OpenAI 분석 요청 중...");try{const r=await fetch(`${API}/api/points/${p.id}/ai`,{method:"POST"});const j=await r.json();if(j.result){setPoints(x=>x.map(z=>z.id===p.id?{...z,ai_result:j.result,ai_confidence:j.confidence,ai_rationale:j.rationale}:z));notify("OpenAI 결과 반영 완료")}else notify(j.message||"AI 결과 없음")}catch(e){notify("AI endpoint 오류: "+e.message)}}
 async function exportFile(kind){if(!project)return notify("먼저 실제 PDF를 업로드해 project를 생성하세요.");const r=await fetch(`${API}/api/projects/${project}/export/${kind}`,{method:"POST"});if(!r.ok)return notify("Export 실패");const blob=await r.blob(),u=URL.createObjectURL(blob),a=document.createElement("a");a.href=u;a.download=`point_report.${kind==="ppt"?"pptx":kind}`;a.click();URL.revokeObjectURL(u);notify(`${kind.toUpperCase()} export 완료`)}
 const p=points[idx],filtered=points.filter(x=>(result==="All"||(x.human_result||x.ai_result)===result)&&Object.values(x).join(" ").toLowerCase().includes(q.toLowerCase()));
 return <div className="app"><aside><div className="logo"><b>EDS</b><span>Insight Lab</span></div><div className="project"><small>PROJECT</small><strong>{project?"UV Tape Residue":"No project selected"}</strong><span>{points.length} points</span></div>{[["Dashboard",LayoutDashboard],["New Analysis",Upload],["AI Review",BrainCircuit],["Image Gallery",Images],["Condition Compare",GitCompare],["Reports",FileText]].map(([n,I])=><button className={page===n?"nav active":"nav"} key={n} onClick={()=>setPage(n)}><I size={16}/>{n}</button>)}<div className="sideBottom"><button className="nav"><Settings size={16}/>Settings</button></div></aside><main><header><div><small>Projects / UV Tape Residue / {page}</small><h1>{page}</h1></div><span className="ready">● {points.length?"Data loaded":"Ready"}</span></header>
 {page==="Dashboard"&&<Dashboard points={points} go={setPage}/>}
 {page==="New Analysis"&&<UploadPage input={input} files={files} setFiles={setFiles} upload={upload}/>}
 {page==="AI Review"&&(p?<Review p={p} idx={idx} total={points.length} prev={()=>setIdx(Math.max(0,idx-1))} next={()=>setIdx(Math.min(points.length-1,idx+1))} human={human} runAI={runAI}/>:<Empty title="분석 데이터가 없습니다" text="EDS PDF를 업로드하면 Point별 분석 결과가 이 화면에 표시됩니다." go={()=>setPage("New Analysis")}/>)}
 {page==="Image Gallery"&&<Gallery points={filtered} q={q} setQ={setQ} result={result} setResult={setResult} open={p=>{setIdx(points.findIndex(x=>x.id===p.id));setPage("AI Review")}}/>}
 {page==="Condition Compare"&&<Compare points={points}/>}
 {page==="Reports"&&<Reports exportFile={exportFile} project={project}/>}
 </main>{msg&&<div className="toast"><Check size={14}/>{msg}</div>}</div>
}
function Dashboard({points,go}){
 let r=points.filter(p=>(p.human_result||p.ai_result)==="Residue").length;
 const hasData=points.length>0;
 return <div className="content">
   <section className="hero">
     <div>
       <span className="badge"><Activity size={13}/> SEM / EDS ANALYSIS</span>
       <h2>{hasData?<>Analysis Overview<br/><em>Current Dataset</em></>:<>SEM / EDS<br/><em>Residue Analysis</em></>}</h2>
       <p>{hasData?"업로드된 실험 데이터를 기반으로 Point별 분석, 검토 및 결과 비교를 수행합니다.":"EDS 분석 데이터를 업로드하여 Point별 SEM/EDS 결과를 분석하고 검토하세요."}</p>
     </div>
     <button className="primary" onClick={()=>go("New Analysis")}><Upload size={15}/> New Analysis</button>
   </section>
   <div className="metrics">
     <Metric t="Points" v={points.length} s={hasData?"analyzed points":"no data"}/>
     <Metric t="Residue" v={r} s={hasData?`${Math.round(r/points.length*100)}% of points`:"—"}/>
     <Metric t="Human verified" v={points.filter(p=>p.human_result).length} s="reviewed"/>
     <Metric t="Analysis" v={hasData?"Active":"Ready"} s={hasData?"dataset loaded":"awaiting upload"}/>
   </div>
   {hasData?
     <section className="panel">
       <div className="panelHead"><b>Analysis Workflow</b><small>Current dataset</small></div>
       <div className="pipeline">{["Source","Point Extraction","Image Analysis","AI Review","Human Review","Comparison","Report"].map((x,i)=><div key={x}><span>{i+1}</span><b>{x}</b></div>)}</div>
     </section>
     :
     <section className="panel emptyPanel">
       <Database size={24}/>
       <b>분석 데이터가 없습니다</b>
       <small>EDS PDF를 업로드하면 분석 결과와 이미지가 이 대시보드에 표시됩니다.</small>
       <button className="secondary" onClick={()=>go("New Analysis")}><Upload size={14}/> EDS 데이터 업로드</button>
     </section>
   }
 </div>
}
function Empty({title,text,go}){return <div className="content"><section className="panel emptyPanel"><Database size={24}/><b>{title}</b><small>{text}</small>{go&&<button className="secondary" onClick={go}><Upload size={14}/> New Analysis</button>}</section></div>}
function Metric({t,v,s}){return <div className="metric"><small>{t}</small><b>{v}</b><span>{s}</span></div>}
function UploadPage({input,files,setFiles,upload}){return <div className="content"><div className="intro"><div><h2>New Analysis</h2><p>실제 EDS PDF를 업로드하면 Python 분석 엔진이 페이지를 분해합니다.</p></div></div><section className="panel"><div className="panelHead"><b>EDS source upload</b><small>PDF / CSV / XLSX</small></div><div className="drop" onClick={()=>input.current.click()}><FileUp size={30}/><b>EDS PDF를 선택하세요</b><small>현재 표준 Point Report 레이아웃을 자동 분석합니다.</small><input ref={input} hidden type="file" multiple accept=".pdf,.csv,.xlsx" onChange={e=>setFiles([...e.target.files])}/></div>{files.map(f=><div className="file" key={f.name}><FileText size={14}/><span>{f.name}</span></div>)}<div className="actions"><button className="primary" onClick={upload}><Play size={14}/> Upload + Analyze</button></div></section></div>}
function Review({p,idx,total,prev,next,human,runAI}){return <div className="content"><div className="intro"><div><label>HUMAN-IN-THE-LOOP</label><h2>AI Review</h2><p>{p.power} / {p.time} · W{p.wafer} / P{p.point} · {p.zone}</p></div><span>{idx+1} / {total}</span></div><div className="reviewGrid"><Visual title="SEM" src={p.assets?.sem}/><Visual title="EDS / Element Map" src={p.assets?.element_maps}/></div><section className="decision panel"><div><small>AI DECISION</small><h2>{p.ai_result||"Not analyzed"}</h2><p>CV residue score: {p.residue_score?.toFixed?.(2)||"-"} · confidence: {p.ai_confidence||p.confidence||"-"}</p></div><div className="decBtns"><button className="danger" onClick={()=>human("Non-residue")}>Non-residue</button><button className="approve" onClick={()=>human("Residue")}>Residue</button><button className="secondary" onClick={next}><SkipForward size={14}/> Skip</button><button className="secondary" onClick={runAI}><BrainCircuit size={14}/> OpenAI</button></div></section><div className="reviewNav"><button className="secondary" onClick={prev}><ChevronLeft size={14}/> Previous</button><button className="secondary" onClick={next}>Next <ChevronRight size={14}/></button></div></div>}
function Visual({title,src}){const url=src?(src.startsWith("data:")||src.startsWith("http")?src:src.startsWith("/")?`${API}${src}`:`${API}/static/${src}`):null;return <div className="visual panel"><div className="panelHead"><b>{title}</b><small>extracted asset</small></div>{url?<img src={url} />:<div className="placeholder">실제 업로드 후 추출 이미지 표시</div>}</div>}
function Gallery({points,q,setQ,result,setResult,open}){return <div className="content"><div className="intro"><div><h2>Image Gallery</h2><p>Point별 결과를 확인합니다.</p></div></div><section className="panel"><div className="filters"><div className="search"><Search size={14}/><input value={q} onChange={e=>setQ(e.target.value)} placeholder="검색"/></div>{["All","Residue","Non-residue"].map(x=><button className={result===x?"sel":"secondary"} onClick={()=>setResult(x)} key={x}>{x}</button>)}</div></section><div className="gallery">{points.slice(0,60).map(p=><div className="tile" onClick={()=>open(p)} key={p.id}><div className="tileImg"><span>SEM</span><em>{p.human_result||p.ai_result}</em></div><div className="tileBody"><b>{p.power} · {p.time}</b><span>W{p.wafer} · P{p.point} · {p.zone}</span></div></div>)}</div></div>}
function Compare({points}){let g={};points.forEach(p=>{let k=`${p.power} / ${p.time}`;(g[k]??=[]).push(p)});return <div className="content"><div className="intro"><div><h2>Condition Comparison</h2><p>조건별 결과 분포.</p></div></div><section className="panel"><div className="table">{Object.entries(g).map(([k,a])=>{let r=a.filter(p=>(p.human_result||p.ai_result)==="Residue").length;return <div className="tr" key={k}><b>{k}</b><span>{a.length}</span><strong>{Math.round(r/a.length*100)}%</strong><span>Avg score {(a.reduce((s,p)=>s+(p.residue_score||0),0)/a.length).toFixed(2)}</span></div>})}</div></section></div>}
function Reports({exportFile,project}){return <div className="content"><div className="intro"><div><h2>Reports & Export</h2><p>네가 보여준 1 Point = 1 Page / Slide 레이아웃.</p></div></div><div className="reportGrid"><Report t="Point PPT" d="SEM / Spectrum / EDS Map / Element Maps / Data / Result" a={()=>exportFile("ppt")} disabled={!project}/><Report t="Point PDF" d="동일 레이아웃으로 1 Point = 1 Page" a={()=>exportFile("pdf")} disabled={!project}/><Report t="Analysis JSON" d="전체 분석 데이터 export" a={()=>exportFile("json")} disabled={!project}/></div></div>}
function Report({t,d,a,disabled}){return <div className="reportCard"><FileText size={20}/><b>{t}</b><p>{d}</p><button className="secondary" disabled={disabled} onClick={a}><Download size={14}/> Export</button></div>}
