import { z } from 'zod';
import { handler,json,body,HttpError,requireUser } from '@/lib/http';
import { answerQuery } from '@/lib/ai';
import { getRecord,saveRecord,limit } from '@/lib/store';
import type { Message } from '@/lib/types';
import { chatGPTRequestUrl, localChatGPTOrigin } from '@/lib/chatgpt';
export const maxDuration=180;
export const POST=handler(async(req)=>{
  const input=z.object({surface:z.enum(['landing','workspace']).default('workspace'),query:z.string().trim().min(2).max(16000),chatId:z.uuid().optional(),fileIds:z.array(z.uuid()).max(3).default([]),includeWorkspace:z.boolean().default(false),history:z.array(z.object({role:z.enum(['user','assistant']),text:z.string().max(8000),id:z.string()})).max(8).default([])}).parse(await body(req));
  const u=input.surface==='workspace'?await requireUser():null;
  if(input.surface==='landing'&&(input.chatId||input.fileIds.length||input.includeWorkspace))throw new HttpError(400,'Open your workspace to use private records or attachments.');
  // A process-wide anonymous budget also prevents spoofed IP headers bypassing limits.
  if(!limit(u?.guest?'ai:guest':u?`ai:${u.id}`:'ai:public',u&&!u.guest?40:60,10*60000))throw new HttpError(429,'The research assistant is busy. Please try again shortly.');
  const previous=u&&input.chatId?getRecord(input.chatId,u.id):null;
  if(input.chatId&&(!previous||previous.kind!=='chat'||previous.readOnly))throw new HttpError(404,'Conversation not found.');
  if(input.fileIds.length&&!u)throw new HttpError(401,'Sign in to review a document.');
  if(u)for(const id of input.fileIds){const r=getRecord(id,u.id);if(!r||r.kind!=='document')throw new HttpError(404,'Attachment not found.');}
  const history=previous?.messages||input.history;
  let chatGPTAllowed=true;try{localChatGPTOrigin(chatGPTRequestUrl(req));}catch{chatGPTAllowed=false;}
  const result=await answerQuery({query:input.query,user:u,history,fileIds:input.fileIds,includeWorkspace:input.includeWorkspace,chatGPTAllowed,billing:input.surface==='landing'?'api':'chatgpt'});
  const messages:Message[]=[...history,{id:crypto.randomUUID(),role:'user',text:input.query},{id:crypto.randomUUID(),role:'assistant',text:result.answer,sources:result.sources,diseases:result.diseases,steps:result.steps,mode:result.mode,model:result.model,effort:result.effort,warning:result.warning,region:result.region,suggestion:result.suggestion}];
  let chatId=input.chatId;
  if(u){
    // Deleting a conversation in another tab must not let an in-flight answer recreate it.
    if(previous&&!getRecord(previous.id,u.id))throw new HttpError(404,'This conversation was deleted. Start a new chat to continue.');
    const chat=saveRecord(u.id,{id:previous?.id,kind:'chat',title:previous?.title||input.query.slice(0,65),content:'',messages:messages.slice(-80)});chatId=chat.id;
  }
  return json({...result,messages,chatId});
});
