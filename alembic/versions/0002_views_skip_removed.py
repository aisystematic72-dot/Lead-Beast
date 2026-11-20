"""Market views skip businesses marked status = 'removed'.

Removed businesses keep their evidence history (the ledger is append-only) but must not be
counted on the dashboard (Found, Scored, class mix, gap mix).

Revision ID: 0002
"""

from alembic import op

revision = "0002"
down_revision = "0001"

LIVE = "join business b on b.id = {t}.business_id and b.status <> 'removed'"

VIEWS = f"""
create or replace view mv_class_mix as
select s.campaign_id, s.class, count(*) as n
from latest_snapshot s {LIVE.format(t='s')}
group by 1, 2;

create or replace view mv_funnel as
select c.id as campaign_id,
  (select count(*) from campaign_business cb {LIVE.format(t='cb')} where cb.campaign_id = c.id) as collected,
  (select count(*) from latest_snapshot s {LIVE.format(t='s')} where s.campaign_id = c.id) as scored,
  (select count(*) from latest_snapshot s {LIVE.format(t='s')}
     where s.campaign_id = c.id and s.class in ('A','B')) as qualified,
  (select count(*) from outreach_draft d where d.campaign_id = c.id) as drafted,
  (select count(*) from outreach_draft d where d.campaign_id = c.id and d.status = 'sent') as sent,
  (select count(*) from outcome o where o.campaign_id = c.id and o.result in ('replied','meeting','won')) as replied,
  (select count(*) from outcome o where o.campaign_id = c.id and o.result = 'won') as won
from campaign c;

create or replace view mv_gap_mix as
select s.campaign_id, g.gap, count(*) as n
from latest_snapshot s {LIVE.format(t='s')}, jsonb_array_elements_text(s.gap_types) as g(gap)
group by 1, 2;
"""

ORIGINAL = """
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


def upgrade() -> None:
    op.execute(VIEWS)


def downgrade() -> None:
    op.execute(ORIGINAL)
