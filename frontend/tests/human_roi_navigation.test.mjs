import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const source=fs.readFileSync(new URL('../app/AppClient.js',import.meta.url),'utf8');
const start=source.indexOf('function HumanRoiEditor({p,saveHumanRoi}){');
const end=source.indexOf('\nfunction VerificationModal(',start);
const editor=source.slice(start,end);

test('open Human ROI editor follows the selected Point',()=>{
  assert.match(editor,/const pointKey=String\(p\?\.id/);
  assert.match(editor,/\[open,pointKey,semSignature\]/);
  assert.match(editor,/data-point-id=\{pointKey\}/);
  assert.match(editor,/현재 Point: SET\{p\.set_number\} P\{p\.point\}/);
});

test('stale image loads cannot repaint the next Point canvas',()=>{
  assert.match(editor,/loadSeqRef=useRef\(0\)/);
  assert.match(editor,/seq!==loadSeqRef\.current/);
  assert.match(editor,/baseImgRef=useRef\(null\)/);
});

test('drawing reuses the loaded base SEM instead of loading on every pointer move',()=>{
  const imageConstructors=(editor.match(/new Image\(\)/g)||[]).length;
  assert.equal(imageConstructors,1);
  assert.match(editor,/Redraw from the already-loaded base image/);
});

test('Human ROI canvas retries and falls back when the next Point SEM is temporarily unavailable',()=>{
  assert.match(editor,/p\?\.assets\?\.sem,p\?\.assets\?\.sem_original,dynamicAsset\(p,"sem"\)/);
  assert.match(editor,/sourceAttempt<2/);
  assert.match(editor,/setSourceIndex\(i=>i\+1\)/);
  assert.match(editor,/SEM 연결 재시도 중/);
  assert.match(editor,/이미지 다시 불러오기/);
});
