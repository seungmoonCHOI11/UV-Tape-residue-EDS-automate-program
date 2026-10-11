import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const source=fs.readFileSync(new URL('../app/AppClient.js',import.meta.url),'utf8');
const start=source.indexOf('function HumanRoiEditor({p,saveHumanRoi}){');
const end=source.indexOf('\nfunction VerificationModal(',start);
const editor=source.slice(start,end);

test('open Human ROI editor follows the selected Point',()=>{
  assert.match(editor,/const pointKey=String\(p\?\.id/);
  assert.match(editor,/\[open,pointKey\]/);
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
