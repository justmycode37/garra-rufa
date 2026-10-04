import { handler,json,requireUser,HttpError } from '@/lib/http';
import { saveRecord,storeFile,listRecords } from "@/lib/persistence";
export const POST=handler(async(req)=>{
  const u=await requireUser();
  if(Number(req.headers.get('content-length')||0)>11*1024*1024)throw new HttpError(413,'Please upload a file smaller than 10 MB.');
  const form=await req.formData();const file=form.get('file');
  if(!(file instanceof File)||file.size>10*1024*1024||!file.size)throw new HttpError(400,'Choose a PDF, image, or text file up to 10 MB.');
  const allowed=['application/pdf','image/png','image/jpeg','image/webp','text/plain','text/csv'];
  if(!allowed.includes(file.type))throw new HttpError(400,'Supported formats: PDF, PNG, JPG, WebP, TXT, CSV.');
  const used=(await listRecords(u.id)).filter(r=>r.ownerId===u.id).reduce((n,r)=>n+(r.fileSize||0),0);
  if(used+file.size>100*1024*1024)throw new HttpError(413,'Your workspace has reached its 100 MB file limit.');
  const bytes=Buffer.from(await file.arrayBuffer());
  const valid=file.type==='application/pdf'?bytes.subarray(0,5).toString()==='%PDF-':file.type==='image/png'?bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])):file.type==='image/jpeg'?bytes[0]===255&&bytes[1]===216:file.type==='image/webp'?bytes.subarray(0,4).toString()==='RIFF'&&bytes.subarray(8,12).toString()==='WEBP':!bytes.includes(0);
  if(!valid)throw new HttpError(400,'The file contents do not match its type.');
  const name=file.name.replace(/[\x00-\x1f/\\]/g,'_').slice(0,160);
  const r=(await saveRecord(u.id,{kind:'document',title:name,content:file.type.startsWith('text/')?bytes.toString('utf8').slice(0,60000):'',status:'Saved',fileName:name,fileType:file.type,fileSize:file.size}));
  (await storeFile(r.id,bytes));return json({record:r},201);
});
