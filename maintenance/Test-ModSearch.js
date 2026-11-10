const assert=require('node:assert/strict');
const {create,contains}=require('../mod-search.js');
const search=create();
const records=[
{path:'a.zipmod',name:'Costume',author:'Alice',items:[],classification:{work:'原神',character:'胡桃',author:''}},
{path:'b.zipmod',name:'Costume',author:'Bob',items:[],classification:{work:'原神',character:'甘雨',author:''}},
{path:'c.zipmod',name:'Outfit',author:'Alice',items:[],classification:{work:'崩坏',character:'琪亚娜',author:''}},
{path:'unclassified/Genshin/Hu Tao.zipmod',name:'Hu Tao dress',author:'Maker',items:[{Name:'hat'}]},
{path:'unknown.zipmod',items:[]}
];
/* The author is the only searchable dimension now. */
assert.deepEqual(search.options(records),['Alice','Bob','Maker']);
assert(search.matches(records[0],{author:'ali'}));
assert(search.matches(records[0],{author:'  ALICE  '}),'trim and case are ignored');
assert(search.matches(records[3],{author:'ma ker'}),'every whitespace token must match');
assert(!search.matches(records[0],{author:'bob'}));
assert(search.matches(records[0],{author:''}),'an empty field matches everything');
assert(!search.matches(records[4],{author:'anyone'}),'a package without an author is excluded');
/* Work and character labels are display-only: they must not filter. */
assert(search.matches(records[0],{work:'原神',character:'胡桃'}),
  'work and character values are ignored by matches()');
assert(contains('alice outfit','ＡＬＩＣＥ outfit'),'NFKC folds full width');
assert(search.contains(search.entry(records[0]).name,'ＣＯＳＴＵＭＥ'));
assert(!contains('alice outfit','alice dress'));
assert(search.entry(records[0]).all.includes('胡桃'),'labels still feed the general search');
const initial=search.entry(records[0]);
records[0].classification={work:'新作',character:'新角色',author:'Override'};
assert.notEqual(search.entry(records[0]),initial,'editing the classification invalidates the cache');
assert.equal(search.entry(records[0]).labels.author,'Override');
assert(search.matches(records[0],{author:'over'}),'the classed author wins over the package author');
assert(!search.matches(records[0],{author:'alice'}),'the package author stops matching once overridden');
assert.deepEqual(search.options(records),['Alice','Bob','Maker','Override']);
records.splice(0,1);
assert.deepEqual(search.options(records),['Alice','Bob','Maker'],'removing a package drops its author');
console.log('PASS: author-only filtering, multi-keyword and Unicode search, label display, cache invalidation, removal');
