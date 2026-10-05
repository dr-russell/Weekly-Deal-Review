import hashlib
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

from rapidfuzz.fuzz import ratio

from .config import FIELDS, TAXONOMY


def normalized_name(value):
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def clean_url(value):
    if not value:
        return ""
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    query = parse_qs(parsed.query)
    for key in ["url", "u", "target", "redirect", "redirect_url"]:
        candidate = query.get(key, [""])[0]
        if candidate.startswith(("https://", "http://")) and candidate != value:
            return clean_url(candidate)
    query = {k: v for k, v in query.items() if not k.lower().startswith("utm_") and k.lower() not in {"mc_cid", "mc_eid", "emci", "emdi", "ceid"}}
    return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path, "", urlencode(query, doseq=True), ""))


def domain(value):
    return (urlparse(clean_url(value)).hostname or "").removeprefix("www.")


def completed_week(today=None):
    today = today or datetime.now(ZoneInfo("America/Los_Angeles")).date()
    end = today - timedelta(days=((today.weekday() - 1) % 7 or 7))
    return end - timedelta(days=6), end


def week_for_day(day):
    value = date.fromisoformat(day)
    end = value + timedelta(days=(1 - value.weekday()) % 7)
    return end - timedelta(days=6), end


FINANCING_EXCLUSIONS = re.compile(r"(?i)(valuation|debt|up to|grant|target|anticipated|expected|proposed|commitment|initial.close|cumulative|combined|total\s+across|prior\s+round|amount excluded from capital total|across\b.*\b(?:rounds?|seed|series))")
CURRENCY_MARKERS = {"US$": "USD", "USD": "USD", "$": "USD", "EUR": "EUR", "\u20ac": "EUR", "GBP": "GBP", "\u00a3": "GBP", "C$": "CAD", "CA$": "CAD", "A$": "AUD", "AU$": "AUD", "NZ$": "NZD", "HK$": "HKD", "SG$": "SGD"}
CURRENCY_MARKERS.update({code: code for code in ["CAD", "AUD", "NZD", "HKD", "SGD", "CHF", "JPY", "CNY", "INR", "SEK", "NOK", "BRL", "MXN", "KRW", "ZAR", "TRY", "PLN", "THB", "MYR", "IDR", "PEN", "TWD"]})
MONEY_PREFIX = re.compile(r"^(?:(?:more than|at least|over)\s+)?(" + "|".join(re.escape(marker) for marker in sorted(CURRENCY_MARKERS, key=len, reverse=True)) + r")\s*\$?\s*([\d,]+(?:\.\d+)?)\s*(billion|million|thousand|bn|m|b|k)?\b", re.I)
MONEY_OCCURRENCE = re.compile(r"(?<![A-Za-z0-9])" + MONEY_PREFIX.pattern[1:], re.I)


def money_prefix(text):
    match = MONEY_PREFIX.match(text.strip())
    if not match:
        return None
    currency = CURRENCY_MARKERS[match[1].upper()]
    unit = (match[3] or "").lower()
    multiplier = {"billion": Decimal(1000), "bn": Decimal(1000), "b": Decimal(1000), "million": Decimal(1), "m": Decimal(1), "thousand": Decimal(".001"), "k": Decimal(".001"), "": Decimal(".000001")}[unit]
    number = Decimal(match[2].replace(",", "")) * multiplier
    return currency, number, text.strip()[match.end():]


def amount_usd_m(text, usd_equivalent=None):
    value = text.strip()
    if FINANCING_EXCLUSIONS.search(value):
        return None, "Mixed grants/debt, valuation, cumulative, or contingent amount needs a documented breakdown"
    parsed = money_prefix(value)
    if not parsed:
        return None, "Round amount undisclosed or not safely parseable"
    currency, number, remainder = parsed
    if currency == "USD":
        return float(number), None
    equivalent = re.fullmatch(r"\s*\(\s*(?:(?:approximately|approx\.?|about|~)\s*)?(.+?)\s*\)\s*", remainder, re.I)
    if equivalent:
        quoted = money_prefix(equivalent[1])
        if quoted and quoted[0] == "USD" and not quoted[2].strip():
            return float(quoted[1]), None
    if usd_equivalent is not None and not remainder.strip():
        try:
            converted = Decimal(str(usd_equivalent))
            if converted.is_finite() and converted > 0:
                return float(converted), None
        except InvalidOperation:
            pass
    return None, "Non-USD amount needs a documented USD equivalent or dated exchange rate"


