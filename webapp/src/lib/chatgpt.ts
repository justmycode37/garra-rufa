import { createHash, randomBytes } from 'node:crypto';
import { createRemoteJWKSet, jwtVerify, type JWTVerifyGetKey } from 'jose';
import { z } from 'zod';
import { accountById, accountForUser, acquireRefresh, browserAccounts, connectAccount, hostId, planEnabled, releaseRefresh, saveAccount, saveAttempt, type ChatGPTAccount, type ChatGPTTokens, type SignInAttempt } from './chatgpt-store';
import type { Role, User } from './types';

export const CHATGPT_ISSUER = 'https://auth.openai.com';
export const CHATGPT_RESOURCE = 'https://api.openai.com/v1';
const scopes = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct';
const randomValue = () => randomBytes(32).toString('base64url');
const discoverySchema = z.object({ issuer: z.literal(CHATGPT_ISSUER), authorization_endpoint: z.url(), token_endpoint: z.url(), jwks_uri: z.url(), revocation_endpoint: z.url().optional() });
type Discovery = z.infer<typeof discoverySchema>;
let discoveryCache: { value: Discovery; expires: number } | undefined;
let remoteKeys: ReturnType<typeof createRemoteJWKSet> | undefined;

export function chatGPTRequestUrl(request: Request) {
  // Next may construct req.url with its internal listening host. The browser's
  // exact loopback Host is what must match the OAuth callback and its cookies.
  const url = new URL(request.url);
  const host = request.headers.get('host');
  if (host) {
    if (!/^127\.0\.0\.1(?::\d{1,5})?$/.test(host)) throw new Error('Open the local app at 127.0.0.1 to sign in with ChatGPT.');
    url.host = host;
  }
  return url.toString();
}

export function localChatGPTOrigin(requestUrl: string, configuredOrigin = process.env.APP_ORIGIN) {
  const request = new URL(requestUrl);
  const origin = new URL(configuredOrigin || request.origin);
  // Dynamic registration is a local-app flow; never enable it on a hosted website.
  if (origin.protocol !== 'http:' || origin.hostname !== '127.0.0.1' || origin.origin !== request.origin || origin.username || origin.password) {
    throw new Error('ChatGPT sign-in is available at http://127.0.0.1 on this computer. Hosted access requires OpenAI approval.');
  }
  return origin.origin;
}

export async function discovery() {
  if (discoveryCache && discoveryCache.expires > Date.now()) return discoveryCache.value;
  const response = await fetch(`${CHATGPT_ISSUER}/.well-known/openid-configuration`, { signal: AbortSignal.timeout(15000), cache: 'no-store', redirect: 'error' });
  if (!response.ok) throw new Error('ChatGPT sign-in is temporarily unavailable. Please try again.');
  const value = discoverySchema.parse(await response.json());
  for (const endpoint of [value.authorization_endpoint, value.token_endpoint, value.jwks_uri, value.revocation_endpoint].filter(Boolean)) {
    if (new URL(endpoint!).origin !== CHATGPT_ISSUER) throw new Error('OpenAI sign-in configuration could not be verified.');
  }
  discoveryCache = { value, expires: Date.now() + 3600000 };
  return value;
}

