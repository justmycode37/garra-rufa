import { readFileSync } from 'node:fs';
const env=Object.fromEntries(readFileSync('.env.local','utf8').split('\n').filter(l=>l.includes('=')).map(l=>[l.slice(0,l.indexOf('=')),l.slice(l.indexOf('=')+1)]));
const r=await fetch('https://api.openai.com/v1/models',{headers:{Authorization:`Bearer ${env.OPENAI_API_KEY}`},signal:AbortSignal.timeout(15000)});
const body=await r.json();
console.log('API authentication:',r.status);
if(r.ok)for(const model of ['gpt-6-luna','gpt-6-astra'])console.log(model,body.data.some(m=>m.id===model)?'available':'not listed');
else console.log('Provider error type:',body.error?.type||'unknown');
