window.WorkspaceExcel=(function makeWorkbook(){
"use strict";
const enc=new TextEncoder(), dec=new TextDecoder();
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&apos;"}[c])).replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g,"");
const crcTable=Uint32Array.from({length:256},(_,i)=>{for(let b=0;b<8;b++)i=(i>>>1)^((i&1)?0xedb88320:0);return i>>>0;});
const checksum=bytes=>{let c=0xffffffff;for(const b of bytes)c=(c>>>8)^crcTable[(c^b)&255];return (c^0xffffffff)>>>0;};
const number=(len,writes)=>{const a=new Uint8Array(len), v=new DataView(a.buffer);for(const [pos,size,n] of writes)v[size===2?"setUint16":"setUint32"](pos,n,true);return a;};
function zip(entries){
  const locals=[],central=[];let offset=0,centralSize=0;
  for(const [name,text] of Object.entries(entries)){
    const n=enc.encode(name),v=enc.encode(text),crc=checksum(v);
    const local=number(30,[[0,4,0x04034b50],[4,2,20],[14,4,crc],[18,4,v.length],[22,4,v.length],[26,2,n.length]]);
    const directory=number(46,[[0,4,0x02014b50],[4,2,20],[6,2,20],[16,4,crc],[20,4,v.length],[24,4,v.length],[28,2,n.length],[42,4,offset]]);
    locals.push(local,n,v);central.push(directory,n);centralSize+=46+n.length;offset+=30+n.length+v.length;
  }
  const count=Object.keys(entries).length;
  return new Blob([...locals,...central,number(22,[[0,4,0x06054b50],[8,2,count],[10,2,count],[12,4,centralSize],[16,4,offset]])],{type:"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"});
}
const base="http://schemas.openxmlformats.org/";
function write(rows){
  const s="spreadsheetml/2006/main",r=base+"officeDocument/2006/relationships";
  const cells=rows.map((row,i)=>`<row r="${i+1}">${row.map((v,j)=>`<c r="${String.fromCharCode(65+j)}${i+1}" t="inlineStr"><is><t xml:space="preserve">${esc(v)}</t></is></c>`).join("")}</row>`).join("");
  return zip({
    "[Content_Types].xml":`<Types xmlns="${base}package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>`,
    "_rels/.rels":`<Relationships xmlns="${base}package/2006/relationships"><Relationship Id="rId1" Type="${r}/officeDocument" Target="xl/workbook.xml"/></Relationships>`,
    "xl/workbook.xml":`<workbook xmlns="${base+s}" xmlns:r="${r}"><sheets><sheet name="法规资料" sheetId="1" r:id="rId1"/></sheets></workbook>`,
    "xl/_rels/workbook.xml.rels":`<Relationships xmlns="${base}package/2006/relationships"><Relationship Id="rId1" Type="${r}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>`,
    "xl/worksheets/sheet1.xml":`<worksheet xmlns="${base+s}"><cols><col min="1" max="${rows[0].length}" width="22" customWidth="1"/></cols><sheetData>${cells}</sheetData></worksheet>`
  });
}
async function read(file){
  if(file.size>8*1024**2)throw Error("请选择 8 MB 以内的 Excel 模板文件。");
  const bytes=new Uint8Array(await file.arrayBuffer()),v=new DataView(bytes.buffer);
  const u16=x=>v.getUint16(x,true),u32=x=>v.getUint32(x,true);
  let end=bytes.length-22;
  while(end>=0&&u32(end)!==0x06054b50)end--;
  if(end<0)throw Error("请选择 .xlsx 工作簿。");
  let pos=u32(end+16),total=0;const entries=new Map();
  for(let i=0;i<u16(end+10);i++){
    if(u32(pos)!==0x02014b50)throw Error("工作簿目录损坏。");
    const len=u16(pos+28),name=dec.decode(bytes.slice(pos+46,pos+46+len)),offset=u32(pos+42),size=u32(pos+20);
    total+=u32(pos+24);if(total>32*1024**2)throw Error("解压后的工作簿过大。");
    entries.set(name,{start:offset+30+u16(offset+26)+u16(offset+28),size,method:u16(pos+10)});
    pos+=46+len+u16(pos+30)+u16(pos+32);
  }
  async function xml(name){
    const e=entries.get(name);if(!e)return null;
    const buf=bytes.subarray(e.start,e.start+e.size);
    if(e.method!==0&&e.method!==8)throw Error("工作簿的压缩方式无法读取。");
    const text=e.method===0?dec.decode(buf):await new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream("deflate-raw"))).text();
    const doc=new DOMParser().parseFromString(text,"application/xml");
    if(doc.getElementsByTagName("parsererror").length)throw Error("工作簿 XML 无法读取。");
    return doc;
  }
  const book=await xml("xl/workbook.xml"),rels=await xml("xl/_rels/workbook.xml.rels");
  const first=book?.getElementsByTagName("sheet")[0];
  const id=first?.getAttributeNS(base+"officeDocument/2006/relationships","id");
  const target=Array.from(rels?.getElementsByTagName("Relationship")||[]).find(r=>r.getAttribute("Id")===id)?.getAttribute("Target");
  if(!target)throw Error("找不到第一张工作表。");
  const sheet=await xml(target.startsWith("/")?target.slice(1):"xl/"+target);
  if(!sheet)throw Error("工作表文件缺失。");
  const shared=await xml("xl/sharedStrings.xml"),strings=[...(shared?.getElementsByTagName("si")||[])].map(n=>n.textContent);
  const rows=[];
  for(const row of sheet.getElementsByTagName("row")){
    if(rows.length>2000)throw Error("每次最多导入 2000 行。");
    const values=[];
    for(const cell of row.getElementsByTagName("c")){
      const letters=(cell.getAttribute("r")||"").match(/^[A-Z]+/)?.[0]||"A";
      const col=[...letters].reduce((k,c)=>k*26+c.charCodeAt(0)-64,0)-1;
      if(col>63)throw Error("请使用法规模板。");
      const type=cell.getAttribute("t"),val=cell.getElementsByTagName("v")[0]?.textContent||"";
      values[col]=type==="inlineStr"?cell.getElementsByTagName("is")[0]?.textContent||"":type==="s"?strings[+val]||"":val;
    }
    if(values.some(x=>x!==undefined&&x!==""))rows.push(values);
  }
  return rows;
}
return {read,write};
})();
