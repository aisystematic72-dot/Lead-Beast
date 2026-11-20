"""Initial schema: all tables, evidence_current view, append-only grants, audit triggers.

Revision ID: 0001
Revises:
Create Date: 2026-11-10
"""

from alembic import op

from leadbeast.db.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EVIDENCE_CURRENT = """
create or replace view evidence_current as
select distinct on (e.business_id, e.signal) e.*
from evidence_event e
where not exists (select 1 from evidence_event s where s.supersedes_id = e.id)
order by e.business_id, e.signal,
  (e.state = 'manually_verified') desc, e.observed_at desc, e.id desc;
"""

LATEST_SNAPSHOT = """
create or replace view latest_snapshot as
select distinct on (business_id, campaign_id) *
from score_snapshot
order by business_id, campaign_id, created_at desc, id desc;
"""

# Section 12 of the market screen: aggregates Metabase can read directly.
MARKET_VIEWS = """
create or replace view mv_class_mix as
select campaign_id, class, count(*) as n from latest_snapshot group by 1, 2;

create or replace view mv_funnel as
select c.id as campaign_id,
  (select count(*) from campaign_business cb where cb.campaign_id = c.id) as collected,
  (select count(*) from latest_snapshot s where s.campaign_id = c.id) as scored,
  (select count(*) from latest_snapshot s where s.campaign_id = c.id and s.class in ('A','B')) as qualified,
  (select count(*) from outreach_draft d where d.campaign_id = c.id) as drafted,
  (select count(*) from outreach_draft d where d.campaign_id = c.id and d.status = 'sent') as sent,
  (select count(*) from outcome o where o.campaign_id = c.id and o.result in ('replied','meeting','won')) as replied,
  (select count(*) from outcome o where o.campaign_id = c.id and o.result = 'won') as won
from campaign c;

create or replace view mv_gap_mix as
select s.campaign_id, g.gap, count(*) as n
from latest_snapshot s, jsonb_array_elements_text(s.gap_types) as g(gap)
group by 1, 2;
"""

AUDIT_TRIGGER = """
create or replace function lb_audit() returns trigger as $$
begin
  insert into audit_log(id, actor, table_name, row_id, action, at)
  values (gen_random_uuid(), coalesce(current_setting('lb.actor', true), current_user),
          tg_table_name, coalesce(new.id, old.id)::text, lower(tg_op), now());
  return coalesce(new, old);
end $$ language plpgsql;
"""


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    op.execute(EVIDENCE_CURRENT)
    op.execute(LATEST_SNAPSHOT)
    op.execute(MARKET_VIEWS)
    op.execute(AUDIT_TRIGGER)
    for t in ("business", "panic_watch", "approval", "identity_link", "suppression"):
        op.execute(
            f"create trigger trg_audit_{t} after insert or update or delete on {t} "
            f"for each row execute function lb_audit();"
        )
    # Least privilege: the app role can never rewrite history (spec 15.2).
    op.execute(
        """
        do $$ begin
          if exists (select 1 from pg_roles where rolname = 'lb_app') then
            revoke update, delete on evidence_event, cost_ledger, audit_log from lb_app;
          end if;
        end $$;
        """
    )


def downgrade() -> None:
    for v in ("mv_gap_mix", "mv_funnel", "mv_class_mix", "latest_snapshot", "evidence_current"):
        op.execute(f"drop view if exists {v} cascade")
    Base.metadata.drop_all(bind=op.get_bind())
    op.execute("drop function if exists lb_audit cascade")
