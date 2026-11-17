-- Seam schema.
-- Every table that holds brand data carries brand_id and is locked down with
-- row level security. The app sets app.brand_id at the start of each
-- transaction, so a missing WHERE clause can never leak another brand's data.

create table brands (
    id            uuid primary key default gen_random_uuid(),
    name          text not null,
    api_key_hash  text not null unique,
    created_at    timestamptz not null default now()
);

create table styles (
    id          uuid primary key default gen_random_uuid(),
    brand_id    uuid not null references brands(id),
    code        text not null,
    name        text not null,
    garment     text not null,
    season      text,
    created_at  timestamptz not null default now(),
    unique (brand_id, code)
);

create table sketches (
    id            uuid primary key default gen_random_uuid(),
    brand_id      uuid not null references brands(id),
    style_id      uuid not null references styles(id) on delete cascade,
    content_type  text not null,
    bytes         bytea not null,
    sha256        text not null,
    hints         jsonb not null default '{}',
    created_at    timestamptz not null default now()
);

create table techpack_versions (
    id           uuid primary key default gen_random_uuid(),
    brand_id     uuid not null references brands(id),
    style_id     uuid not null references styles(id) on delete cascade,
    version      int  not null,
    status       text not null check (status in ('draft', 'approved', 'superseded')),
    source       text not null check (source in ('generated', 'edited')),
    parent_id    uuid references techpack_versions(id),
    body         jsonb not null,
    note         text,
    created_at   timestamptz not null default now(),
    approved_at  timestamptz,
    unique (style_id, version)
);
-- Only one approved tech pack per style at any moment.
create unique index techpack_one_approved on techpack_versions (style_id) where status = 'approved';

create table suppliers (
    id        uuid primary key default gen_random_uuid(),
    brand_id  uuid not null references brands(id),
    code      text not null,
    name      text not null,
    country   text not null,
    unique (brand_id, code)
);

create table supplier_requests (
    id               uuid primary key default gen_random_uuid(),
    brand_id         uuid not null references brands(id),
    style_id         uuid not null references styles(id) on delete cascade,
    version_id       uuid not null references techpack_versions(id),
    supplier_id      uuid not null references suppliers(id),
    idempotency_key  text not null unique,
    status           text not null default 'pending' check (status in ('pending', 'quoted', 'failed')),
    attempts         int  not null default 0,
    last_error       text,
    quote            jsonb,
    created_at       timestamptz not null default now(),
    quoted_at        timestamptz,
    unique (version_id, supplier_id)
);

-- A draft must never reach a factory. Checked in the database, not just the API.
create function supplier_request_needs_approved() returns trigger language plpgsql as $$
begin
    if (select status from techpack_versions where id = new.version_id) <> 'approved' then
        raise exception 'tech pack version % is not approved', new.version_id
            using errcode = 'check_violation';
    end if;
    return new;
end $$;
create trigger supplier_request_needs_approved
    before insert on supplier_requests
    for each row execute function supplier_request_needs_approved();

create table image_budgets (
    brand_id     uuid primary key references brands(id),
    limit_cents  int not null check (limit_cents >= 0),
    spent_cents  int not null default 0,
    check (spent_cents <= limit_cents)
);

create table images (
    id            uuid primary key default gen_random_uuid(),
    brand_id      uuid not null references brands(id),
    style_id      uuid not null references styles(id) on delete cascade,
    version_id    uuid not null references techpack_versions(id),
    request_hash  text not null,
    shot          text not null,
    status        text not null default 'queued' check (status in ('queued', 'done', 'failed')),
    cost_cents    int not null,
    provenance    jsonb not null,
    svg           text,
    reused        int not null default 0,
    created_at    timestamptz not null default now(),
    finished_at   timestamptz,
    unique (brand_id, request_hash)
);

create table events (
    id        bigserial primary key,
    brand_id  uuid not null references brands(id),
    style_id  uuid not null references styles(id) on delete cascade,
    kind      text not null,
    detail    jsonb not null default '{}',
    at        timestamptz not null default clock_timestamp()
);
create index events_style on events (style_id, id);

-- Row level security on every brand table.
do $$
declare t text;
begin
    foreach t in array array['styles', 'sketches', 'techpack_versions', 'suppliers',
                             'supplier_requests', 'image_budgets', 'images', 'events']
    loop
        execute format('alter table %I enable row level security', t);
        execute format('alter table %I force row level security', t);
        execute format($p$create policy brand_only on %I
                          using (brand_id = nullif(current_setting('app.brand_id', true), '')::uuid)
                          with check (brand_id = nullif(current_setting('app.brand_id', true), '')::uuid)$p$, t);
    end loop;
end $$;

-- The job queue is shared by all brands and has no RLS. Each job carries its
-- brand_id and the worker switches into that brand before touching its data.
create table jobs (
    id            bigserial primary key,
    brand_id      uuid not null references brands(id),
    kind          text not null,
    payload       jsonb not null,
    dedupe_key    text,
    status        text not null default 'queued' check (status in ('queued', 'running', 'done', 'dead')),
    priority      int  not null default 100,
    attempts      int  not null default 0,
    max_attempts  int  not null default 6,
    run_after     timestamptz not null default now(),
    locked_until  timestamptz,
    locked_by     text,
    last_error    text,
    created_at    timestamptz not null default now(),
    finished_at   timestamptz
);
create index jobs_ready on jobs (priority, run_after) where status in ('queued', 'running');
create unique index jobs_dedupe on jobs (kind, dedupe_key)
    where dedupe_key is not null and status in ('queued', 'running');

-- Stands in for the factories' own systems. Lives outside the brand tables on
-- purpose: it is "somebody else's database".
create table sim_supplier_inbox (
    idempotency_key  text primary key,
    supplier_code    text not null,
    hits             int  not null default 1,
    quote            jsonb not null,
    first_seen       timestamptz not null default now()
);
