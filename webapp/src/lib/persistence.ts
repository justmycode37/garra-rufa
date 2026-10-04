// All runtime database access goes through this async boundary. Local tests and
// self-hosted development retain SQLite; serverless deployments use Supabase.
import * as store from './store';
import * as conditions from './conditions';
import * as accounts from './chatgpt-store';
import { hostedStorage } from './supabase/admin';
export { ConditionError } from './conditions';
export { planEnabled } from './chatgpt-store';
export async function createUser(...args: Parameters<typeof store.createUser>): Promise<ReturnType<typeof store.createUser>> {
  return hostedStorage() ? (await import('./supabase/persistence')).createUser(...args) : store.createUser(...args);
}
export async function loginUser(...args: Parameters<typeof store.loginUser>): Promise<ReturnType<typeof store.loginUser>> {
  return hostedStorage() ? (await import('./supabase/persistence')).loginUser(...args) : store.loginUser(...args);
}
export async function createSession(...args: Parameters<typeof store.createSession>): Promise<ReturnType<typeof store.createSession>> {
  return hostedStorage() ? (await import('./supabase/persistence')).createSession(...args) : store.createSession(...args);
}
export async function sessionUser(...args: Parameters<typeof store.sessionUser>): Promise<ReturnType<typeof store.sessionUser>> {
  return hostedStorage() ? (await import('./supabase/persistence')).sessionUser(...args) : store.sessionUser(...args);
}
export async function createGuest(...args: Parameters<typeof store.createGuest>): Promise<ReturnType<typeof store.createGuest>> {
  return hostedStorage() ? (await import('./supabase/persistence')).createGuest(...args) : store.createGuest(...args);
}
export async function updateGuestRole(...args: Parameters<typeof store.updateGuestRole>): Promise<ReturnType<typeof store.updateGuestRole>> {
  return hostedStorage() ? (await import('./supabase/persistence')).updateGuestRole(...args) : store.updateGuestRole(...args);
}
export async function removeSession(...args: Parameters<typeof store.removeSession>): Promise<ReturnType<typeof store.removeSession>> {
  return hostedStorage() ? (await import('./supabase/persistence')).removeSession(...args) : store.removeSession(...args);
}
export async function limit(...args: Parameters<typeof store.limit>): Promise<ReturnType<typeof store.limit>> {
  return hostedStorage() ? (await import('./supabase/persistence')).limit(...args) : store.limit(...args);
}
export async function listRecords(...args: Parameters<typeof store.listRecords>): Promise<ReturnType<typeof store.listRecords>> {
  return hostedStorage() ? (await import('./supabase/persistence')).listRecords(...args) : store.listRecords(...args);
}
export async function getRecord(...args: Parameters<typeof store.getRecord>): Promise<ReturnType<typeof store.getRecord>> {
  return hostedStorage() ? (await import('./supabase/persistence')).getRecord(...args) : store.getRecord(...args);
}
export async function saveRecord(...args: Parameters<typeof store.saveRecord>): Promise<ReturnType<typeof store.saveRecord>> {
  return hostedStorage() ? (await import('./supabase/persistence')).saveRecord(...args) : store.saveRecord(...args);
}
export async function deleteRecord(...args: Parameters<typeof store.deleteRecord>): Promise<ReturnType<typeof store.deleteRecord>> {
  return hostedStorage() ? (await import('./supabase/persistence')).deleteRecord(...args) : store.deleteRecord(...args);
}
export async function deleteChatHistory(...args: Parameters<typeof store.deleteChatHistory>): Promise<ReturnType<typeof store.deleteChatHistory>> {
  return hostedStorage() ? (await import('./supabase/persistence')).deleteChatHistory(...args) : store.deleteChatHistory(...args);
}
export async function storeFile(...args: Parameters<typeof store.storeFile>): Promise<ReturnType<typeof store.storeFile>> {
  return hostedStorage() ? (await import('./supabase/persistence')).storeFile(...args) : store.storeFile(...args);
}
export async function getFile(...args: Parameters<typeof store.getFile>): Promise<ReturnType<typeof store.getFile>> {
  return hostedStorage() ? (await import('./supabase/persistence')).getFile(...args) : store.getFile(...args);
}
export async function shareRecord(...args: Parameters<typeof store.shareRecord>): Promise<ReturnType<typeof store.shareRecord>> {
  return hostedStorage() ? (await import('./supabase/persistence')).shareRecord(...args) : store.shareRecord(...args);
}
export async function publishRecord(...args: Parameters<typeof store.publishRecord>): Promise<ReturnType<typeof store.publishRecord>> {
  return hostedStorage() ? (await import('./supabase/persistence')).publishRecord(...args) : store.publishRecord(...args);
}
export async function publicProjects(...args: Parameters<typeof store.publicProjects>): Promise<ReturnType<typeof store.publicProjects>> {
  return hostedStorage() ? (await import('./supabase/persistence')).publicProjects(...args) : store.publicProjects(...args);
}
export async function listConditions(...args: Parameters<typeof conditions.listConditions>): Promise<ReturnType<typeof conditions.listConditions>> {
  return hostedStorage() ? (await import('./supabase/persistence')).listConditions(...args) : conditions.listConditions(...args);
}
export async function findCondition(...args: Parameters<typeof conditions.findCondition>): Promise<ReturnType<typeof conditions.findCondition>> {
  return hostedStorage() ? (await import('./supabase/persistence')).findCondition(...args) : conditions.findCondition(...args);
}
export async function resolveCondition(...args: Parameters<typeof conditions.resolveCondition>): Promise<ReturnType<typeof conditions.resolveCondition>> {
  return hostedStorage() ? (await import('./supabase/persistence')).resolveCondition(...args) : conditions.resolveCondition(...args);
}
export async function saveRecordWithCondition(...args: Parameters<typeof conditions.saveRecordWithCondition>): Promise<ReturnType<typeof conditions.saveRecordWithCondition>> {
  return hostedStorage() ? (await import('./supabase/persistence')).saveRecordWithCondition(...args) : conditions.saveRecordWithCondition(...args);
}
export async function getCommunityProfiles(...args: Parameters<typeof conditions.getCommunityProfiles>): Promise<ReturnType<typeof conditions.getCommunityProfiles>> {
  return hostedStorage() ? (await import('./supabase/persistence')).getCommunityProfiles(...args) : conditions.getCommunityProfiles(...args);
}
export async function getCommunityProfile(...args: Parameters<typeof conditions.getCommunityProfile>): Promise<ReturnType<typeof conditions.getCommunityProfile>> {
  return hostedStorage() ? (await import('./supabase/persistence')).getCommunityProfile(...args) : conditions.getCommunityProfile(...args);
}
export async function saveCommunityProfile(...args: Parameters<typeof conditions.saveCommunityProfile>): Promise<ReturnType<typeof conditions.saveCommunityProfile>> {
  return hostedStorage() ? (await import('./supabase/persistence')).saveCommunityProfile(...args) : conditions.saveCommunityProfile(...args);
}
export async function communityData(...args: Parameters<typeof conditions.communityData>): Promise<ReturnType<typeof conditions.communityData>> {
  return hostedStorage() ? (await import('./supabase/persistence')).communityData(...args) : conditions.communityData(...args);
}
export async function addCommunityPost(...args: Parameters<typeof conditions.addCommunityPost>): Promise<ReturnType<typeof conditions.addCommunityPost>> {
  return hostedStorage() ? (await import('./supabase/persistence')).addCommunityPost(...args) : conditions.addCommunityPost(...args);
}
export async function deleteCommunityPost(...args: Parameters<typeof conditions.deleteCommunityPost>): Promise<ReturnType<typeof conditions.deleteCommunityPost>> {
  return hostedStorage() ? (await import('./supabase/persistence')).deleteCommunityPost(...args) : conditions.deleteCommunityPost(...args);
}
export async function leaveCommunity(...args: Parameters<typeof conditions.leaveCommunity>): Promise<ReturnType<typeof conditions.leaveCommunity>> {
  return hostedStorage() ? (await import('./supabase/persistence')).leaveCommunity(...args) : conditions.leaveCommunity(...args);
}
export async function hostId(...args: Parameters<typeof accounts.hostId>): Promise<ReturnType<typeof accounts.hostId>> {
  return hostedStorage() ? (await import('./supabase/persistence')).hostId(...args) : accounts.hostId(...args);
}
export async function saveAttempt(...args: Parameters<typeof accounts.saveAttempt>): Promise<ReturnType<typeof accounts.saveAttempt>> {
  return hostedStorage() ? (await import('./supabase/persistence')).saveAttempt(...args) : accounts.saveAttempt(...args);
}
export async function consumeAttempt(...args: Parameters<typeof accounts.consumeAttempt>): Promise<ReturnType<typeof accounts.consumeAttempt>> {
  return hostedStorage() ? (await import('./supabase/persistence')).consumeAttempt(...args) : accounts.consumeAttempt(...args);
}
export async function accountById(...args: Parameters<typeof accounts.accountById>): Promise<ReturnType<typeof accounts.accountById>> {
  return hostedStorage() ? (await import('./supabase/persistence')).accountById(...args) : accounts.accountById(...args);
}
export async function accountForUser(...args: Parameters<typeof accounts.accountForUser>): Promise<ReturnType<typeof accounts.accountForUser>> {
  return hostedStorage() ? (await import('./supabase/persistence')).accountForUser(...args) : accounts.accountForUser(...args);
}
export async function browserAccounts(...args: Parameters<typeof accounts.browserAccounts>): Promise<ReturnType<typeof accounts.browserAccounts>> {
  return hostedStorage() ? (await import('./supabase/persistence')).browserAccounts(...args) : accounts.browserAccounts(...args);
}
export async function saveAccount(...args: Parameters<typeof accounts.saveAccount>): Promise<ReturnType<typeof accounts.saveAccount>> {
  return hostedStorage() ? (await import('./supabase/persistence')).saveAccount(...args) : accounts.saveAccount(...args);
}
export async function connectAccount(...args: Parameters<typeof accounts.connectAccount>): Promise<ReturnType<typeof accounts.connectAccount>> {
  return hostedStorage() ? (await import('./supabase/persistence')).connectAccount(...args) : accounts.connectAccount(...args);
}
export async function withChatGPT(...args: Parameters<typeof accounts.withChatGPT>): Promise<ReturnType<typeof accounts.withChatGPT>> {
  return hostedStorage() ? (await import('./supabase/persistence')).withChatGPT(...args) : accounts.withChatGPT(...args);
}
export async function workspaceUser(...args: Parameters<typeof accounts.workspaceUser>): Promise<ReturnType<typeof accounts.workspaceUser>> {
  return hostedStorage() ? (await import('./supabase/persistence')).workspaceUser(...args) : accounts.workspaceUser(...args);
}
export async function acquireRefresh(...args: Parameters<typeof accounts.acquireRefresh>): Promise<ReturnType<typeof accounts.acquireRefresh>> {
  return hostedStorage() ? (await import('./supabase/persistence')).acquireRefresh(...args) : accounts.acquireRefresh(...args);
}
export async function releaseRefresh(...args: Parameters<typeof accounts.releaseRefresh>): Promise<ReturnType<typeof accounts.releaseRefresh>> {
  return hostedStorage() ? (await import('./supabase/persistence')).releaseRefresh(...args) : accounts.releaseRefresh(...args);
}
export async function updateUserRole(id: string, role: import('./types').Role) {
  if (hostedStorage()) return (await import('./supabase/persistence')).updateUserRole(id, role);
  store.db().prepare('UPDATE users SET role=? WHERE id=?').run(role,id);
}
export type { ChatGPTAccount, ChatGPTTokens, SignInAttempt } from './chatgpt-store';
