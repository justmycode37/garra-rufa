-- Existing workspaces keep their selected perspective. New Auth accounts insert false.
alter table public.garra_users add column if not exists role_selected boolean not null default true;