export async function startChatGPTSignIn(options: { requestUrl: string; browserId: string; role: Role; current: User | null; accountId?: string; enablePlan?: boolean; conversation?: SignInAttempt['conversation'] }) {
  const origin = localChatGPTOrigin(options.requestUrl);
  const account = options.accountId ? browserAccounts(options.browserId).find(a => a.id === options.accountId) : undefined;
  if (options.accountId && !account) throw new Error('Choose a saved account from this browser or add a new one.');
  const config = await discovery();
  const attempt: SignInAttempt = {
    state: randomValue(), nonce: randomValue(), verifier: randomValue(), redirectUri: `${origin}/api/auth/chatgpt/callback`,
    browserId: options.browserId, role: options.role, currentUserId: options.current?.id,
    guestUserId: options.current?.guest ? options.current.id : undefined,
    accountId: account?.id, clientId: account?.clientId || 'dynamic_agent_client',
    conversation: options.conversation,
  };
  const url = new URL(config.authorization_endpoint);
  url.search = new URLSearchParams({ client_id: attempt.clientId, ext_agent_host_id: hostId(), response_type: 'code', redirect_uri: attempt.redirectUri,
    scope: scopes, resource: CHATGPT_RESOURCE, state: attempt.state, nonce: attempt.nonce, code_challenge_method: 'S256',
    code_challenge: createHash('sha256').update(attempt.verifier).digest('base64url'),
  }).toString();
  if (!account) url.searchParams.set('agent_name_hint', 'Garra Rufa');
  if (account?.tokens?.idToken) url.searchParams.set('id_token_hint', account.tokens.idToken);
  if (account?.email) url.searchParams.set('login_hint', account.email);
  if (options.enablePlan) url.searchParams.set('prompt', 'consent');
  return { url: url.toString(), attemptId: saveAttempt(attempt) };
}

export function callbackClientId(attempt: SignInAttempt, returnedId: string | null) {
  if (attempt.clientId === 'dynamic_agent_client') {
    if (!returnedId || !/^oaiapp_[A-Za-z0-9_-]+$/.test(returnedId)) throw new Error('OpenAI registration was incomplete. Please try again.');
    return returnedId;
  }
  if (returnedId && returnedId !== attempt.clientId) throw new Error('The ChatGPT registration did not match.');
  return attempt.clientId;
}

export async function verifyChatGPTIdentity(idToken: string, clientId: string, nonce: string, key?: JWTVerifyGetKey) {
  if (!key) {
    const config = await discovery();
    remoteKeys ??= createRemoteJWKSet(new URL(config.jwks_uri));
    key = remoteKeys;
  }
  const { payload } = await jwtVerify(idToken, key, { issuer: CHATGPT_ISSUER, audience: clientId, requiredClaims: ['sub', 'exp', 'iat', 'nonce'], clockTolerance: 5 });
  if (payload.nonce !== nonce || !payload.sub || (payload.azp && payload.azp !== clientId) || (Array.isArray(payload.aud) && payload.aud.length > 1 && payload.azp !== clientId)) throw new Error('The ChatGPT identity could not be verified.');
  return { issuer: CHATGPT_ISSUER, clientId, subject: payload.sub, name: typeof payload.name === 'string' ? payload.name.slice(0, 80) : 'ChatGPT user', email: typeof payload.email === 'string' ? payload.email.slice(0, 254) : '', emailVerified: payload.email_verified === true };
}

const tokensSchema = z.object({ access_token: z.string().min(1).optional(), refresh_token: z.string().min(1).optional(), id_token: z.string().min(1).optional(), token_type: z.string().optional(), expires_in: z.number().positive().optional(), scope: z.string().optional() });
export function readTokens(value: unknown, previous?: ChatGPTTokens): ChatGPTTokens {
  const data = tokensSchema.parse(value);
  const grantedScopes = data.scope === undefined ? previous?.scopes || [] : data.scope.split(/\s+/).filter(Boolean);
  if ((!data.id_token && !previous?.idToken) || (data.access_token && (data.token_type?.toLowerCase() !== 'bearer' || !data.expires_in)) || (grantedScopes.includes('chatgpt.tokens.use.direct') && !data.access_token)) throw new Error('The ChatGPT credentials were incomplete.');
  if (previous && !data.refresh_token) throw new Error('ChatGPT did not return a replacement refresh token. Please try again.');
  return { accessToken: data.access_token || '', refreshToken: data.refresh_token, idToken: data.id_token || previous!.idToken,
    expiresAt: Date.now() + (data.expires_in || 0) * 1000, scopes: grantedScopes };
}

