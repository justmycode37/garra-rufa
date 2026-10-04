-- ChatGPT identities are verified by the app server. They are not Supabase Auth
-- identities. No browser role can read these tables, including encrypted tokens.
create table public.garra_users (
  id text primary key, name text not null, email text not null unique,
  password text not null, role text not null check (role in ('doctor','patient','researcher')),
  created_at text not null
);
create table public.garra_guests (user_id text primary key references public.garra_users on delete cascade);
create table public.garra_sessions (token text primary key, user_id text not null references public.garra_users on delete cascade, expires bigint not null);
create index garra_session_user on public.garra_sessions(user_id);
create index garra_session_expiry on public.garra_sessions(expires);
create table public.garra_records (
  id text primary key, owner_id text not null references public.garra_users on delete cascade,
  kind text not null, visibility text not null default 'private' check (visibility in ('private','public')),
  payload text not null, updated_at text not null, condition_id text
);
create index garra_record_owner on public.garra_records(owner_id,kind,updated_at);
create index garra_public_projects on public.garra_records(condition_id,updated_at) where kind='project' and visibility='public';
create table public.garra_shares (
  record_id text not null references public.garra_records on delete cascade,
  user_id text not null references public.garra_users on delete cascade, primary key(record_id,user_id)
);
create index garra_share_user on public.garra_shares(user_id);
create table public.garra_files (record_id text primary key references public.garra_records on delete cascade, data text not null);
create table public.garra_limits (bucket text primary key, hits integer not null, reset bigint not null);
create index garra_limit_reset on public.garra_limits(reset);
create table public.garra_chatgpt_settings(key text primary key, value text not null);
create table public.garra_chatgpt_accounts (
  id text primary key, user_id text not null unique references public.garra_users on delete cascade,
  identity text not null unique, payload text not null, version integer not null default 0,
  refresh_until bigint not null default 0, created_at timestamptz not null default now()
);
create table public.garra_chatgpt_browsers (
  browser_hash text not null, account_id text not null references public.garra_chatgpt_accounts on delete cascade,
  primary key(browser_hash,account_id)
);
create index garra_browser_account on public.garra_chatgpt_browsers(account_id);
create table public.garra_chatgpt_attempts(id text primary key, payload text not null, expires bigint not null);
create index garra_attempt_expiry on public.garra_chatgpt_attempts(expires);
create table public.garra_conditions (
  id text primary key, name text not null, normalized_name text not null unique,
  curated boolean not null default false, created_at text not null
);
create table public.garra_condition_aliases (
  normalized_name text primary key, label text not null,
  condition_id text not null references public.garra_conditions on delete cascade
);
create index garra_alias_condition on public.garra_condition_aliases(condition_id);
create table public.garra_memberships (
  user_id text not null references public.garra_users on delete cascade,
  condition_id text not null references public.garra_conditions on delete cascade,
  alias text not null, bio text not null, primary key(user_id,condition_id)
);
create index garra_membership_condition on public.garra_memberships(condition_id);
create table public.garra_posts (
  id text primary key, condition_id text not null references public.garra_conditions on delete cascade,
  author_id text not null references public.garra_users on delete cascade,
  author_alias text not null, content text not null, created_at text not null
);
create index garra_posts_condition on public.garra_posts(condition_id,created_at);
create index garra_posts_author on public.garra_posts(author_id);

create function public.garra_limit(p_bucket text, p_max integer, p_window bigint, p_now bigint)
returns boolean language plpgsql security invoker set search_path='' as $$
declare total integer;
begin
  insert into public.garra_limits as l values(p_bucket,1,p_now+p_window)
  on conflict(bucket) do update set hits=case when l.reset<=p_now then 1 else l.hits+1 end,
    reset=case when l.reset<=p_now then p_now+p_window else l.reset end returning hits into total;
  return total<=p_max;
end $$;
create function public.garra_session_user(p_token text,p_now bigint)
returns jsonb language sql security invoker set search_path='' as $$
  select jsonb_build_object('id',u.id,'name',u.name,'email',u.email,'role',u.role,
    'guest',exists(select 1 from public.garra_guests g where g.user_id=u.id))
  from public.garra_users u join public.garra_sessions s on s.user_id=u.id
  where s.token=p_token and s.expires>p_now;
