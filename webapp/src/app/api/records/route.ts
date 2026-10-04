import { z } from 'zod';
import { handler,json,body,requireUser,HttpError } from '@/lib/http';
import { getRecord,listRecords,deleteRecord,deleteChatHistory,shareRecord,publishRecord } from '@/lib/store';
import { ConditionError,saveRecordWithCondition } from '@/lib/conditions';
const schema=z.object({id:z.uuid().optional(),kind:z.enum(['project','patient','document','note','chat','bookmark']),title:z.string().trim().min(1).max(160),content:z.string().max(60000).optional(),diseaseId:z.string().max(80).optional(),conditionName:z.string().trim().min(2).max(120).optional(),status:z.enum(['In progress','Planning','Complete','Draft','Saved']).optional(),tags:z.array(z.string().max(40)).max(12).optional()});
export const GET=handler(async()=>json({records:listRecords((await requireUser()).id)}));
export const POST=handler(async(req)=>{
  const u=await requireUser();const input=schema.parse(await body(req));
  if(input.kind==='patient'&&u.role!=='doctor')throw new HttpError(403,'Patient workspaces are available in doctor mode.');
  if(input.kind==='document'||input.kind==='chat')throw new HttpError(400,'Use the upload or chat flow for this item.');
  if(input.id){const existing=getRecord(input.id,u.id);if(!existing)throw new HttpError(404,'Item not found.');if(existing.ownerId!==u.id)throw new HttpError(403,'This item is read-only.');if(existing.kind!==input.kind)throw new HttpError(400,'An item’s type cannot be changed.');}
  try{return json({record:saveRecordWithCondition(u.id,input)});}catch(error){if(error instanceof ConditionError)throw new HttpError(error.status,error.message);throw error;}
});
export const PATCH=handler(async(req)=>{
  const u=await requireUser();const input=z.object({id:z.uuid(),action:z.enum(['share','revoke','publish','unpublish']),email:z.email().optional()}).parse(await body(req));
  const r=getRecord(input.id,u.id);if(!r||r.ownerId!==u.id)throw new HttpError(404,'Item not found.');
  try{
    if(input.action==='share'||input.action==='revoke'){if(!input.email)throw new HttpError(400,'Enter a recipient email.');shareRecord(input.id,u.id,input.email,input.action==='revoke');}
    else {if(u.role!=='researcher')throw new HttpError(403,'Only researchers can publish projects.');publishRecord(input.id,u.id,input.action==='publish');}
  }catch(e){throw new HttpError(400,e instanceof Error?e.message:'Unable to update sharing.');}
  return json({record:getRecord(input.id,u.id)});
});
export const DELETE=handler(async(req)=>{
  const u=await requireUser();
  const input=z.union([z.object({id:z.uuid()}).strict(),z.object({scope:z.literal('chat-history')}).strict()]).parse(await body(req));
  if('scope' in input)return json({ok:true,deleted:deleteChatHistory(u.id)});
  const record=getRecord(input.id,u.id);
  if(!record||record.ownerId!==u.id)throw new HttpError(404,'Item not found.');
  deleteRecord(input.id,u.id);return json({ok:true});
});
