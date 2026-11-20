"""HTML screens. Handlers only gather data from services and render; no business logic here."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.api.auth import COOKIE, User, current_user, make_session_cookie, verify_password
from leadbeast.audit.report import HtmlPdfRenderer
from leadbeast.calls.brief import FB_TEXT, call_list
from leadbeast.core.config import get_settings
from leadbeast.core.db import get_session
from leadbeast.core.errors import LeadBeastError
from leadbeast.db import models as m
from leadbeast.market.queries import (
    EXPORT_COLUMNS,
    OpportunityFilter,
    approval_queue,
    business_view,
    export_rows,
    market_summary,
    opportunities,
    removed_businesses,
    sources_view,
)
from leadbeast.outreach import service as outreach
from leadbeast.packs import service as packs
from leadbeast.packs.schema import Offer
from leadbeast.social.reachable import reachable, reachable_count
from leadbeast.web.render import TEMPLATES, audit_context

router = APIRouter()


UNREAD_ALERT = ("pending", "held", "sent", "digested")


async def page_user(request: Request, user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session)) -> User:
    """current_user, plus the sidebar badge counts for full-page renders."""
    if s is not None and not request.headers.get("hx-request"):
        request.state.nav = {
            "alerts": int(await s.scalar(select(func.count()).select_from(m.PanicAlert)
                                         .where(m.PanicAlert.status.in_(UNREAD_ALERT))) or 0),
            "approvals": int(await s.scalar(select(func.count()).select_from(m.OutreachDraft)
                                            .where(m.OutreachDraft.status == "draft")) or 0),
            "reachable": await reachable_count(s),
        }
    return user


def page(request: Request, name: str, user: User | None = None, **ctx: Any) -> HTMLResponse:
    nav = getattr(request.state, "nav", {"alerts": 0, "approvals": 0})
    return TEMPLATES.TemplateResponse(request, name, {"user": user, "path": request.url.path,
                                                      "nav": nav, **ctx})


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request) -> HTMLResponse:
    return page(request, "login.html")


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...),
                s: AsyncSession = Depends(get_session)) -> Response:
    u = await s.scalar(select(m.AppUser).where(m.AppUser.username == username))
    if u is None or not verify_password(password, u.password_hash):
        return page(request, "login.html", error="Wrong username or password.")
    r = RedirectResponse("/opportunities", status_code=303)
    r.set_cookie(COOKIE, make_session_cookie(u.username), httponly=True, samesite="lax",
                 secure=get_settings().environment == "production", max_age=60 * 60 * 24 * 7)
    return r


@router.post("/logout")
async def logout() -> Response:
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie(COOKIE)
    return r


@router.get("/")
async def home() -> Response:
    return RedirectResponse("/opportunities")


@router.get("/opportunities", response_class=HTMLResponse)
async def opportunities_page(request: Request, campaign: UUID | None = None,
                             cls: str | None = None, gap: str | None = None,
                             q: str | None = None, s: AsyncSession = Depends(get_session),
                             user: User = Depends(page_user)) -> HTMLResponse:
    rows = await opportunities(s, OpportunityFilter(campaign, cls or None, gap or None, None,
                                                    q or None, 200))
    f = {"campaign": campaign, "cls": cls, "gap": gap, "q": q}
    if request.headers.get("hx-request"):
        return page(request, "_opps_rows.html", user, rows=rows, f=f)
    camps = (await s.scalars(select(m.Campaign).order_by(m.Campaign.created_at.desc()))).all()
    return page(request, "opportunities.html", user, rows=rows, campaigns=camps, f=f,
                summary=await market_summary(s, campaign))


@router.get("/business/{bid}", response_class=HTMLResponse)
async def business_page(request: Request, bid: UUID, s: AsyncSession = Depends(get_session),
                        user: User = Depends(page_user)) -> HTMLResponse:
    v = await business_view(s, bid)
    if v is None:
        raise HTTPException(404)
    tpl = "_business_panel.html" if request.headers.get("hx-request") else "business.html"
    return page(request, tpl, user, v=v)


@router.post("/drafts/{did}/decision", response_class=HTMLResponse)
async def draft_decision(request: Request, did: UUID, decision: str = Form(...),
                         edited_body: str | None = Form(None),
                         s: AsyncSession = Depends(get_session),
                         user: User = Depends(current_user)) -> HTMLResponse:
    if not user.can("approver"):
        raise HTTPException(403)
    try:
        d = await outreach.decide(s, did, user.username, decision, edited_body)
        msg, ok = f"Draft {d.status}.", True
    except LeadBeastError as e:
        msg, ok = str(e), False
    return page(request, "_flash.html", user, msg=msg, ok=ok)


@router.post("/business/{bid}/mystery", response_class=HTMLResponse)
async def mystery(request: Request, bid: UUID, searched: bool = Form(False),
                  called: bool = Form(False), site_opened: bool = Form(False),
                  passed: bool = Form(False), notes: str = Form(""),
                  s: AsyncSession = Depends(get_session),
                  user: User = Depends(current_user)) -> HTMLResponse:
    if not user.can("approver"):
        raise HTTPException(403)
    await outreach.record_mystery_check(s, bid, user.username, searched=searched, called=called,
                                        site_opened=site_opened, passed=passed, notes=notes)
    return page(request, "_flash.html", user, msg="Mystery check recorded.", ok=True)


async def _set_status(s: AsyncSession, bid: UUID, status: str) -> m.Business:
    b = await s.get(m.Business, bid)
    if b is None:
        raise HTTPException(404)
    b.status = status
    return b


@router.post("/business/{bid}/remove")
async def remove_business(request: Request, bid: UUID, s: AsyncSession = Depends(get_session),
                          user: User = Depends(current_user)) -> Response:
    """Hide a business from every screen and export. Its evidence history is kept, so it can be
    restored from Settings."""
    if not user.can("operator"):
        raise HTTPException(403)
    await _set_status(s, bid, "removed")
    if request.headers.get("hx-request"):
        r = Response(status_code=204)
        r.headers["HX-Redirect"] = "/opportunities"
        return r
    return RedirectResponse("/opportunities", status_code=303)


@router.post("/business/{bid}/restore")
async def restore_business(bid: UUID, s: AsyncSession = Depends(get_session),
                           user: User = Depends(current_user)) -> Response:
    if not user.can("operator"):
        raise HTTPException(403)
    await _set_status(s, bid, "active")
    return RedirectResponse("/settings", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, s: AsyncSession = Depends(get_session),
                        user: User = Depends(page_user)) -> HTMLResponse:
    camps = (await s.scalars(select(m.Campaign).order_by(m.Campaign.created_at.desc()))).all()
    visible = len(await export_rows(s))
    return page(request, "settings.html", user, removed=await removed_businesses(s),
                campaigns=camps, visible=visible)


@router.get("/export/businesses.csv")
async def export_businesses(campaign: UUID | None = None, s: AsyncSession = Depends(get_session),
                            user: User = Depends(current_user)) -> Response:
    import csv
    import io

    from leadbeast.core.clock import now

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLUMNS)
    w.writerows(await export_rows(s, campaign))
    name = f"leadbeast-businesses-{now().strftime('%Y-%m-%d')}.csv"
    # BOM so Excel opens accented names (e.g. Caffé) correctly
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/approvals", response_class=HTMLResponse)
async def approvals(request: Request, s: AsyncSession = Depends(get_session),
                    user: User = Depends(page_user)) -> HTMLResponse:
    return page(request, "approvals.html", user, items=await approval_queue(s))


@router.get("/campaigns/new", response_class=HTMLResponse)
async def campaign_new(request: Request, user: User = Depends(page_user),
                       s: AsyncSession = Depends(get_session)) -> HTMLResponse:
    offers = (await s.scalars(select(m.Offer).order_by(m.Offer.created_at.desc()))).all()
    return page(request, "campaign_new.html", user, offers=offers)


@router.post("/campaigns/new", response_class=HTMLResponse)
async def campaign_create(
    request: Request, name: str = Form(...), area: str = Form(...),
    pack: str = Form("visibility_gap"), offer_id: str = Form(""), offer_name: str = Form(""),
    monthly_fee: float = Form(0), value_low: float = Form(0), value_high: float = Form(0),
    budget_cap: float = Form(0), s: AsyncSession = Depends(get_session),
    user: User = Depends(page_user),
) -> Response:
    if not user.can("operator"):
        raise HTTPException(403)
    try:
        if offer_id:
            oid = UUID(offer_id)
        else:
            o = Offer(name=offer_name, monthly_fee=monthly_fee, customer_value_low=value_low,
                      customer_value_high=value_high,
                      service_fit_profile={"A": 90, "B": 80, "C": 65, "D": 55})
            oid = (await packs.create_offer(s, o)).id
        c = await packs.create_campaign(s, name=name, offer_id=oid, pack_name=pack, area=area,
                                        budget_cap=int(budget_cap * 100))
    except (LeadBeastError, ValueError) as e:
        offers = (await s.scalars(select(m.Offer))).all()
        return page(request, "campaign_new.html", user, offers=offers, error=str(e))
    return RedirectResponse(f"/campaigns/{c.id}", status_code=303)


@router.get("/campaigns/{cid}", response_class=HTMLResponse)
async def campaign_page(request: Request, cid: UUID, s: AsyncSession = Depends(get_session),
                        user: User = Depends(page_user)) -> HTMLResponse:
    from leadbeast.intel.payback import payback

    c = await s.get(m.Campaign, cid)
    if c is None:
        raise HTTPException(404)
    offer = await s.get(m.Offer, c.offer_id)
    pk = await s.get(m.Pack, c.pack_id)
    assert offer is not None and pk is not None
    pb = payback(packs.offer_domain(offer))
    return page(request, "campaign.html", user, c=c, offer=offer, pack=pk, payback=pb,
                summary=await market_summary(s, cid))


@router.get("/market", response_class=HTMLResponse)
async def market_page(request: Request, s: AsyncSession = Depends(get_session),
                      user: User = Depends(page_user)) -> HTMLResponse:
    return page(request, "market.html", user, m=await market_summary(s))


@router.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request, s: AsyncSession = Depends(get_session),
                       user: User = Depends(page_user)) -> HTMLResponse:
    return page(request, "sources.html", user, v=await sources_view(s))


@router.get("/alerts", response_class=HTMLResponse)
async def alerts_page(request: Request, s: AsyncSession = Depends(get_session),
                      user: User = Depends(page_user)) -> HTMLResponse:
    alerts = (await s.execute(select(m.PanicAlert, m.Business.canonical_name).join(
        m.Business, m.Business.id == m.PanicAlert.business_id)
        .order_by(m.PanicAlert.created_at.desc()).limit(100))).all()
    watches = (await s.scalars(select(m.PanicWatch).order_by(m.PanicWatch.created_at))).all()
    return page(request, "alerts.html", user, alerts=alerts, watches=watches)


@router.get("/reachable", response_class=HTMLResponse)
async def reachable_page(request: Request, campaign: str | None = None,
                         s: AsyncSession = Depends(get_session),
                         user: User = Depends(page_user)) -> HTMLResponse:
    cid = UUID(campaign) if campaign else None
    data = await reachable(s, cid)
    camps = (await s.scalars(select(m.Campaign).order_by(m.Campaign.created_at.desc()))).all()
    return page(request, "reachable.html", user, r=data, campaigns=camps, campaign=cid)


@router.get("/calls", response_class=HTMLResponse)
async def calls_page(request: Request, s: AsyncSession = Depends(get_session),
                     user: User = Depends(page_user)) -> HTMLResponse:
    rows = await call_list(s, get_settings().operator_name)
    trades = sorted({b.trade for b in rows if b.trade})
    return page(request, "calls.html", user, rows=rows, trades=trades, FB_TEXT=FB_TEXT)


@router.get("/calls/{bid}", response_class=HTMLResponse)
async def call_brief_page(request: Request, bid: UUID, s: AsyncSession = Depends(get_session),
                          user: User = Depends(page_user)) -> HTMLResponse:
    rows = await call_list(s, get_settings().operator_name)  # same order as the list, for prev/next
    i = next((n for n, b in enumerate(rows) if b.id == bid), None)
    if i is None:
        raise HTTPException(404)
    return page(request, "call_brief.html", user, b=rows[i], FB_TEXT=FB_TEXT,
                prev=rows[i - 1] if i > 0 else None, next=rows[i + 1] if i + 1 < len(rows) else None,
                pos=i + 1, total=len(rows))


@router.get("/report/{aid}", response_class=HTMLResponse)
async def report(aid: str, s: AsyncSession = Depends(get_session),
                 user: User = Depends(current_user)) -> Response:
    pdf = aid.endswith(".pdf")
    a = await s.get(m.Audit, UUID(aid.removesuffix(".pdf")))
    if a is None:
        raise HTTPException(404)
    ctx = await audit_context(s, a)
    r = HtmlPdfRenderer()
    if pdf:
        try:
            return Response(r.pdf(ctx), media_type="application/pdf")
        except RuntimeError as e:
            raise HTTPException(501, str(e)) from e
    return HTMLResponse(r.html(ctx))
