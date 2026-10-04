import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { db, seal, unseal, createUser, sessionUser } from './store';
import type { Message, Role, User } from './types';

export type ChatGPTTokens = { accessToken: string; refreshToken?: string; idToken: string; scopes: string[]; expiresAt: number };
export type ChatGPTAccount = {
  id: string; userId: string; issuer: string; clientId: string; subject: string;
  name: string; email: string; tokens?: ChatGPTTokens; welcomed: boolean; version: number;
};
export type SignInAttempt = {
  state: string; nonce: string; verifier: string; redirectUri: string; role: Role;
  browserId: string; currentUserId?: string; guestUserId?: string; accountId?: string; clientId: string;
  conversation?: { messages: Message[] };
};
const hash = (value: string) => createHash('sha256').update(value).digest('hex');

function database() {
  const d = db();
  d.exec(`
    CREATE TABLE IF NOT EXISTS chatgpt_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS chatgpt_accounts(
      id TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE REFERENCES users(id),
      identity TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
      refresh_until INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS chatgpt_browsers(
      browser_hash TEXT NOT NULL, account_id TEXT NOT NULL REFERENCES chatgpt_accounts(id),
      PRIMARY KEY(browser_hash, account_id)
    );
    CREATE TABLE IF NOT EXISTS chatgpt_attempts(id TEXT PRIMARY KEY, payload TEXT NOT NULL, expires INTEGER NOT NULL);
  `);
  return d;
}

export function hostId() {
  const d = database();
  d.prepare("INSERT OR IGNORE INTO chatgpt_settings VALUES('host', ?)").run(`urn:uuid:${randomUUID()}`);
  return (d.prepare("SELECT value FROM chatgpt_settings WHERE key='host'").get() as { value: string }).value;
}

export function saveAttempt(attempt: SignInAttempt) {
  const id = randomBytes(32).toString('base64url');
  const d = database();
  d.prepare('DELETE FROM chatgpt_attempts WHERE expires <= ?').run(Date.now());
  d.prepare('INSERT INTO chatgpt_attempts VALUES(?, ?, ?)').run(hash(id), seal(JSON.stringify(attempt)), Date.now() + 10 * 60000);
  return id;
}

export function consumeAttempt(id: string, state: string | null): SignInAttempt {
  // DELETE RETURNING consumes the transaction atomically, including invalid callbacks.
  const row = database().prepare('DELETE FROM chatgpt_attempts WHERE id=? RETURNING payload, expires').get(hash(id)) as { payload: string; expires: number } | undefined;
  if (!row || row.expires <= Date.now()) throw new Error('Sign-in expired. Please try again.');
  const attempt = JSON.parse(unseal(row.payload)) as SignInAttempt;
  if (!state || state !== attempt.state) throw new Error('Sign-in could not be verified. Please try again.');
  return attempt;
}

type AccountRow = { id: string; user_id: string; payload: string; version: number };
function decode(row: AccountRow | undefined): ChatGPTAccount | null {
  return row ? { ...JSON.parse(unseal(row.payload)), id: row.id, userId: row.user_id, version: row.version } : null;
}
export function accountById(id: string) { return decode(database().prepare('SELECT * FROM chatgpt_accounts WHERE id=?').get(id) as AccountRow | undefined); }
export function accountForUser(userId: string) { return decode(database().prepare('SELECT * FROM chatgpt_accounts WHERE user_id=?').get(userId) as AccountRow | undefined); }
export function browserAccounts(browserId: string) {
  if (!browserId) return [];
  return (database().prepare('SELECT a.* FROM chatgpt_accounts a JOIN chatgpt_browsers b ON a.id=b.account_id WHERE b.browser_hash=? ORDER BY a.rowid').all(hash(browserId)) as AccountRow[]).map(row => decode(row)!);
}
export function saveAccount(account: ChatGPTAccount) {
  const updated = database().prepare('UPDATE chatgpt_accounts SET payload=?, version=version+1 WHERE id=? AND version=?').run(seal(JSON.stringify(account)), account.id, account.version);
  if (!updated.changes) throw new Error('The ChatGPT connection changed. Please sign in again.');
  return { ...account, version: account.version + 1 };
}

export function connectAccount(identity: { issuer: string; clientId: string; subject: string; name: string; email: string; emailVerified?: boolean }, tokens: ChatGPTTokens, attempt: SignInAttempt) {
  const d = database();
  const identityKey = hash(JSON.stringify([identity.issuer, identity.clientId, identity.subject]));
  d.exec('BEGIN IMMEDIATE');
  try {
    let account = decode(d.prepare('SELECT * FROM chatgpt_accounts WHERE identity=?').get(identityKey) as AccountRow | undefined);
    if (attempt.accountId && account?.id !== attempt.accountId) throw new Error('The ChatGPT account did not match. Please try again.');
    if (!account) {
      // Only a new registration can adopt the explicitly selected guest workspace. Never link by email.
      const guest = attempt.guestUserId && d.prepare('SELECT user_id FROM guest_users WHERE user_id=?').get(attempt.guestUserId);
      const userId = guest ? attempt.guestUserId! : createUser(identity.name, `${randomUUID()}@chatgpt.invalid`, randomBytes(48).toString('hex'), attempt.role).id;
      account = { ...identity, id: randomUUID(), userId, tokens, welcomed: false, version: 0 };
      d.prepare('INSERT INTO chatgpt_accounts(id,user_id,identity,payload) VALUES(?,?,?,?)').run(account.id, userId, identityKey, seal(JSON.stringify(account)));
      if (guest) {
        d.prepare('DELETE FROM guest_users WHERE user_id=?').run(userId);
        d.prepare('UPDATE users SET name=?,role=? WHERE id=?').run(identity.name, attempt.role, userId);
      }
      // Preserve explicit email-based sharing when OpenAI verifies an unclaimed email.
      // A collision keeps the new account separate instead of linking or taking over the existing one.
      const email = identity.email.toLowerCase();
      if (identity.emailVerified && email && !d.prepare('SELECT id FROM users WHERE email=?').get(email)) {
        d.prepare('UPDATE users SET email=? WHERE id=?').run(email, userId);
      }
    } else account = saveAccount({ ...account, ...identity, tokens });
    d.prepare('INSERT OR IGNORE INTO chatgpt_browsers VALUES(?,?)').run(hash(attempt.browserId), account.id);
    d.exec('COMMIT');
    return account;
  } catch (error) { d.exec('ROLLBACK'); throw error; }
}

export function planEnabled(account: ChatGPTAccount | null) { return !!account?.tokens?.scopes.includes('chatgpt.tokens.use.direct'); }
export function withChatGPT(user: User | null): User | null {
  if (!user) return null;
  const account = accountForUser(user.id);
  return account ? { ...user, name: account.name, email: account.email, chatgpt: { accountId: account.id, planEnabled: planEnabled(account), needsWelcome: planEnabled(account) && !account.welcomed } } : user;
}
export function workspaceUser(token: string) {
  const user = withChatGPT(sessionUser(token));
  return user?.chatgpt && !user.guest ? user : null;
}
export function acquireRefresh(account: ChatGPTAccount) {
  return !!database().prepare('UPDATE chatgpt_accounts SET refresh_until=? WHERE id=? AND version=? AND refresh_until<?').run(Date.now() + 60000, account.id, account.version, Date.now()).changes;
}
export function releaseRefresh(id: string) { database().prepare('UPDATE chatgpt_accounts SET refresh_until=0 WHERE id=?').run(id); }
