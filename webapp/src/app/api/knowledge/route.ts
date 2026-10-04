import { diseases,searchDiseases } from '@/lib/knowledge';
import { handler,json,HttpError } from '@/lib/http';
import { getEvidence } from '@/lib/ai';
import { ResearchUnavailable } from '@/lib/research';
export const GET=handler(async(req)=>{
  const params=new URL(req.url).searchParams;const q=(params.get('q')||'').slice(0,200);
  try {return json(params.get('papers')==='1'?await getEvidence(q,req.signal):{diseases:q?searchDiseases(q):diseases});}
  catch(error){if(error instanceof ResearchUnavailable)throw new HttpError(503,error.message);throw error;}
});
