import fs from 'node:fs/promises';
import path from 'node:path';
import {FileBlob,SpreadsheetFile,Workbook} from '@oai/artifact-tool';
const root=path.resolve(process.argv[2]);
const specs=JSON.parse(await fs.readFile(path.join(root,'customer_specs.json'),'utf8'));
const compact=process.argv.includes('--compact');
const internal=compact?[]:JSON.parse(await fs.readFile(path.join(root,'engineering_specs.json'),'utf8'));
const output=path.join(root,'供应商试题Part2_解析成果');
await fs.mkdir(output,{recursive:true});
await fs.mkdir(path.join(root,'previews'),{recursive:true});
function col(n){let s='';for(;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
for(const [bi,spec] of [...specs,...internal].entries()){
 const cert=bi===1&&!compact;
 const wb=cert?await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(root,'input/certification_reviewed.xlsx'))):Workbook.create();
 if(cert){
  const edits=JSON.parse(await fs.readFile(path.join(root,'input/cert_edits.json'),'utf8'));
  for(const edit of edits)wb.worksheets.getItem(edit.sheet).getRange(edit.cell).values=[[edit.after]];
 }
 for(const [si,s] of spec.sheets.entries()){
  const sh=cert?wb.worksheets.getItem(s.name):wb.worksheets.add(s.name),nr=s.rows.length,nc=s.rows[0].length;
  const range=sh.getRange(`A1:${col(nc)}${nr}`);
  if(!cert){
   range.values=s.rows.map(r=>r.map(v=>typeof v==='string'&&v.startsWith('=')?"'"+v:v));
   for(let i=0;i<nc;i++)sh.getRange(`${col(i+1)}1:${col(i+1)}${nr}`).format.columnWidth=s.widths[i]||40;
   for(let i=1;i<=nr;i++){
    const lines=Math.max(...s.rows[i-1].map((v,c)=>String(v??'').split('\n').reduce((a,t)=>a+Math.max(1,Math.ceil(t.length/Math.max(6,(s.widths[c]||40)/2))),0)));
    sh.getRange(`A${i}:${col(nc)}${i}`).format.rowHeight=Math.min(409,Math.max(30,(lines+1)*15));
   }
   if(nr>1&&new Set(s.rows[0]).size===nc)sh.tables.add(`A1:${col(nc)}${nr}`,true,`Delivery_${bi}_${si}`).showFilterButton=true;
   const link=s.rows[0].indexOf('原页链接');
   // Native external hyperlink relationships are attached after export.
   // Keep the literal URL as a readable fallback; never use HYPERLINK formulas.
   if(link>=0&&nr>1)sh.getRange(`${col(link+1)}2:${col(link+1)}${nr}`).format.font={name:'Arial',size:11,color:'#000000',underline:true};
  }
  range.format.font={name:'Arial',size:11,color:'#000000'};range.format.wrapText=true;range.format.verticalAlignment='top';
  sh.showGridLines=false;sh.freezePanes.freezeRows(cert&&si===0?3:1);sh.freezePanes.freezeColumns(1);
  const hr=cert&&si===0?3:1,header=sh.getRange(`A${hr}:${col(nc)}${hr}`);
  header.format.fill='#DFE7EE';header.format.font={name:'Arial',size:11,bold:true,color:'#000000'};
  if(cert){
   header.format.rowHeight=40;
   if(si===0){sh.getRange('A1:AG5').format.columnWidth=65;sh.getRange('A4:AG5').format.rowHeight=409;}
   if(si>0&&si<3){sh.getRange(`C1:C${nr}`).format.columnWidth=100;sh.getRange(`A2:G${nr}`).format.rowHeight=260;}
   if(si===3)sh.getRange(`A1:C${nr}`).format.rowHeight=70;
  }
  const region=s.name==='条款解析'?'E1:J3':s.name==='测试要求'?'A1:E3':s.name==='证据定位'?'C1:F3':cert&&si===0?'A3:C4':`A1:${col(Math.min(nc,3))}${Math.min(nr,3)}`;
  const img=await wb.render({sheetName:s.name,range:region,scale:1,format:'png'});
  await fs.writeFile(path.join(root,'previews',`${bi}-${s.name}.png`),new Uint8Array(await img.arrayBuffer()));
  console.log(spec.filename,s.name,nr-1);
 }
 const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#SPILL!',options:{useRegex:true,maxResults:30},summary:'formula checks'});
 await fs.writeFile(path.join(root,`formula-${bi}.ndjson`),errors.ndjson);
 const dest=cert?path.join(root,'input/cert_edited_with_audit.xlsx'):bi<3?path.join(output,spec.filename):path.join(root,'engineering',spec.filename);
 await(await SpreadsheetFile.exportXlsx(wb)).save(dest);
}
