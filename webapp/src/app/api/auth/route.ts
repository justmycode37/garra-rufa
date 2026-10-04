import { cookies } from 'next/headers';
import { handler,json,currentUser,HttpError } from '@/lib/http';
import { removeSession } from '@/lib/store';
import { disconnectChatGPT } from '@/lib/chatgpt';
export const GET=handler(async()=>json({user:await currentUser()}));
export const POST=handler(async()=>{throw new HttpError(401,'Use ChatGPT to sign in to your workspace.');});
export const DELETE=handler(async()=>{
  const c=await cookies();const user=await currentUser();
  removeSession(c.get('garra_session')?.value||'');c.delete('garra_session');
  const revoked=user?await disconnectChatGPT(user.id):true;
  return json({ok:true,warning:revoked?undefined:'Signed out locally. OpenAI could not confirm disconnection; disconnect Garra Rufa in ChatGPT settings.'});
});
