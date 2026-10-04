import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';
import { workspaceUser } from './chatgpt-store';
export class HttpError extends Error { constructor(public status:number,message:string){super(message);} }
export async function currentUser(){return workspaceUser((await cookies()).get('garra_session')?.value||'');}
export async function requireUser(){const user=await currentUser();if(!user)throw new HttpError(401,'Please sign in to continue.');return user;}
export function checkOrigin(req:Request){
  const origin=req.headers.get('origin');
  const host=req.headers.get('host')||new URL(req.url).host;
  let sameOrigin=true;
  if(origin){try{const parsed=new URL(origin);sameOrigin=process.env.APP_ORIGIN?parsed.origin===process.env.APP_ORIGIN:parsed.host===host&&['http:','https:'].includes(parsed.protocol);}catch{sameOrigin=false;}}
  if(!sameOrigin||req.headers.get('sec-fetch-site')==='cross-site')throw new HttpError(403,'Cross-site request rejected.');
}
export async function body(req:Request){
  if(Number(req.headers.get('content-length')||0)>200000)throw new HttpError(413,'This request is too large.');
  const text=await req.text();if(text.length>200000)throw new HttpError(413,'This request is too large.');
  try{return JSON.parse(text);}catch{throw new HttpError(400,'Please send a valid request.');}
}
export function handler(fn:(req:Request)=>Promise<Response>){return async(req:Request)=>{
  try {if(!['GET','HEAD'].includes(req.method))checkOrigin(req);return await fn(req);}
  catch(e){if(e instanceof HttpError)return NextResponse.json({error:e.message},{status:e.status});
    if(e instanceof Error&&e.name==='ZodError')return NextResponse.json({error:'Please check the form fields and try again.'},{status:400});
    console.error('Garra Rufa request failed:',e instanceof Error?e.name:'unknown');
    return NextResponse.json({error:'Something went wrong. Please try again.'},{status:500});}
};}
export function json(data:unknown,status=200){return NextResponse.json(data,{status,headers:{'Cache-Control':'no-store'}});}
