"use strict";
const data = JSON.parse(document.getElementById("snapshot").textContent);
const view = document.getElementById("view");
const sections = [
  ["工作台", [["home","工作台"],["map","法规认证地图"],["access","产品准入"],["qa","法规问答"]]],
  ["采集与证据", [["sources","来源台账"],["collection","采集结果"],["docs","解析原文"],["runs","采集执行"]]],
  ["知识治理", [["catalog","正式知识目录"],["changes","变化待办"],["governance","候选校核"],["ontology","知识本体"],["graph","知识图谱"]]]
];
const sectionOf = {source:"sources",item:"collection",doc:"docs",supplied:"docs"};
const labels = {
  detail:"详情页",listing:"入口 / 列表",attachment:"附件",regulation:"法规",standard:"标准",certification:"认证",gma:"市场准入",
  requirement:"技术要求",test_item:"检测项目",direct:"可直接采集",adapter:"需要适配",manual_authorized:"需人工或授权",
  metadata_only:"仅元数据",unavailable:"暂不可采集",verified:"已联网核验",failed:"失败",preclassified:"待实测",
  pending:"待校核",approved:"已确认",completed:"完成",partial:"部分完成",restricted:"受限",passed:"通过",
  high:"高",medium:"中",low:"低",open:"待处理",resolved:"已处理",dismissed:"已忽略",unknown:"待确认",
  possible_new_rule:"可能新增法规或标准",possible_new_requirement:"可能新增认证或要求",
  insufficient_data:"正式依据不足",ready:"资料链较完整",domain:"知识领域",node_type:"对象类型",record_type:"记录类型",relation:"关系类型",active:"有效",
  mixed:"混合来源",html:"网页",xml:"XML",api:"公开接口",stable:"稳定",attention:"待关注",not_started:"待建设"
};
const say = x=>labels[x] || x || "未记录";
const h = value => String(value ?? "").replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const uniq = a=>[...new Set(a.filter(Boolean))];
const sourceMap = new Map(data.sources.map(x=>[x.source_key,x]));
const documentMap = new Map(data.documents.map(x=>[x.id,x]));
const itemMap = new Map(data.items.map(x=>[x.id,x]));
const titles = new Map(sections.flatMap(([,items])=>items));
const regionName = code=>data.regions[code]?.name || code || "未标注";
const kinds = [["listing","入口 / 列表"],["detail","详情页"],["attachment","附件"]];
let page, params;
const val = k=>params.get(k)||"";
const addr = (route, values={})=>{
  const q = new URLSearchParams(Object.entries(values).filter(([,v])=>v!=null && String(v)!==""));
  return "#"+encodeURIComponent(route)+(q.size?"?"+q:"");
};
const link = (route, text, values={}, cls="")=>`<a class="${h(cls)}" href="${h(addr(route,values))}">${h(text)}</a>`;
const badge = text=>`<span class="pill-off">${h(text)}</span>`;
const empty = text=>`<div class="empty-off">${h(text)}</div>`;
const card = (title,body)=>`<section class="card"><h2>${h(title)}</h2>${body}</section>`;
const head = (title, desc="")=>`<header class="offhead"><h1>${h(title)}</h1>${desc?`<p>${h(desc)}</p>`:""}</header>`;
const details = pairs=>`<dl class="facts">${pairs.filter(([,v])=>v!==""&&v!=null).map(([k,v])=>`<dt>${h(k)}</dt><dd>${h(v)}</dd>`).join("")}</dl>`;
const tiles = (items, fallback="暂无记录。")=>items.length ? `<div class="tiles">${items.map(([route,title,note,values])=>`<a class="tile" href="${h(addr(route,values))}"><b>${h(title)}</b><small>${h(note)}</small></a>`).join("")}</div>` : empty(fallback);
const metrics = entries=>`<div class="metrics">${entries.map(([name,n,route,values])=>`<a class="metric" href="${h(addr(route,values))}"><span>${h(name)}</span><strong>${h(n)}</strong></a>`).join("")}</div>`;
const asDay = s=>(s||"").slice(0,10);
const sourceLink = k=>sourceMap.has(k)?link("source",k,{key:k}):h(k);
const docLink = id=>documentMap.has(id)?link("doc","阅读解析原文",{id}):"";
const itemsFor = key=>data.items.filter(i=>i.source_key===key);
const docsFor = key=>data.documents.filter(d=>d.source_key===key);
const sourceOf = i=>sourceMap.get(i.source_key)||{};
const table = (columns,body)=>body.length?`<div class="tablewrap"><table><thead><tr>${columns.map(x=>`<th scope="col">${h(x)}</th>`).join("")}</tr></thead><tbody>${body.map(line=>`<tr>${line.map(x=>`<td>${x}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`:empty("没有符合条件的记录。");
function paged(rows, columns, row, size=24) {
  const pages=Math.max(1,Math.ceil(rows.length/size));
  const p=Math.min(pages,Math.max(1,parseInt(val("p"),10)||1));
  const to = n=>({...Object.fromEntries(params),p:n});
  return `<p class="note-line">共 ${rows.length} 条${pages>1?` · 第 ${p} / ${pages} 页`:""}</p>`+
    table(columns, rows.slice((p-1)*size,p*size).map(row))+
    (pages>1?`<div class="pager">${p>1?link(page,"上一页",to(p-1)):""} ${p<pages?link(page,"下一页",to(p+1)):""}</div>`:"");
}
function selector(key, name, options) {
  return `<label>${h(name)}<select name="${h(key)}"><option value="">全部</option>${options.map(([k,v])=>`<option value="${h(k)}"${val(key)===String(k)?" selected":""}>${h(v)}</option>`).join("")}</select></label>`;
}
function form(filters=[], placeholder="标题、来源或关键词") {
  return `<form class="tools" data-filter><label class="query">搜索<input name="q" value="${h(val("q"))}" placeholder="${h(placeholder)}" autocomplete="off"></label>${filters.join("")}<button class="btn" type="submit">查询</button>${link(page,"清除")}</form>`;
}
const match = (...fields)=>!val("q") || fields.join(" ").toLowerCase().includes(val("q").toLowerCase().trim());
const is = (key,actual)=>!val(key)||val(key)===actual;
const regions = ()=>uniq(data.sources.map(s=>s.region_code)).map(r=>[r,regionName(r)]);
const sourceOptions = list=>uniq(list.map(x=>x.source_key)).sort().map(k=>[k,k]);
const itemRow = i=>[link("item",i.title||i.source_url||i.source_key,{id:i.id}),sourceLink(i.source_key),h(say(i.item_type)),h(regionName(sourceOf(i).region_code)),docLink(i.document_id)|| (data.files[i.id]?badge("内附原件"):"—")];
const itemCols = ["采集标题","来源","记录类型","市场","证据"];
const officialAddress = url=>url?details([["官方地址",url]]):"";
function home() {
  const m=data.map.summary;
  const nSites=uniq(data.items.map(i=>i.source_key)).length;
  const p=data.first50.summary;
  const highlights=data.documents.filter(d=>!/^\s*(§|入口页|.*\.(pdf|zip)$)/i.test(d.title));
  const open=data.changes.filter(c=>c.status==="open").length;
  const cards=[
    ["采集结果",data.items.length+" 条",`${nSites} 个来源已有采集记录，入口页、详情和附件按原站归档。`,"collection","查看采集结果"],
    ["正式知识",m.knowledge+" 条","查询已审核记录的适用市场、版本和效力状态。","catalog","打开正式知识目录"],
    ["变化待办",open+" 项",`${m.high_changes} 项高优先级变化，待办可追溯到已有材料。`,"changes","查看变化待办"],
    ["产品准入",data.products.length+" 个产品族",`按产品及目标市场查阅 ${data.access.length} 条已保存的准入查询。`,"access","查看准入结果"]
  ];
  return `<div class="hero"><div><h1>法规、认证与产品准入统一查询</h1><p>从市场和产品查找法规依据，沿来源网站、采集记录和解析原文核对每份材料。</p></div></div>`+
    metrics([["已接入资料",data.counts.documents,"docs"],["登记来源",data.sources.length,"sources"],["待校核内容",data.counts.pending,"governance"],["目标市场",m.target_markets,"map"]])+
    `<div class="business-grid">${cards.map(([a,b,c,r,t])=>`<div class="business-card"><span class="eyebrow">${h(a)}</span><strong>${h(b)}</strong><p>${h(c)}</p>${link(r,t+" →",{},"business-link")}</div>`).join("")}</div>`+
    card("采集原文",tiles(highlights.slice(0,6).map(d=>["doc",d.title,d.source_key+" · "+asDay(d.created_at),{id:d.id}])))+
    `<div class="two">${card("知识库建设",details([["法规 / 标准",(m.type_counts.regulation||0)+" / "+(m.type_counts.standard||0)],["认证 / 技术要求",(m.type_counts.certification||0)+" / "+(m.type_counts.requirement||0)],["检测项目",m.type_counts.test_item||0],["准入链完整市场",m.complete_markets+" / "+m.target_markets]])+link("map","查看法规认证地图 →"))}${card("首批来源采集核验",details([["核验范围",p.total+" 个站点"],["通过 / 部分完成",p.passed+" / "+p.partial],["受限 / 失败",p.restricted+" / "+p.failed],["核验批次","第 "+data.first50.round+" 轮"]])+link("runs","查看逐站结果 →"))}</div>`;
}
function sources() {
  const filtered=data.sources.filter(s=>is("region",s.region_code)&&is("type",s.source_type)&&is("v",s.verification_status)&&match(s.source_name,s.source_key,s.authority,s.base_url,regionName(s.region_code)));
  return head("来源台账","按市场和业务领域查找网站，打开来源即可查看已采集的材料。")+card("登记来源",form([selector("region","市场",regions()),selector("type","领域",uniq(data.sources.map(s=>s.source_type)).map(k=>[k,say(k)])),selector("v","联网核验",[["preclassified","待实测"],["verified","已联网核验"],["failed","核验异常"]])])+
    paged(filtered,["来源","市场","领域","可采集性","已采集"],s=>[link("source",s.source_name,{key:s.source_key})+`<small class="sub">${h(s.source_key)}</small>`,h(regionName(s.region_code)),h(say(s.source_type)),h(say(s.harvestability)),String(itemsFor(s.source_key).length)]));
}
function source() {
  const s=sourceMap.get(val("key"));if(!s)return empty("未找到此来源。");
  const records=itemsFor(s.source_key);
  return link("sources","← 来源台账",{},"back")+head(s.source_name,s.source_key)+card("来源信息",details([["市场",regionName(s.region_code)],["主管机构",s.authority],["领域",say(s.source_type)],["接入方式",say(s.access_method)],["可采集性",say(s.harvestability)],["联网核验",s.verification_status==="failed"?"核验异常":say(s.verification_status)],["最近核验",s.last_verified_at],["HTTP 状态",s.last_http_status],["官方地址",s.base_url]]))+
    card("已采集记录",paged(records,itemCols,itemRow))+card("解析原文",tiles(docsFor(s.source_key).map(d=>["doc",d.title,d.parser,{id:d.id}]),"此来源尚无随附的公开解析原文。"));
}
function collection() {
  let items=data.items.filter(i=>is("type",i.item_type)&&is("source",i.source_key)&&is("region",sourceOf(i).region_code)&&match(i.title,i.text_excerpt,i.source_key,i.source_url,...Object.values(i.fields)));
  return head("采集结果","查看实际取得的网页记录、抽取片段和原始附件。")+card("采集记录",form([selector("region","市场",regions()),selector("type","记录类型",kinds),selector("source","来源",sourceOptions(data.items))])+paged(items,itemCols,itemRow));
}
function item() {
  const i=itemMap.get(Number(val("id"))); if(!i)return empty("未找到此采集记录。");
  const file=data.files[i.id], fields=Object.entries(i.fields).map(([k,v])=>[({code:"编号候选",date:"日期候选",status:"状态候选"})[k]||k,Array.isArray(v)?v.join(" · "):v]);
  return link("collection","← 采集结果",{source:i.source_key},"back")+head(i.title||i.source_key,say(i.item_type)+" · "+regionName(sourceOf(i).region_code))+
    card("采集依据",sourceLink(i.source_key)+details([["采集时间",i.last_seen_at],["官方地址",i.source_url]])+`<div class="actions">${docLink(i.document_id)}${file?`<button class="btn" data-download="${Number(i.id)}">保存原附件 · ${h(file.name)}（${Math.ceil(file.size/1024)} KB）</button>`:""}</div>`)+
    (i.text_excerpt?card("原始网页摘录",`<div class="source-text">${h(i.text_excerpt)}</div>`):"")+
    (fields.length?card("自动抽取字段",details(fields)+`<p class="note-line">以上为采集时提取的候选字段，正式知识可在目录中查询。</p>`):"");
}
function docs() {
  const docs=data.documents.filter(d=>is("source",d.source_key)&&match(d.title,d.source_key,d.full_text));
  const suppliedDocs=data.supplied.filter(d=>!val("source")&&match(d.title,d.full_text));
  const suppliedList=card("接入的法规与标准",table(["资料名称","页数 / 工作表数","随附内容"],suppliedDocs.map(d=>[link("supplied",d.title,{id:d.id}),h(d.pages?d.pages+" 页":d.sheets?d.sheets+" 张工作表":"—"),h(d.full_text?"解析全文":"题录与解析信息")])));
  return head("解析原文",`平台共接入 ${data.counts.documents} 份资料，本文件随附 ${data.documents.length} 份公开采集原文及 ${data.supplied.length} 份法规标准资料的题录。`)+card("公开资料",form([selector("source","来源",sourceOptions(data.documents))],"在标题或全文中搜索")+paged(docs,["资料名称","来源","解析方式","正文字符"],d=>[link("doc",d.title,{id:d.id}),sourceLink(d.source_key),h(d.parser),String(d.full_text.length)]))+(val("source")?"":suppliedList);
}
function doc() {
  const d=documentMap.get(Number(val("id")));if(!d)return empty("该资料未随本文件提供。");
  const relates=data.items.filter(i=>i.document_id===d.id);
  return link("docs","← 解析原文",{},"back")+head(d.title,d.source_key+" · "+d.parser)+
    card("来源依据",sourceLink(d.source_key)+officialAddress(d.url)+tiles(relates.map(i=>["item",i.title,say(i.item_type)+(data.files[i.id]?" · 内附原件":""),{id:i.id}]),"来源于平台的入口页快照。"))+
    card("解析全文",`<div class="source-text" tabindex="0">${h(d.full_text)}</div>`);
}
function runs() {
  const r=data.first50;
  const tab=val("show")||"runs";
  const cols=["来源","状态","抓取页数","发现条目","发现附件","采集开始"];
  const tabs=`<div class="chips">${link("runs","本地采集批次")}${link("runs","首批站点核验",{show:"sites"})}</div>`;
  const body=tab==="sites"?paged(Object.entries(r.status),["来源","第 "+r.round+" 轮核验"],([k,v])=>[sourceLink(k),h(say(v))]):
    paged(data.runs,cols,x=>[sourceLink(x.source_key),h(say(x.status)),String(x.pages_fetched),String(x.items_discovered),String(x.attachments_discovered),h(x.started_at)]);
  return head("采集执行","本地执行记录与首批站点核验按各自批次展示。")+card(tab==="sites"?"首批站点核验":"本地深采集批次",tabs+body);
}
function catalog() {
  const records=data.catalog.filter(r=>match(r.name,r.code,r.region_code,r.region_name,r.product_class));
  return head("正式知识目录","目录收录平台已审核的记录。效力状态与审核状态分别列示。")+card("正式知识",form()+ (records.length?records.map(r=>`<h3>${h(r.name)}</h3>${badge(say(r.record_type))}${badge("已审核")}${details([["市场",r.region_name||r.region_code],["产品分类",r.product_class],["编号",r.code||"未标注"],["效力状态",say(r.status)],["版本",r.version||"未标注"],["生效日期",r.effective_from],["失效日期",r.effective_to]])}${docLink(r.source_document_id)}`).join("<hr>"):empty("没有符合条件的正式知识。")));
}
function changes() {
  return head("变化待办","按记录时间查看系统识别的变化及其处理状态。")+card("变化记录",form([selector("severity","优先级",uniq(data.changes.map(x=>x.severity)).map(k=>[k,say(k)]))])+
    paged(data.changes.filter(x=>is("severity",x.severity)&&match(x.title,x.id,x.region_code,x.product_class)),["事项","类型","优先级","状态","登记时间","随附依据"],x=>[h("#"+x.id+" · "+x.title),h(say(x.event_type)),h(say(x.severity)),h(say(x.status)),h(x.created_at),docLink(x.source_document_id)||"—"]));
}
function governance() {
  const rows=data.candidates.filter(r=>is("type",r.candidate_type)&&match(r.candidate_name,r.candidate_code,documentMap.get(r.document_id)?.title));
  return head("候选校核",`全平台有 ${data.counts.pending} 条待校核内容。本页列出随公开原文一同导出的 ${data.candidates.length} 条候选记录。`)+card("公开来源候选",form([selector("type","类型",uniq(data.candidates.map(c=>c.candidate_type)).map(k=>[k,say(k)]))])+ paged(rows,["候选名称","编号","类型","状态","原文"],r=>[h(r.candidate_name),h(r.candidate_code||"—"),h(say(r.candidate_type)),h(say(r.status)),docLink(r.document_id)]));
}
function ontology() {
  const o=data.ontology, names=new Map(o.node_types.map(n=>[n.key,n.label]));
  const loc=arr=>arr.map(k=>names.get(k)||say(k)).join("、");
  return head("知识本体",o.principle)+card("知识领域",`<div class="tiles">${o.domains.map(d=>`<div class="tile"><b>${h(d.label)}</b><small>${h(d.purpose)}</small></div>`).join("")}</div>`)+
    card("业务对象",table(["对象","说明"],o.node_types.map(n=>[h(n.label),h(n.description)])))+
    card("关系定义",table(["关系","起点","终点"],o.relations.map(r=>[h(r.label),h(loc(r.source_types)),h(loc(r.target_types))])))+
    card("本体词表",`<details><summary>查看 ${data.terms.length} 个登记术语</summary>${table(["术语","编码","类别"],data.terms.map(r=>[h(r.canonical_name),h(r.code),h(say(r.term_type))]))}</details>`);
}
function graph() {
  const names=new Map(data.catalog.map(r=>[r.id,r.name]));
  const relLabels=new Map(data.ontology.relations.map(r=>[r.key,r.label]));
  return head("知识图谱",`当前正式目录含 ${data.catalog.length} 个知识节点和 ${data.relations.length} 条已确认关系。`)+card("知识节点",tiles(data.catalog.map(r=>["catalog",r.name,say(r.record_type)+" · "+(r.region_name||r.region_code||"市场待标注"),{q:r.name}])))+
    card("已确认关系",data.relations.length?table(["起点","关系","终点"],data.relations.map(r=>[h(names.get(r.source_record_id)),h(relLabels.get(r.relation_type)||r.relation_type),h(names.get(r.target_record_id))])):empty("这份快照中暂无已确认的节点连线。"));
}
function market() {
  const all=data.map.target_markets;
  const selected=all.find(m=>m.region_code===val("region"))||all.find(m=>m.region_code==="DE")||all[0];
  if(!selected)return head("法规认证地图")+empty("暂无目标市场。");
  const region=selected.region_code;
  const chips=`<div class="chips">${all.map(m=>`<a href="${h(addr("map",{region:m.region_code}))}" aria-current="${m===selected}">${h(m.region_name)}</a>`).join("")}</div>`;
  const world=document.getElementById("world").content.cloneNode(true);
  const markers=world.querySelector("#markers");
  markers.innerHTML=all.filter(m=>m.mappable).map(m=>`<a href="${h(addr("map",{region:m.region_code}))}" aria-label="${h(m.region_name)}"><circle class="pin ${m===selected?"active":""}" cx="${Number(m.x)}" cy="${Number(m.y)}" r="${m===selected?8:5}" fill="${({high:"#c74e4e",attention:"#d4952d",stable:"#2b8a5b"})[m.attention]||"#8496ab"}"><title>${h(m.region_name)}</title></circle></a>`).join("");
  const records=data.items.filter(i=>sourceOf(i).region_code===region);
  return head("法规认证地图",`按平台 ${data.map.as_of} 的有效性口径展示 ${all.length} 个目标市场。`)+`<div class="two">${card("全球市场",world.querySelector("svg").outerHTML+chips)}${card(selected.region_name+" · 市场详情",details([["正式知识范围",selected.knowledge_scope_codes.map(regionName).join(" + ")],["正式知识",selected.knowledge_count+" 条"],["变化待办",selected.pending_changes+" 项"],["准入链覆盖",selected.chain_stage_count+" / 5 个环节"],["当地登记来源",data.sources.filter(s=>s.region_code===region).length+" 个"],["当地采集记录",records.length+" 条"]])+`<div class="actions">${link("sources","查看当地来源",{region})}${link("collection","查看当地采集结果",{region})}</div>`)}</div>`+
    card("当地采集材料",tiles(records.slice(0,6).map(i=>["item",i.title,i.source_key,{id:i.id}]),"此市场尚无本地采集样本。"));
}
function access() {
  const family=data.products.find(f=>f.id===val("family"))||data.products[0];
  if(!family)return head("产品准入")+empty("暂无产品分类。");
  const samples=data.access.filter(a=>a.family===family.id);
  const sample=samples.find(a=>a.region===val("region"))||samples[0];
  const families=`<div class="chips">${data.products.map(f=>`<a aria-current="${f===family}" href="${h(addr("access",{family:f.id,region:val("region")}))}">${h(f.name_zh)}</a>`).join("")}</div>`;
  const markets=`<div class="chips">${samples.map(a=>`<a aria-current="${a===sample}" href="${h(addr("access",{family:family.id,region:a.region}))}">${h(regionName(a.region))}</a>`).join("")}</div>`;
  return head("产品准入","选择产品族和市场，查看平台已经执行并保存的查询。判断日期以该次查询记录为准。")+card("产品分类",families+details([["产品形态",family.children.map(k=>k.name_zh).join("、")],["判断参数",family.decision_attributes.map(k=>k.label).join("、")]]))+card("已保存的市场准入查询",markets+(sample?badge(say(sample.status))+details([["判断日期",sample.date],["产品路径",sample.path.join(" › ")],["监管分类",sample.category],["监管维度",sample.dimensions.join("、")],["匹配正式知识",sample.summary.matched_records],["已确认路径",sample.summary.approved_paths],["后续工作",sample.next_action]])+
    (sample.leads.length?`<h3>研究线索</h3><ul>${sample.leads.map(t=>`<li>${h(t)}</li>`).join("")}</ul>`:"")+`<div class="actions">${sample.sources.map(sourceLink).join(" ")}</div>`:empty("本文件中没有该产品的历史准入查询。")));
}
function supplied() {
  const d=data.supplied.find(x=>x.id===Number(val("id"))); if(!d)return empty("未找到这份资料。");
  return link("docs","← 解析原文",{},"back")+head(d.title,"平台接入的法规与标准资料")+
    card("解析信息",details([["PDF 页数",d.pages],["工作表数",d.sheets],["解析方式",d.parser],["接入日期",asDay(d.created_at)]]))+
    (d.full_text?card("解析全文",'<div class="source-text" tabindex="0">'+h(d.full_text)+'</div>'):empty("本文件随附此资料的题录与解析信息。"));
}
function savedAnswer() {
  const a=data.saved_question;
  if(!a)return "";
  const refs=a.citations.map((r,i)=>'<details><summary>来源 '+(i+1)+' · '+h(documentMap.get(r.doc).title)+'</summary><blockquote class="source-text">'+h(r.text)+'</blockquote>'+docLink(r.doc)+'</details>').join("");
  return card("平台历史问答",'<h3>'+h(a.question)+'</h3><p class="note-line">采集批次为 2026-09-21。关于测试程序的位置，历史回答引用了 2017 年提案，仍需根据现行 eCFR 复核。</p><details><summary>查看当次回答及 '+a.citations.length+' 条引用</summary><div class="source-text">'+h(a.answer)+'</div>'+refs+'</details>');
}
const vocabulary = [["电视","television"],["能效","energy"],["冰箱","refrigerat"],["制冷","refrigerat"],["洗碗机","dishwasher"],["产品安全","prodsg"],["德国","prodsg"],["美国","energy"],["市场准入","market access"],["欧盟","directive"],["印尼","permen"]];
function qa() {
  const q=val("q").trim().toLowerCase();
  const terms=uniq([...q.split(/[\s，。？?、,;；]+/).filter(t=>t.length>1),...vocabulary.filter(([k])=>q.includes(k)).map(([,v])=>v)]);
  const hits=[];
  if(terms.length)for(const d of [...data.documents,...data.supplied.filter(d=>d.full_text)]){
    const raw=d.full_text.toLowerCase(), title=d.title.toLowerCase();
    let score=0, pos=-1;
    for(const term of terms){const p=raw.indexOf(term);if(p>=0){score++;if(pos<0)pos=p;}if(title.includes(term))score+=3;}
    if(score)hits.push({d,score,pos:Math.max(0,pos)});
  }
  hits.sort((a,b)=>b.score-a.score);
  const result=hits.map(({d,pos})=>`<article><h3>${link(d.source_key?"doc":"supplied",d.title,{id:d.id})}</h3><p class="sub">${h(d.source_key||"接入法规")} · ${h(asDay(d.created_at))}</p><blockquote class="source-text">${h(d.full_text.slice(Math.max(0,pos-75),pos+320))}</blockquote></article>`).join("");
  return head("法规问答 · 原文检索","离线模式按关键词检索随附全文，直接展示命中的证据片段。在线平台可进一步生成模型回答。")+card("查找依据",form([],"输入关键词，例如电视、洗碗机或 ProdSG")+`<div class="chips">${["洗碗机","电视","ProdSG"].map(q=>link("qa",q,{q})).join("")}</div>`)+
    (q?card("原文匹配 · "+hits.length+" 份",result||empty("随附的公开原文未匹配到关键词。")):"")+savedAnswer();
}
const views={home,sources,source,collection,item,docs,doc,supplied,runs,catalog,changes,governance,ontology,graph,map:market,access,qa};
function render() {
  const [raw,query=""]=location.hash.slice(1).split("?");
  page=Object.hasOwn(views,raw)?raw:"home";
  params=new URLSearchParams(query);
  const active=sectionOf[page]||page;
  document.getElementById("nav").innerHTML=sections.map(([group,items])=>`<h2>${h(group)}</h2>${items.map(([r,n])=>`<a href="${addr(r)}"${active===r?' aria-current="page"':""}>${h(n)}</a>`).join("")}`).join("");
  document.title=(titles.get(active)||"工作台")+" · 法规认证知识平台";
  view.innerHTML=views[page]();
}
view.addEventListener("submit",e=>{
  if(!e.target.matches("[data-filter]"))return;
  e.preventDefault();
  location.hash=addr(page,Object.fromEntries(new FormData(e.target)));
});
view.addEventListener("change",e=>{if(e.target.matches("[data-filter] select"))e.target.form.requestSubmit();});
view.addEventListener("click",e=>{
  const b=e.target.closest("[data-download]");if(!b)return;
  const file=data.files[b.dataset.download];if(!file)return;
  const bytes=Uint8Array.from(atob(file.data),c=>c.charCodeAt(0));
  const url=URL.createObjectURL(new Blob([bytes],{type:file.mime}));
  const a=document.createElement("a");a.href=url;a.download=file.name;document.body.append(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(url),60000);
});
document.getElementById("edition").textContent="离线阅览 · "+asDay(data.created);
window.addEventListener("hashchange",()=>{render();window.scrollTo(0,0);});
render();
