import { z } from 'zod';
import { db } from './store';
import { hostedStorage, admin, data } from './supabase/admin';
import type { Role, User } from './types';
const identitySchema = z.object({ id: z.uuid(), email: z.email(), name: z.string().trim().min(1).max(100) });
export type AuthIdentity = z.infer<typeof identitySchema>;

// Call only after Auth verifies the session. The immutable Auth ID owns the
// workspace. An email match never adopts an older account's private records.
export async function authWorkspaceUser(identity: AuthIdentity, initialRole?: Role): Promise<User> {
  const verified = identitySchema.parse(identity);
  const role = z.enum(['researcher', 'doctor', 'patient']).parse(initialRole ?? 'researcher');
  const email = verified.email.toLowerCase();
  if (hostedStorage()) {
    let row = await data<(User & {role_selected:boolean}) | null>(admin().from('garra_users').select('id,name,email,role,role_selected').eq('id', verified.id).maybeSingle());
    if (!row) {
      const conflict = await data<{id:string} | null>(admin().from('garra_users').select('id').eq('email', email).maybeSingle());
      await data(admin().from('garra_users').upsert({ id: verified.id, name: verified.name, email: conflict ? `${verified.id}@auth.invalid` : email,
        role, role_selected: initialRole !== undefined, password: 'supabase-auth', created_at: new Date().toISOString() }, { onConflict: 'id', ignoreDuplicates: true }));
      row = await data<User & {role_selected:boolean}>(admin().from('garra_users').select('id,name,email,role,role_selected').eq('id', verified.id).single());
    }
    return { id:row!.id, name:row!.name, role:row!.role, email, needsRole:!row!.role_selected };
  }
  const database = db();
  if (!database.prepare('PRAGMA table_info(users)').all().some(column=>column.name==='role_selected')) database.exec('ALTER TABLE users ADD COLUMN role_selected INTEGER NOT NULL DEFAULT 1');
  let row = database.prepare('SELECT id,name,email,role,role_selected FROM users WHERE id=?').get(verified.id) as (User & {role_selected:number}) | undefined;
  if (!row) {
    const conflict = database.prepare('SELECT id FROM users WHERE email=?').get(email);
    database.prepare('INSERT OR IGNORE INTO users(id,name,email,password,role,created_at,role_selected) VALUES(?,?,?,?,?,?,?)')
      .run(verified.id, verified.name, conflict ? `${verified.id}@auth.invalid` : email, 'supabase-auth', role, new Date().toISOString(), initialRole ? 1 : 0);
    row = database.prepare('SELECT id,name,email,role,role_selected FROM users WHERE id=?').get(verified.id) as unknown as User & {role_selected:number};
  }
  return { id:row.id, name:row.name, role:row.role, email, needsRole:!row.role_selected };
}
