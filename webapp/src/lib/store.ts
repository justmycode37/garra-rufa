import { DatabaseSync } from 'node:sqlite';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { randomBytes, randomUUID, createCipheriv, createDecipheriv, createHash, scryptSync, timingSafeEqual } from 'node:crypto';
import type { Role, User, WorkspaceRecord, RecordKind } from './types';

const globals=globalThis as unknown as {rariviaDB?:DatabaseSync;rariviaKey?:Buffer;garraGuestReady?:boolean};
function dataDir(){return process.env.GARRA_DATA_DIR?path.resolve(/* turbopackIgnore: true */process.env.GARRA_DATA_DIR):path.join(process.cwd(),'data');}
function key(){
  if(globals.rariviaKey)return globals.rariviaKey;
  const configured=process.env.DATA_ENCRYPTION_KEY;
  if(configured&&!/^[a-f0-9]{64}$/i.test(configured))throw new Error('DATA_ENCRYPTION_KEY must contain 64 hex characters');
  if(configured)return globals.rariviaKey=Buffer.from(configured,'hex');
  if(process.env.VERCEL || process.env.GARRA_STORAGE==='supabase')throw new Error('Hosted storage requires a stable DATA_ENCRYPTION_KEY.');
  const dir=dataDir(); mkdirSync(dir,{recursive:true,mode:0o700});
  const file=path.join(dir,'.encryption-key');
  try { writeFileSync(file,randomBytes(32),{flag:'wx',mode:0o600}); } catch(e) {if((e as NodeJS.ErrnoException).code!=='EEXIST')throw e;}
  return globals.rariviaKey=readFileSync(file);
}
export function seal(value:string):string {
  const iv=randomBytes(12); const cipher=createCipheriv('aes-256-gcm',key(),iv);
  return Buffer.concat([iv,cipher.update(value,'utf8'),cipher.final(),cipher.getAuthTag()]).toString('base64');
}
export function unseal(value:string):string {
  const b=Buffer.from(value,'base64');const decipher=createDecipheriv('aes-256-gcm',key(),b.subarray(0,12));
  decipher.setAuthTag(b.subarray(-16));return Buffer.concat([decipher.update(b.subarray(12,-16)),decipher.final()]).toString('utf8');
}
export function db(){
  if(globals.rariviaDB){if(!globals.garraGuestReady){globals.rariviaDB.exec('CREATE TABLE IF NOT EXISTS guest_users(user_id TEXT PRIMARY KEY REFERENCES users(id))');globals.garraGuestReady=true;}return globals.rariviaDB;}
  mkdirSync(dataDir(),{recursive:true,mode:0o700});
  const d=new DatabaseSync(path.join(dataDir(),'garra.sqlite'));
  d.exec(`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL,email TEXT NOT NULL UNIQUE,password TEXT NOT NULL,role TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS guest_users(user_id TEXT PRIMARY KEY REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),expires INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL REFERENCES users(id),kind TEXT NOT NULL,visibility TEXT NOT NULL DEFAULT 'private',payload TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS shares(record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id),PRIMARY KEY(record_id,user_id));
    CREATE TABLE IF NOT EXISTS files(record_id TEXT PRIMARY KEY REFERENCES records(id) ON DELETE CASCADE,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS community(user_id TEXT PRIMARY KEY REFERENCES users(id),alias TEXT NOT NULL,disease_id TEXT NOT NULL,bio TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS limits(bucket TEXT PRIMARY KEY,hits INTEGER NOT NULL,reset INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS records_owner ON records(owner_id,kind);
  `);
  globals.garraGuestReady=true;return globals.rariviaDB=d;
}
export function hashPassword(password:string){const salt=randomBytes(16).toString('hex');return salt+':'+scryptSync(password,salt,64).toString('hex');}
export function verifyPassword(password:string,hash:string){const [salt,stored]=hash.split(':');const digest=scryptSync(password,salt,64);return stored?.length===128&&timingSafeEqual(digest,Buffer.from(stored,'hex'));}
export function createUser(name:string,email:string,password:string,role:Role):User {
  const user={id:randomUUID(),name,email:email.toLowerCase(),role};
  db().prepare('INSERT INTO users VALUES(?,?,?,?,?,?)').run(user.id,name,user.email,hashPassword(password),role,new Date().toISOString());return user;
}
export function loginUser(email:string,password:string):User|null {
  const u=db().prepare('SELECT * FROM users WHERE email=?').get(email.toLowerCase()) as (User&{password:string})|undefined;
  // Also perform the expensive hash on missing accounts to reduce timing differences.
  const valid=verifyPassword(password,u?.password||'00000000000000000000000000000000:'+ '0'.repeat(128));
  return u&&valid?{id:u.id,name:u.name,email:u.email,role:u.role}:null;
}
const digest=(t:string)=>createHash('sha256').update(t).digest('hex');
export function createSession(userId:string){const token=randomBytes(32).toString('hex');db().prepare('DELETE FROM sessions WHERE expires < ?').run(Date.now());db().prepare('INSERT INTO sessions VALUES(?,?,?)').run(digest(token),userId,Date.now()+7*86400000);return token;}
export function sessionUser(token:string):User|null {
  const row=db().prepare('SELECT u.id,u.name,u.email,u.role,EXISTS(SELECT 1 FROM guest_users g WHERE g.user_id=u.id) AS guest FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>?').get(digest(token),Date.now()) as (Omit<User,'guest'>&{guest:number})|undefined;
  return row?{...row,guest:!!row.guest}:null;
}
export function createGuest(role:Role):User {
  const name=role==='researcher'?'Researcher':role==='doctor'?'Doctor':'Patient';
  const user=createUser(name,`${randomUUID()}@guest.invalid`,randomBytes(48).toString('hex'),role);
  db().prepare('INSERT INTO guest_users VALUES(?)').run(user.id);
  return {...user,guest:true};
}
export function updateGuestRole(userId:string,role:Role):User {
  const guest=db().prepare('SELECT user_id FROM guest_users WHERE user_id=?').get(userId);
  if(!guest)throw new Error('Only guest workspaces can change perspective.');
  const name=role==='researcher'?'Researcher':role==='doctor'?'Doctor':'Patient';
  db().prepare('UPDATE users SET role=?,name=? WHERE id=?').run(role,name,userId);
  const user=db().prepare('SELECT id,name,email,role FROM users WHERE id=?').get(userId) as unknown as User;
  return {...user,guest:true};
}
export function removeSession(token:string){db().prepare('DELETE FROM sessions WHERE token=?').run(digest(token));}
export function limit(bucket:string,max:number,windowMs:number){
  const now=Date.now();db().prepare('DELETE FROM limits WHERE reset<?').run(now);
  db().prepare('INSERT INTO limits VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET hits=hits+1').run(bucket,now+windowMs);
  const row=db().prepare('SELECT hits FROM limits WHERE bucket=?').get(bucket) as {hits:number};return row.hits<=max;
}
type Row={id:string;owner_id:string;payload:string};
function decode(row:Row,userId:string):WorkspaceRecord {
  const record=JSON.parse(unseal(row.payload)) as WorkspaceRecord;
  record.readOnly=record.ownerId!==userId;
  record.sharedWith=record.readOnly?[]:(db().prepare('SELECT u.email FROM shares s JOIN users u ON s.user_id=u.id WHERE s.record_id=?').all(row.id) as {email:string}[]).map(x=>x.email);
  return record;
}
export function listRecords(userId:string):WorkspaceRecord[]{
  return (db().prepare('SELECT DISTINCT r.* FROM records r LEFT JOIN shares s ON s.record_id=r.id WHERE r.owner_id=? OR s.user_id=? ORDER BY r.updated_at DESC').all(userId,userId) as Row[]).map(r=>decode(r,userId));
}
export function getRecord(id:string,userId:string):WorkspaceRecord|null {
  const row=db().prepare('SELECT DISTINCT r.* FROM records r LEFT JOIN shares s ON s.record_id=r.id WHERE r.id=? AND (r.owner_id=? OR s.user_id=?)').get(id,userId,userId) as Row|undefined;
  return row?decode(row,userId):null;
}
export function saveRecord(userId:string,input:Partial<WorkspaceRecord>&{kind:RecordKind;title:string}):WorkspaceRecord {
  const previous=input.id?getRecord(input.id,userId):null;
  if(input.id&&(!previous||previous.ownerId!==userId))throw new Error('Record unavailable');
  const now=new Date().toISOString();
  const record:WorkspaceRecord={...previous,...input,id:previous?.id||randomUUID(),ownerId:userId,createdAt:previous?.createdAt||now,updatedAt:now,content:input.content??previous?.content??'',status:input.status||previous?.status||'In progress',visibility:previous?.visibility||'private',sharedWith:[]};
  db().prepare('INSERT INTO records VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at').run(record.id,userId,record.kind,record.visibility,seal(JSON.stringify(record)),now);
  return getRecord(record.id,userId)!;
}
export function deleteRecord(id:string,userId:string){db().prepare('DELETE FROM records WHERE id=? AND owner_id=?').run(id,userId);}
export function deleteChatHistory(userId:string){return db().prepare("DELETE FROM records WHERE owner_id=? AND kind='chat'").run(userId).changes;}
export function storeFile(recordId:string,data:Buffer){db().prepare('INSERT INTO files VALUES(?,?)').run(recordId,seal(data.toString('base64')));}
export function getFile(recordId:string,userId:string):Buffer|null {
  if(!getRecord(recordId,userId))return null;
  const row=db().prepare('SELECT data FROM files WHERE record_id=?').get(recordId) as {data:string}|undefined;
  return row?Buffer.from(unseal(row.data),'base64'):null;
}
export function shareRecord(id:string,userId:string,email:string,revoke=false){
  const record=getRecord(id,userId);if(!record||record.ownerId!==userId)throw new Error('Record unavailable');
  const target=db().prepare('SELECT id FROM users WHERE email=?').get(email.toLowerCase()) as {id:string}|undefined;
  if(!target)throw new Error('The recipient needs a Garra Rufa account before you can share this item.');
  if(revoke)db().prepare('DELETE FROM shares WHERE record_id=? AND user_id=?').run(id,target.id);
  else db().prepare('INSERT OR IGNORE INTO shares VALUES(?,?)').run(id,target.id);
}
export function publishRecord(id:string,userId:string,publish:boolean){
  const r=getRecord(id,userId);if(!r||r.ownerId!==userId||r.kind!=='project')throw new Error('Only your research projects can be published.');
  r.visibility=publish?'public':'private';r.status=publish?'Published':'In progress';
  db().prepare('UPDATE records SET visibility=?,payload=? WHERE id=?').run(r.visibility,seal(JSON.stringify(r)),id);
}
export function publicProjects(conditionId?:string){
  return (db().prepare("SELECT r.*,u.name FROM records r JOIN users u ON r.owner_id=u.id WHERE r.kind='project' AND r.visibility='public' ORDER BY r.updated_at DESC LIMIT ?").all(conditionId?-1:50) as (Row&{name:string})[]).map(row=>{
    const r=JSON.parse(unseal(row.payload)) as WorkspaceRecord;
    return {id:r.id,title:r.title,content:r.content,diseaseId:r.diseaseId,author:row.name,updatedAt:r.updatedAt};
  }).filter(project=>!conditionId||project.diseaseId===conditionId).slice(0,50);
}
