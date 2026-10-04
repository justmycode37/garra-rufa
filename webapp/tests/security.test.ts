import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync,rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import * as store from '../src/lib/store';
import {reasoningEffort,searchDiseases,inferRegion} from '../src/lib/knowledge';
const temporary=mkdtempSync(path.join(tmpdir(),'garra-tests-'));
process.env.GARRA_DATA_DIR=temporary;
process.env.DATA_ENCRYPTION_KEY=randomBytes(32).toString('hex');
after(()=>{store.db().close();rmSync(temporary,{recursive:true,force:true});});

test('passwords and session tokens are verified without storing plaintext',()=>{
  const u=store.createUser('Test Researcher','researcher@example.test','a-long-test-password','researcher');
  assert.equal(store.loginUser(u.email,'incorrect-password'),null);
  assert.equal(store.loginUser(u.email,'a-long-test-password')?.id,u.id);
  const token=store.createSession(u.id);assert.equal(store.sessionUser(token)?.id,u.id);
  assert.equal(store.sessionUser('a-wrong-token'),null);
  const row=store.db().prepare('SELECT token FROM sessions WHERE user_id=?').get(u.id) as {token:string};assert.notEqual(row.token,token);
  store.removeSession(token);assert.equal(store.sessionUser(token),null);
});
test('records and attachments require ownership or an explicit grant; grants are read-only and revocable',()=>{
  const owner=store.createUser('Patient','patient@example.test','patient-test-password','patient');
  const doctor=store.createUser('Doctor','doctor@example.test','doctor-test-password','doctor');
  const stranger=store.createUser('Other','other@example.test','other-test-password','researcher');
  const r=store.saveRecord(owner.id,{kind:'document',title:'Private medical file',content:'Private clinical content'});
  store.storeFile(r.id,Buffer.from('Secret file bytes'));
  assert.equal(store.getRecord(r.id,doctor.id),null);assert.equal(store.getFile(r.id,stranger.id),null);
  const encrypted=store.db().prepare('SELECT payload FROM records WHERE id=?').get(r.id) as {payload:string};assert.ok(!encrypted.payload.includes('Private'));
  store.shareRecord(r.id,owner.id,doctor.email);
  assert.equal(store.getRecord(r.id,doctor.id)?.readOnly,true);assert.equal(store.getFile(r.id,doctor.id)?.toString(),'Secret file bytes');
  assert.throws(()=>store.saveRecord(doctor.id,{id:r.id,kind:'document',title:'Tampered'}));
  store.deleteRecord(r.id,doctor.id);assert.ok(store.getRecord(r.id,owner.id));
  assert.equal(store.getRecord(r.id,stranger.id),null);
  store.shareRecord(r.id,owner.id,doctor.email,true);assert.equal(store.getRecord(r.id,doctor.id),null);
});
test('only published research projects enter the public collection',()=>{
  const u=store.loginUser('researcher@example.test','a-long-test-password')!;
  const project=store.saveRecord(u.id,{kind:'project',title:'Public research',content:'Research summary'});
  assert.equal(store.publicProjects().length,0);store.publishRecord(project.id,u.id,true);assert.equal(store.publicProjects().length,1);
  store.publishRecord(project.id,u.id,false);assert.equal(store.publicProjects().length,0);
  const patient=store.saveRecord(u.id,{kind:'patient',title:'Private patient'});assert.throws(()=>store.publishRecord(patient.id,u.id,true));
});
test('authenticated encryption rejects tampered payloads',()=>{
  const sealed=store.seal('sensitive');assert.equal(store.unseal(sealed),'sensitive');
  const bytes=Buffer.from(sealed,'base64');bytes[15]^=1;assert.throws(()=>store.unseal(bytes.toString('base64')));
});
test('effort routing escalates document review and mechanistic comparisons',()=>{
  assert.equal(reasoningEffort('What is Fabry disease?'),'low');
  assert.equal(reasoningEffort('Compare treatment evidence for Fabry and Pompe'),'high');
  assert.equal(reasoningEffort('Review this paper'),'max');
  assert.equal(reasoningEffort('Summarize this',true),'max');
});
test('anatomical navigation and coverage gaps are explicit',()=>{
  assert.equal(inferRegion('I have this weird hand pain'),'hands');
  assert.ok(searchDiseases('hand diseases').some(d=>d.id==='cmt'));
  assert.equal(searchDiseases('unknown-zxy-condition').length,0);
  assert.equal(searchDiseases('rare diseases').length,6);
});

test('guest roles remain isolated, survive refresh, and cannot change registered accounts',()=>{
  const researcher=store.createGuest('researcher'),other=store.createGuest('doctor');
  const token=store.createSession(researcher.id);
  const saved=store.saveRecord(researcher.id,{kind:'project',title:'Guest project'});
  assert.equal(store.sessionUser(token)?.guest,true);
  assert.equal(store.getRecord(saved.id,other.id),null);
  assert.equal(store.listRecords(other.id).length,0);
  const switched=store.updateGuestRole(researcher.id,'patient');
  assert.equal(switched.id,researcher.id);
  assert.equal(store.sessionUser(token)?.role,'patient');
  assert.equal(store.getRecord(saved.id,switched.id)?.title,'Guest project');
  const registered=store.loginUser('researcher@example.test','a-long-test-password')!;
  assert.throws(()=>store.updateGuestRole(registered.id,'doctor'));
});

test('chat deletion is owner-scoped and clearing history preserves other records',()=>{
  const owner=store.createGuest('researcher'),other=store.createGuest('patient');
  const chat=store.saveRecord(owner.id,{kind:'chat',title:'Delete this conversation',content:'',messages:[]});
  const second=store.saveRecord(owner.id,{kind:'chat',title:'Another conversation',content:'',messages:[]});
  const project=store.saveRecord(owner.id,{kind:'project',title:'Keep my project',content:'Research'});
  const otherChat=store.saveRecord(other.id,{kind:'chat',title:'Another owner',content:'',messages:[]});
  store.deleteRecord(chat.id,other.id);assert.ok(store.getRecord(chat.id,owner.id));
  store.deleteRecord(chat.id,owner.id);assert.equal(store.getRecord(chat.id,owner.id),null);
  assert.equal(store.deleteChatHistory(owner.id),1);
  assert.equal(store.getRecord(second.id,owner.id),null);
  assert.ok(store.getRecord(project.id,owner.id));
  assert.ok(store.getRecord(otherChat.id,other.id));
});
