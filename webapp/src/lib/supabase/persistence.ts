import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { admin, data, rpc } from './admin';
import { seal, unseal, hashPassword, verifyPassword } from '../store';
import { ConditionError, type Condition, type CommunityProfile } from '../conditions';
import { normalizeCondition } from '../community-recommendations';
import { planEnabled, type ChatGPTAccount, type SignInAttempt, type ChatGPTTokens } from '../chatgpt-store';
import type { Role, User, WorkspaceRecord, RecordKind } from '../types';

const hash = (text: string) => createHash('sha256').update(text).digest('hex');
const table = (name: string) => admin().from(`garra_${name}`);
export async function createUser(name: string, email: string, password: string, role: Role): Promise<User> {
  const user = { id: randomUUID(), name, email: email.toLowerCase(), role };
  await data(table('users').insert({ ...user, password: hashPassword(password), created_at: new Date().toISOString() }));
  return user;
}
export async function loginUser(email: string, password: string): Promise<User | null> {
  const user = await data<(User & { password: string }) | null>(table('users').select('*').eq('email', email.toLowerCase()).maybeSingle());
  const valid = verifyPassword(password, user?.password || '0'.repeat(32) + ':' + '0'.repeat(128));
  return valid && user ? { id: user.id, name: user.name, email: user.email, role: user.role } : null;
}
export async function createSession(userId: string) {
  const token = randomBytes(32).toString('hex');
  await data(table('sessions').delete().lt('expires', Date.now()));
  await data(table('sessions').insert({ token: hash(token), user_id: userId, expires: Date.now() + 7 * 86400000 }));
  return token;
}
export function sessionUser(token: string) { return rpc<User | null>('garra_session_user', { p_token: hash(token), p_now: Date.now() }); }
export async function removeSession(token: string) { await data(table('sessions').delete().eq('token', hash(token))); }
export function limit(bucket: string, max: number, windowMs: number) { return rpc<boolean>('garra_limit', { p_bucket: bucket, p_max: max, p_window: windowMs, p_now: Date.now() }); }
export async function createGuest(role: Role): Promise<User> {
  const name = role === 'doctor' ? 'Doctor' : role === 'patient' ? 'Patient' : 'Researcher';
  const user = await createUser(name, `${randomUUID()}@guest.invalid`, randomBytes(48).toString('hex'), role);
  await data(table('guests').insert({ user_id: user.id }));
  return { ...user, guest: true };
}
export async function updateUserRole(id: string, role: Role) { await data(table('users').update({ role, role_selected: true }).eq('id', id)); }
export async function updateGuestRole(id: string, role: Role): Promise<User> {
  if (!await data(table('guests').select('user_id').eq('user_id', id).maybeSingle())) throw new Error('Only guest workspaces can change perspective.');
  const name = role === 'doctor' ? 'Doctor' : role === 'patient' ? 'Patient' : 'Researcher';
  const user = await data<User>(table('users').update({ role, name }).eq('id', id).select('id,name,email,role').single());
  return { ...user, guest: true } as User;
}
type RecordRow = { id: string; owner_id: string; kind: RecordKind; visibility: 'private' | 'public'; payload: string; shared_emails: string[] };
function decode(row: RecordRow, userId: string): WorkspaceRecord {
  const record = JSON.parse(unseal(row.payload)) as WorkspaceRecord;
  return { ...record, id: row.id, ownerId: row.owner_id, kind: row.kind, visibility: row.visibility, readOnly: row.owner_id !== userId, sharedWith: row.owner_id === userId ? row.shared_emails || [] : [] };
}
export async function listRecords(userId: string) {
  return (await rpc<RecordRow[]>('garra_accessible_records', { p_user: userId })).map(row => decode(row, userId));
}
export async function getRecord(id: string, userId: string) {
  const rows = await rpc<RecordRow[]>('garra_accessible_records', { p_user: userId, p_record: id });
  return rows[0] ? decode(rows[0], userId) : null;
}
export async function saveRecord(userId: string, input: Partial<WorkspaceRecord> & { kind: RecordKind; title: string }): Promise<WorkspaceRecord> {
  const previous = input.id ? await getRecord(input.id, userId) : null;
  if (input.id && (!previous || previous.ownerId !== userId)) throw new Error('Record unavailable');
  const now = new Date().toISOString();
  const record: WorkspaceRecord = { ...previous, ...input, id: previous?.id || randomUUID(), ownerId: userId, createdAt: previous?.createdAt || now, updatedAt: now, content: input.content ?? previous?.content ?? '', status: input.status || previous?.status || 'In progress', visibility: previous?.visibility || 'private', sharedWith: [] };
  const row = { id: record.id, owner_id: userId, kind: record.kind, visibility: record.visibility, payload: seal(JSON.stringify(record)), updated_at: now, condition_id: record.diseaseId || null };
  // Updating must never recreate a concurrently deleted record, or overwrite a
  // record with a colliding ID owned by someone else.
  if (previous) {
    await data(table('records').update({ payload: row.payload, updated_at: now, condition_id: row.condition_id }).eq('id', record.id).eq('owner_id', userId).select('id').single());
  } else await data(table('records').insert(row));
  const saved = await getRecord(record.id, userId);
  if (!saved) throw new Error('Record unavailable');
  return saved;
}
export async function deleteRecord(id: string, userId: string) { await data(table('records').delete().eq('id', id).eq('owner_id', userId)); }
export async function deleteChatHistory(userId: string) { return (await data(table('records').delete().eq('owner_id', userId).eq('kind', 'chat').select('id'))).length; }
export async function storeFile(recordId: string, bytes: Buffer) { await data(table('files').insert({ record_id: recordId, data: seal(bytes.toString('base64')) })); }
export async function getFile(recordId: string, userId: string): Promise<Buffer | null> {
  if (!await getRecord(recordId, userId)) return null;
  const row = await data<{ data: string } | null>(table('files').select('data').eq('record_id', recordId).maybeSingle());
  return row ? Buffer.from(unseal(row.data), 'base64') : null;
}
export async function shareRecord(id: string, userId: string, email: string, revoke = false) {
  const record = await getRecord(id, userId);
  if (!record || record.ownerId !== userId) throw new Error('Record unavailable');
  const target = await data<{ id: string } | null>(table('users').select('id').eq('email', email.toLowerCase()).maybeSingle());
  if (!target) throw new Error('The recipient needs a Garra Rufa account before you can share this item.');
  if (revoke) await data(table('shares').delete().eq('record_id', id).eq('user_id', target.id));
  else await data(table('shares').upsert({ record_id: id, user_id: target.id }, { onConflict: 'record_id,user_id', ignoreDuplicates: true }));
}
export async function publishRecord(id: string, userId: string, publish: boolean) {
  const record = await getRecord(id, userId);
  if (!record || record.ownerId !== userId || record.kind !== 'project') throw new Error('Only your research projects can be published.');
  record.visibility = publish ? 'public' : 'private'; record.status = publish ? 'Published' : 'In progress';
  await data(table('records').update({ visibility: record.visibility, payload: seal(JSON.stringify(record)) }).eq('id', id).eq('owner_id', userId));
}
export async function publicProjects(conditionId?: string) {
  let query = table('records').select('payload,garra_users!owner_id(name)').eq('kind', 'project').eq('visibility', 'public').order('updated_at', { ascending: false }).limit(50);
  if (conditionId) query = query.eq('condition_id', conditionId);
  const rows = await data(query);
  return rows.map(row => {
    const record = JSON.parse(unseal(row.payload)) as WorkspaceRecord;
    const author = row.garra_users as unknown as { name: string };
    return { id: record.id, title: record.title, content: record.content, diseaseId: record.diseaseId, author: author.name, updatedAt: record.updatedAt };
  });
}