def capital_usd_m(text, day, ratebook=None):
    numeric, reason = amount_usd_m(text)
    parsed = money_prefix(text)
    if numeric is not None:
        method = "source_usd_equivalent" if parsed[0] != "USD" else "source_usd"
        return numeric, None, {"method": method, "included_usd_m": numeric}
    descriptive_suffix = parsed and re.fullmatch(r"\s*(?:(?:primary|equity|venture)\s+)?(?:investment|funding|financing)(?:\s+round)?\s*", parsed[2], re.I)
    if not parsed or FINANCING_EXCLUSIONS.search(text) or parsed[2].strip() and not descriptive_suffix:
        return None, reason, None
    currency, number, _ = parsed
    book = ratebook or {}
    # CAD-based cross rates use the same dated observation for both legs.
    candidates = sorted((r for r in book.get("observations", []) if r.get("d", "") <= day), key=lambda r: r["d"], reverse=True)
    for observation in candidates:
        if (date.fromisoformat(day) - date.fromisoformat(observation["d"])).days > 7:
            break
        try:
            usd_cad = Decimal(observation["FXUSDCAD"]["v"])
            local_cad = Decimal(1) if currency == "CAD" else Decimal(observation["FX" + currency + "CAD"]["v"])
            if not all(value.is_finite() and value > 0 for value in [usd_cad, local_cad]):
                continue
        except (KeyError, InvalidOperation, TypeError):
            continue
        rate = local_cad / usd_cad
        numeric = float(number * rate)
        return numeric, None, {"method": "dated_fx", "currency": currency, "local_amount_m": float(number), "rate_date": observation["d"], "deal_date": day, "usd_per_unit": str(rate), "included_usd_m": numeric, "source": book.get("source_url"), "via": "CAD cross-rate; same observation date"}
    return None, "No confirmed exchange rate on or within seven days before the deal date", None


