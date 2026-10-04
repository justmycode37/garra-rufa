import { requireUser,HttpError } from '@/lib/http';
import { getRecord,getFile } from "@/lib/persistence";
export async function GET(req:Request,context:{params:Promise<{id:string}>}){
  try{
    const u=await requireUser();const {id}=await context.params;const r=(await getRecord(id,u.id));const b=(await getFile(id,u.id));
    if(!r||!b)return new Response('Not found',{status:404});
    return new Response(new Uint8Array(b),{headers:{'Content-Type':r.fileType||'application/octet-stream','Content-Disposition':`attachment; filename*=UTF-8''${encodeURIComponent(r.fileName||'document')}`,'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}});
  }catch(e){return new Response('Not authorized',{status:e instanceof HttpError?e.status:500});}
}
