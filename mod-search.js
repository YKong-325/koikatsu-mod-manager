(function(root){
'use strict';
const normalize=value=>String(value??'').normalize('NFKC').toLocaleLowerCase().trim();
const words=value=>normalize(value).split(/\s+/u).filter(Boolean);
const contains=(text,query)=>words(query).every(word=>text.includes(word));
function create(){
const cache=new WeakMap();
function entry(record){
const classification=record.classification||{};
const stamp=JSON.stringify(classification);
let value=cache.get(record);
if(value?.stamp===stamp)return value;
const labels={work:classification.work||'',character:classification.character||'',
author:classification.author||record.author||''};
const name=normalize([record.name,record.file].join('\n'));
const fallback=normalize([record.path,record.name,record.guid,record.author,
...(record.items||[]).map(item=>item.Name)].join('\n'));
value={stamp,labels,name,all:normalize([fallback,...Object.values(labels)].join('\n')),
author:normalize(labels.author)};
cache.set(record,value);return value;
}
/* Only the author is a searchable dimension. Work and character names are
 * written differently in nearly every package, so filtering on them mostly
 * repeated the general search; the labels stay on the entry for display. */
function matches(record,queries={}){
return contains(entry(record).author,queries.author);
}
function options(records){
const distinct=new Map();
for(const record of records){
const label=entry(record).labels.author;
if(label&&!distinct.has(normalize(label)))distinct.set(normalize(label),label);
}
return [...distinct.values()].sort((a,b)=>a.localeCompare(b,'zh-CN'));
}
return {entry,matches,options,contains};
}
const api={create,normalize,contains};
if(typeof module==='object'&&module.exports)module.exports=api;
else root.KK_MOD_SEARCH=api;
})(typeof window==='object'?window:globalThis);
