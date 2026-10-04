import { z } from 'zod';
import { body, handler, json, requireUser } from '@/lib/http';
import { db } from '@/lib/store';

export const POST = handler(async req => {
  const user = await requireUser();
  const { role } = z.object({ role: z.enum(['researcher', 'doctor', 'patient']) }).parse(await body(req));
  db().prepare('UPDATE users SET role=? WHERE id=?').run(role, user.id);
  return json({ user: { ...user, role } });
});
