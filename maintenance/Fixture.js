/* Shared fixture for the browser-side tests: two role cards, one scene, 41 packs
 * and the dependency snapshot the server would compute for them. */

const mods = Array.from({length: 41}, (_, index) => ({
  path: `pack${String(index).padStart(3, '0')}.zipmod`,
  file: `pack${index}.zipmod`,
  name: `Pack ${index}`,
  guid: index < 40 ? `guid.${index}` : '',
  author: 'tester',
  bytes: 1024 * (index + 1),
  slots: index % 2 ? ['头发'] : ['服装'],
  dataDirs: ['chara'],
  items: [{Slot: '头发', Name: `Item ${index}`, ID: 1, ListCategory: 1}],
  images: [],
  thumbBundles: [],
  errors: []
}));

const cards = [
  {path: 'chara/a.png', file: 'a.png', kind: '角色卡', bytes: 2048,
    thumb: 'library_thumbs/a.jpg',
    mods: ['pack000.zipmod'], references: [{guid: 'guid.0'}, {guid: 'absent'}],
    missing: true, missingTokens: ['absent'], baseGameTokens: [], duplicate: false,
    duplicates: ['chara/a.png'], dependencyStatus: 'parsed'},
  {path: 'chara/b.png', file: 'b.png', kind: '角色卡', bytes: 2048, thumb: null,
    mods: ['pack001.zipmod'], references: [{guid: 'guid.1'}],
    missing: false, missingTokens: [], baseGameTokens: [], duplicate: false,
    duplicates: ['chara/b.png'], dependencyStatus: 'parsed'},
  {path: 'studio/c.png', file: 'c.png', kind: '场景', bytes: 2048, thumb: null,
    mods: [], references: [], missing: false, missingTokens: [], baseGameTokens: [],
    duplicate: false, duplicates: ['studio/c.png'], dependencyStatus: 'unknown'}
];

const deps = {
  cards: {
    'chara/a.png': {state: 'missing', missing: ['absent']},
    'chara/b.png': {state: 'complete', missing: []},
    'studio/c.png': {state: 'unknown', missing: []}
  },
  mods: {
    'pack000.zipmod': {state: 'matched', cards: ['chara/a.png'], outfits: [], scenes: [],
      duplicateGuid: false},
    'pack001.zipmod': {state: 'unmatched', cards: [], outfits: [], scenes: [],
      duplicateGuid: false},
    'pack002.zipmod': {state: 'unknown', cards: [], outfits: [], scenes: [],
      duplicateGuid: false}
  },
  coverage: {total: 2, parsed: 2, unknown: 0, missing: 1, complete: 1},
  coverageByKind: {
    '角色卡': {total: 2, parsed: 2, unknown: 0, missing: 1, complete: 1},
    '场景': {total: 1, parsed: 0, unknown: 1, missing: 0, complete: 0},
    '服装卡': {total: 0, parsed: 0, unknown: 0, missing: 0, complete: 0}
  },
  summary: {characterMods: 41, matched: 1, unmatched: 1, unknown: 39}
};

for (let index = 3; index < 40; index++) {
  deps.mods[`pack${String(index).padStart(3, '0')}.zipmod`] = {
    state: 'unmatched', cards: [], outfits: [], scenes: [], duplicateGuid: false
  };
}

/* One pack per non-character kind, so a filter that only counts character
 * cards fails the moment it assumes "no card wears it" means "unused". */
deps.mods['pack003.zipmod'].scenes = ['studio/c.png'];
deps.mods['pack004.zipmod'].outfits = ['coordinate/d.png'];

module.exports = {
  get catalog() { return mods; },
  get library() { return cards; },
  get deps() { return deps; }
};
