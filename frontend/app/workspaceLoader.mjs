export async function fetchWorkspacePages(api, fetcher=fetch){
 const points=new Map();let offset=0,last={};
 for(let page=0;page<1000;page++){
  let data,error;
  for(let attempt=0;attempt<3;attempt++){
   try{const r=await fetcher(`${api}/api/workspace?offset=${offset}&limit=60`,{cache:'no-store',signal:AbortSignal.timeout(45000)});if(!r.ok)throw Error(`HTTP ${r.status}`);data=await r.json();if(!Array.isArray(data.points))throw Error('Invalid workspace response');error=null;break}catch(e){error=e;if(attempt<2)await new Promise(r=>setTimeout(r,1000*(attempt+1)))}
  }
  if(error)throw error;
  for(const p of data.points)points.set(p.id,p);last=data;
  if(!data.has_more)return {...last,points:[...points.values()]};
  if(!Number.isInteger(data.next_offset)||data.next_offset<=offset)throw Error('Invalid workspace page cursor');
  offset=data.next_offset;
 }
 throw Error('Workspace pagination limit exceeded');
}