$$;
create function public.garra_accessible_records(p_user text,p_record text default null)
returns jsonb language sql security invoker set search_path='' as $$
  select coalesce(jsonb_agg(value order by updated_at desc),'[]'::jsonb) from (
    select r.updated_at,to_jsonb(r)||jsonb_build_object('shared_emails',case when r.owner_id=p_user then
      (select coalesce(jsonb_agg(u.email),'[]'::jsonb) from public.garra_shares s join public.garra_users u on u.id=s.user_id where s.record_id=r.id)
      else '[]'::jsonb end) as value
    from public.garra_records r where (p_record is null or r.id=p_record) and
      (r.owner_id=p_user or exists(select 1 from public.garra_shares s where s.record_id=r.id and s.user_id=p_user))
  ) allowed;
$$;
create function public.garra_host_id(p_candidate text)
returns text language plpgsql security invoker set search_path='' as $$
begin
  insert into public.garra_chatgpt_settings values('host',p_candidate) on conflict do nothing;
  return (select value from public.garra_chatgpt_settings where key='host');
end $$;
create function public.garra_connect_account(p_identity text,p_account text,p_user text,p_payload text,p_name text,p_email text,p_verified boolean,p_password text,p_role text,p_guest text,p_expected text,p_browser text)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare a public.garra_chatgpt_accounts; uid text; was_created boolean:=false;
begin
  perform pg_advisory_xact_lock(hashtextextended(p_identity,0));
  select * into a from public.garra_chatgpt_accounts where identity=p_identity;
  if p_expected is not null and (a.id is null or a.id<>p_expected) then raise exception 'ChatGPT account mismatch'; end if;
  if a.id is null then
    select user_id into uid from public.garra_guests where user_id=p_guest for update;
    if uid is null then
      uid:=p_user;
      insert into public.garra_users values(uid,p_name,p_user||'@chatgpt.invalid',p_password,p_role,to_char(now() at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'));
    else
      delete from public.garra_guests where user_id=uid;
      update public.garra_users set name=p_name,role=p_role where id=uid;
    end if;
    if p_verified and p_email<>'' then
      begin update public.garra_users set email=lower(p_email) where id=uid;
      exception when unique_violation then null; end;
    end if;
    insert into public.garra_chatgpt_accounts(id,user_id,identity,payload) values(p_account,uid,p_identity,p_payload) returning * into a;
    was_created:=true;
  end if;
  insert into public.garra_chatgpt_browsers values(p_browser,a.id) on conflict do nothing;
  return to_jsonb(a)||jsonb_build_object('created',was_created);
end $$;
create function public.garra_conditions_list()
returns jsonb language sql security invoker set search_path='' as $$
  select coalesce(jsonb_agg(value order by name),'[]'::jsonb) from (
    select c.name,jsonb_build_object('id',c.id,'name',c.name,'curated',c.curated,
      'aliases',(select coalesce(jsonb_agg(a.label order by a.label),'[]'::jsonb) from public.garra_condition_aliases a where a.condition_id=c.id),
      'memberCount',(select count(*) from public.garra_memberships m where m.condition_id=c.id),
      'postCount',(select count(*) from public.garra_posts p where p.condition_id=c.id)) as value from public.garra_conditions c
  ) conditions;
$$;
create function public.garra_add_condition(p_id text,p_name text,p_normalized text,p_actor text,p_now bigint)
returns text language plpgsql security invoker set search_path='' as $$
declare found text;
begin
  perform pg_advisory_xact_lock(hashtextextended(p_normalized,1));
  select condition_id into found from public.garra_condition_aliases where normalized_name=p_normalized;
  if found is not null then return found; end if;
  if not exists(select 1 from public.garra_users where id=p_actor) then raise exception 'Unknown user'; end if;
  if not public.garra_limit('condition-create:'||p_actor,20,3600000,p_now) then raise exception 'Condition rate limit exceeded'; end if;
  insert into public.garra_conditions values(p_id,p_name,p_normalized,false,to_char(now() at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'));
  insert into public.garra_condition_aliases values(p_normalized,p_name,p_id);
  return p_id;
end $$;
create function public.garra_add_post(p_id text,p_user text,p_condition text,p_content text,p_created text,p_now bigint)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare member public.garra_memberships;
begin
  select * into member from public.garra_memberships where user_id=p_user and condition_id=p_condition for update;
  if member.user_id is null then raise exception 'Join this community before posting'; end if;
  if length(trim(p_content))=0 or length(p_content)>4000 then raise exception 'Invalid post'; end if;
  if not public.garra_limit('community-post:'||p_user,30,3600000,p_now) then raise exception 'Post rate limit exceeded'; end if;
  insert into public.garra_posts values(p_id,p_condition,p_user,member.alias,p_content,p_created);
  return jsonb_build_object('id',p_id,'conditionId',p_condition,'author',member.alias,'content',p_content,'createdAt',p_created,'own',true);
end $$;

-- App-server access only. Browser requests must go through authenticated routes
-- which enforce record ownership/sharing before decrypting or returning data.
do $$ declare item record; begin
  for item in select tablename from pg_tables where schemaname='public' and tablename like 'garra\_%' escape '\' loop
    execute format('alter table public.%I enable row level security',item.tablename);
    execute format('revoke all on public.%I from public,anon,authenticated',item.tablename);
    execute format('grant select,insert,update,delete on public.%I to service_role',item.tablename);
  end loop;
  for item in select p.oid::regprocedure as signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and p.proname like 'garra\_%' escape '\' loop
    execute format('revoke all on function %s from public,anon,authenticated',item.signature);
    execute format('grant execute on function %s to service_role',item.signature);
  end loop;
end $$;

-- Curated reference communities; no user or private workspace data.
insert into public.garra_conditions values('cmt','Charcot-Marie-Tooth disease','charcot marie tooth disease',true,'2026-10-04T04:38:21.282Z');
insert into public.garra_condition_aliases values('charcot marie tooth disease','Charcot-Marie-Tooth disease','cmt') on conflict do nothing;
insert into public.garra_condition_aliases values('charcot marie tooth','Charcot-Marie-Tooth','cmt') on conflict do nothing;
insert into public.garra_condition_aliases values('cmt','cmt','cmt') on conflict do nothing;
insert into public.garra_conditions values('eds','Ehlers-Danlos syndromes','ehlers danlos syndromes',true,'2026-10-04T04:38:21.282Z');
insert into public.garra_condition_aliases values('ehlers danlos syndromes','Ehlers-Danlos syndromes','eds') on conflict do nothing;
insert into public.garra_condition_aliases values('ehlers danlos','Ehlers-Danlos','eds') on conflict do nothing;
insert into public.garra_condition_aliases values('eds','eds','eds') on conflict do nothing;
insert into public.garra_condition_aliases values('ehlers danlos syndrome','Ehlers-Danlos syndrome','eds') on conflict do nothing;
insert into public.garra_conditions values('fabry','Fabry disease','fabry disease',true,'2026-10-04T04:38:21.282Z');
insert into public.garra_condition_aliases values('fabry disease','Fabry disease','fabry') on conflict do nothing;
insert into public.garra_condition_aliases values('fabry','fabry','fabry') on conflict do nothing;
insert into public.garra_conditions values('marfan','Marfan syndrome','marfan syndrome',true,'2026-10-04T04:38:21.282Z');
insert into public.garra_condition_aliases values('marfan syndrome','Marfan syndrome','marfan') on conflict do nothing;
insert into public.garra_condition_aliases values('marfan','marfan','marfan') on conflict do nothing;
insert into public.garra_conditions values('huntington','Huntington disease','huntington disease',true,'2026-10-04T04:38:21.282Z');
insert into public.garra_condition_aliases values('huntington disease','Huntington disease','huntington') on conflict do nothing;
insert into public.garra_condition_aliases values('huntington','huntington','huntington') on conflict do nothing;
insert into public.garra_condition_aliases values('huntingtons disease','Huntingtons disease','huntington') on conflict do nothing;
insert into public.garra_conditions values('sma','Spinal muscular atrophy','spinal muscular atrophy',true,'2026-10-04T04:38:21.282Z');
insert into public.garra_condition_aliases values('spinal muscular atrophy','Spinal muscular atrophy','sma') on conflict do nothing;
insert into public.garra_condition_aliases values('sma','sma','sma') on conflict do nothing;
insert into public.garra_conditions values('pompe','Pompe disease','pompe disease',true,'2026-10-04T04:38:21.282Z');
insert into public.garra_condition_aliases values('pompe disease','Pompe disease','pompe') on conflict do nothing;
insert into public.garra_condition_aliases values('pompe','pompe','pompe') on conflict do nothing;
insert into public.garra_conditions values('rett','Rett syndrome','rett syndrome',true,'2026-10-04T04:38:21.282Z');
insert into public.garra_condition_aliases values('rett syndrome','Rett syndrome','rett') on conflict do nothing;
insert into public.garra_condition_aliases values('rett','rett','rett') on conflict do nothing;