export function hostId() { return rpc<string>('garra_host_id', { p_candidate: `urn:uuid:${randomUUID()}` }); }
export async function saveAttempt(attempt: SignInAttempt) {
  const id = randomBytes(32).toString('base64url');
  await data(table('chatgpt_attempts').delete().lte('expires', Date.now()));
  await data(table('chatgpt_attempts').insert({ id: hash(id), payload: seal(JSON.stringify(attempt)), expires: Date.now() + 600000 }));
  return id;
}
export async function consumeAttempt(id: string, state: string | null): Promise<SignInAttempt> {
  const row = await data<{ payload: string; expires: number } | null>(table('chatgpt_attempts').delete().eq('id', hash(id)).select('payload,expires').maybeSingle());
  if (!row || row.expires <= Date.now()) throw new Error('Sign-in expired. Please try again.');
  const attempt = JSON.parse(unseal(row.payload)) as SignInAttempt;
  if (!state || state !== attempt.state) throw new Error('Sign-in could not be verified. Please try again.');
  return attempt;
}
type AccountRow = { id: string; user_id: string; payload: string; version: number; created?: boolean };
function decodeAccount(row: AccountRow | null): ChatGPTAccount | null {
  return row ? { ...JSON.parse(unseal(row.payload)), id: row.id, userId: row.user_id, version: row.version } : null;
}
export async function accountById(id: string) { return decodeAccount(await data(table('chatgpt_accounts').select('*').eq('id', id).maybeSingle())); }
export async function accountForUser(userId: string) { return decodeAccount(await data(table('chatgpt_accounts').select('*').eq('user_id', userId).maybeSingle())); }
export async function browserAccounts(browserId: string) {
  if (!browserId) return [];
  const memberships = await data(table('chatgpt_browsers').select('account_id').eq('browser_hash', hash(browserId)));
  if (!memberships.length) return [];
  return (await data(table('chatgpt_accounts').select('*').in('id', memberships.map(row => row.account_id)).order('created_at'))).map(row => decodeAccount(row as AccountRow)!);
}
export async function saveAccount(account: ChatGPTAccount) {
  const rows = await data(table('chatgpt_accounts').update({ payload: seal(JSON.stringify(account)), version: account.version + 1 }).eq('id', account.id).eq('version', account.version).select('id'));
  if (!rows.length) throw new Error('The ChatGPT connection changed. Please sign in again.');
  return { ...account, version: account.version + 1 };
}
export async function connectAccount(identity: { issuer: string; clientId: string; subject: string; name: string; email: string; emailVerified?: boolean }, tokens: ChatGPTTokens, attempt: SignInAttempt) {
  const candidate: ChatGPTAccount = { ...identity, id: randomUUID(), userId: randomUUID(), tokens, welcomed: false, version: 0 };
  const row = await rpc<AccountRow>('garra_connect_account', {
    p_identity: hash(JSON.stringify([identity.issuer, identity.clientId, identity.subject])), p_account: candidate.id, p_user: candidate.userId,
    p_payload: seal(JSON.stringify(candidate)), p_name: identity.name, p_email: identity.email, p_verified: !!identity.emailVerified,
    p_password: hashPassword(randomBytes(48).toString('hex')), p_role: attempt.role, p_guest: attempt.guestUserId || null,
    p_expected: attempt.accountId || null, p_browser: hash(attempt.browserId),
  });
  const account = decodeAccount(row)!;
  return row.created ? account : saveAccount({ ...account, ...identity, tokens });
}
export async function withChatGPT(user: User | null): Promise<User | null> {
  if (!user) return null;
  const account = await accountForUser(user.id);
  return account ? { ...user, name: account.name, email: account.email, chatgpt: { accountId: account.id, planEnabled: planEnabled(account), needsWelcome: planEnabled(account) && !account.welcomed } } : user;
}
export async function workspaceUser(token: string) {
  const user = await withChatGPT(await sessionUser(token));
  return user?.chatgpt && !user.guest ? user : null;
}
export async function acquireRefresh(account: ChatGPTAccount) {
  return !!(await data(table('chatgpt_accounts').update({ refresh_until: Date.now() + 60000 }).eq('id', account.id).eq('version', account.version).lt('refresh_until', Date.now()).select('id'))).length;
}
export async function releaseRefresh(id: string) { await data(table('chatgpt_accounts').update({ refresh_until: 0 }).eq('id', id)); }

