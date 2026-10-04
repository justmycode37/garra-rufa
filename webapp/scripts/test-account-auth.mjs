// Synthetic account checks. Removes only the users and records created here.
import assert from 'node:assert/strict';
import { randomUUID, randomBytes } from 'node:crypto';
import { createClient } from '@supabase/supabase-js';
const origin = process.env.GARRA_TEST_ORIGIN || 'http://127.0.0.1:3100';
const admin = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL, process.env.SUPABASE_SECRET_KEY, { auth: { persistSession: false, autoRefreshToken: false } });
const suffix = randomUUID();
const password = randomBytes(24).toString('base64url');
const users = [];
const accounts = [1,2].map(index => ({ email: `garra-check-${suffix}-${index}@example.com`, jar: new Map() }));
async function request(account, path, method='GET', input, extra={}) {
  const response = await fetch(origin+path, { method, redirect:'manual', signal:AbortSignal.timeout(180000), headers:{Origin:origin, Cookie:[...account.jar].map(([k,v])=>`${k}=${v}`).join('; '), ...(input?{'Content-Type':'application/json'}:{}),...extra},body:input?JSON.stringify(input):undefined });
  const setCookies = response.headers.getSetCookie();
  for (const cookie of setCookies) { const pair=cookie.split(';')[0];const split=pair.indexOf('=');const name=pair.slice(0,split), value=pair.slice(split+1);if(value)account.jar.set(name,value);else account.jar.delete(name); }
  let data;try{data=await response.json()}catch{data={}}
  return {status:response.status,data,setCookies,location:response.headers.get('location')};
}
function check(value,label) { assert.ok(value,label);console.log('PASS '+label); }
try {
  for (const [index,account] of accounts.entries()) {
    const response = await request(account,'/api/auth','POST',{action:'signup',name:'Synthetic account check',email:account.email,password,role:index?'patient':'doctor'});
    check(response.status===200&&response.data.user?.id,`account ${index+1} registers without confirmation`);
    account.id=response.data.user.id;users.push(account.id);
    check(response.data.user.needsRole===false&&response.data.user.role===(index?'patient':'doctor'),'role chosen before signup is saved');
    const chosen=await request(account,'/api/auth/role','POST',{role:index?'patient':'doctor'});
    check(chosen.status===200&&chosen.data.user.needsRole===false,'role selection completes onboarding');
    check(response.setCookies.some(c=>c.startsWith('garra_session=')&&/httponly/i.test(c)&&/samesite=lax/i.test(c)), 'app session is HTTP-only');
    check(!response.data.user.chatgpt,'account requires no ChatGPT connection');
  }
  const [a,b]=accounts;
  const current=await request(a,'/api/auth');
  check(current.data.user?.id===a.id&&!current.data.user.needsRole,'session and completed onboarding survive a new request');
  const save=await request(a,'/api/records','POST',{kind:'note',title:'Synthetic auth isolation check',content:'Disposable test record'});
  check(save.status===200&&save.data.record?.id,'signed-in member can save a private record');
  const bRecords=await request(b,'/api/records');
  check(bRecords.status===200&&!bRecords.data.records.some(r=>r.id===save.data.record.id),'other accounts cannot read the private record');
  check((await request(b,'/api/records','DELETE',{id:save.data.record.id})).status===404,'other accounts cannot delete the private record');
  const mismatched={jar:new Map(b.jar)};mismatched.jar.set('garra_session',a.jar.get('garra_session'));
  check((await request(mismatched,'/api/records')).status===401,'session and Auth identity must belong to the same user');
  const replay={jar:new Map(a.jar)};
  check((await request(a,'/api/auth','DELETE')).status===200,'sign out succeeds');
  check((await request(replay,'/api/records')).status===401,'old cookie replay is rejected immediately after sign out');
  check((await request(a,'/api/auth','POST',{action:'login',email:a.email,password})).data.user?.id===a.id,'email/password signs back into the same account');
  check((await request(a,'/api/records')).data.records.some(r=>r.id===save.data.record.id),'records persist after sign out and sign in');
  check((await request(a,'/api/auth','POST',{action:'login',email:a.email,password:'incorrect-password'})).status===401,'wrong passwords are rejected');
  check((await request(a,'/api/auth/chatgpt')).status===410,'ChatGPT sign-in endpoint is retired');
  check((await request(a,'/api/auth/callback?code=invalid&next=https://example.com')).location===origin+'/?auth=error','invalid OAuth callback stays on the app');
  const anon={jar:new Map()};
  check((await request(anon,'/api/records')).status===401,'anonymous workspace access is rejected');
  check((await request(a,'/api/auth','POST',{action:'password',password:'another-synthetic-password'},{Origin:'https://example.com'})).status===403,'cross-site credential changes are rejected');
  if (process.env.GARRA_TEST_AI==='1') {
    const ai=await request(b,'/api/search','POST',{surface:'workspace',query:'Find the paper PMID:38517496. Give its title and a one-sentence description.'});
    check(ai.status===200&&ai.data.mode==='ai','workspace answers through the OpenAI API');
    check(ai.data.sources.some(s=>s.pmid==='38517496'),'AI returns the real paper from the dedicated repository stream');
    console.log(JSON.stringify({model:ai.data.model,sources:ai.data.sources.length,warning:ai.data.warning||null}));
  }
} finally {
  for(const account of accounts) {
    try { await request(account,'/api/auth','DELETE'); } catch {}
    // An account can have been created even if the response was interrupted.
    if(!account.id) {
      const {data}=await admin.auth.admin.listUsers({page:1,perPage:1000});
      account.id=data?.users.find(user=>user.email===account.email)?.id;
    }
    if(account.id) {
      const {error}=await admin.from('garra_users').delete().eq('id',account.id);if(error)throw new Error('Synthetic workspace cleanup failed');
      const removed=await admin.auth.admin.deleteUser(account.id);if(removed.error)throw new Error('Synthetic Auth cleanup failed');
    }
  }
  console.log('Synthetic accounts cleaned up.');
}
