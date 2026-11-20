-- Extra databases for the optional CRM and reporting services.
create database espocrm owner lb;
create database metabase owner lb;
-- Least-privilege role the app can use instead of the owner (see LB_DATABASE_URL).
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'lb_app') then
    create role lb_app login password 'change-me-app';
  end if;
end $$;