export function listConditions() { return rpc<Condition[]>('garra_conditions_list'); }
export async function findCondition(id: string) { return (await listConditions()).find(item => item.id === id) || null; }
export async function resolveCondition(input: { diseaseId?: string; conditionName?: string }, actorId: string): Promise<Condition | null> {
  if (!input.diseaseId && !input.conditionName?.trim()) return null;
  if (input.diseaseId) {
    const existing = await findCondition(input.diseaseId);
    if (!existing) throw new ConditionError(400, 'Choose an existing condition or enter a new name.');
    if (input.conditionName?.trim() && !existing.aliases.some(alias => normalizeCondition(alias) === normalizeCondition(input.conditionName!))) throw new ConditionError(400, 'Choose one related condition.');
    return existing;
  }
  const name = input.conditionName!.normalize('NFKC').trim().replace(/\s+/g, ' ');
  const normalized = normalizeCondition(name);
  if (name.length < 2 || name.length > 120 || !normalized || /[\u0000-\u001f\u007f]/.test(name)) throw new ConditionError(400, 'Enter a condition name between 2 and 120 characters.');
  const id = await rpc<string>('garra_add_condition', { p_id: randomUUID(), p_name: name, p_normalized: normalized, p_actor: actorId, p_now: Date.now() });
  return findCondition(id);
}
export async function saveRecordWithCondition(userId: string, input: Partial<WorkspaceRecord> & { kind: RecordKind; title: string; conditionName?: string }): Promise<WorkspaceRecord> {
  if (input.id) {
    const previous = await getRecord(input.id, userId);
    if (!previous || previous.ownerId !== userId) throw new ConditionError(403, 'This item is read-only.');
    if (previous.kind !== input.kind) throw new ConditionError(400, 'An item’s type cannot be changed.');
  }
  const { conditionName, ...record } = input;
  if (input.diseaseId !== undefined || conditionName !== undefined) record.diseaseId = (await resolveCondition(input, userId))?.id || '';
  return saveRecord(userId, record);
}
export async function getCommunityProfiles(userId: string): Promise<CommunityProfile[]> {
  return await data(table('memberships').select('alias,diseaseId:condition_id,bio').eq('user_id', userId).order('condition_id'));
}
export async function getCommunityProfile(userId: string, conditionId: string): Promise<CommunityProfile | null> {
  return await data(table('memberships').select('alias,diseaseId:condition_id,bio').eq('user_id', userId).eq('condition_id', conditionId).maybeSingle());
}
export async function saveCommunityProfile(userId: string, input: { alias: string; diseaseId?: string; conditionName?: string; bio: string }) {
  const condition = await resolveCondition(input, userId);
  if (!condition) throw new ConditionError(400, 'Choose a condition for your community.');
  await data(table('memberships').upsert({ user_id: userId, condition_id: condition.id, alias: input.alias, bio: input.bio }, { onConflict: 'user_id,condition_id' }));
  return { profile: await getCommunityProfile(userId, condition.id), condition };
}
export async function communityData(userId?: string, conditionId?: string) {
  const conditions = await listConditions();
  const condition = conditionId ? conditions.find(item => item.id === conditionId) || null : null;
  if (conditionId && !condition) throw new ConditionError(404, 'Community not found.');
  const profiles = userId ? await getCommunityProfiles(userId) : [];
  const members = userId && condition ? await data(table('memberships').select('id:user_id,alias,diseaseId:condition_id,bio').eq('condition_id', condition.id).order('alias').limit(100)) : [];
  const posts = userId && condition ? (await data(table('posts').select('id,conditionId:condition_id,author:author_alias,content,createdAt:created_at,authorId:author_id').eq('condition_id', condition.id).order('created_at', { ascending: false }).order('id', { ascending: false }).limit(100))).map(({ authorId, ...post }) => ({ ...post, own: authorId === userId })) : [];
  return { conditions: conditions.map(item => userId ? { ...item, joined: profiles.some(profile => profile.diseaseId === item.id) } : item), condition, profiles, profile: profiles.find(item => item.diseaseId === conditionId) || null, members, posts, projects: condition ? await publicProjects(condition.id) : [] };
}
export async function addCommunityPost(userId: string, conditionId: string, content: string) {
  const text = content.trim();
  if (!text || text.length > 4000) throw new ConditionError(400, 'Write a post of up to 4,000 characters.');
  if (!await getCommunityProfile(userId, conditionId)) throw new ConditionError(403, 'Join this community before sharing a post.');
  return rpc<{ id: ReturnType<typeof randomUUID>; conditionId: string; author: string; content: string; createdAt: string; own: boolean }>('garra_add_post', { p_id: randomUUID(), p_user: userId, p_condition: conditionId, p_content: text, p_created: new Date().toISOString(), p_now: Date.now() });
}
export async function deleteCommunityPost(userId: string, postId: string) {
  const rows = await data(table('posts').delete().eq('id', postId).eq('author_id', userId).select('id'));
  if (!rows.length) throw new ConditionError(404, 'Post not found.');
}
export async function leaveCommunity(userId: string, conditionId?: string) {
  let query = table('memberships').delete().eq('user_id', userId);
  if (conditionId) query = query.eq('condition_id', conditionId);
  await data(query);
}
