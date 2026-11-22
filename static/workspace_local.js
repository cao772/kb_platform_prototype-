window.createOfflineWorkspace=function offline(snapshot){
"use strict";
const meta=snapshot.workspace;
const key="kb-local-workspace-v2:"+snapshot.created;
const clone=x=>JSON.parse(JSON.stringify(x));
const today=()=>new Date().toISOString().slice(0,19).replace("T"," ");
const next=rows=>Math.max(0,...rows.map(x=>Number(x.id)||0))+1;
let storage, saved={}, files=new Map();
try{storage=localStorage;saved=JSON.parse(storage.getItem(key)||"{}");}catch{}
const baseDocs=[...snapshot.documents,...snapshot.supplied];
const db=Object.assign({role:"admin",records:snapshot.catalog.map(r=>({...r,revision:"initial:"+r.id,review_status:"approved",pdfs:[]})),
 nodes:meta.nodes,mapping:meta.mapping,requests:[],preferences:{},threads:[],turns:[],feedback:[],leads:[],scores:[],facts:meta.facts||[],audit:[],watches:[],independent:false},saved);
const ids={reader:1,product:2,expert:3,manager:4,admin:5};
const actor=()=>({id:ids[db.role],username:meta.roles[db.role]+"体验",role:db.role,permissions:meta.role_permissions[db.role]||[]});
function save(){try{storage?.setItem(key,JSON.stringify(db));}catch{ /* The open tab retains the in-memory edits. */ }}
const requireRole=cap=>{if(cap&&!actor().permissions.includes(cap))throw Error("请在账户页切换到相应的体验角色。");};
const caps={record:"edit",import:"edit",review:"review",policy:"admin",node:"manage",mapping:"manage",score:"review",role:"admin",watch:"collect",watchRun:"collect",entities:"edit","fact-review":"review","feedback-resolve":"review"};
function state(){
 const u=actor();return {...meta,...db,user:u,docs:baseDocs.map(d=>({id:d.id,title:d.title,parser:d.parser,filename:d.title})),
 sources:snapshot.sources,counts:snapshot.counts,collected:snapshot.items.length,recent:snapshot.items.slice(0,12),
 records:db.records.filter(r=>r.review_status==="approved"||u.permissions.includes("edit")&&r.review_status==="disabled"),
 threads:db.threads.filter(x=>x.owner===u.id),feedback:db.feedback.filter(x=>x.actor===u.id||u.permissions.includes("review")),
 requests:db.requests.filter(x=>x.actor===u.id||u.permissions.includes("review")),
 preferences:db.preferences[u.id]||{},users:Object.entries(ids).map(([role,id])=>({id,username:meta.roles[role]+"体验",role,enabled:1})),
 leads:u.permissions.includes("contacts")?db.leads:db.leads.filter(x=>x.actor===u.id),services:{},sync_policy:{},
 audit:db.audit.slice().reverse(),facts:db.facts.filter(x=>u.permissions.includes("edit")||x.status==="approved")};
}
function searchPassages(q,docs){
 const words=(String(q).toLowerCase().match(/[a-z0-9§][a-z0-9._/-]*|[\u3400-\u9fff]+/g)||[]);
 const synonyms=[["电视","television"],["冰箱","refrigerat"],["洗碗机","dishwasher"],["德国","prodsg"],["能效","energy"],["印尼","permen"],["欧盟","directive"]];
 for(const [k,v] of synonyms)if(q.includes(k))words.push(v);
 const parts=[...new Set(words.flatMap(w=>/[\u3400-\u9fff]/.test(w)&&w.length>2?[w,...Array.from({length:w.length-1},(_,i)=>w.slice(i,i+2))]:[w]))];
 if(!parts.length)return [];
 const found=[];
 for(const doc of docs){
  const raw=doc.full_text||"",lower=raw.toLowerCase();if(!raw)continue;
  for(let start=0;start<raw.length;start+=540){
   const texts=lower.slice(start,start+650), counts=parts.map(t=>texts.split(t).length-1);
   const shared=counts.reduce((s,v)=>s+v,0);if(!shared)continue;
   const words=texts.match(/[a-z0-9]+|[\u3400-\u9fff]/g)||[];
   const freq=new Map();for(const w of words)freq.set(w,(freq.get(w)||0)+1);
   const norm=Math.sqrt([...freq.values()].reduce((s,n)=>s+n*n,0));
   const score=Math.min(1,shared/(Math.sqrt(parts.length)*Math.max(1,norm)));
   found.push({document_id:doc.id,title:doc.title,chunk:Math.floor(start/540),score,text:raw.slice(start,start+650)});
  }
 }
 return found.sort((a,b)=>b.score-a.score).slice(0,8);
}
const index=snapshot.search_index||{vocabulary:{},passages:[]};
const titles=new Map(baseDocs.map(d=>[d.id,d.title]));
let indexed;
function vectorPassages(query){
 const vocabulary=index.vocabulary,terms=String(query).toLowerCase().match(/[a-z0-9_.+-]+|[\u4e00-\u9fff]+/g)||[];
 const concepts=[["电视","television"],["冰箱","refrigerator"],["能效","energy"],["欧盟","directive"],["泄漏电流","leakage"],["电压","voltage"]];
 for(const [zh,en] of concepts)if(query.includes(zh))terms.push(en);
 const frequencies=new Map();
 for(const term of terms){
  let features=[term];
  if(/[\u4e00-\u9fff]/.test(term))features=[...new Set([...features,...term,...Array.from({length:Math.max(0,term.length-1)},(_,i)=>term.slice(i,i+2))])];
  for(const word of features)if(Object.hasOwn(vocabulary,word))frequencies.set(word,(frequencies.get(word)||0)+1);
 }
 if(!frequencies.size)return [];
 const q=new Float32Array(128);
 for(const [word,count] of frequencies){const bucket=vocabulary[word];q[Math.abs(bucket)-1]+=Math.sign(bucket)*(1+Math.log(count));}
 const norm=Math.hypot(...q);if(!norm)return [];
 if(!indexed)indexed=index.passages.map(([id,chunk,page,text,encoded])=>{
  const data=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));
  return {document_id:id,title:titles.get(id),chunk,page,text,vector:new Float32Array(data.buffer)};
 });
 const results=[];
 for(const row of indexed){
  let dot=0;for(let i=0;i<q.length;i++)dot+=q[i]*row.vector[i];
  if(dot<=0)continue;
  const lower=row.text.toLowerCase();
  const overlap=[...frequencies.keys()].filter(w=>lower.includes(w)).length;
  if(!overlap)continue;
  results.push({document_id:row.document_id,title:row.title,chunk:row.chunk,page:row.page,text:row.text,
                score:0.8*dot/norm+0.2*overlap/frequencies.size});
 }
 return results.sort((a,b)=>b.score-a.score).slice(0,8);
}
function validate(v){
 if(!String(v.name||"").trim())throw Error("请填写名称。");
 if(v.record_type&&!meta.types[v.record_type])throw Error("资料类型无效。");
 if(v.status&&!meta.statuses[v.status])throw Error("效力状态无效。");
 if(v.effective_from&&v.effective_to&&v.effective_from>v.effective_to)throw Error("失效日期早于生效日期。");
 if(v.source_url&&!/^https?:\/\//i.test(v.source_url))throw Error("请输入 HTTP(S) 原文链接。");
}
function stage(action,items,p){
 if(!items?.length)throw Error("请先选择记录。");
 const pending=items.map(row=>{
  if(["create","import","edit"].includes(action))validate(row);
  if(!["create","import"].includes(action)&&db.records.find(r=>r.id===row.id)?.revision!==row.revision)throw Error("数据已变化，请刷新。");
  return clone(row);
 });
 const id=next(db.requests);
 db.requests.push({id,actor:actor().id,username:actor().username,kind:action,status:"pending",amount:items.length,created_at:today(),payload:{items:pending,correction:p.correction}});
 if(p.pdf)files.set("pdfR"+id,p.pdf);
 return {request_id:id,count:items.length};
}
function publish(p){
 const r=db.requests.find(x=>x.id===Number(p.id)&&x.status==="pending");
 if(!r)throw Error("此申请已处理。");
 if(db.independent&&r.actor===actor().id)throw Error("请切换另一位审核人员。");
 if(p.decision==="approve"){
  const temp=clone(db.records),published=[];
  for(const value of r.payload.items){
   let target=temp.find(x=>x.id===value.id);
   if(!["create","import"].includes(r.kind)&&(!target||target.revision!==value.revision))throw Error("原记录已变化，请退回重提。");
   if(["create","import"].includes(r.kind)){target={id:next(temp),review_status:"approved"};temp.push(target);}
   if(["edit","create","import"].includes(r.kind)){
    Object.assign(target,Object.fromEntries(Object.entries(value).filter(([k])=>Object.hasOwn(meta.fields,k))));
    if(files.has("pdfR"+r.id)){target.pdfs=[...(target.pdfs||[]),r.id];}
   }else target.review_status=({enable:"approved",disable:"disabled",delete:"deleted"})[r.kind];
   target.revision="rev:"+r.id;published.push(target.id);
  }
  db.records=temp;
  const fb=db.feedback.find(x=>x.id===Number(r.payload.correction));
  if(fb)Object.assign(fb,{status:"resolved",record_id:published[0],resolution:p.note||"修订已通过"});
 }
 r.status=p.decision==="approve"?"approved":"rejected";r.note=p.note;
 return {};
}
async function op(name,p={}){
 requireRole(caps[name]);
 let result={ok:true};const uid=actor().id;
 if(name==="demo-role"){if(!Object.hasOwn(ids,p.role))throw Error("角色无效。");db.role=p.role;}
 else if(name==="match-file"){
  const digest=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",await p.file.arrayBuffer()))).map(b=>b.toString(16).padStart(2,"0")).join("");
  const id=meta.document_hashes[digest],doc=baseDocs.find(r=>r.id===id);
  if(!doc)throw Error("这份文件没有随附已解析结果，可在运行平台的法规文件解析页上传。");
  return {id,title:doc.title,parser:doc.parser};
 }
 else if(name==="entities"){
  const doc=baseDocs.find(d=>d.id===Number(p.document_id));
  if(!doc?.full_text)throw Error("当前文件仅附题录，请选择随附正文的资料。");
  const seen=new Set(db.facts.map(f=>JSON.stringify([f.document_id,f.chunk_id,f.subject,f.predicate,f.object])));
  const products=db.nodes.filter(n=>n.kind==="category").map(n=>n.name);
  const authorities=[...new Set(snapshot.sources.map(s=>s.authority).filter(x=>x&&x.length>3))];
  let created=0,id=next(db.facts);
  for(const [docId,chunk,,raw] of index.passages){
   if(docId!==doc.id)continue;
   for(const sentence of raw.split(/[\n。；;]/)){
    const clause=sentence.match(/第[〇零一二三四五六七八九十百千\d]+条|(?:Article|Clause|Pasal|§)\s*\d+(?:\.\d+)*/i)?.[0];
    const subject=clause||doc.title,edges=clause?[[doc.title,"包含条款",clause]]:[];
    for(const m of sentence.matchAll(/(修订|修正|amends?|替代|代替|replaces?|supersedes?|引用|参见|pursuant to|references?)\s+(?:the\s+)?((?:Directive|Regulation|IEC|ISO|GB\/?T?)\s*(?:\([A-Z]+\)\s*)?\d[\w.:/-]*|《[^》]+》)/gi))
      edges.push([subject,/修|amend/i.test(m[1])?"修订":/替|代|replace|supersede/i.test(m[1])?"替代":"引用",m[2]]);
    for(const [names,verb] of [[products,"提及产品"],[authorities,"提及机构"]])for(const n of names)if(sentence.toLowerCase().includes(n.toLowerCase()))edges.push([subject,verb,n]);
    for(const [s,verb,o] of edges){
     const key=JSON.stringify([docId,chunk,s,verb,o]);if(seen.has(key))continue;seen.add(key);
     db.facts.push({id:id++,document_id:docId,chunk_id:chunk,subject:s,predicate:verb,object:o,excerpt:sentence.trim().slice(0,1400),status:"pending"});created++;
    }
   }
  }
  result={created};
 }else if(name==="fact-review"){
  const f=db.facts.find(x=>x.id===p.id&&x.status==="pending");
  if(!f||!["approved","rejected"].includes(p.decision))throw Error("候选关系状态已改变。");
  f.status=p.decision;
 }
 else if(name==="record")result=stage(p.action,p.items,p);
 else if(name==="import"){
   const rows=await window.WorkspaceExcel.read(p.file), header=rows.shift()||[];
   const keys=header.map(n=>Object.keys(meta.fields).find(k=>meta.fields[k]===n||k===n));
   if(keys.includes(undefined)||!keys.includes("name")||new Set(keys).size!==keys.length)throw Error("请核对模板表头。");
   const items=rows.map(line=>{const r=Object.fromEntries(keys.map((k,i)=>[k,line[i]||""]));
     for(const [k,dict,def] of [["record_type",meta.types,"regulation"],["status",meta.statuses,"unknown"]])r[k]=Object.entries(dict).find(([id,label])=>r[k]===id||r[k]===label)?.[0]||r[k]||def;
     return r;});
   result=stage("import",items,p);
 }else if(name==="review")result=publish(p);
 else if(name==="policy")db.independent=p.independent;
 else if(name==="preference"){(db.preferences[uid]||={})[p.name]=p.value;}
 else if(name==="node"){
  const old=db.nodes.find(n=>n.key===p.key);
  if(!p.name||!p.key||old&&old.kind!==p.kind)throw Error("请填写唯一编码及名称。");
  const seen=new Set([p.key]);let parent=p.parent;
  while(parent){const n=db.nodes.find(x=>x.key===parent&&x.kind===p.kind);if(seen.has(parent)||!n)throw Error("父项无效或存在循环。");seen.add(parent);parent=n.parent;}
  if(p.delete){if(db.nodes.some(n=>n.parent===p.key||n.members?.includes(p.key))||db.mapping.some(m=>[m.category,m.region,m.mode,m.mark].includes(p.key)))throw Error("该项仍被引用。");db.nodes=db.nodes.filter(n=>n.key!==p.key);}
  else if(old)Object.assign(old,p);else db.nodes.push(p);
 }else if(name==="mapping"){
  if(p.delete)db.mapping=db.mapping.filter(m=>m.id!==Number(p.id));
  else{const rec=db.records.find(r=>r.id===Number(p.record_id)&&r.review_status==="approved"),node=k=>db.nodes.find(n=>n.key===k)?.name||"";
    if(!rec||!node(p.category))throw Error("请选择类别和正式知识。");
    const old=db.mapping.find(m=>m.category===p.category&&m.region===p.region&&m.record_id===Number(p.record_id));
    const m={...p,record_id:rec.id,category_name:node(p.category),record_name:rec.name,region_name:node(p.region),mode_name:node(p.mode),mark_name:node(p.mark)};
    if(old)Object.assign(old,m);else db.mapping.push({...m,id:next(db.mapping)});}
 }else if(name==="score"){
  if(!p.model||!Object.keys(p.reference).length)throw Error("请填写模型名和标准答案。");
  const ks=Object.keys(p.reference).sort(),bm=JSON.stringify(ks.map(k=>[k,p.reference[k]]));
  const norm=v=>typeof v==="string"?v.trim().replace(/\s+/g," "):JSON.stringify(v);
  const doc=baseDocs.find(d=>d.id===Number(p.document_id));
  const rec={title:doc?.title,document_id:+p.document_id,model:p.model,benchmark:bm,total:ks.length,correct:ks.filter(k=>Object.hasOwn(p.output,k)&&norm(p.output[k])===norm(p.reference[k])).length};
  const old=db.scores.find(r=>r.document_id===rec.document_id&&r.model===rec.model&&r.benchmark===bm);
  if(old)Object.assign(old,rec);else db.scores.push(rec);
 }else if(name==="thread"){result={id:next(db.threads)};db.threads.push({...result,owner:uid,title:p.title||"新会话"});}
 else if(name==="chat-upload"){
  const file=p.file;
  if(file.size>12*1024**2)throw Error("附件超过 12 MB。");
  let doc;
  if(/\.(txt|md|csv)$/i.test(file.name))doc={title:file.name,full_text:await file.text(),id:"upload"};
  else if(crypto.subtle){const hash=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",await file.arrayBuffer())),b=>b.toString(16).padStart(2,"0")).join("");
    const id=meta.document_hashes?.[hash];if(id)doc=baseDocs.find(d=>d.id===id&&d.full_text);}
  if(!doc)throw Error("离线文件可读取文本，或识别与随附解析原文完全一致的文件。新扫描件和图片请在平台上传识别。");
  files.set("chat"+p.thread_id,[...(files.get("chat"+p.thread_id)||[]),doc]);result={name:doc.title};
 }else if(name==="ask"){
  const ts=db.turns.filter(t=>t.thread_id===p.thread_id);
  const docs=p.scope==="document"?(files.get("chat"+p.thread_id)||[]):baseDocs;
  if(!docs.length)throw Error("请先为本会话上传资料。");
  const query=[...ts.slice(-2).map(t=>t.question),p.question].join(" ");
  const hits=(p.scope==="document"?searchPassages(query,docs):vectorPassages(query)).slice(0,4);
  const en=p.lang==="en"||p.lang==="auto"&&!/[\u3400-\u9fff]/.test(p.question);
  const answer=hits.length?(en?"Matching source passages":"匹配的原文片段")+"\n"+hits.map((r,i)=>`[${i+1}] ${r.text.slice(0,300)}`).join("\n\n"):(en?"Insufficient evidence.":"所选资料中未找到足够依据。");
  const turn={id:next(db.turns),thread_id:p.thread_id,question:p.question,answer,citations:hits.map(r=>({...r,excerpt:r.text})),vote:0};db.turns.push(turn);result=turn;
 }else if(name==="vote"){const t=db.turns.find(t=>t.id===p.id&&db.threads.some(h=>h.id===t.thread_id&&h.owner===uid));if(!t)throw Error("回答不存在。");t.vote=p.vote;}
 else if(name==="feedback"){db.feedback.push({id:next(db.feedback),...p,status:"open",actor:uid,created_at:today()});}
 else if(name==="feedback-resolve"){const f=db.feedback.find(x=>x.id===Number(p.id));if(f)Object.assign(f,{status:"resolved",resolution:p.resolution});}
 else if(name==="lead"){if(!p.consent)throw Error("请确认联系授权。");db.leads.push({id:next(db.leads),actor:uid,contact:"本地体验 · 已隐去",intent:p.intent,created_at:today()});}
 else if(name==="lookup")result={local:db.records.filter(r=>r.review_status==="approved"&&r.record_type==="standard"&&r.code?.toLowerCase()===p.code.toLowerCase()),external:[],configured:false};
 else if(name==="watch"){const old=db.watches.find(x=>x.source_key===p.source_key);if(old)Object.assign(old,p);else db.watches.push(p);}
 else if(name==="vector")return {items:vectorPassages(p.q)};
 else if(name==="search"){const q=p.q.trim().toLowerCase();return {items:[...db.records.filter(r=>r.review_status==="approved").map(r=>({kind:"法规",title:r.name,detail:r.code,id:r.id})),...baseDocs.map(d=>({kind:"资料",title:d.title,detail:d.parser,id:d.id})),...snapshot.sources.map(s=>({kind:"来源",title:s.source_name,detail:s.source_key,id:s.source_key})),...snapshot.items.map(i=>({kind:"采集",title:i.title,detail:i.source_key,id:i.id}))].filter(r=>(r.title+" "+r.detail).toLowerCase().includes(q)).slice(0,250)};}
 else throw Error("此操作使用运行中的平台服务。");
 db.audit.push({id:next(db.audit),username:actor().username,action:name,subject:"本地试操作",at:today()});save();return result;
}
function get(name,p){
 if(name==="request"){const r=db.requests.find(x=>x.id===+p.id);if(!r)throw Error("申请不存在。");return r;}
 if(name==="thread")return {turns:db.turns.filter(x=>x.thread_id===+p.id),files:(files.get("chat"+p.id)||[]).map(d=>({name:d.title}))};
}
return {state,op,get,pdf:id=>files.get("pdfR"+id),snapshot};
};
