import { z } from 'zod';
import { body, handler, json, requireUser } from '@/lib/http';
import { updateUserRole } from '@/lib/persistence';

export const POST = handler(async req => {
  const user = await requireUser();
  const { role } = z.object({ role: z.enum(['researcher', 'doctor', 'patient']) }).parse(await body(req));
  await updateUserRole(user.id, role);
  return json({ user: { ...user, role, needsRole: false } });
});
