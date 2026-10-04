import { z } from 'zod';
import { handler,json,body,HttpError,requireUser } from '@/lib/http';
import { answerQuery } from '@/lib/ai';
import { getRecord,saveRecord,limit } from "@/lib/persistence";
import type { Message } from '@/lib/types';
import type { AnswerProgress, SearchResponse, SearchStreamEvent } from '@/lib/search-stream';
import { listConditions } from "@/lib/persistence";
import { recommendCommunities } from '@/lib/community-recommendations';
import { graphContext, type GraphContext } from '@/lib/graph-context';
import { GraphBuildsUnavailable } from '@/lib/graph-builds';
export const maxDuration=180;
export const POST=handler(async(req)=>{
  const input=z.object({surface:z.enum(['landing','workspace']).default('workspace'),query:z.string().trim().min(2).max(16000),chatId:z.uuid().optional(),fileIds:z.array(z.uuid()).max(3).default([]),includeWorkspace:z.boolean().default(false),history:z.array(z.object({role:z.enum(['user','assistant']),text:z.string().max(8000),id:z.string()})).max(8).default([]),graph:z.object({id:z.string().regex(/^[a-z0-9-]{1,60}$/),view:z.enum(['present','evidence'])}).optional()}).parse(await body(req));
  const u=input.surface==='workspace'?await requireUser():null;
  if(input.surface==='landing'&&(input.chatId||input.fileIds.length||input.includeWorkspace))throw new HttpError(400,'Open your workspace to use private records or attachments.');
  // A process-wide anonymous budget also prevents spoofed IP headers bypassing limits.
  if(!(await limit(u?.guest?'ai:guest':u?`ai:${u.id}`:'ai:public',u&&!u.guest?40:60,10*60000)))throw new HttpError(429,'The research assistant is busy. Please try again shortly.');
  if(!(await limit('ai:deployment',200,10*60000)))throw new HttpError(429,'The assistant is busy. Please try again shortly.');
  const previous=u&&input.chatId?(await getRecord(input.chatId,u.id)):null;
  if(input.chatId&&(!previous||previous.kind!=='chat'||previous.readOnly))throw new HttpError(404,'Conversation not found.');
  if(input.fileIds.length&&!u)throw new HttpError(401,'Sign in to review a document.');
  if(u)for(const id of input.fileIds){const r=(await getRecord(id,u.id));if(!r||r.kind!=='document')throw new HttpError(404,'Attachment not found.');}
  const history=previous?.messages||input.history;
  // The graph chat: the open graph's pipeline report is the answer's context.
  let graph:GraphContext|null=null;
  if(input.graph){
    try{graph=await graphContext(input.graph.id,input.graph.view);}
    catch(error){if(error instanceof GraphBuildsUnavailable)throw new HttpError(503,error.message);throw error;}
    if(!graph)throw new HttpError(404,'The report of this graph is not available.');
  }
  async function complete(signal:AbortSignal,onProgress?:(event:AnswerProgress)=>void):Promise<SearchResponse>{
    signal.throwIfAborted();
    const result=await answerQuery({query:input.query,user:u,history,fileIds:input.fileIds,includeWorkspace:input.includeWorkspace,surface:input.surface,graph,onProgress,signal});
    signal.throwIfAborted();
    const communities=recommendCommunities(input.query,(await listConditions()));
    const messages:Message[]=[...history,{id:crypto.randomUUID(),role:'user',text:input.query},{id:crypto.randomUUID(),role:'assistant',text:result.answer,sources:result.sources,diseases:result.diseases,steps:result.steps,mode:result.mode,model:result.model,effort:result.effort,warning:result.warning,region:result.region,suggestion:result.suggestion}];
    if(communities.length)messages[messages.length-1].communities=communities;
    let chatId=input.chatId;
    if(u){
      // Deleting a conversation in another tab must not let an in-flight answer recreate it.
      if(previous&&!(await getRecord(previous.id,u.id)))throw new HttpError(404,'This conversation was deleted. Start a new chat to continue.');
      const chat=(await saveRecord(u.id,{id:previous?.id,kind:'chat',title:previous?.title||input.query.slice(0,65),content:'',messages:messages.slice(-80)}));chatId=chat.id;
    }
    return {...result,messages,chatId};
  }
  if(!req.headers.get('accept')?.includes('application/x-ndjson'))return json(await complete(req.signal));

  const abort=new AbortController();
  const signal=AbortSignal.any([req.signal,abort.signal]);
  const encoder=new TextEncoder();
  let cancelled=false;
  const stream=new ReadableStream<Uint8Array>({
    async start(controller){
      const send=(event:SearchStreamEvent)=>{if(!signal.aborted)controller.enqueue(encoder.encode(JSON.stringify(event)+'\n'));};
      // Flush headers while evidence lookup and the model's first tokens are pending.
      controller.enqueue(encoder.encode('\n'));
      try{send({type:'complete',result:await complete(signal,send)});}
      catch(error){send({type:'error',error:error instanceof HttpError?error.message:'The answer was interrupted. Please try again.'});}
      finally{if(!cancelled)controller.close();}
    },
    cancel(){cancelled=true;abort.abort();},
  });
  return new Response(stream,{headers:{'Content-Type':'application/x-ndjson; charset=utf-8','Cache-Control':'no-store, no-transform','X-Accel-Buffering':'no'}});
});
