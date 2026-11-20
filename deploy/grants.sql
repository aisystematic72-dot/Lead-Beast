-- Run after `leadbeast db migrate` (as the schema owner) to scope the app role.
-- psql -U lb -d lb -f deploy/grants.sql
grant connect on database lb to lb_app;
grant usage on schema public to lb_app;
grant select, insert, update, delete on all tables in schema public to lb_app;
grant usage, select on all sequences in schema public to lb_app;
-- the ledger and cost history can never be rewritten by the app
revoke update, delete on evidence_event, cost_ledger, audit_log from lb_app;
