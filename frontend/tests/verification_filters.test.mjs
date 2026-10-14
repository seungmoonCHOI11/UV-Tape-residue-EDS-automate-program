import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const source=fs.readFileSync(new URL('../app/AppClient.js',import.meta.url),'utf8');
const start=source.indexOf('function VerificationModal(');
const end=source.indexOf('\n\nfunction ImageWithFallback',start);
const modal=source.slice(start,end);

test('Verification exposes substrate and wafer filters together',()=>{
  assert.match(source,/verificationSubstrate,setVerificationSubstrate/);
  assert.match(modal,/label>기판<select/);
  assert.match(modal,/label>웨이퍼 선택<select/);
  assert.match(modal,/substrateMatch/);
  assert.match(modal,/wafer===\"ALL\"/);
});

test('Verification makes W9 reference and substrate identity visible',()=>{
  assert.match(modal,/9:'Reference'/);
  assert.match(modal,/waferSubstrates/);
  assert.match(modal,/substrateCounts/);
  assert.match(modal,/W\{w\} · \{locationLabel\(w\)\}/);
});
