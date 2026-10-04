import { randomUUID } from 'node:crypto';
import { diseases } from './knowledge';
import { db, getRecord, limit, publicProjects, saveRecord } from './store';
import type { RecordKind, WorkspaceRecord } from './types';

export type Condition = { id: string; name: string; curated: boolean; aliases: string[]; memberCount: number; postCount: number; joined?: boolean };
export type CommunityProfile = { alias: string; diseaseId: string; bio: string };
export class ConditionError extends Error { constructor(public status: number, message: string) { super(message); } }
type ConditionRow = { id: string; name: string; curated: number; memberCount: number; postCount: number };
const initialized = new WeakSet<object>();
const extraAliases: Record<string, string[]> = {
  eds: ['Ehlers Danlos syndrome', 'Ehlers-Danlos syndrome'],
  huntington: ["Huntington's disease", 'Huntingtons disease'],
};

/** Punctuation and spacing variants share a community; subtype words and numbers remain significant. */
export function normalizeCondition(value: string): string {
  return value.normalize('NFKC').toLocaleLowerCase('en').replace(/[’'`]/g, '').replace(/\+/g, ' plus ').replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/\s+/g, ' ');
}
function cleanName(value: string): string {
  const name = value.normalize('NFKC').trim().replace(/\s+/g, ' ');
  if (name.length < 2 || name.length > 120 || !normalizeCondition(name) || /[\u0000-\u001f\u007f]/.test(name)) throw new ConditionError(400, 'Enter a condition name between 2 and 120 characters.');
  return name;
}
function transaction<T>(work: () => T): T {
  const database = db();
  database.exec('SAVEPOINT condition_write');
  try { const result = work(); database.exec('RELEASE condition_write'); return result; }
  catch (error) { database.exec('ROLLBACK TO condition_write; RELEASE condition_write'); throw error; }
}
function registry() {
  const database = db();
  if (initialized.has(database)) return database;
  database.exec(`
    CREATE TABLE IF NOT EXISTS conditions (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE,
      curated INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS condition_aliases (
      normalized_name TEXT PRIMARY KEY, label TEXT NOT NULL,
      condition_id TEXT NOT NULL REFERENCES conditions(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS condition_alias_condition ON condition_aliases(condition_id);
    CREATE TABLE IF NOT EXISTS community_memberships (
      user_id TEXT NOT NULL REFERENCES users(id), condition_id TEXT NOT NULL REFERENCES conditions(id),
      alias TEXT NOT NULL, bio TEXT NOT NULL, PRIMARY KEY(user_id,condition_id)
    );
    CREATE INDEX IF NOT EXISTS community_membership_condition ON community_memberships(condition_id);
    CREATE TABLE IF NOT EXISTS condition_migrations (name TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS community_posts (
      id TEXT PRIMARY KEY, condition_id TEXT NOT NULL REFERENCES conditions(id),
      author_id TEXT NOT NULL REFERENCES users(id), author_alias TEXT NOT NULL,
      content TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS community_posts_condition ON community_posts(condition_id,created_at);
  `);
  transaction(() => {
    for (const disease of diseases) {
      database.prepare('INSERT OR IGNORE INTO conditions VALUES(?,?,?,?,?)').run(disease.id, disease.name, normalizeCondition(disease.name), 1, new Date().toISOString());
      for (const alias of [disease.name, disease.shortName, disease.id, ...(extraAliases[disease.id] || [])]) {
        database.prepare('INSERT OR IGNORE INTO condition_aliases VALUES(?,?,?)').run(normalizeCondition(alias), alias, disease.id);
      }
    }
    if (!database.prepare("SELECT name FROM condition_migrations WHERE name='memberships-v1'").get()) {
      database.exec(`INSERT OR IGNORE INTO community_memberships(user_id,condition_id,alias,bio)
        SELECT m.user_id,m.disease_id,m.alias,m.bio FROM community m JOIN conditions c ON c.id=m.disease_id;
        INSERT INTO condition_migrations VALUES('memberships-v1');`);
    }
  });
  initialized.add(database);
  return database;
}
export function listConditions(): Condition[] {
  const database = registry();
  const aliases = database.prepare('SELECT condition_id AS conditionId,label FROM condition_aliases ORDER BY label').all() as { conditionId: string; label: string }[];
  return (database.prepare(`SELECT c.id,c.name,c.curated,
    (SELECT count(*) FROM community_memberships m WHERE m.condition_id=c.id) AS memberCount,
    (SELECT count(*) FROM community_posts p WHERE p.condition_id=c.id) AS postCount
    FROM conditions c ORDER BY c.name COLLATE NOCASE`).all() as ConditionRow[])
    .map(condition => ({ ...condition, curated: !!condition.curated, aliases: aliases.filter(alias => alias.conditionId === condition.id).map(alias => alias.label) }));
}
export function findCondition(id: string): Condition | null {
  return listConditions().find(condition => condition.id === id) || null;
}
export function resolveCondition(input: { diseaseId?: string; conditionName?: string }, actorId: string): Condition | null {
  const database = registry();
  if (!input.diseaseId && !input.conditionName?.trim()) return null;
  if (input.diseaseId) {
    const existing = findCondition(input.diseaseId);
    if (!existing) throw new ConditionError(400, 'Choose an existing condition or enter a new name.');
    if (input.conditionName?.trim() && !existing.aliases.some(alias => normalizeCondition(alias) === normalizeCondition(input.conditionName!))) throw new ConditionError(400, 'Choose one related condition.');
    return existing;
  }
  const name = cleanName(input.conditionName!);
  const normalized = normalizeCondition(name);
  const existing = database.prepare('SELECT condition_id AS id FROM condition_aliases WHERE normalized_name=?').get(normalized) as { id: string } | undefined;
  if (existing) return findCondition(existing.id);
  if (!database.prepare('SELECT id FROM users WHERE id=?').get(actorId)) throw new ConditionError(401, 'Open a workspace to add a condition.');
  if (!limit(`condition-create:${actorId}`, 20, 60 * 60 * 1000)) throw new ConditionError(429, 'Please wait before adding more conditions.');
  const id = randomUUID();
  database.prepare('INSERT INTO conditions VALUES(?,?,?,?,?)').run(id, name, normalized, 0, new Date().toISOString());
  database.prepare('INSERT INTO condition_aliases VALUES(?,?,?)').run(normalized, name, id);
  return findCondition(id);
}
export function saveRecordWithCondition(userId: string, input: Partial<WorkspaceRecord> & { kind: RecordKind; title: string; conditionName?: string }): WorkspaceRecord {
  registry();
  return transaction(() => {
    if (input.id) {
      const previous = getRecord(input.id, userId);
      if (!previous || previous.ownerId !== userId) throw new ConditionError(403, 'This item is read-only.');
      if (previous.kind !== input.kind) throw new ConditionError(400, 'An item’s type cannot be changed.');
    }
    const { conditionName, ...record } = input;
    if (input.diseaseId !== undefined || conditionName !== undefined) record.diseaseId = resolveCondition(input, userId)?.id || '';
    return saveRecord(userId, record);
  });
}
export function getCommunityProfiles(userId: string): CommunityProfile[] {
  return registry().prepare('SELECT alias,condition_id AS diseaseId,bio FROM community_memberships WHERE user_id=? ORDER BY condition_id').all(userId) as CommunityProfile[];
}
export function getCommunityProfile(userId: string, conditionId: string): CommunityProfile | null {
  return registry().prepare('SELECT alias,condition_id AS diseaseId,bio FROM community_memberships WHERE user_id=? AND condition_id=?').get(userId, conditionId) as CommunityProfile | undefined || null;
}
export function saveCommunityProfile(userId: string, input: { alias: string; diseaseId?: string; conditionName?: string; bio: string }) {
  registry();
  return transaction(() => {
    const condition = resolveCondition(input, userId);
    if (!condition) throw new ConditionError(400, 'Choose a condition for your community.');
    registry().prepare('INSERT INTO community_memberships VALUES(?,?,?,?) ON CONFLICT(user_id,condition_id) DO UPDATE SET alias=excluded.alias,bio=excluded.bio').run(userId, condition.id, input.alias, input.bio);
    return { profile: getCommunityProfile(userId, condition.id), condition };
  });
}
export function communityData(userId?: string, conditionId?: string) {
  const database = registry();
  const profiles = userId ? getCommunityProfiles(userId) : [];
  const condition = conditionId ? findCondition(conditionId) : null;
  if (conditionId && !condition) throw new ConditionError(404, 'Community not found.');
  const profile = profiles.find(item => item.diseaseId === conditionId) || null;
  const members = userId && condition ? database.prepare('SELECT user_id AS id,alias,condition_id AS diseaseId,bio FROM community_memberships WHERE condition_id=? ORDER BY alias LIMIT 100').all(condition.id) : [];
  const posts = userId && condition ? (database.prepare('SELECT id,condition_id AS conditionId,author_alias AS author,content,created_at AS createdAt,author_id AS authorId FROM community_posts WHERE condition_id=? ORDER BY created_at DESC,id DESC LIMIT 100').all(condition.id) as { id: string; conditionId: string; author: string; content: string; createdAt: string; authorId: string }[]).map(({ authorId, ...post }) => ({ ...post, own: authorId === userId })) : [];
  const projects = condition ? publicProjects(condition.id) : [];
  const conditions = listConditions().map(item => userId ? { ...item, joined: profiles.some(member => member.diseaseId === item.id) } : item);
  return { conditions, condition, profile, profiles, members, posts, projects };
}
export function addCommunityPost(userId: string, conditionId: string, content: string) {
  const database = registry();
  const text = content.trim();
  if (!text || text.length > 4000) throw new ConditionError(400, 'Write a post of up to 4,000 characters.');
  const profile = getCommunityProfile(userId, conditionId);
  if (!profile || profile.diseaseId !== conditionId) throw new ConditionError(403, 'Join this community before sharing a post.');
  if (!findCondition(conditionId)) throw new ConditionError(404, 'Community not found.');
  if (!limit(`community-post:${userId}`, 30, 60 * 60 * 1000)) throw new ConditionError(429, 'Please wait before sharing more posts.');
  const post = { id: randomUUID(), conditionId, author: profile.alias, content: text, createdAt: new Date().toISOString(), own: true };
  database.prepare('INSERT INTO community_posts VALUES(?,?,?,?,?,?)').run(post.id, conditionId, userId, profile.alias, text, post.createdAt);
  return post;
}
export function deleteCommunityPost(userId: string, postId: string) {
  const result = registry().prepare('DELETE FROM community_posts WHERE id=? AND author_id=?').run(postId, userId);
  if (!result.changes) throw new ConditionError(404, 'Post not found.');
}
export function leaveCommunity(userId: string, conditionId?: string) {
  if (conditionId) registry().prepare('DELETE FROM community_memberships WHERE user_id=? AND condition_id=?').run(userId, conditionId);
  else registry().prepare('DELETE FROM community_memberships WHERE user_id=?').run(userId);
  // Also remove the obsolete entry so it cannot imply membership to older code.
  registry().prepare('DELETE FROM community WHERE user_id=?').run(userId);
}
