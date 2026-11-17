-- An image can be used by many styles and versions (reuse across a brand).
-- images holds the paid-for picture once; image_uses says where it is shown.
create table image_uses (
    brand_id    uuid not null references brands(id),
    image_id    uuid not null references images(id) on delete cascade,
    style_id    uuid not null references styles(id) on delete cascade,
    version_id  uuid not null references techpack_versions(id) on delete cascade,
    created_at  timestamptz not null default now(),
    primary key (image_id, version_id)
);
create index image_uses_style on image_uses (style_id);

alter table image_uses enable row level security;
alter table image_uses force row level security;
create policy brand_only on image_uses
    using (brand_id = nullif(current_setting('app.brand_id', true), '')::uuid)
    with check (brand_id = nullif(current_setting('app.brand_id', true), '')::uuid);

insert into image_uses (brand_id, image_id, style_id, version_id)
select brand_id, id, style_id, version_id from images
on conflict do nothing;
