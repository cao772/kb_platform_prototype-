import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';
const root=path.resolve(process.argv[2]);
const specs=JSON.parse(await fs.readFile(path.join(root,'excel_generator/workbooks.json'),'utf8'));
const preview=path.join(root,'validation/previews');await fs.mkdir(preview,{recursive:true});
function col(n){let s='';for(;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
for(const [bi,spec] of specs.entries()){
 const wb=Workbook.create();
 for(const [si,t] of spec.sheets.entries()){
  const sh=wb.worksheets.add(t.name),nr=t.rows.length,nc=t.rows[0].length;
  if(t.rows.some(r=>r.length!==nc))throw new Error('Nonrectangular '+t.name);
  const range=sh.getRange(`A1:${col(nc)}${nr}`);
  range.values=t.rows.map(r=>r.map(v=>typeof v==='string'&&v.startsWith('=')?"'"+v:v));
  range.format.font={name:'Arial',size:11,color:'#000000'};range.format.wrapText=true;range.format.verticalAlignment='top';
  sh.showGridLines=false;sh.freezePanes.freezeRows(1);sh.freezePanes.freezeColumns(1);
  for(let i=0;i<nc;i++)sh.getRange(`${col(i+1)}1:${col(i+1)}${nr}`).format.columnWidth=t.widths[i]||40;
  for(let i=1;i<=nr;i++){
   const lines=Math.max(...t.rows[i-1].map((v,c)=>String(v??'').split('\n').reduce((a,s)=>a+Math.max(1,Math.ceil(s.length/Math.max(6,(t.widths[c]||40)/2))),0)));
   sh.getRange(`A${i}:${col(nc)}${i}`).format.rowHeight=Math.min(409,Math.max(30,(lines+1)*15));
  }
  if(nr>1&&new Set(t.rows[0]).size===nc){const tab=sh.tables.add(`A1:${col(nc)}${nr}`,true,`Output_${bi}_${si}`);tab.showFilterButton=true;}
  const header=sh.getRange(`A1:${col(nc)}1`);header.format.fill='#DFE7EE';header.format.font={name:'Arial',size:11,bold:true,color:'#000000'};
  const link=t.rows[0].indexOf('原页链接');
  if(link>=0&&nr>1)sh.getRange(`${col(link+1)}2:${col(link+1)}${nr}`).formulas=t.rows.slice(1).map(r=>r[link]?[`=HYPERLINK("${r[link]}","查看原页")`]:['']);
  const region=t.name==='条款解析'?'E1:H3':t.name==='证据定位'?'C1:F3':t.name==='测试要求'?'A1:E3':`A1:${col(Math.min(nc,3))}${Math.min(nr,4)}`;
  const img=await wb.render({sheetName:t.name,range:region,scale:1,format:'png'});
  await fs.writeFile(path.join(preview,`${bi}-${t.name}.png`),new Uint8Array(await img.arrayBuffer()));
  console.log(spec.filename,t.name,nr-1);
 }
 const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#SPILL!',options:{useRegex:true,maxResults:20},summary:'Export error scan'});
 await fs.writeFile(path.join(root,'validation',`${bi}-formula-check.ndjson`),errors.ndjson);
 console.log((await wb.inspect({kind:'table',range:`'${spec.sheets[0].name}'!A1:C3`,include:'values',tableMaxRows:3,tableMaxCols:3})).ndjson);
 await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(root,spec.filename));
}