export async function finishChatGPTSignIn(attempt: SignInAttempt, callback: URL) {
  if (callback.searchParams.has('error')) throw new Error('ChatGPT sign-in was cancelled. You can try again whenever you’re ready.');
  const code = callback.searchParams.get('code');
  if (!code) throw new Error('OpenAI did not return an authorization code. Please try again.');
  const clientId = callbackClientId(attempt, callback.searchParams.get('client_id'));
  const config = await discovery();
  const response = await fetch(config.token_endpoint, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'authorization_code', client_id: clientId, code, code_verifier: attempt.verifier, redirect_uri: attempt.redirectUri, resource: CHATGPT_RESOURCE }),
    signal: AbortSignal.timeout(20000), redirect: 'error', cache: 'no-store' });
  if (!response.ok) throw new Error('OpenAI could not complete sign-in. Please start sign-in again.');
  const tokens = readTokens(await response.json());
  const identity = await verifyChatGPTIdentity(tokens.idToken, clientId, attempt.nonce);
  return connectAccount(identity, tokens, attempt);
}

const terminalRefreshErrors = new Set(['invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired', 'refresh_token_invalidated', 'refresh_token_reused']);
const globals = globalThis as unknown as { garraChatGPTRefreshes?: Map<string, Promise<ChatGPTAccount>> };
const refreshes = globals.garraChatGPTRefreshes ??= new Map();

async function refreshAccount(account: ChatGPTAccount): Promise<ChatGPTAccount> {
  if (!account.tokens?.refreshToken) throw new Error('Please sign in with ChatGPT again to renew your connection.');
  if (!acquireRefresh(account)) throw new Error('Your ChatGPT connection is being renewed. Please try again shortly.');
  try {
    const config = await discovery();
    const response = await fetch(config.token_endpoint, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ grant_type: 'refresh_token', client_id: account.clientId, refresh_token: account.tokens.refreshToken, resource: CHATGPT_RESOURCE }),
      signal: AbortSignal.timeout(20000), redirect: 'error', cache: 'no-store' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (terminalRefreshErrors.has(typeof data.error === 'string' ? data.error : data.error?.code)) {
        saveAccount({ ...account, tokens: undefined });
        throw new Error('Your ChatGPT connection has expired. Please sign in again.');
      }
      throw new Error('ChatGPT could not renew the connection. Please try again later.');
    }
    return saveAccount({ ...account, tokens: readTokens(data, account.tokens) });
  } finally { releaseRefresh(account.id); }
}

export async function chatGPTAccess(userId: string) {
  let account = accountForUser(userId);
  if (!account?.tokens) throw new Error('Continue with ChatGPT to use your plan for AI answers.');
  if (!planEnabled(account)) throw new Error('ChatGPT plan access is not enabled. Enable it from your account menu to use AI answers.');
  if (account.tokens.expiresAt <= Date.now() + 60000) {
    let pending = refreshes.get(account.id);
    if (!pending) {
      const id = account.id;
      pending = refreshAccount(account).finally(() => refreshes.delete(id));
      refreshes.set(id, pending);
    }
    account = await pending;
  }
  if (!account || !planEnabled(account)) throw new Error('ChatGPT plan access is not enabled. Enable it from your account menu.');
  return { accountId: account.id, accessToken: account.tokens!.accessToken };
}

export async function disconnectChatGPT(userId: string) {
  let account = accountForUser(userId);
  if (!account) return true;
  const pending = refreshes.get(account.id);
  if (pending) await pending.catch(() => {});
  account = accountById(account.id)!;
  const token = account.tokens?.refreshToken;
  // The version check prevents a concurrent refresh from resurrecting cleared credentials.
  saveAccount({ ...account, tokens: undefined });
  if (!token) return true;
  try {
    const config = await discovery();
    if (!config.revocation_endpoint) return false;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const response = await fetch(config.revocation_endpoint, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({ token, token_type_hint: 'refresh_token', client_id: account.clientId }), signal: AbortSignal.timeout(8000), redirect: 'error' });
        if (response.status === 200) return true;
        if (response.status < 500) return false;
      } catch { /* One bounded retry for temporary transport failures. */ }
      if (attempt === 0) await new Promise(resolve => setTimeout(resolve, 250));
    }
  } catch { /* Local sign-out remains effective when discovery is unavailable. */ }
  return false;
}
