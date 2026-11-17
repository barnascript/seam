-- The style list is "newest first, one brand". Measured at 330 ms p50 with
-- ~500 styles and no index; this makes it an index scan.
create index if not exists styles_brand_created on styles (brand_id, created_at desc);
