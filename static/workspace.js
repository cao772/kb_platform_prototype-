(function workbench(){
"use strict";
const root=document.getElementById("ws-root");
if(!root)return;
const embedded=document.getElementById("snapshot");
const offline=!!embedded;
const local=offline?window.createOfflineWorkspace(window.PlatformPublicSnapshot||JSON.parse(embedded.textContent)):null;
const groups=[
 ["工作台",[["overview","业务概览"],["requirements","功能对照"],["quality","数据质量"],["search","全局搜索"]]],
 ["采集与知识建设",[["collect","关注信息采集"],["parse","法规文件解析"],["vector","向量化服务"],["ontology","本体建模"],["entities","实体关系抽取"],["graph","知识图谱"]]],
 ["管理端",[["records","法规基本信息库"],["review","专家审批"],["scores","模型评分"],["mapping","产品技术库"],["regions","地区库"],["categories","产品类别库"],["accounts","账户与角色"],["logs","操作日志"],["inbox","消息中心"],["integrations","API 接口"]]],
 ["用户端",[["chat","知识问答"],["feedback","反馈纠错"],["leads","咨询留资"]]]
];
const navNames=Object.fromEntries(groups.flatMap(g=>g[1]));
const requiredMenu={review:"review",scores:"review",logs:"admin"};
const actions={create:"新增",edit:"修改",delete:"删除",enable:"启用",disable:"停用",import:"导入",evidence:"补充原文依据",
 pending:"待审批",approved:"已通过",rejected:"已退回",superseded:"已被新申请替代",open:"待处理",resolved:"已处理",disabled:"已停用",deleted:"已移除"};
const fieldDefault=["name","code","record_type","region_name","status","version"];
let model,route="",stateId=0,dialog,main,msg,activeThread=0,history={turns:[],files:[]},filter={},columns=[...fieldDefault],size=15,number=1;
let selected=new Set(),queryOutput="",moduleFilter="",feedbackTurn=null,evidenceDraft=null,focusSearch=false,searchTerm="";
const escape=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const $=s=>root.querySelector(s);
const cap=n=>model.user.permissions.includes(n);
const href=k=>"#w/"+k;
const anchor=(k,t,cls="btn-w")=>`<a class="${cls}" href="${href(k)}">${escape(t||navNames[k])}</a>`;
const external=(web,file,t)=>`<a class="btn-w" href="${escape(offline?file:web)}">${escape(t)}</a>`;
const rawlink=(url,t)=>`<a class="btn-w" href="${escape(url)}">${escape(t)}</a>`;
const status=x=>`<span class="status-w">${escape(actions[x]||x||"未记录")}</span>`;
const box=(title,body)=>`<section class="box"><h2>${escape(title)}</h2>${body}</section>`;
const empty=text=>`<p class="empty-w">${escape(text||"暂无记录。")}</p>`;
const small=text=>`<p class="dim">${escape(text)}</p>`;
const button=(name,label,id="",css="")=>`<button type="button" data-act="${name}" data-id="${escape(id)}" class="${css}">${escape(label)}</button>`;
const submit=(t="保存")=>`<button class="primary" type="submit">${escape(t)}</button>`;
const form=(name,body)=>`<form data-form="${name}">${body}</form>`;
const input=(k,label,value="",type="text",attrs="")=>`<label>${escape(label)}<input name="${k}" type="${type}" value="${escape(value)}" ${attrs}></label>`;
const area=(k,label,v="",attrs="")=>`<label>${escape(label)}<textarea name="${k}" ${attrs}>${escape(v)}</textarea></label>`;
const check=(k,label,on=false)=>`<label class="check-w"><input type="checkbox" name="${k}" ${on?"checked":""}>${escape(label)}</label>`;
const options=(obj,chosen="")=>Object.entries(obj).map(([v,t])=>`<option value="${escape(v)}" ${String(chosen)===v?"selected":""}>${escape(t)}</option>`).join("");
const select=(k,label,obj,chosen="")=>`<label>${escape(label)}<select name="${k}">${options(obj,chosen)}</select></label>`;
const pairs=(rows,key,name)=>Object.fromEntries(rows.map(r=>[r[key],r[name]]));
const table=(cols,rows,fallback)=>rows.length?`<div class="wscroll"><table><thead><tr>${cols.map(c=>`<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(c=>`<td>${c??""}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`:empty(fallback);
const info=(obj)=>table(["字段","内容"],Object.entries(obj).filter(([,v])=>v!==null&&v!==undefined&&v!=="").map(([k,v])=>[escape(k),escape(v)]));
const tiles=items=>`<div class="grid-w">${items.map(([k,title,desc])=>`<a class="tile-w" href="${href(k)}"><b>${escape(title)}</b><span>${escape(desc)}</span></a>`).join("")}</div>`;
const tools=(...items)=>`<div class="tools-w">${items.join("")}</div>`;
const fields=(...items)=>`<div class="fields-w">${items.join("")}</div>`;
const needs=(which)=>empty((model.user.id?"当前角色缺少":"请先登录以使用")+"此项"+which+"权限。")+anchor("accounts",offline?"切换体验角色":"账户登录");
const labelValue=(r,k)=>k==="record_type"?model.types[r[k]]:k==="status"?model.statuses[r[k]]:r[k]??"";
const docOptions=()=>pairs(model.docs,"id","title");
const nodeOptions=kind=>pairs(model.nodes.filter(n=>n.kind===kind),"key","name");
function message(s,error=false){if(!msg)return;msg.className="msg-w"+(error?" error":"");msg.textContent=s||"";}
async function request(name,p,method="POST",binary=false){
 const qs=method==="GET"&&p?"?"+new URLSearchParams(p):"";
 const res=await fetch("/api/workspace/"+name+qs,{method,credentials:"same-origin",headers:method==="POST"?{"Content-Type":"application/json"}:{},body:method==="POST"?JSON.stringify(p||{}):undefined});
 if(!res.ok){let err;try{err=await res.json();}catch{}throw Error(err?.error||"请求失败。");}
 return binary?res.blob():res.json();
}
const mutate=(k,p)=>offline?local.op(k,p):request(k,p);
const get=(k,p)=>offline?local.get(k,p):request(k,p,"GET");
async function refresh(){
 const previous=model?.user.id;
 model=offline?local.state():await request("state",null,"GET");
 if(previous!==undefined&&previous!==model.user.id){activeThread=0;history={turns:[],files:[]};feedbackTurn=null;evidenceDraft=null;filter={};columns=[...fieldDefault];number=1;size=15;queryOutput="";searchTerm="";selected.clear();}
}
function shell(){
 const group=groups.find(([,arr])=>arr.some(x=>x[0]===route))?.[0]||"工作台";
 root.innerHTML=`<button class="skip-w" data-act="skip">跳到主要内容</button><header class="wtop"><a class="wordmark" href="${href("overview")}"><i></i>法规认证知识平台</a><div class="role-w"><span>${offline?"离线交互原型 · "+local.snapshot.created.slice(0,10):"本地业务平台"}</span><button type="button" class="find-w" data-act="quick-search" aria-keyshortcuts="/">搜索 <kbd>/</kbd></button>${anchor("inbox","消息","")} ${anchor("accounts",model.user.username,"")}</div></header>
 <div class="wlayout"><nav class="wnav" aria-label="平台功能">${groups.map(([g,arr])=>`<h2>${escape(g)}</h2>${arr.filter(([k])=>!requiredMenu[k]||cap(requiredMenu[k])).map(([k,t])=>`<a href="${href(k)}"${k===route?' aria-current="page"':""}>${escape(t)}</a>`).join("")}`).join("")}</nav>
 <main><div class="crumb">${anchor("overview","平台首页","")}　/　${escape(group)}　/　${escape(navNames[route])}</div><div id="wbody" tabindex="-1"></div><div class="msg-w" role="status" aria-live="polite"></div></main></div><dialog id="wmodal" aria-labelledby="ws-modal-title"><div data-modal></div></dialog>`;
 main=$("#wbody");dialog=$("#wmodal");msg=$(".msg-w");
}
function showModal(title,html){dialog.querySelector("[data-modal]").innerHTML=`<h2 id="ws-modal-title" tabindex="-1">${escape(title)}</h2>${html}${tools(button("close","关闭"))}`;if(!dialog.open)dialog.showModal();dialog.querySelector("h2").focus();}
const dataLink={
 home:["/business","#home","监管数据工作台"],source:["/sources","#sources","来源台账"],items:["/collection","#collection","采集结果"],
 docs:["/parse-review","#docs","查阅解析结果"],govern:["/admin","#governance","资料与模型配置"],schema:["/ontology","#ontology","对象与关系定义"],
 graph:["/graph","#graph","法规关联图谱"],map:["/map","#map","法规认证地图"],access:["/business?tab=access","#access","产品准入查询"],
 cat:["/catalog","#catalog","正式知识目录"],changes:["/changes","#changes","变化待办"],qa:["/business?tab=qa","#qa","问答与原文证据"]};
const jump=k=>external(...dataLink[k]);
function overview(){
 const links=[["collect","数据采集","网站、公众号、视频及授权数据源"],["parse","文档解析工具","法规、标准和认证规则"],["vector","向量库服务","文档向量与原文召回"],["ontology","知识本体","概念、实体关系和版本血缘"],["records","管理端","法规、审批、分类、权限和接口"],["chat","用户端","文档问答、纠错和咨询"]];
 return `<div class="whero"><h1>法规认证业务工作台</h1><p>${offline?"浏览随附数据，或切换角色试用业务流程。试操作仅保存在当前浏览器。":"从来源监测和解析进入专家审核，再按产品与市场查询正式知识。"}</p></div><div class="metrics-w">${[["登记来源",model.sources.length],["已采集记录",model.collected],["正式知识",model.records.filter(r=>r.review_status==="approved").length],["功能要求",model.requirements.length]].map(([t,v])=>`<div class="stat-w"><strong>${v}</strong>${t}</div>`).join("")}</div>`+
 tiles(links)+box("数据与业务查询",tools(...["home","source","items","docs","cat","map","access","changes"].map(jump)))+
 box("按需求查看功能",tools(anchor("requirements","打开功能对照"),anchor("quality","检查法规依据")));
}
function quality(){
 const q=model.quality,names={located:"已定位原文片段",excerpt:"已附出处及摘录",background:"仅关联项目背景资料",missing:"待补原文证据"};
 return box(offline?"随附目录与本地试操作":"正式知识依据",small("按条检查已审核记录的原始依据和适用地区。")+
 info({"已定位原文片段":q.located,"已附出处及摘录":q.excerpt,"仅有项目背景依据":q.background,"待补原文证据":q.missing,"未标注地区":q.without_region}))+
 box("逐条核对",table(["知识名称","适用地区","证据状态","操作"],q.items.map(x=>[escape(x.name),escape(x.region||"待标注"),escape(names[x.kind]),tools(button("detail","查看资料",x.id),cap("edit")?button("attach-evidence","提交原文依据",x.id):"")])));
}
function requirements(){
 const ms=Object.fromEntries([...new Set(model.requirements.map(r=>r.module))].map(k=>[k,k]));
 const list=model.requirements.filter(r=>!moduleFilter||r.module===moduleFilter);
 const conditions={watch:"网站按站点规则执行，公众号、视频需授权信息源。",standards:model.services.ability?"已配置机构接口":"本地标准号可查，机构能力库待接入。",
 feeds:model.services.feed?"已配置外部接口":"待采购方提供数据接口及授权。",login:offline?"可切换体验角色，真实账号在运行平台使用。":model.services.wechat?"已配置微信网站应用":"本地账号可用，微信网站应用待接入。",
 parse:offline?"可核对随附解析结果，新文档在运行平台处理。":"按所选文档结构提取字段。扫描件使用本机 OCR。",
 upload:offline?"可上传文本或匹配随附资料，新增扫描件 OCR 在运行平台执行。":"上传后在所属会话检索原文。"};
 return form("module",tools(select("module","一级模块",{"":"全部模块",...ms},moduleFilter),submit("筛选")))+
 table(["一级模块","二级模块","功能与要求","供应方式","入口"],list.map(r=>[escape(r.module),escape(r.group),`<b>${escape(r.name)}</b><br><small>${escape(r.detail)}</small>`+(conditions[r.id]?`<p>${escape(conditions[r.id])}</p>`:""),escape(r.supply),anchor(r.view,"打开")]));
}
function filtered(){
 return model.records.filter(r=>{
 const combined=[r.name,r.name_en,r.code,r.authority,r.product_class,r.technical_domain,r.region_name].join(" ").toLowerCase();
 if(filter.q&&!combined.includes(filter.q.toLowerCase()))return false;
 if(["record_type","status","review_status"].some(k=>filter[k]&&filter[k]!==r[k]))return false;
 if(["region","domain","authority"].some(k=>filter[k]&&!String(k==="region"?(r.region_name||"")+" "+(r.region_code||""):k==="domain"?r.technical_domain||"":r.authority||"").toLowerCase().includes(filter[k].toLowerCase())))return false;
 return (!filter.start||r.effective_from&&r.effective_from>=filter.start)&&(!filter.end||r.effective_from&&r.effective_from<=filter.end);
 });
}
function recordsPage(){
 const rows=filtered(),pages=Math.max(1,Math.ceil(rows.length/size));number=Math.min(number,pages);
 const preset=pairs(Object.keys(model.preferences).map(k=>({k})),"k","k");
 const search=form("filter",fields(input("q","名称 / 编号",filter.q),select("record_type","类型",{"":"全部",...model.types},filter.record_type),
 input("region","地区名称 / 代码",filter.region),select("status","效力状态",{"":"全部",...model.statuses},filter.status),
 input("start","生效日期起",filter.start,"date"),input("end","生效日期止",filter.end,"date"),input("domain","技术领域",filter.domain),input("authority","发布机构",filter.authority),
 select("review_status","启停状态",{"":"全部",approved:"启用",disabled:"停用"},filter.review_status))+
 tools(submit("应用筛选"),button("clear","清除"),cap("ask")?button("save-filter","保存条件"):"",Object.keys(preset).length?select("preset","已保存筛选",{"":"请选择",...preset})+button("load-filter","载入"):""));
 const edit=cap("edit");
 const actionsBar=tools(edit?button("new","新增法规","","primary"):"",button("columns","选择及排序列"),
 edit?button("import","Excel 导入"):"",button("template","下载 Excel 模板"),edit?button("export","导出选中 / 筛选结果"):"",
 ...(edit?["enable","disable","delete"].map(x=>button("bulk-"+x,"批量"+actions[x])):[]));
 const visible=rows.slice((number-1)*size,number*size);
 const first=edit?['<input type="checkbox" aria-label="选中本页" data-pick-page>']:[];
 const tableRows=visible.map(r=>[...(edit?[`<input type="checkbox" data-pick="${+r.id}" aria-label="选中 ${escape(r.name)}" ${selected.has(r.id)?"checked":""}>`]:[]),
 ...columns.map(k=>k==="name"?button("detail",r.name,r.id):escape(labelValue(r,k))),escape(r.review_status==="disabled"?"停用":"启用"),
 tools(button("detail","详情",r.id),...(edit?[button("edit","编辑",r.id),button(r.review_status==="disabled"?"one-enable":"one-disable",r.review_status==="disabled"?"启用":"停用",r.id),button("one-delete","删除",r.id)]:[]))]);
 return box("组合筛选",search)+box("法规记录",actionsBar+table([...first,...columns.map(k=>escape(model.fields[k])),"启停","操作"],tableRows,"当前条件下没有记录。")+tools(small(`${rows.length} 条 · 第 ${number}/${pages} 页 · 已选 ${selected.size} 条`),select("pageSize","每页",{10:"10",15:"15",30:"30",50:"50"},size),button("prev","上一页"),button("next","下一页")));
}
function editor(record={},correction=null){
 const filled=Object.entries(model.fields).map(([k,t])=>k==="record_type"?select(k,t,model.types,record[k]||"regulation"):k==="status"?select(k,t,model.statuses,record[k]||"unknown"):
 ["body_zh","body_en","applicability_scope","source_excerpt"].includes(k)?area(k,t,record[k]):input(k,t,record[k],k.startsWith("effective_")?"date":k==="source_url"?"url":"text",k==="name"?"required":""));
 showModal(record.id?"编辑法规":"新增法规",form("record",`<input type="hidden" name="id" value="${+record.id||0}"><input type="hidden" name="revision" value="${escape(record.revision||"")}"><input type="hidden" name="correction" value="${+correction||0}">`+fields(...filled,input("pdf","附加 PDF 原件","","file",'accept="application/pdf,.pdf"'))+tools(submit("提交专家审批"))));
}
function review(){
 const list=model.requests;
 return box("审批流程",small(model.independent?"申请须由另一位审核专家或管理员复核。":"法规经审核专家或管理员确认后发布。")+
 (cap("admin")?form("policy",tools(check("independent","启用提交人与审核人分离",model.independent),submit("保存流程"))):""))+
 box("审批单",table(["单号","操作","记录数","提交人","状态","明细"],list.map(r=>[r.id,escape(actions[r.kind]),r.amount,escape(r.username),status(r.status),button("request","查看申请",r.id)])));
}
function dictionaries(kind){
 const groups=kind==="regions"?["region","alliance"]:["category"];
 const typeNames={region:"洲 / 国家 / 地区",alliance:"经济联盟",category:"产品分类",mode:"评定模式",mark:"认证标志"};
 const nodes=model.nodes.filter(n=>groups.includes(n.kind));
 const ns=new Map(model.nodes.map(n=>[n.key,n.name]));
 return box(kind==="regions"?"地区及联盟":"产品分类树",(cap("manage")?tools(...groups.map(k=>button("new-node","新增"+typeNames[k],k))):"")+
 table(["名称","类型","编码","上级 / 成员","操作"],nodes.map(n=>[escape(n.name),escape(typeNames[n.kind]),escape(n.key),escape([ns.get(n.parent),...(n.members||[]).map(k=>ns.get(k)||k)].filter(Boolean).join(" · ")),cap("manage")?button("edit-node","维护",n.key):"—"])));
}
function mapping(){
 const nodes=Object.fromEntries(model.nodes.map(n=>[n.key,n.name])),registry=model.nodes.filter(n=>["mode","mark"].includes(n.kind));
 return box("产品类别与适用法规",tools(jump("access"),cap("manage")?button("mapping","新增适用映射"):"")+
 table(["产品类别","正式知识","市场","适用范围","模式 / 标志","操作"],model.mapping.map(m=>[escape(m.category_name),escape(m.record_name),escape(m.region_name||"全部"),escape(m.scope),escape([nodes[m.mode],nodes[m.mark]].filter(Boolean).join(" · ")),cap("manage")?button("remove-map","移除",m.id):"—"])))+
 box("合格评定模式与标志",(cap("manage")?tools(button("new-node","新增模式","mode"),button("new-node","新增标志","mark")):"")+table(["名称","类别","操作"],registry.map(n=>[escape(n.name),n.kind==="mode"?"评定模式":"标志",cap("manage")?button("edit-node","维护",n.key):"—"])));
}
function nodeModal(kind,old={}){
 const parents={ "":"无上级",...nodeOptions(kind)};delete parents[old.key];
 showModal("维护分类与字典",form("node",`<input type="hidden" name="kind" value="${escape(kind)}">`+fields(input("key","唯一编码",old.key||"","text",old.key?"readonly":"required"),input("name","名称",old.name||"","text","required"),select("parent","上级",parents,old.parent))+
 (kind==="alliance"?`<label>成员市场<select name="members" multiple size="7">${model.nodes.filter(n=>n.kind==="region"&&n.parent).map(n=>`<option value="${escape(n.key)}" ${(old.members||[]).includes(n.key)?"selected":""}>${escape(n.name)}</option>`).join("")}</select></label>`:"")+
 tools(submit(),old.key?button("remove-node","删除此项",old.key):"")));
}
function collect(){
 const counts=model.sources.reduce((a,s)=>{a[s.verification_status]=(a[s.verification_status]||0)+1;return a;},{});
 return box("信息来源",small(`来源台账已登记 ${model.sources.length} 个来源，目标规模为 280–300 个。已采集 ${model.collected} 条记录。`)+tools(jump("source"),jump("items"),offline?rawlink("#runs","采集执行记录"):rawlink("/collection-experience","逐站核验与适配"))+
 info({"记录总数":model.collected,"已联网核验":counts.verified||0,"核验异常":counts.failed||0,"待实测":counts.preclassified||0}))+
 box("最近采集结果",table(["标题","来源","最近采集","查阅"],model.recent.map(r=>[escape(r.title),escape(r.source_key),escape(String(r.last_seen_at||"").slice(0,10)),offline?rawlink("#item?id="+Number(r.id),"记录详情"):button("collected-item","记录详情",r.id)])))+
 box("栏目、公众号与视频监测",small(offline?"可配置本地监测方案，历史材料在采集结果中查看。":"按来源保存监测关键词。网站跟进详情与附件，公众号和视频通过授权数据接口接入。定时任务在启用后按周期执行。")+
 (cap("collect")?form("watch",fields(select("source_key","来源",Object.fromEntries(model.sources.map(s=>[s.source_key,s.source_key+" · "+s.source_name]))),select("channel","采集渠道",{"网站":"网站","公众号":"公众号（授权信息流）","视频":"视频（授权题录与字幕）"}),input("keywords","关键词（逗号分隔）"),input("minutes","间隔（分钟）",60,"number",'min="15" max="10080"'))+tools(check("enabled","启用定时监测"),submit("保存监测配置"))):anchor("accounts","登录后维护监测"))+
 table(["来源","渠道","关键词","周期","最近结果","操作"],model.watches.map(w=>[escape(w.source_key),escape(w.channel),escape(Array.isArray(w.keywords)?w.keywords.join("、"):w.keywords),escape(w.enabled?w.minutes+" 分钟":"手动"),escape(w.result||"待执行"),offline?external("/collection","#collection?source="+encodeURIComponent(w.source_key),"查看采集样本"):button("run-watch","执行本来源",w.source_key)])));
}
function parsePage(){
 return box("法规、标准与认证规则",small(offline?"打开已有原文核对解析结果。新文档的 OCR 和字段抽取在运行平台执行。":"按文档结构选用解析工具，保留页码、条款、表格和异常页。解析结果进入校核流程。")+
 tools(jump("docs"),offline?rawlink("#governance","查看候选字段"):rawlink("/candidate-normalization","候选归一"),anchor("scores","模型评分"))+
 (offline?form("match-file",tools(input("file","选择已有法规原件","","file","required"),submit("匹配随附解析结果"))):"")+
 (!offline&&cap("edit")?form("parse",fields(select("family","文件结构",{"regulation":"法规文件","standard":"标准","certification":"认证规则"}),input("docfile","Word、PDF 或扫描件","","file",'accept=".docx,.pdf,.png,.jpg,.jpeg,.tif,.tiff,.xlsx" required'))+tools(submit("上传并开始解析"))):""))+
 box("已接入法规资料",table(["资料名称","解析方式","解析结果"],model.docs.map(d=>[escape(d.title),escape(d.parser),docAnchor(d.id)])));
}
function docAnchor(id){
 if(offline){
  const collected=local.snapshot.documents.some(d=>d.id===+id),doc=local.snapshot.supplied.find(d=>d.id===+id);
  return rawlink("#"+(collected?"doc":"supplied")+"?id="+Number(id),collected||doc?.full_text?"查阅原文":"查看题录");
 }
 return cap("edit")?button("read-document","解析审阅",id):anchor("accounts","登录后审阅");
}
function passageAction(source){
 if(source.document_id!=null&&Number.isInteger(Number(source.document_id))&&Number(source.document_id)>0&&source.chunk!=null&&cap("ask")){
  return button("passage","定位原文",Number(source.document_id)+"/"+Number(source.chunk));
 }
 return source.record_id?button("detail","查看正式记录",source.record_id):"";
}
function vector(){
 return box("文档向量索引",table(["索引模型","维度","文档","分片"],model.vectors.map(v=>[escape(v.model_name),v.dimensions,v.documents,v.chunks])))+
 box(offline?"随附文档向量检索":"向量相似度检索",small(offline?"使用随附原文的已有向量与词项匹配，在当前浏览器返回出处和相似度。":"使用已建立的文档向量索引，返回原文片段和相似度。")+
 (cap("ask")?form("vector",tools(input("q","搜索内容","","text","required"),submit("匹配片段"))):needs("查询"))+queryOutput);
}
function semantics(mode){
 const native=mode==="ontology"?"schema":"graph";
 const key=mode==="ontology"?"概念、属性与关系模型":"已审核关联与版本血缘";
 if(mode!=="entities")return box(key,tools(jump(native),anchor("entities","源文实体关系"),jump("cat"))+
 (mode==="ontology"?small("查看各类业务对象、必填属性、关系的起点与终点。"):small("在图谱中选择知识条目查看上下游关联，打开正式目录查看版本沿革。")))+
 (mode==="graph"?factTable(true):box("建模对象",tiles([["regions","地区与联盟","适用地域及经济联盟"],["categories","多级产品分类","大类、中类与小类"],["mapping","适用映射","法规、评定模式与标志"]])) );
 return box("从原文抽取实体和关系",small("抽取条款、产品、机构及引用、修订和替代关系，逐项保留原文供专家复核。")+
 (cap("edit")?form("entities",tools(select("document_id","选择一份资料",offline?pairs([...local.snapshot.documents,...local.snapshot.supplied].filter(d=>d.full_text),"id","title"):docOptions()),submit("抽取该文档"))):needs("抽取")))+factTable(false);
}
function factTable(approved){
 const facts=model.facts.filter(r=>!approved||r.status==="approved");
 return box(approved?"已确认的原文关系":"关系与出处",table(["主体","关系","对象","依据","状态"],facts.map(r=>[escape(r.subject),escape(r.predicate),escape(r.object),`<details><summary>原文</summary>${escape(r.excerpt)}</details>`,r.status==="pending"&&cap("review")?tools(button("fact-yes","确认",r.id),button("fact-no","驳回",r.id)):status(r.status)])));
}
function scores(){
 const rows=[...model.scores].sort((a,b)=>a.document_id-b.document_id||a.benchmark.localeCompare(b.benchmark)||b.correct/b.total-a.correct/a.total);
 return box("模型解析评分",small("对同一文档、同一组参考字段逐项比较。得分为相符字段数除以参考字段数，同组内按得分排序。")+
 form("score",fields(select("document_id","原文",docOptions()),input("model","模型或解析器名称","","text","required"),area("reference","专家参考字段（JSON 对象）","",'placeholder=\'{"标准号":"…"}\' required'),area("output","该模型解析结果（JSON 对象）","","required"))+tools(submit("计算并保存"))))+
 box("同一基准排名",table(["文档 / 基准","模型","相符字段","得分"],rows.map(r=>[escape(r.title)+"<br><small>"+escape(r.benchmark)+"</small>",escape(r.model),r.correct+" / "+r.total,(100*r.correct/r.total).toFixed(1)+"%"])));
}
function accounts(){
 const me=model.user;
 const auth=offline?small("选择体验身份查看相应菜单和操作权限。离线原型使用演示身份。")+form("demo-role",tools(select("role","体验角色",model.roles,me.role),submit("切换角色"))):
 me.id?tools(small("当前身份 · "+me.username+" / "+(model.roles[me.role]||me.role)),button("logout","退出账号")):
 form("auth",fields(select("action","账号操作",{login:"登录",register:"注册账号",recover:"找回密码"}),input("username","账号","","text",'autocomplete="username" required'),input("password","密码 / 新密码","","password",'autocomplete="off" required'),input("recovery","找回时填写恢复码","","password",'autocomplete="off"'))+tools(submit("继续"),model.services.wechat?rawlink("/auth/wechat/start","微信登录"):status("微信登录 · 待配置网站应用")))+
 `<details><summary>本机管理员入口</summary><p>在项目目录运行 <code>python3 scripts/open_platform_admin.py</code> 即可在本机浏览器打开管理会话。</p></details>`;
 return box("账户登录",auth)+(offline?small("在线平台提供账号注册、密码恢复和微信网站应用登录。"):"")+
 box("角色权限",table(["角色","可用权限"],Object.entries(model.roles).map(([k,v])=>[escape(v),escape((model.role_permissions[k]||[]).map(p=>({read:"浏览",ask:"问答",edit:"起草",collect:"采集",manage:"业务配置",contacts:"咨询处理",review:"专家审核",admin:"系统管理"})[p]).join(" · "))])))+
 (cap("admin")?box("账号管理",table(["用户","角色","启用状态","操作"],model.users.map(u=>[escape(u.username),escape(model.roles[u.role]),u.enabled?"启用":"停用",offline?"体验身份":u.username==="@localhost"||u.id===me.id?"当前 / 本机账号":button("user-role","配置",u.id)]))):"");
}
function inbox(){
 const my=model.requests.filter(r=>r.actor===model.user.id||cap("review"));
 return box("审批与反馈通知",table(["事项","状态","说明"],[...my.slice(0,60).map(r=>[button("request","法规审批 #"+r.id,r.id),status(r.status),escape(r.note||actions[r.kind])]),...model.feedback.slice(0,40).map(f=>[anchor("feedback","纠错 #"+f.id),status(f.status),escape(f.resolution||f.message)])]));
}
function integrations(){
 return box("按标准号拉取能力库记录",small("查询正式标准目录。连接机构能力库后，可同时返回外部能力与适用范围。")+
 form("lookup",tools(input("code","标准号","","text","required"),submit("查标准号")))+queryOutput)+
 box("外部数据自动接入",info({"能力库接口":model.services.ability?"已配置":"待提供接口","外部数据源":model.services.feed?"已配置":"待提供接口","同步方式":"HTTPS JSON，按外部记录 ID 识别新增及变化，送专家审核","最近同步":model.sync_policy?.result||"尚无执行记录"})+
 (cap("admin")&&!offline?form("sync-policy",tools(input("minutes","同步间隔（分钟）",model.sync_policy?.minutes||60,"number",'min="15"'),check("enabled","启用定时拉取",model.sync_policy?.enabled),submit("保存同步配置"))):"")+
 (cap("collect")&&!offline&&model.services.feed?tools(button("sync","立即同步")):""));
}
function chat(){
 if(!cap("ask"))return box("知识问答",needs("问答")+tools(jump("qa")));
 const conversations=model.threads;
 const current=conversations.find(t=>t.id===activeThread);
 const turns=history.turns||[];
 const threadTools=form("thread",tools(input("title","新对话主题","","text",'maxlength="180" placeholder="如：电器安全标准"'),submit("新建对话")))+
 (conversations.length?form("open-thread",tools(select("id","已有对话",pairs(conversations,"id","title"),activeThread),submit("打开"))):"");
 const compose=current?form("ask",area("question","问题或追问","","required")+fields(select("scope","证据范围",{library:"法规知识库",document:"本会话上传资料"}),select("lang","回答语言",{auto:"跟随问题",zh:"中文",en:"English"}))+
 tools(!offline?check("generate","使用已配置的问答模型"):"",submit("发送问题"))):small("新建一个对话即可提问。");
 const upload=current?`<details><summary>上传文件并基于文档提问</summary>${small(offline?"可读取本地文本，或匹配文件中已有解析结果的 PDF。新图片与扫描件的识别在在线平台进行。":"支持照片、扫描件、PDF、Word 与文本。文件仅在本会话中用于检索。")}${form("chat-upload",tools(input("file","选择文件","","file",'accept=".pdf,.docx,.png,.jpg,.jpeg,.tiff,.txt,.md,.csv" required'),submit("读取文件")))}${escape((history.files||[]).map(f=>f.name).join(" · "))}</details>`:"";
 const messages=turns.map(t=>`<article><h3>${escape(t.question)}</h3><pre>${escape(t.answer)}</pre><details><summary>${t.citations.length} 条原文出处</summary>${t.citations.map((c,i)=>`<h3>[${i+1}] ${escape(c.title)} · ${c.page?"第 "+escape(c.page)+" 页":c.chunk!=null?"分片 "+escape(c.chunk):"正式目录 #"+escape(c.record_id)}</h3><blockquote>${escape(c.excerpt)}</blockquote>${passageAction(c)}`).join("")}</details>${tools(button("vote-up",t.vote===1?"已点赞":"点赞",t.id),button("vote-down",t.vote===-1?"已点踩":"点踩",t.id),button("turn-feedback","提出纠错",t.id))}</article>`).join("");
 const intent=turns.some(t=>/采购|报价|咨询|购买|quotation|purchase|contact/i.test(t.question));
 return box("我的对话",threadTools)+box(current?.title||"法规知识问答",messages+upload+compose+(intent?tools(anchor("leads","登记咨询或采购意向")):""))+tools(jump("qa"),anchor("leads","联系业务人员"));
}
function feedback(){
 if(!cap("ask"))return needs("反馈");
 return box("提交纠错或补充",form("feedback",area("message","反馈内容","","required")+(feedbackTurn?small("关联回答 #"+feedbackTurn):"")+tools(submit("提交反馈"))))+
 box("反馈处理记录",table(["编号","问题","状态","处理意见 / 修订","操作"],model.feedback.map(f=>[f.id,escape(f.message),status(f.status),escape(f.resolution||"")+(f.record_id?" · #"+f.record_id:""),cap("review")&&f.status==="open"?tools(button("resolve","回复并结案",f.id),button("correct","发起知识修订",f.id)):"—"])));
}
function leads(){
 return box("咨询与采购意向",cap("ask")?form("lead",fields(input("contact","联系方式","","text",'required maxlength="240"'),area("intent","需要咨询的产品与市场","","required"))+tools(check("consent","同意业务人员就本次需求联系我"),submit(offline?"体验登记":"登记意向"))):needs("咨询"))+
 (offline?small("离线体验仅演示登记流程，联系方式会被隐去。"):"")+
 box(cap("contacts")?"咨询登记":"我的咨询",table(["登记时间","联系信息","咨询意向"],model.leads.map(l=>[escape(l.created_at),escape(l.contact),escape(l.intent)])));
}
function search(){
 return box("查询法规、资料、来源与采集记录",form("search",tools(input("q","编号、名称或关键词",searchTerm,"text","required"),submit("全局搜索")))+queryOutput);
}
function renderView(){
 if(!main)return;
 const pages={overview,requirements,quality,records:recordsPage,review,scores,mapping,regions:()=>dictionaries("regions"),categories:()=>dictionaries("categories"),accounts,inbox,integrations,
 logs:()=>box("关键操作记录",table(["时间","用户","操作","对象"],model.audit.map(l=>[escape(l.at),escape(l.username||"访客"),escape(l.action),escape(l.subject)]))),
 collect,parse:parsePage,vector,ontology:()=>semantics("ontology"),entities:()=>semantics("entities"),graph:()=>semantics("graph"),chat,feedback,leads,search};
 main.innerHTML=(route==="overview"?"":`<h1>${escape(navNames[route])}</h1>`)+(requiredMenu[route]&&!cap(requiredMenu[route])?needs("管理"):pages[route]());
}
async function boot(){
 const hash=location.hash.slice(1),active=!offline||!hash||hash.startsWith("w/");
 if(dialog?.open)dialog.close();
 const serial=++stateId;
 document.body.classList.toggle("ws-active",active);root.hidden=!active;
 if(!active)return;
 try{
  if(!offline&&hash.startsWith("ticket=")){const ticket=hash.slice(7);window.history.replaceState(null,"",location.pathname+"#w/accounts");await request("auth/ticket",{ticket});}
  const wanted=location.hash.replace(/^#(?:w\/)?/,"")||"overview";route=Object.hasOwn(navNames,wanted)?wanted:"overview";
  queryOutput="";await refresh();if(serial!==stateId)return;
  if(route==="chat"){if(!model.threads.some(t=>t.id===activeThread))activeThread=model.threads[0]?.id||0;history=activeThread?await get("thread",{id:activeThread}):{turns:[],files:[]};}
  if(serial!==stateId)return;
  shell();renderView();document.title=navNames[route]+" · 法规认证知识平台";window.scrollTo(0,0);
  if(focusSearch&&route==="search"){$('[data-form="search"] [name="q"]').focus();focusSearch=false;}
 }catch(e){root.textContent="工作台读取失败。"+e.message;}
}
function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}
function read64(file){if(!file||!file.size||file.size>12*1024**2)return Promise.reject(Error("请选择 12 MB 以内的文件。"));return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(",")[1]);reader.onerror=()=>reject(Error("文件读取失败。"));reader.readAsDataURL(file);});}
async function changed(note){await refresh();if(route==="chat"&&activeThread)history=await get("thread",{id:activeThread});shell();renderView();message([note,model.storageNotice].filter(Boolean).join(" "));}
function pdfLink(r,id){
 return offline?button("pdf","保存原附件",id):rawlink("/api/workspace/pdf?record="+Number(r.id)+"&id="+Number(id),"保存原附件");
}
function focusGlobalSearch(){
 const field=!root.hidden&&route==="search"&&$('[data-form="search"] [name="q"]');
 if(field){field.focus();field.select();return;}
 focusSearch=true;location.hash=href("search");
}
function resultAction(r){
 if(r.kind==="法规")return button("detail","查看法规",r.id);
 if(r.kind==="资料")return docAnchor(r.id);
 if(r.kind==="来源")return offline?rawlink("#source?key="+encodeURIComponent(r.id),"查看来源"):button("source-result","查看来源",r.id);
 return offline?rawlink("#item?id="+Number(r.id),"查看采集记录"):button("collected-item","查看采集记录",r.id);
}
function passagePosition(p){
 return [p.page?"第 "+p.page+" 页":"", "片段 "+p.chunk].filter(Boolean).join(" · ");
}
function previewPassage(){
 const picker=dialog.querySelector('[name="chunk"]'),preview=dialog.querySelector("[data-source-preview]");
 if(picker&&preview)preview.textContent=evidenceDraft?.chunks.find(c=>c.chunk===Number(picker.value))?.text||"";
}
async function act(name,id){
 const r=model.records.find(x=>x.id===Number(id));
 if(name==="skip"){main.focus();return;}
 if(name==="quick-search"){focusGlobalSearch();return;}
 if(name==="source-result"||name==="source-page"){
  const [key,page]=name==="source-result"?[id,1]:JSON.parse(id),res=await get("source",{key,page}),s=res.source;
  showModal(s.source_name,info({"来源编码":key,"市场":s.region_code,"机构":s.authority})+
   small("已采集 "+res.total+" 条"+(res.pages>1?" · 第 "+res.page+" / "+res.pages+" 页":""))+
   table(["关联采集记录","查阅"],res.items.map(x=>[escape(x.title),button("collected-item","查看采集记录",x.id)]),"此来源暂无收录的采集记录。")+
   tools(res.page>1?button("source-page","上一页",JSON.stringify([key,res.page-1])):"",res.page<res.pages?button("source-page","下一页",JSON.stringify([key,res.page+1])):""));return;
 }
 if(name==="passage"){
  const [docId,chunk]=id.split("/").map(Number),p=await get("passage",{id:docId,chunk});
  let link="";
  if(offline){
   const k=local.snapshot.documents.some(d=>d.id===docId)?"doc":"supplied";
   link=rawlink("#"+k+"?id="+docId+"&chunk="+chunk,"在随附全文中查看");
  }else if(cap("edit")&&p.page&&model.docs.some(d=>d.id===docId&&d.mime_type==="application/pdf")){
   link=rawlink("/parse-review?document_id="+docId+"&page="+Number(p.page),"打开对应 PDF 原页");
  }
  showModal(p.title,small(passagePosition(p))+"<blockquote class=\"passage-w\">"+escape(p.text)+"</blockquote>"+tools(link));return;
 }
 if(name==="attach-evidence"){
  if(!r||!cap("edit"))throw Error("当前记录无法提交依据。");
  const docs=offline?[...local.snapshot.documents,...local.snapshot.supplied].filter(d=>d.full_text):model.docs.filter(d=>d.parser!=="image-metadata");
  if(!docs.length)throw Error("暂无可选择的已解析原文。");
  evidenceDraft={id:r.id,revision:r.revision,chunks:[]};
  showModal("为「"+r.name+"」提交原文依据",form("source-document",select("doc","原始资料",pairs(docs,"id","title"))+
   input("keyword","在原文内筛选（可选）")+tools(submit("选择原文片段"))));return;
 }
 if(name==="collected-item"){
  const item=await get(name,{id});
  showModal(item.title,info({"采集来源":item.source_key,"最近采集":item.date,...item.fields})+
   (item.excerpt?"<blockquote>"+escape(item.excerpt)+"</blockquote>":small("本条收录题录及可用字段。")));return;
 }
 if(name==="read-document"){
  const d=await get("document",{id}),families={regulation:"法规文件",standard:"标准",certification:"认证规则"};
  const profile=Object.entries(d.profile||{}).flatMap(([k,rows])=>rows.map(v=>[escape(k),escape(v.value),escape(v.excerpt),v.page?"第 "+Number(v.page)+" 页":"片段 "+Number(v.chunk)]));
  showModal(d.title,(d.family?small("解析结构 · "+families[d.family])+table(["字段","匹配原词","原文依据","定位"],profile,"该结构的字段暂无原文命中。"):"")+
   (d.mime_type==="application/pdf"?tools(rawlink("/parse-review?document_id="+Number(id),"逐页对照 PDF 原件")):"")+
   table(["原文位置","解析内容"],d.chunks.map(v=>[v.page?"第 "+Number(v.page)+" 页":"片段 "+Number(v.chunk),escape(v.text)])));return;
 }
 if(name==="close"){dialog.close();return;}
 if(name==="clear"){filter={};number=1;selected.clear();renderView();return;}
 if(name==="prev"||name==="next"){number=Math.max(1,number+(name==="next"?1:-1));renderView();return;}
 if(name==="detail"){
  if(!r)throw Error("此记录不可用。");
  const located=model.quality.items.some(x=>x.id===r.id&&x.kind==="located");
  showModal(r.name,info(Object.fromEntries(Object.entries(model.fields).map(([k,t])=>[t,labelValue(r,k)])))+
   tools(...(r.pdfs||[]).map(id=>pdfLink(r,id)),located?passageAction({document_id:r.source_document_id,chunk:r.source_chunk_index}):"",
    cap("edit")?button("attach-evidence","提交原文依据",r.id):"",!offline&&cap("edit")?rawlink("/catalog?record_id="+r.id,"目录与版本沿革"):""));return;}
 if(name==="pdf"){const f=local.pdf(id);if(!f)throw Error("离线试传的 PDF 仅在本次打开期间可保存。");const bytes=Uint8Array.from(atob(f.data),c=>c.charCodeAt(0));download(new Blob([bytes],{type:"application/pdf"}),f.name);return;}
 if(name==="new"||name==="edit"){editor(r||{});return;}
 if(name==="columns"){
  const ordered=[...columns,...Object.keys(model.fields).filter(k=>!columns.includes(k))];
  showModal("选择显示字段和列顺序",form("columns",ordered.map(k=>`<div data-col="${escape(k)}">${check(k,model.fields[k],columns.includes(k))}${button("up","上移",k)} ${button("down","下移",k)}</div>`).join("")+tools(submit("应用列设置"))));return;}
 if(["up","down"].includes(name)){const row=dialog.querySelector(`[data-col="${CSS.escape(id)}"]`);if(name==="up"&&row.previousElementSibling?.dataset.col)row.parentNode.insertBefore(row,row.previousElementSibling);if(name==="down"&&row.nextElementSibling?.dataset.col)row.parentNode.insertBefore(row.nextElementSibling,row);return;}
 if(name==="save-filter"){showModal("保存个人筛选模板",form("preference",input("name","模板名","","text","required")+tools(submit())));return;}
 if(name==="load-filter"){const p=model.preferences[$('[name="preset"]').value];if(p){filter=p.filter||{};columns=p.columns||fieldDefault;size=p.size||15;number=1;selected.clear();renderView();}return;}
 if(name==="import"){showModal("Excel 模板导入",small("第一张工作表使用模板表头。每一行生成一条待审核资料。")+form("import",input("file","Excel 文件","","file",'accept=".xlsx" required')+tools(submit("导入并提交审批"))));return;}
 if(name==="template"||name==="export"){
  const keys=name==="template"?Object.keys(model.fields):columns;
  const items=name==="template"?[]:selected.size?model.records.filter(r=>selected.has(r.id)):filtered();
  const blob=offline?window.WorkspaceExcel.write([keys.map(k=>model.fields[k]),...items.map(r=>keys.map(k=>labelValue(r,k)))]):
    name==="template"?await request("template",null,"GET",true):await request("export",{columns:keys,ids:items.map(r=>r.id)},"POST",true);
  download(blob,name==="template"?"法规导入模板.xlsx":"法规资料.xlsx");return;}
 if(name.startsWith("one-")||name.startsWith("bulk-")){
  const ids=name.startsWith("one-")?[Number(id)]:[...selected];if(!ids.length)throw Error("请勾选要操作的记录。");
  const operation=name.split("-")[1];showModal("确认提交"+actions[operation],form("bulk",`<input type="hidden" name="action" value="${operation}"><input type="hidden" name="ids" value="${ids.join(",")}">`+small(`对 ${ids.length} 条记录提交${actions[operation]}申请，经审核后生效。`)+submit("提交申请")));return;}
 if(name==="request"){
  const req=await get("request",{id});const payload=req.payload;
  const rows=payload.items.map(i=>{
   const v=i.values||i,e=i.evidence;
   const detail=e?small(e.title+" · "+passagePosition(e))+'<blockquote class="passage-w">'+escape(e.text)+'</blockquote>':
    `<details><summary>字段明细</summary>${info(Object.fromEntries(Object.entries(model.fields).map(([k,vv])=>[vv,labelValue(v,k)])))}${i.pdf?small("含待审 PDF 附件"):""}</details>`;
   return [escape(i.id||"新增"),escape(v.name||model.records.find(r=>r.id===i.id)?.name||""),detail];});
  showModal("审批单 #"+id,table(["目标","名称","明细"],rows)+(req.status==="pending"&&cap("review")?form("review",`<input type="hidden" name="id" value="${+id}">`+fields(select("decision","决定",{approve:"审核通过",reject:"退回"}),input("note","审核意见"))+submit("确认审核")):status(req.status)));return;}
 if(name==="new-node"){nodeModal(id);return;}
 if(name==="edit-node"){const n=model.nodes.find(x=>x.key===id);nodeModal(n.kind,n);return;}
 if(name==="remove-node"){const n=model.nodes.find(x=>x.key===id);if(!window.confirm("删除“"+n.name+"”？"))return;await mutate("node",{...n,delete:true});await changed("已删除。");return;}
 if(name==="mapping"){showModal("建立适用映射",form("mapping",fields(select("category","产品分类",nodeOptions("category")),select("record_id","已审核知识",pairs(model.records.filter(r=>r.review_status==="approved"),"id","name")),
 select("region","适用市场或联盟",{"":"全部",...nodeOptions("region"),...nodeOptions("alliance")}),select("mode","评定模式",{"":"未指定",...nodeOptions("mode")}),select("mark","标志",{"":"未指定",...nodeOptions("mark")}),input("scope","适用范围"))+tools(submit())));return;}
 if(name==="remove-map"){if(window.confirm("移除此适用映射？")){await mutate("mapping",{id:+id,delete:true});await changed("已移除。");}return;}
 if(name==="user-role"){const u=model.users.find(x=>x.id===+id);showModal("账号权限 · "+u.username,form("role",`<input type="hidden" name="id" value="${+id}">`+tools(select("role","角色",model.roles,u.role),check("enabled","启用账号",u.enabled),submit())));return;}
 if(name==="logout"){await mutate("auth/logout",{});await changed("已退出。");return;}
 if(name==="run-watch"||name==="sync"){message("正在连接已配置的来源…");const res=await mutate(name==="sync"?"sync":"watch-run",{source_key:id});await changed(`新增或变化 ${res.submitted} 条，内容相同 ${res.unchanged} 条。`);return;}
 if(name==="fact-yes"||name==="fact-no"){await mutate("fact-review",{id:+id,decision:name==="fact-yes"?"approved":"rejected"});await changed("已保存审核结果。");return;}
 if(name.startsWith("vote-")){const t=history.turns.find(t=>t.id===+id),v=name==="vote-up"?1:-1;await mutate("vote",{id:+id,vote:t.vote===v?0:v});await changed("已保存评价。");return;}
 if(name==="turn-feedback"){feedbackTurn=+id;location.hash=href("feedback");return;}
 if(name==="correct"){showModal("关联纠错的知识修订",form("correction",`<input type="hidden" name="feedback" value="${+id}">`+select("record","选择记录",{"":"新增法规",...pairs(model.records,"id","name")})+tools(submit("填写修订内容"))));return;}
 if(name==="resolve"){showModal("反馈处理",form("feedback-resolve",`<input type="hidden" name="id" value="${+id}">`+area("resolution","处理意见","","required")+tools(submit("回复并结案"))));return;}
}
async function onForm(name,f){
 const formData=new FormData(f),p=Object.fromEntries(formData);
 if(name==="source-document"){
  if(!evidenceDraft)throw Error("请重新选择要补充依据的法规。");
  const d=await get("document",{id:p.doc}),term=p.keyword.trim().toLowerCase();
  const chunks=d.chunks.filter(c=>c.text?.trim()&&(!term||c.text.toLowerCase().includes(term)));
  if(!chunks.length)throw Error("未找到可选的片段，请修改关键词或资料。");
  Object.assign(evidenceDraft,{doc:Number(p.doc),chunks});
  const choices=Object.fromEntries(chunks.map(c=>[c.chunk,passagePosition(c)+" · "+c.text.slice(0,65)]));
  showModal(d.title,form("submit-evidence",select("chunk","待审核原文片段",choices)+
   '<blockquote class="passage-w" data-source-preview aria-live="polite"></blockquote>'+tools(submit("提交专家审核"))));
  previewPassage();return;
 }
 if(name==="submit-evidence"){
  if(!evidenceDraft)throw Error("请重新选择待审原文。");
  const {id,revision,doc}=evidenceDraft,res=await mutate("record",{action:"evidence",items:[{id,revision,document_id:doc,chunk:Number(p.chunk)}]});
  evidenceDraft=null;await changed("已提交原文依据，审批单 #"+res.request_id);return;
 }
 if(name==="match-file"){const d=await mutate(name,p);showModal(d.title,small("文件摘要匹配，解析方式为 "+d.parser)+tools(docAnchor(d.id)));return;}
 if(name==="module"){moduleFilter=p.module;renderView();return;}
 if(name==="filter"){filter=p;number=1;selected.clear();renderView();return;}
 if(name==="columns"){const keys=[...f.querySelectorAll("[data-col]")].filter(n=>n.querySelector("input").checked).map(n=>n.dataset.col);
 if(!keys.length)throw Error("请保留至少一列。");columns=keys;dialog.close();renderView();return;}
 if(name==="preference"){await mutate(name,{name:p.name,value:{filter,columns,size}});await changed("已保存个人筛选。");return;}
 if(name==="record"){
  const rid=Number(p.id),file=p.pdf;delete p.pdf;const correction=Number(p.correction)||null;delete p.correction;
  const res=await mutate("record",{action:rid?"edit":"create",items:[{...p,id:rid}],pdf:file?.size?{name:file.name,data:await read64(file)}:null,correction});await changed("已提交审批单 #"+res.request_id);return;}
 if(name==="bulk"){const ids=p.ids.split(",").map(Number);const res=await mutate("record",{action:p.action,items:ids.map(id=>({id,revision:model.records.find(r=>r.id===id)?.revision}))});selected.clear();await changed("已提交审批单 #"+res.request_id);return;}
 if(name==="import"){const res=await mutate(name,offline?{file:p.file}:{data:await read64(p.file)});await changed(`共 ${res.count} 条，审批单 #${res.request_id}`);return;}
 if(name==="auth"){const res=await mutate("auth/"+p.action,p);await changed("已登录。");if(res.recovery)showModal("请保存一次性恢复码",small("找回密码时用此码验证身份。请在关闭前自行保存。")+`<pre>${escape(res.recovery)}</pre>`);return;}
 if(name==="policy"||name==="role"||name==="sync-policy"){await mutate(name,{...p,independent:p.independent==="on",enabled:p.enabled==="on"});await changed("配置已保存。");return;}
 if(name==="node"){await mutate(name,{...p,members:formData.getAll("members")});await changed("分类已更新。");return;}
 if(name==="watch"){await mutate(name,{...p,keywords:p.keywords.split(/[,，、]/).map(x=>x.trim()).filter(Boolean),enabled:p.enabled==="on"});await changed("监测配置已保存。");return;}
 if(name==="score"){await mutate(name,{...p,reference:JSON.parse(p.reference),output:JSON.parse(p.output)});await changed("已记录本组评分。");return;}
 if(name==="parse"){const response=await fetch("/api/processing/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({filename:p.docfile.name,content_base64:await read64(p.docfile),document_family:p.family,auto_extract:true,use_model:false})});
  const res=await response.json();if(!response.ok||res.error)throw Error(res.error||"上传失败。");showModal("解析任务",small(`已接收 ${p.docfile.name}，任务编号 ${res.task?.id||""}`)+tools(rawlink("/business","查看处理进度"),jump("docs")));return;}
 if(name==="entities"){const res=await mutate(name,p);await changed("已新增 "+res.created+" 条候选关系。");return;}
 if(name==="lookup"){const res=await mutate(name,p);queryOutput=table(["编号","名称","版本","来源"],[...res.local.map(r=>({...r,src:"正式目录"})),...res.external.map(r=>({...r,src:"机构接口"}))].map(r=>[escape(r.code),escape(r.name),escape(r.version),r.src]),res.configured?"未匹配此编号。":"本地目录未匹配此编号，外部能力库待接入。");renderView();return;}
 if(name==="search"||name==="vector"){
  const res=await mutate(name,p);if(name==="search")searchTerm=p.q;
  queryOutput=name==="vector"?table(["出处","相似度","原文片段","定位"],res.items.map(r=>[escape(r.title)+" · "+escape(passagePosition(r)),Number(r.score).toFixed(3),escape(r.text),passageAction(r)])):
    table(["类别","名称","编号 / 来源","查看"],res.items.map(r=>[escape(r.kind),escape(r.title),escape(r.detail),resultAction(r)]));
  renderView();return;}
 if(name==="thread"){const res=await mutate(name,p);activeThread=res.id;await changed("已新建会话。");return;}
 if(name==="open-thread"){activeThread=+p.id;await changed("");return;}
 if(name==="chat-upload"){const file=p.file;message("正在读取文件…");const res=await mutate(name,offline?{file,thread_id:activeThread}:{data:await read64(file),name:file.name,thread_id:activeThread});await changed("已接入 "+res.name+"。提问时选择“本会话上传资料”。");return;}
 if(name==="ask"){message("正在根据当前会话检索依据…");await mutate(name,{...p,thread_id:activeThread,generate:p.generate==="on"});await changed("");return;}
 if(name==="correction"){editor(model.records.find(r=>r.id===+p.record)||{},+p.feedback);return;}
 if(name==="feedback"){await mutate(name,{...p,turn_id:feedbackTurn});feedbackTurn=null;await changed("已登记反馈。");return;}
 if(name==="lead"){await mutate(name,{...p,consent:p.consent==="on"});await changed(offline?"本地体验已登记。":"咨询意向已登记。");return;}
 if(["review","mapping","feedback-resolve","demo-role"].includes(name)){await mutate(name,p);await changed("已保存。");}
}
function fail(e){if(dialog?.open){let err=dialog.querySelector("[data-error]");if(!err){err=document.createElement("p");err.dataset.error="1";err.className="msg-w error";dialog.querySelector("[data-modal]").prepend(err);}err.textContent=e.message;}else message(e.message,true);}
root.addEventListener("click",async e=>{const btn=e.target.closest("[data-act]");if(!btn||btn.disabled)return;btn.disabled=true;try{await act(btn.dataset.act,btn.dataset.id);}catch(x){fail(x);}finally{btn.disabled=false;}});
root.addEventListener("submit",async e=>{const f=e.target.closest("[data-form]");if(!f)return;e.preventDefault();const btn=e.submitter||f.querySelector('[type="submit"]');if(btn?.disabled)return;if(btn)btn.disabled=true;try{await onForm(f.dataset.form,f);}catch(x){fail(x);}finally{if(btn)btn.disabled=false;}});
root.addEventListener("change",e=>{const input=e.target;
 if(input.name==="chunk"&&dialog?.open)previewPassage();
 if(input.name==="pageSize"){size=+input.value;number=1;renderView();}
 if(input.matches("[data-pick]")){const id=+input.dataset.pick;if(input.checked)selected.add(id);else selected.delete(id);renderView();}
 if(input.matches("[data-pick-page]")){filtered().slice((number-1)*size,number*size).forEach(r=>input.checked?selected.add(r.id):selected.delete(r.id));renderView();}
});
document.addEventListener("keydown",e=>{
 if(e.key==="/"&&!e.ctrlKey&&!e.altKey&&!e.metaKey&&!e.isComposing&&!e.defaultPrevented&&!document.querySelector("dialog[open]")&&
    !e.target.closest?.("input,textarea,select,[contenteditable=true]")){e.preventDefault();focusGlobalSearch();}
});
window.addEventListener("hashchange",boot);boot();
})();
