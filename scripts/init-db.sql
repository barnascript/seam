-- Superusers skip row level security, so the app gets its own normal role.
create role seam_owner login password 'seam' nosuperuser nobypassrls;
create database seamdb owner seam_owner;
create database seamtest owner seam_owner;
