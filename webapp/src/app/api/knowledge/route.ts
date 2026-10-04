import { diseases,searchDiseases } from '@/lib/knowledge';
import { handler,json } from '@/lib/http';
import { getEvidence } from '@/lib/ai';
export const GET=handler(async(req)=>{
  const params=new URL(req.url).searchParams;const q=(params.get('q')||'').slice(0,200);
  return json(params.get('papers')==='1'?await getEvidence(q):{diseases:q?searchDiseases(q):diseases});
});