def normalized_usd_amount(text, day, ratebook=None):
    original = text.strip()
    if original.startswith("USD amount unavailable"):
        return original, None, {"original_amount":original,"display_amount":original,"deal_date":day,"included_in_capital":False,"exclusion_reason":"Current-round USD amount not confirmed; see original source audit","conversion":None}
    numeric, reason, basis = capital_usd_m(original, day, ratebook)
    included = numeric is not None
    parsed = money_prefix(original)
    upper_bound = re.match(r"(?i)^up to\s+", original)
    parse_text = original[upper_bound.end():] if upper_bound else original
    if upper_bound:
        parsed = money_prefix(parse_text)
    if parsed is None and FINANCING_EXCLUSIONS.search(original):
        mentions = list(MONEY_OCCURRENCE.finditer(original))
        if len(mentions) == 1:
            mention = mentions[0]
            reported, _, reported_basis = capital_usd_m(mention[0], day, ratebook)
            if reported is not None:
                dollars = (Decimal(str(reported)) * Decimal(1000000)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
                suffix = re.sub(r"^\s*USD\b", "", original[mention.end():], flags=re.I)
                display = original[:mention.start()] + f"${dollars:,.0f} USD" + suffix
                return display, None, {"original_amount":original,"display_amount":display,"deal_date":day,"included_in_capital":False,"exclusion_reason":reason,"conversion":reported_basis,"usd_amount":float(dollars)}
    qualifier = ""
    if parsed:
        qualifier = re.sub(r"^\s*USD\b", "", parsed[2], flags=re.I).lstrip(" ;").strip()
        equivalent = re.match(r"^\((?:approximately\s+|approx\.?\s+|about\s+|~\s*)?(.+?)\)\s*", qualifier, re.I)
        if equivalent:
            quoted = money_prefix(equivalent[1])
            if quoted and quoted[0] == "USD" and not quoted[2].strip():
                qualifier = qualifier[equivalent.end():].strip()
                if numeric is None:
                    numeric = float(quoted[1])
                    basis = {"method": "source_usd_equivalent", "included_usd_m": numeric}
        if numeric is None:
            match = MONEY_PREFIX.match(parse_text)
            numeric, _, basis = capital_usd_m(parse_text[:match.end()], day, ratebook)
    audit = {"original_amount": original, "deal_date": day, "included_in_capital": included, "exclusion_reason": reason, "conversion": basis}
    if numeric is None:
        display = "Undisclosed" if not original or original.lower() == "undisclosed" else "USD amount unavailable"
        if qualifier:
            display += "; " + qualifier
        audit['display_amount'] = display
        return display, None, audit
    dollars = (Decimal(str(numeric)) * Decimal(1000000)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    display = f"${dollars:,.0f} USD"
    lower = re.match(r"(?i)^(more than|at least|over)\b", original)
    if lower:
        display = lower[1].capitalize() + " " + display
    elif upper_bound:
        display = "Up to " + display
    if qualifier:
        display += "; " + qualifier
    audit.update(display_amount=display, usd_amount=float(dollars), unrounded_usd_m=numeric)
    if not included and not FINANCING_EXCLUSIONS.search(original):
        # Uninterpreted source prose must not become countable after formatting.
        display += "; amount excluded from capital total"
        audit["display_amount"] = display
    return display, float(dollars / Decimal(1000000)) if included else None, audit


def sheet_amount(text):
    match = MONEY_OCCURRENCE.search(text)
    if match:
        parsed = money_prefix(match[0])
        if parsed and parsed[0] == 'USD':
            dollars = (parsed[1] * Decimal(1000000)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            return f'${dollars:,.0f}'
    return 'Undisclosed' if 'undisclosed' in text.lower() else 'Unavailable'


def announced_usd_amount(text, day, ratebook=None):
    display, _, audit = normalized_usd_amount(text, day, ratebook)
    excluded = re.search(r"(?i)(valuation|cumulative|(?:total\s+)?funding to date|total funding|lifetime|total\s+across|prior\s+round|across\b.*\b(?:rounds?|seed|series)|including (?:debt|grants)|equity.only amount not|financing.only amount not)", text)
    pure_non_vc = re.search(r"(?i)\b(debt|grant|project financing)\b", text)
    match = MONEY_OCCURRENCE.search(display)
    numeric = None
    if match and not excluded and not pure_non_vc:
        parsed = money_prefix(match[0])
        if parsed and parsed[0] == 'USD':
            numeric = float(parsed[1])
    audit.update(included_in_capital=numeric is not None,
                 exclusion_reason=None if numeric is not None else 'Current-round announced VC amount unavailable, cumulative, valuation, or non-VC financing',
                 metric='Capital Announced')
    return display, numeric, audit


def normalize_round(value, stage):
    if re.search(r"(?i)\bpre[ -]?series\b", value):
        return stage if stage in {'Early', 'Growth'} else ''
    series = re.search(r"(?i)\bseries\s+([a-z])(?:-?\d+)?\b", value)
    if series:
        return 'Series ' + series[1].upper()
    if re.search(r"(?i)\bpre[ -]?seed\b", value):
        return 'Pre-seed'
    if re.search(r"(?i)\bseed\b", value):
        return 'Seed'
    return stage if stage in {'Early', 'Growth'} else ''


def financing_caveats(deal):
    amount = deal["fields"]["amount"]
    parsed = money_prefix(amount)
    if parsed is None or FINANCING_EXCLUSIONS.search(amount):
        return []
    currency, numeric, _ = parsed
    caveats = []
    for evidence in deal.get("evidence", []):
        if evidence.get("kind") != "web" or "amount" not in evidence.get("fields", []):
            continue
        quote = " ".join([evidence.get("quote", ""), evidence.get("summary", "")]).strip()
        money = list(MONEY_OCCURRENCE.finditer(quote))
        matching = [match for match in money if (value := money_prefix(match[0])) is not None and value[0] == currency and abs(value[1] - numeric) <= Decimal(".0001")]
        if not matching:
            continue
        # Associate a cumulative qualifier with this amount, not a different
        # lifetime total later in the same financing announcement.
        cumulative = any(
            re.search(r"(?i)(?:bringing|brings|brought).{0,35}\btotal.{0,30}\bto\s*$", quote[max(0, match.start() - 100):match.start()])
            or re.match(r"(?i)\s*(?:in\s+)?(?:total\s+)?funding\s+(?:to date\b|(?:across|over)\b.{0,60}\b(?:rounds?|seed|series)\b)", quote[match.end():match.end() + 120])
            or re.match(r"(?i)\s*(?:total\s+)?(?:across|over)\b.{0,60}\b(?:rounds?|seed|series)\b", quote[match.end():match.end() + 100])
            for match in matching
        )
        kind = "cumulative" if cumulative else "anticipated" if re.search(r"(?i)\b(anticipated|expected|proposed|targeted|seeking)\b", quote) else "conditional" if re.search(r"(?i)\b(up to|commitments?|contingent|subject to|milestones?)\b", quote) else "mixed_grants" if re.search(r"(?i)\bgrants?\b", quote) else "mixed_debt" if re.search(r"(?i)\bdebt\b", quote) else None
        if kind:
            caveats.append({"evidence_id": evidence["id"], "kind": kind, "quote": quote})
    return caveats


def preserve_financing_caveats(deal):
    caveats = financing_caveats(deal)
    if not caveats:
        return None
    caveat, old = caveats[0], deal["fields"]["amount"]
    suffix = {"cumulative": "cumulative total across rounds; current-round amount not separately disclosed", "anticipated": "anticipated round; closed amount not separately disclosed", "conditional": "conditional commitments; unconditional amount not separately disclosed", "mixed_grants": "including grants; financing-only amount not separately disclosed", "mixed_debt": "including debt; equity-only amount not separately disclosed"}[caveat["kind"]]
    deal["fields"]["amount"] = f"{old} {suffix}"
    return {"field": "amount", "old": old, "value": deal["fields"]["amount"], "reason": "Preserved source financing context; Capital Announced is not verified cash closed", "evidence_id": caveat["evidence_id"], "kind": caveat["kind"]}


def normalize_fields(fields):
    result = {k: str(fields.get(k) or "").strip() for k in FIELDS}
    for key in ["website", "announcement_link"]:
        result[key] = clean_url(result[key])
    result["round"] = normalize_round(result["round"], result["growth_stage"])
    return result


def same_financing(a, b):
    af, bf = a["fields"], b["fields"]
    same_name = normalized_name(a["company"]) == normalized_name(b["company"])
    same_domain = domain(af["website"]) and domain(af["website"]) == domain(bf["website"])
    if not (same_name or same_domain or ratio(normalized_name(a["company"]), normalized_name(b["company"])) >= 94):
        return False
    ad, bd = date.fromisoformat(af["deal_date"]), date.fromisoformat(bf["deal_date"])
    if abs((ad - bd).days) > 7:
        return False
    def round_key(value):
        series = re.search(r"(?i)\bseries\s+([A-Z])\b", value)
        if series:
            return "series " + series[1].lower()
        if value.lower().startswith("strategic"):
            return "strategic"
        return value.casefold()
    if af["round"] and bf["round"] and round_key(af["round"]) != round_key(bf["round"]):
        return False
    am, _ = amount_usd_m(af["amount"])
    bm, _ = amount_usd_m(bf["amount"])
    if am is None or bm is None:
        ap, bp = money_prefix(af["amount"]), money_prefix(bf["amount"])
        if ap and bp and ap[0] == bp[0] and not FINANCING_EXCLUSIONS.search(af["amount"] + " " + bf["amount"]):
            am, bm = float(ap[1]), float(bp[1])
    if am is not None and bm is not None and abs(am - bm) > max(.1, max(am, bm) * .03):
        return False
    same_announcement = af["announcement_link"] and af["announcement_link"] == bf["announcement_link"]
    return bool(same_name or same_domain or same_announcement)


def consolidate_prepared(deals):
    merged, changes = [], []
    for item in sorted(deals, key=lambda d: (d["fields"]["deal_date"], d["id"])):
        hit = next((d for d in merged if same_financing(d, item)), None)
        if hit is None:
            merged.append(item)
            continue
        hit["block_ids"] = list(dict.fromkeys(hit["block_ids"] + item["block_ids"]))
        hit["sources"] = list(dict.fromkeys(hit["sources"] + item["sources"]))
        hit["evidence"] = list({e["id"]: e for e in hit["evidence"] + item["evidence"]}.values())
        for key, value in item["fields"].items():
            if value and not hit["fields"].get(key):
                hit["fields"][key] = value
                hit["missing"].pop(key, None)
        investors = []
        seen = set()
        for value in [hit["fields"]["investors"], item["fields"]["investors"]]:
            for investor in re.split(r";|,", value):
                investor = investor.strip()
                key = normalized_name(investor)
                key = "ycombinator" if key == "yc" else key
                if key and key not in seen:
                    investors.append(investor)
                    seen.add(key)
        hit["fields"]["investors"] = "; ".join(investors)
        changes.append({"company": item["company"], "retained": hit["id"], "superseded": item["id"], "reason": "Same company/domain and compatible round, amount, and announcement window after enrichment"})
    return merged, changes


def merge_deals(mentions):
    merged = []
    for item in sorted(mentions, key=lambda x: (x["fields"]["deal_date"], x["company"])):
        hit = next((d for d in merged if same_financing(d, item)), None)
        if hit is None:
            item["block_ids"] = list(dict.fromkeys(item["block_ids"]))
            item["sources"] = list(dict.fromkeys(item["sources"]))
            merged.append(item)
            continue
        hit["block_ids"] = list(dict.fromkeys(hit["block_ids"] + item["block_ids"]))
        hit["sources"] = list(dict.fromkeys(hit["sources"] + item["sources"]))
        hit["evidence"].extend(item["evidence"])
        for key, value in item["fields"].items():
            if value and (not hit["fields"].get(key) or key in {"description", "investors"} and len(value) > len(hit["fields"][key])):
                hit["fields"][key] = value
        hit["fields"]["deal_date"] = min(hit["fields"]["deal_date"], item["fields"]["deal_date"])
    for item in merged:
        fingerprint = "|".join([normalized_name(item["company"]), item["fields"]["deal_date"], item["fields"]["round"], item["fields"]["amount"]])
        item["id"] = hashlib.sha256(fingerprint.encode()).hexdigest()[:20]
    return merged


def portfolio_match(deal, portfolio):
    key, host = normalized_name(deal["company"]), domain(deal["fields"]["website"])
    possible = []
    for item in portfolio:
        name = str(item.get("Name") or "").strip()
        pkey, phost = normalized_name(name), domain(str(item.get("Company URL") or ""))
        if key == pkey or host and host == phost:
            return {"status": "known", "company": name, "method": "domain" if host and host == phost else "normalized_name", "score": 1.0, "url": item.get("Company URL")}
        score = ratio(key, pkey) / 100
        if score >= .86:
            possible.append({"company": name, "score": score, "url": item.get("Company URL"), "description": item.get("Brief Description")})
    return {"status": "ambiguous" if possible else "not_matched", "candidates": sorted(possible, key=lambda x: -x["score"])[:3]}


def needs_verification(decision, match):
    return decision["fit"] != "Yes" or decision["confidence"] < .9 or match["status"] == "ambiguous" or bool(decision.get("field_corrections"))


def validate_selected(deals, decisions, start=None, end=None):
    issues = []
    for deal in deals:
        decision = decisions[deal["id"]]
        if decision["fit"] not in {"Yes", "No"}:
            issues.append(f"{deal['company']}: final fit must be Yes or No; unresolved routing decision")
            continue
        if decision["fit"] == "No" or decision.get("known_investment"):
            continue
        fields = deal["fields"]
        if start and not start <= fields["deal_date"] <= end:
            continue
        for key in FIELDS:
            if not fields[key] and key not in deal["missing"]:
                issues.append(f"{deal['company']}: {key} has no value or documented missing reason")
        for key in ["description", "sector", "deal_date"]:
            if not fields[key]:
                issues.append(f"{deal['company']}: required {key} missing")
        if fields["growth_stage"] not in {"Early", "Growth"}:
            issues.append(f"{deal['company']}: invalid stage")
        if fields["sector"] not in TAXONOMY:
            issues.append(f"{deal['company']}: invalid sector")
        if not decision["evidence_ids"]:
            issues.append(f"{deal['company']}: decision has no evidence")
        if financing_caveats(deal):
            issues.append(f"{deal['company']}: numeric amount drops a source financing qualifier")
    selected = [d for d in deals if decisions[d["id"]]["fit"] == "Yes" and not decisions[d["id"]].get("known_investment") and (not start or start <= d["fields"]["deal_date"] <= end)]
    for i, deal in enumerate(selected):
        if any(same_financing(deal, previous) for previous in selected[:i]):
            issues.append(f"{deal['company']}: duplicate selected financing remains")
    return issues


def make_package(deals, decisions, start, end, ratebook=None):
    if any(decision["fit"] not in {"Yes", "No"} for decision in decisions.values()):
        raise ValueError("Final package cannot contain unresolved Maybe decisions")
    selected = [d for d in deals if decisions[d["id"]]["fit"] == "Yes" and not decisions[d["id"]].get("known_investment") and start <= d["fields"]["deal_date"] <= end]
    rows, amount, excluded, lower_bounds, conversions, normalized = [], Decimal(0), [], [], [], []
    for deal in selected:
        f, decision = deal["fields"], decisions[deal["id"]]
        display, numeric, audit = announced_usd_amount(f["amount"], f["deal_date"], ratebook)
        audit.update(original_round=f['round'], normalized_round=normalize_round(f['round'], f['growth_stage']))
        normalized.append({"deal_id": deal["id"], "company": deal["company"], **audit})
        row = [True, f["growth_stage"], f["deal_date"], deal["company"], f["website"], audit['normalized_round'], display, f["investors"] or "Undisclosed", f["sector"], f["description"], f["geography"] or "Not disclosed", "; ".join(deal["sources"]), f["announcement_link"], decision["fit"]]
        rows.append(row)
        reason, basis = audit["exclusion_reason"], audit["conversion"]
        if numeric is None:
            excluded.append({"company": deal["company"], "amount": f["amount"], "reason": reason})
        else:
            amount += Decimal(str(numeric))
            conversions.append({"company": deal["company"], "amount": f["amount"], "display_amount": display, "amount_source": f["announcement_link"], "source_items": deal["block_ids"], **basis, "included_usd_m": numeric})
            if re.match(r"(?i)^(more than|at least|over)\b", f["amount"]):
                lower_bounds.append({"company": deal["company"], "amount": f["amount"], "included_usd_m": numeric})
    rows.sort(key=lambda x: (x[2], x[3]), reverse=True)
    return {"rows": rows, "start": start, "end": end, "fx_rates": ratebook or {}, "stats": {"count": len(rows), "confirmed": len(rows), "borderline": 0, "capital_usd_m": float(amount), "excluded_amounts": excluded, "lower_bound_amounts": lower_bounds, "capital_basis": conversions, "amount_normalization": normalized}, "policy": "Final binary Yes/No; only Yes published; USD amounts rounded to whole dollars with the same values used for totals; reasonable evidence-backed borderlines may be Yes; known portfolio excluded; unmatched portfolio does not mean never seen in CRM"}
