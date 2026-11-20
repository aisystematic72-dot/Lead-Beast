from leadbeast.identity import normalise as N
from leadbeast.identity.match import Rec, Verdict, blocking_keys, compare, decide


def test_name_normalisation():
    assert N.name("Acme Plumbing & Heating, LLC") == "acme plumbing and heating"
    assert N.name("The Joe's HVAC Svc Inc.") == "joe s hvac service"


def test_phone_and_domain():
    assert N.phone("(212) 555-0187") == "+12125550187"
    assert N.phone("123") is None
    assert N.domain("https://www.AcmePlumbing.com/contact") == "acmeplumbing.com"
    assert N.domain("office@acme.co.uk") == "acme.co.uk"


def test_address_parsing():
    a = N.address("123 Main St, Brooklyn, NY 11201")
    assert a.postcode == "11201"


def test_webmail_and_role():
    assert N.is_webmail("joe@gmail.com") and not N.is_webmail("joe@acme.com")
    assert N.is_role_address("info@acme.com") and not N.is_role_address("maria@acme.com")


def r(**kw):
    base = dict(key="x", norm_name="acme plumbing", phone_e164="+12125550187",
                domain="acmeplumbing.com", postcode="11201", address_key="123 main 11201")
    base.update(kw)
    return Rec(**base)


def test_exact_match_merges():
    ms = compare(r(), r(key="y"))
    assert decide(ms, 0.88, 0.70) is Verdict.MERGE


def test_same_phone_different_name_never_auto_merges():
    ms = compare(r(), r(key="y", norm_name="brooklyn dental care", domain=None))
    assert decide(ms, 0.88, 0.70) is not Verdict.MERGE


def test_different_businesses_separate():
    ms = compare(r(), r(key="y", norm_name="zen cafe", phone_e164="+12125559999",
                       domain="zencafe.com", address_key="9 elm 11201"))
    assert decide(ms, 0.88, 0.70) is Verdict.SEPARATE


def test_trade_conflict_goes_to_review():
    ms = compare(r(trade="plumbing"), r(key="y", trade="dental"))
    assert ms.conflict and decide(ms, 0.88, 0.70) is Verdict.REVIEW


def test_blocking_skips_webmail_domains():
    keys = blocking_keys(r(domain="gmail.com"))
    assert not any(k.startswith("d:") for k in keys)
