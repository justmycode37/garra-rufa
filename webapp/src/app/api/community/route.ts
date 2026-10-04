import { requireUser } from '@/lib/http';
import { createCommunityHandlers } from '@/lib/community-api';

export const { GET, POST, DELETE } = createCommunityHandlers(requireUser);
