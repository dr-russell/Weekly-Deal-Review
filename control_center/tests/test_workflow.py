import copy
import json
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from control_center.config import FIELDS, HEADERS, PROJECT
from control_center.google import Publisher, blurb, build_doc, build_sheet, stats_for, u16, validate_blurb_styles
from control_center.logic import amount_usd_m, capital_usd_m, clean_url, completed_week, consolidate_prepared, financing_caveats, make_package, merge_deals, needs_verification, normalize_fields, normalized_usd_amount, portfolio_match, preserve_financing_caveats, sheet_amount, validate_selected
from control_center.models import compact_review_payload, reconcile_ids, restore_review_ids


def deal(name="Example", amount="$10 million", day="2026-09-18", round="Series A", website="https://example.com/"):
    return {"id": name, "company": name, "fields": {"growth_stage": "Early", "deal_date": day, "website": website, "round": round, "amount": amount, "investors": "Alpha, Beta", "sector": "Construction Tech", "description": "Construction estimating software", "geography": "New York, NY", "announcement_link": "https://example.com/news/funding"}, "block_ids": [name], "sources": ["Term Sheet"], "evidence": [], "missing": {}}


def decision(fit="Yes", confidence=.95):
    return {"fit": fit, "confidence": confidence, "evidence_ids": ["Example"], "reason": "Construction software in US", "known_investment": False}


class LogicTests(unittest.TestCase):
    def test_sheet_amount_is_dollar_only_and_history_uses_audited_eligibility(self):
        self.assertEqual(sheet_amount('$11,976,763 USD; combined investment'), '$11,976,763')
        self.assertEqual(sheet_amount('$50,000,000 USD; Series A round size; initial closing announced, cash closed undisclosed'), '$50,000,000')
        self.assertEqual(sheet_amount('Initial close of anticipated $3,900,000,000 USD Series F'), '$3,900,000,000')
        d = deal(day='2026-09-18')
        package = make_package([d], {d['id']:decision()}, '2026-09-16','2026-09-22')
        old = list(package['rows'][0])
        old[2],old[6] = '2026-09-10','$50,000,000'
        package['historical_amount_sources'] = {'example|2026-09-10':'$50 million anticipated round; initial-close amount undisclosed'}
        table, excluded = stats_for(package,[old])
        self.assertEqual(table[-2][2],50)
        self.assertEqual(len(excluded),0)

    def test_usd_display_and_capital_share_the_same_rounded_amount(self):
        book = {"source_url":"official-rates", "observations":[{"d":"2026-09-18","FXUSDCAD":{"v":"1.4"},"FXEURCAD":{"v":"1.6"}}]}
        value, numeric, audit = normalized_usd_amount("EUR 3m", "2026-09-18", book)
        self.assertEqual(value, "$3,428,571 USD")
        self.assertEqual(numeric, 3.428571)
        self.assertEqual(audit["original_amount"], "EUR 3m")
        d = deal(amount="EUR 3m")
        package = make_package([d], {d['id']:decision()}, "2026-09-16", "2026-09-22", book)
        self.assertEqual(package['rows'][0][6], value)
        self.assertEqual(package['stats']['capital_usd_m'], numeric)
        self.assertEqual(amount_usd_m(value)[0], numeric)

    def test_usd_format_preserves_exclusions_lower_bounds_and_source_equivalents(self):
        self.assertEqual(normalized_usd_amount("$8m", "2026-09-18")[0], "$8,000,000 USD")
        self.assertEqual(normalized_usd_amount("GBP 1.1 million ($1.5 million)", "2026-09-18")[0], "$1,500,000 USD")
        lower, numeric, _ = normalized_usd_amount("More than $18m", "2026-09-18")
        self.assertEqual(lower, "More than $18,000,000 USD")
        self.assertEqual(numeric, 18)
        book = {"observations":[{"d":"2026-09-18","FXUSDCAD":{"v":"1.4"},"FXEURCAD":{"v":"1.6"}}]}
        value, numeric, audit = normalized_usd_amount("EUR 14m including debt", "2026-09-18", book)
        self.assertEqual(value, "$16,000,000 USD; including debt")
        self.assertIsNone(numeric)
        self.assertFalse(audit['included_in_capital'])
        self.assertIsNone(amount_usd_m(value)[0])
        self.assertEqual(normalized_usd_amount(value, "2026-09-18", book)[0], value)
        self.assertEqual(normalized_usd_amount("EUR 14m", "2026-09-18")[0], "USD amount unavailable")
        unavailable = "USD amount unavailable; including debt"
        self.assertEqual(normalized_usd_amount(unavailable, "2026-09-18")[0], unavailable)
        self.assertEqual(normalized_usd_amount("Up to US$100 million in equity financing commitments", "2026-09-18")[0], "Up to $100,000,000 USD; in equity financing commitments")
        value, numeric, _ = normalized_usd_amount("EUR 14m primary investment", "2026-09-18", book)
        self.assertEqual(numeric, 16)
        self.assertEqual(amount_usd_m(value)[0], 16)

    def test_anticipated_round_keeps_reported_amount_without_counting_it_as_closed(self):
        source = 'Initial close of anticipated $3.9 billion Series F; initial-close amount undisclosed'
        value, numeric, audit = normalized_usd_amount(source, '2026-09-18')
        self.assertEqual(value, 'Initial close of anticipated $3,900,000,000 USD Series F; initial-close amount undisclosed')
        self.assertIsNone(numeric)
        self.assertFalse(audit['included_in_capital'])
        self.assertIsNone(amount_usd_m(value)[0])
        self.assertEqual(normalized_usd_amount(value, '2026-09-18')[0], value)

    def test_window_uses_latest_completed_tuesday(self):
        self.assertEqual(completed_week(date(2026, 10, 2)), (date(2026, 9, 23), date(2026, 9, 29)))
        self.assertEqual(completed_week(date(2026, 9, 29)), (date(2026, 9, 16), date(2026, 9, 22)))

    def test_currency_and_valuation_are_not_silently_added(self):
        self.assertEqual(amount_usd_m("$1.2 billion")[0], 1200)
        self.assertIsNone(amount_usd_m("$21 million across both rounds")[0])
        self.assertEqual(amount_usd_m("More than $18 million")[0], 18)
        for amount in ["EUR 10 million", "C$10 million", "$20 million valuation", "$10 million debt", "$135 million including grants", "$20 million target", "$3.9 billion anticipated round", "$8 million in conditional commitments", "$8 million total across two rounds", "$21m combined", "Undisclosed"]:
            self.assertIsNone(amount_usd_m(amount)[0])

    def test_source_usd_equivalent_is_used_without_guessing_fx(self):
        self.assertEqual(amount_usd_m("\u00a31.1 million ($1.5 million)")[0], 1.5)
        self.assertEqual(amount_usd_m("EUR 20m (approximately US$23m)")[0], 23)
        self.assertIsNone(amount_usd_m("\u00a31.1 million (C$1.5 million)")[0])
        self.assertIsNone(amount_usd_m("\u00a31.1 million ($1.5 million) and $2 million")[0])
        self.assertIsNone(amount_usd_m("\u00a31.1 million ($1.5 million) including debt", 1.5)[0])
        self.assertIsNone(amount_usd_m("$100 million anticipated round", 100)[0])
        self.assertIsNone(amount_usd_m("EUR 10m", "NaN")[0])

    def test_dated_fx_cross_rate_and_weekend_fallback(self):
        book = {"source_url": "https://www.bankofcanada.ca/valet/observations/group/FX_RATES_DAILY/json", "observations": [{"d": "2026-09-18", "FXUSDCAD": {"v": "1.4"}, "FXEURCAD": {"v": "1.6"}}, {"d": "2026-09-21", "FXUSDCAD": {"v": "1.5"}, "FXEURCAD": {"v": "1.65"}}]}
        value, error, basis = capital_usd_m("EUR 14 million", "2026-09-19", book)
        self.assertEqual(value, 16)
        self.assertIsNone(error)
        self.assertEqual(basis["rate_date"], "2026-09-18")
        self.assertEqual(capital_usd_m("C$14 million", "2026-09-19", book)[0], 10)
        self.assertIsNone(capital_usd_m("EUR 14m", "2026-09-17", book)[0])
        self.assertIsNone(capital_usd_m("EUR 14m", "2026-10-01", book)[0])
        self.assertIsNone(capital_usd_m("AUD 14m", "2026-09-19", book)[0])
        self.assertIsNone(capital_usd_m("EUR 14m total across two rounds", "2026-09-19", book)[0])

    def test_package_and_historical_stats_use_the_same_frozen_fx(self):
        d = deal(amount="EUR 14m")
        book = {"source_url": "https://www.bankofcanada.ca/valet/observations/group/FX_RATES_DAILY/json", "observations": [{"d": "2026-09-10", "FXUSDCAD": {"v": "1.4"}, "FXEURCAD": {"v": "1.6"}}, {"d": "2026-09-18", "FXUSDCAD": {"v": "1.4"}, "FXEURCAD": {"v": "1.6"}}]}
        p = make_package([d], {d["id"]: decision()}, "2026-09-16", "2026-09-22", book)
        self.assertEqual(p["stats"]["capital_usd_m"], 16)
        self.assertEqual(p["stats"]["capital_basis"][0]["method"], "dated_fx")
        old = list(p["rows"][0])
        old[2] = "2026-09-10"
        table, excluded = stats_for(p, [old])
        self.assertEqual(table[-2], ["9/9 - 9/15", 1, 16])
        self.assertFalse(excluded)

    def test_source_caveat_prevents_counting_anticipated_full_round(self):
        d = deal(amount="$3.9 billion", round="Series F")
        d["evidence"] = [{"id": "official", "kind": "web", "fields": ["amount"], "quote": "Announced the initial closing of its anticipated $3.9 billion Series F funding round"}]
        self.assertEqual(financing_caveats(d)[0]["kind"], "anticipated")
        self.assertIn("drops a source financing qualifier", " ".join(validate_selected([d], {d["id"]: decision()})))
        change = preserve_financing_caveats(d)
        self.assertEqual(change["old"], "$3.9 billion")
        self.assertIn("anticipated", d["fields"]["amount"])
        self.assertEqual(make_package([d], {d["id"]: decision()}, "2026-09-16", "2026-09-22")["stats"]["capital_usd_m"], 3900)
        self.assertFalse(validate_selected([d], {d["id"]: decision()}))
        self.assertIsNone(preserve_financing_caveats(d))

    def test_announced_capital_counts_reported_round_not_cash_closed(self):
        for text, expected in [('$50 million; initial closing announced, cash closed undisclosed',50),
                               ('Up to US$100 million in conditional commitments',100),
                               ('$12 million combined investment',12),
                               ('$21 million cumulative total across rounds',0),
                               ('$100 million valuation',0),
                               ('$10 million debt',0)]:
            d = deal(amount=text)
            p = make_package([d], {d['id']:decision()}, '2026-09-16','2026-09-22')
            self.assertEqual(p['stats']['capital_usd_m'],expected,text)

    def test_round_labels_are_normalized_without_prose(self):
        for raw, expected in [('pre seed financing','Pre-seed'),('Seed extension','Seed'),
                              ('series b2 extension','Series B'),('Series F initial closing','Series F'),
                              ('First priced venture round','Early'),('SAFE','Early'),('Pre-Series A','Early')]:
            self.assertEqual(normalize_fields(deal(round=raw)['fields'])['round'],expected)

    def test_caveat_for_different_amount_does_not_hide_supported_close(self):
        d = deal(amount="$200 million")
        d["evidence"] = [{"id": "official", "kind": "web", "fields": ["amount"], "quote": "An initial closing of an anticipated $3.9 billion round"}]
        self.assertFalse(financing_caveats(d))
        d["fields"]["amount"] = "$3.9 billion"
        d["evidence"][0]["fields"] = ["investors"]
        self.assertFalse(financing_caveats(d))

    def test_foreign_currency_qualifier_is_not_lost_when_fx_is_available(self):
        d = deal(amount="EUR 14 million")
        d["evidence"] = [{"id": "official", "kind": "web", "fields": ["amount"], "quote": "Up to EUR 14 million in conditional financing commitments"}]
        self.assertEqual(financing_caveats(d)[0]["kind"], "conditional")
        preserve_financing_caveats(d)
        book = {"observations": [{"d": "2026-09-18", "FXUSDCAD": {"v": "1.4"}, "FXEURCAD": {"v": "1.6"}}]}
        self.assertIsNone(capital_usd_m(d["fields"]["amount"], "2026-09-18", book)[0])

    def test_cumulative_guard_preserves_current_round_separate_from_lifetime_total(self):
        d = deal(amount="$21 million")
        d["evidence"] = [{"id": "official", "kind": "web", "fields": ["amount"], "quote": "Announced a total of $21 million in funding across Seed and Series A"}]
        self.assertEqual(financing_caveats(d)[0]["kind"], "cumulative")
        preserve_financing_caveats(d)
        self.assertIn("current-round amount not separately disclosed", d["fields"]["amount"])
        self.assertIsNone(amount_usd_m(d["fields"]["amount"])[0])
        d["fields"]["amount"] = "$10 million"
        d["evidence"][0]["quote"] = "Raised $10 million, bringing its total funding to $21 million"
        self.assertFalse(financing_caveats(d))
        d["fields"]["amount"] = "$21 million"
        self.assertEqual(financing_caveats(d)[0]["kind"], "cumulative")

    def test_dedupe_merges_sources_not_different_rounds(self):
        a, b = deal(), deal(day="2026-09-19")
        b["sources"] = ["Pro Rata"]
        merged = merge_deals([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(set(merged[0]["sources"]), {"Term Sheet", "Pro Rata"})
        self.assertEqual(len(merge_deals([deal(), deal(round="Series B")])), 2)
        self.assertEqual(len(merge_deals([deal(), deal(amount="$20 million")])), 2)
        self.assertEqual(len(merge_deals([deal(), deal(day="2026-10-02")])), 2)
        self.assertEqual(len(merge_deals([deal(amount="EUR 10 million"), deal(amount="EUR 20 million")])), 2)
        self.assertEqual(len(merge_deals([deal(amount="EUR 10 million"), deal(amount="EUR 10m")])), 1)

    def test_domain_and_fuzzy_portfolio_matching(self):
        portfolio = [{"Name": "Example Inc", "Company URL": "https://www.example.com"}]
        self.assertEqual(portfolio_match(deal(), portfolio)["status"], "known")
        ambiguous = portfolio_match(deal(name="Example In", website="https://other.com"), portfolio)
        self.assertEqual(ambiguous["status"], "ambiguous")

    def test_post_enrichment_dedupe_combines_evidence_and_investors(self):
        a, b = deal(), deal(day="2026-09-19", round="Series A first close")
        a["evidence"] = [{"id": "evidence-a"}]
        b["id"], b["block_ids"], b["evidence"] = "duplicate", ["duplicate"], [{"id": "evidence-b"}]
        b["fields"]["investors"] = "Beta; Gamma"
        merged, changes = consolidate_prepared([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["fields"]["investors"], "Alpha; Beta; Gamma")
        self.assertEqual({e["id"] for e in merged[0]["evidence"]}, {"evidence-a", "evidence-b"})
        self.assertEqual(changes[0]["superseded"], "duplicate")
        self.assertEqual(len(consolidate_prepared([deal(round="Series A"), deal(round="Series B")])[0]), 2)

    def test_selected_duplicate_fails_before_publication(self):
        a, b = deal(), deal()
        b["id"] = "duplicate"
        self.assertIn("duplicate selected financing", " ".join(validate_selected([a, b], {a["id"]: decision(), b["id"]: decision()})))

    def test_route_all_no_and_uncertain_decisions(self):
        self.assertFalse(needs_verification(decision(), {"status": "not_matched"}))
        self.assertTrue(needs_verification(decision("No", .99), {"status": "not_matched"}))
        self.assertTrue(needs_verification(decision("Maybe", .99), {"status": "not_matched"}))
        self.assertTrue(needs_verification(decision("Yes", .8), {"status": "not_matched"}))
        self.assertTrue(needs_verification(decision(), {"status": "ambiguous"}))

    def test_coverage_fails_on_missing_and_duplicate(self):
        reconcile_ids([{"id": "a"}], ["a"])
        for records in [[], [{"id": "a"}, {"id": "a"}], [{"id": "b"}]]:
            with self.assertRaises(ValueError):
                reconcile_ids(records, ["a"])

    def test_compact_citations_restore_exact_original_sources(self):
        data = [{"id": "original-company", "evidence": [{"id": "original-source", "quote": "Product evidence"}], "data_quality_flags": [{"evidence_id": "original-source"}]}]
        compact, mapping = compact_review_payload(data)
        self.assertEqual(compact[0]["id"], "D1")
        self.assertEqual(data[0]["id"], "original-company")
        self.assertEqual(compact[0]["data_quality_flags"][0]["evidence_id"], "E1")
        value = [{"id": "D1", "evidence_ids": ["E1"], "field_corrections": [{"evidence_id": "E1"}]}]
        restored = restore_review_ids(value, mapping)
        self.assertEqual(restored[0]["evidence_ids"], ["original-source"])
        self.assertEqual(restored[0]["field_corrections"][0]["evidence_id"], "original-source")
        with self.assertRaisesRegex(ValueError, "absent"):
            restore_review_ids([{"id": "D1", "evidence_ids": ["E2"], "field_corrections": []}], mapping)

    def test_selected_blanks_need_reason(self):
        d = deal()
        d["fields"]["website"] = ""
        self.assertTrue(validate_selected([d], {d["id"]: decision()}))
        d["missing"]["website"] = "Two ambiguous entities found after official-site searches"
        self.assertFalse(validate_selected([d], {d["id"]: decision()}))

    def test_final_binary_package_excludes_portfolio_and_old(self):
        values = [deal(), deal("Adjacent"), deal("Known"), deal("Old", day="2026-09-10")]
        decisions = {d["id"]: decision() for d in values}
        decisions["Known"]["known_investment"] = True
        p = make_package(values, decisions, "2026-09-16", "2026-09-22")
        self.assertEqual(p["stats"]["count"], 2)
        self.assertEqual(p["stats"]["borderline"], 0)
        self.assertTrue(all(row[13] == "Yes" for row in p["rows"]))
        self.assertEqual(p["stats"]["capital_usd_m"], 20)

    def test_unresolved_maybe_stops_publication_not_silently_promoted(self):
        d = deal()
        decisions = {d["id"]: decision("Maybe")}
        self.assertIn("final fit must be Yes or No", " ".join(validate_selected([d], decisions)))
        with self.assertRaisesRegex(ValueError, "unresolved Maybe"):
            make_package([d], decisions, "2026-09-16", "2026-09-22")

    def test_independent_schema_requires_binary_decision(self):
        from control_center.models import BINARY_DECISION_SCHEMA, DECISION_SCHEMA
        first = DECISION_SCHEMA["properties"]["decisions"]["items"]["properties"]["fit"]["enum"]
        final = BINARY_DECISION_SCHEMA["properties"]["decisions"]["items"]["properties"]["fit"]["enum"]
        self.assertEqual(first, ["Yes", "Maybe", "No"])
        self.assertEqual(final, ["Yes", "No"])

    def test_round_fallback_uses_reviewed_stage_without_inventing_series(self):
        fields = deal(round="")["fields"]
        self.assertEqual(normalize_fields(fields)["round"], "Early")
        fields["growth_stage"] = "Growth"
        self.assertEqual(normalize_fields(fields)["round"], "Growth")
        fields["growth_stage"] = "Unknown"
        self.assertEqual(normalize_fields(fields)["round"], "")
        fields["round"] = "Series A2 extension"
        self.assertEqual(normalize_fields(fields)["round"], "Series A")
        fields["round"] = "Series A-2"
        self.assertEqual(normalize_fields(fields)["round"], "Series A")

    def test_url_tracking_cleanup(self):
        self.assertEqual(clean_url("https://example.com/?utm_source=email&x=a%20b"), "https://example.com/?x=a+b")
        self.assertEqual(clean_url("https://tracker.com/?url=https%3A%2F%2Fexample.com%2F"), "https://example.com/")
        self.assertEqual(clean_url("javascript:alert(1)"), "")


class PublicationTests(unittest.TestCase):
    def package(self):
        d = deal()
        return make_package([d], {d["id"]: decision()}, "2026-09-16", "2026-09-22")

    def test_blurb_limits_backers_without_truncating_sheet(self):
        p = self.package()
        p["rows"][0][7] = ", ".join("Investor" + str(i) for i in range(10))
        text, spans = blurb(p["rows"][0])
        self.assertIn("Investor7, and others", text)
        self.assertNotIn("Investor8", text)
        self.assertIn("Investor9", p["rows"][0][7])
        self.assertEqual(sum(bool(url) for _, _, _, url in spans), 2)
        self.assertGreaterEqual(sum(bold for _, _, bold, _ in spans), 10)

    def test_blurb_does_not_claim_contingent_amount_was_raised(self):
        row = self.package()["rows"][0]
        for amount in ["Up to US$100 million in commitments", "$3.9 billion anticipated round; initial close undisclosed"]:
            row[6] = amount
            text, _ = blurb(row)
            self.assertIn("Financing: " + amount, text)
        self.assertNotIn("It raised", text)

    def test_blurb_describes_stage_fallback_without_named_round_claim(self):
        row = self.package()["rows"][0]
        row[5] = "Early"
        self.assertIn("in an early-stage funding round", blurb(row)[0])
        self.assertNotIn("Series A", blurb(row)[0])
        row[5] = "Growth"
        self.assertIn("in a growth-stage funding round", blurb(row)[0])

    def test_doc_preserves_stats_charts_and_uses_fresh_revision(self):
        def paragraph(start, text):
            return {"startIndex": start, "endIndex": start + len(text),
                    "paragraph": {"elements": [{"textRun": {"content": text}}]}}
        rows = []
        for a, b, label, value in [(130, 150, "Metric\n", "Value\n"),
                                    (170, 190, "Deals\n", "0\n"),
                                    (210, 240, "Capital Announced\n", "$0M\n")]:
            rows.append({"tableCells": [{"content": [paragraph(a, label)]},
                                        {"content": [paragraph(b, value)]}]})
        doc = {"revisionId": "synthetic-revision", "tabs": [{"tabProperties": {"tabId": "t.0"},
               "documentTab": {"body": {"content": [paragraph(1, "Early Stage Deals:\n"),
                   paragraph(40, "Growth Stage Deals:\n"), paragraph(80, "Market Stats:\n"),
                   {"startIndex": 100, "endIndex": 101, "paragraph": {"elements": [{"inlineObjectElement": {"inlineObjectId": "chart"}}]}},
                   {"startIndex": 120, "endIndex": 260, "table": {"tableRows": rows}}]},
                   "inlineObjects": {"chart": {"inlineObjectProperties": {"embeddedObject": {
                       "linkedContentReference": {"sheetsChartReference": {"spreadsheetId": "synthetic-sheet", "chartId": 1}}}}}}}}]}
        p = self.package()
        payload = build_doc(p, doc)
        self.assertEqual(payload["writeControl"]["requiredRevisionId"], doc["revisionId"])
        self.assertIn("N/A - no relevant deals this week", str(payload))
        self.assertIn("keepWithNext", str(payload))
        self.assertNotIn("deleteInlineObject", str(payload))
        for request in payload['requests']:
            if 'deleteContentRange' in request:
                region = request['deleteContentRange']['range']
                self.assertTrue(region['endIndex'] <= 100 or region['startIndex'] >= 101)
        self.assertTrue(all(r["updateTextStyle"]["textStyle"].get("fontSize", {"magnitude": 10})["magnitude"] == 10 for r in payload["requests"] if "updateTextStyle" in r))

    def test_readback_rejects_missing_hyperlink_and_emphasis(self):
        row = self.package()["rows"][0]
        text, spans = blurb(row)
        boundaries = sorted({0, len(text)} | {a for a, _, _, _ in spans} | {b for _, b, _, _ in spans})
        elements = []
        for a, b in zip(boundaries, boundaries[1:]):
            span = next((s for s in spans if s[0] <= a < s[1]), None)
            style = {"bold": bool(span and span[2])}
            if span and span[3]:
                style["link"] = {"url": span[3]}
            elements.append({"textRun": {"content": text[a:b], "textStyle": style}})
        paragraph = {"paragraph": {"elements": elements}}
        validate_blurb_styles(paragraph, row)
        broken = copy.deepcopy(paragraph)
        broken["paragraph"]["elements"][0]["textRun"]["textStyle"].pop("link")
        with self.assertRaisesRegex(ValueError, "hyperlink"):
            validate_blurb_styles(broken, row)
        broken = copy.deepcopy(paragraph)
        broken["paragraph"]["elements"][0]["textRun"]["textStyle"]["bold"] = False
        with self.assertRaisesRegex(ValueError, "emphasis"):
            validate_blurb_styles(broken, row)

    def test_sheet_repeat_publication_replaces_week_and_preserves_history(self):
        package = self.package()
        old_current = list(package["rows"][0])
        historical = list(old_current)
        historical[2], historical[3] = "2026-09-10", "Historical"
        snapshot = {"meta": {"sheets": [{"properties": {"title": "List", "sheetId": 0}}, {"properties": {"title": "Stats", "sheetId": 7}}]}, "values": {"values": [HEADERS, old_current, historical]}}
        requests, summary = build_sheet(package, snapshot)
        deletions = [r["deleteDimension"]["range"]["startIndex"] for r in requests if "deleteDimension" in r]
        self.assertEqual(deletions, [1])
        self.assertEqual(summary["written_rows"], 1)
        self.assertEqual(summary["stats"][-1], ["9/16 - 9/22", 1, 10.0])
        self.assertTrue(any("setBasicFilter" in r for r in requests))

    def test_sheet_refuses_maybe_rows_and_retains_binary_dropdown(self):
        p = self.package()
        snapshot = {"meta": {"sheets": [{"properties": {"title": "List", "sheetId": 0}}, {"properties": {"title": "Stats", "sheetId": 7}}]}, "values": {"values": [HEADERS]}}
        requests, _ = build_sheet(p, snapshot)
        fit_rule = next(r["setDataValidation"]["rule"] for r in requests if "setDataValidation" in r and r["setDataValidation"]["range"]["startColumnIndex"] == 13)
        self.assertEqual([v["userEnteredValue"] for v in fit_rule["condition"]["values"]], ["Yes", "No"])
        p["rows"][0][13] = "Maybe"
        with self.assertRaisesRegex(ValueError, "Only final Yes"):
            build_sheet(p, snapshot)

    def test_sheet_normalizes_historical_text_dates_before_sorting(self):
        p = self.package()
        historical = list(p["rows"][0])
        historical[2], historical[3] = "2026-07-14", "Historical"
        snapshot = {"meta": {"sheets": [{"properties": {"title": "List", "sheetId": 0}}, {"properties": {"title": "Stats", "sheetId": 7}}]}, "values": {"values": [HEADERS, historical]}}
        requests, _ = build_sheet(p, snapshot)
        converted = next(r["updateCells"] for r in requests if r.get("updateCells", {}).get("start") == {"sheetId": 0, "rowIndex": 2, "columnIndex": 2})
        expected = (date(2026, 7, 14) - date(1899, 12, 30)).days
        self.assertEqual(converted["rows"][0]["values"][0]["userEnteredValue"], {"numberValue": expected})
        self.assertLess(requests.index({"updateCells": converted}), next(i for i, r in enumerate(requests) if "sortRange" in r))

    def test_live_readback_rejects_out_of_order_dates(self):
        p = self.package()
        historical = list(p["rows"][0])
        historical[2], historical[3] = "2026-07-14", "Historical"
        publisher = object.__new__(Publisher)
        publisher.sheet_snapshot = lambda: {"values": {"values": [HEADERS, historical, p["rows"][0]]}}
        publisher.doc_snapshot = lambda: {}
        with self.assertRaisesRegex(ValueError, "Deal Date descending"):
            publisher.verify(p)

    def test_live_readback_rejects_a_stray_maybe(self):
        p = self.package()
        invalid = list(p["rows"][0])
        invalid[13] = "Maybe"
        publisher = object.__new__(Publisher)
        publisher.sheet_snapshot = lambda: {"values": {"values": [HEADERS, invalid]}}
        publisher.doc_snapshot = lambda: {}
        with self.assertRaisesRegex(ValueError, "still contains a Maybe"):
            publisher.verify(p)


class SchedulingTests(unittest.TestCase):
    def payload(self):
        return [{"id": name, "company": name, "portfolio_match": {"status": "not_matched"}} for name in ["Slow", "Fast"]]

    def classify(self, batch, independent=False):
        return [{"id": item["id"], **decision("No"), "field_corrections": []} for item in batch], {"model": "test", "seconds": 0, "usage": {}, "artifact": "test"}

    def test_streaming_starts_independent_check_before_slow_first_pass_finishes(self):
        from control_center import pipeline
        verification_started = threading.Event()
        slow_finished = threading.Event()
        checked_inputs = []

        def classify(batch, run_id, independent=False, mandate=None):
            if independent and batch[0]["id"] == "Fast":
                self.assertFalse(slow_finished.is_set())
                checked_inputs.extend(copy.deepcopy(batch))
                verification_started.set()
                time.sleep(.02)
            elif not independent and batch[0]["id"] == "Slow":
                self.assertTrue(verification_started.wait(2))
                time.sleep(.01)
                slow_finished.set()
            return self.classify(batch, independent)

        with patch.object(pipeline, "BATCH_SIZE", 1), patch.object(pipeline, "REVIEW_WORKERS", 2), patch.object(pipeline, "classify_batch", classify), patch.object(pipeline.store, "event"), patch.object(pipeline.store, "update_run"):
            first, verified, jobs, uncertain, timings = pipeline.review_batches(self.payload(), "test", {}, "streaming")
        self.assertEqual(set(first), {"Slow", "Fast"})
        self.assertEqual(set(verified), set(first))
        self.assertEqual(len(jobs), 4)
        self.assertEqual(uncertain, 0)
        self.assertEqual(checked_inputs, [self.payload()[1]])
        self.assertLess(timings["decisioning"], timings["review"] + timings["verification"])
        self.assertGreater(timings["review_check_overlap"], 0)

    def test_phased_schedule_keeps_barrier_and_same_routing(self):
        from control_center import pipeline
        first_ready = set()

        def classify(batch, run_id, independent=False, mandate=None):
            if independent:
                self.assertEqual(first_ready, {"Slow", "Fast"})
            else:
                first_ready.update(item["id"] for item in batch)
            return self.classify(batch, independent)

        with patch.object(pipeline, "BATCH_SIZE", 1), patch.object(pipeline, "REVIEW_WORKERS", 2), patch.object(pipeline, "classify_batch", classify), patch.object(pipeline.store, "event"), patch.object(pipeline.store, "update_run"):
            first, verified, jobs, _, timings = pipeline.review_batches(self.payload(), "test", {}, "phased")
        self.assertEqual(set(first), set(verified))
        self.assertEqual(len(jobs), 4)
        self.assertEqual(timings["review_check_overlap"], 0)

    def test_known_investments_do_not_create_model_jobs(self):
        from control_center import pipeline
        initial = {"Slow": {"id": "Slow", **decision("No"), "known_investment": True}, "Fast": {"id": "Fast", **decision("No"), "known_investment": True}}
        with patch.object(pipeline, "classify_batch") as classify, patch.object(pipeline.store, "update_run"):
            first, verified, jobs, _, timings = pipeline.review_batches(self.payload(), "test", initial)
        classify.assert_not_called()
        self.assertEqual(first, initial)
        self.assertEqual(verified, {})
        self.assertEqual(jobs, [])
        self.assertEqual(timings["verification"], 0)

    def test_all_passes_use_one_frozen_mandate(self):
        from control_center import pipeline
        seen = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mandate.md"
            path.write_text("Frozen mandate")

            def classify(batch, run_id, independent=False, mandate=None):
                seen.append(mandate)
                path.write_text("Later edit must not change an active run")
                return self.classify(batch, independent)

            with patch.object(pipeline, "MANDATE", path), patch.object(pipeline, "BATCH_SIZE", 1), patch.object(pipeline, "REVIEW_WORKERS", 1), patch.object(pipeline, "classify_batch", classify), patch.object(pipeline.store, "event"), patch.object(pipeline.store, "update_run"):
                pipeline.review_batches(self.payload(), "test", {}, "streaming")
        self.assertEqual(seen, ["Frozen mandate"] * 4)

    def test_source_risk_routes_an_otherwise_clear_yes(self):
        from control_center import pipeline
        payload = self.payload()[:1]
        payload[0]["data_quality_flags"] = [{"kind": "anticipated", "evidence_id": "official"}]

        def classify(batch, run_id, independent=False, mandate=None):
            values, meta = self.classify(batch, independent)
            for value in values:
                value["fit"] = "Yes"
            return values, meta

        with patch.object(pipeline, "classify_batch", classify), patch.object(pipeline.store, "event"), patch.object(pipeline.store, "update_run"):
            first, verified, jobs, _, _ = pipeline.review_batches(payload, "test", {})
        self.assertEqual(first["Slow"]["confidence"], .95)
        self.assertEqual(set(verified), {"Slow"})
        self.assertEqual(len(jobs), 2)


class StoreTests(unittest.TestCase):
    def test_failed_preparation_reuses_only_completed_source_matched_research(self):
        from control_center import pipeline, store
        from control_center.models import ENRICHMENT_SCHEMA
        previous = "0123456789abcdef"
        for forbidden in [False, True]:
            with self.subTest(forbidden=forbidden), tempfile.TemporaryDirectory() as directory:
                artifacts = Path(directory)
                folder = artifacts / previous / "enrich" / "job"
                folder.mkdir(parents=True)
                candidate = deal()
                item = {"id": candidate["id"], "fields": candidate["fields"], "missing": {key: "" for key in FIELDS}, "evidence": [{"url": "https://example.com/product", "quote": "Construction estimating software", "summary": "The product serves construction contractors", "fields": ["description"]}]}
                (folder / "schema.json").write_text(json.dumps(ENRICHMENT_SCHEMA))
                (folder / "output.json").write_text(json.dumps({"deals": [item]}))
                (folder / "prompt.txt").write_text("Do not return any fit decision\nCOMPANIES:\n" + json.dumps([{"id": candidate["id"], "company": candidate["company"], "newsletter_evidence": []}]))
                events = [{"type": "turn.completed", "usage": {"input_tokens": 100}}]
                if forbidden:
                    events.append({"type": "item.completed", "item": {"type": "command_execution"}})
                (folder / "events.jsonl").write_text("\n".join(json.dumps(event) for event in events))
                with patch.object(pipeline, "ARTIFACTS", artifacts), patch.object(store, "get_run", return_value={"week_id": "week", "kind": "prepare", "status": "failed"}), patch.object(store, "get_deals", return_value=[candidate]), patch.object(pipeline, "source_evidence", return_value=[]), patch.object(pipeline, "save_deal") as save, patch.object(store, "event"), patch.object(pipeline, "run_model") as model:
                    report = pipeline.recover_preparation_enrichment("new-run", "week", previous)
                    self.assertEqual(report["new_model_calls"], 0)
                    self.assertEqual(len(report["recovered_companies"]), 0 if forbidden else 1)
                    self.assertEqual(save.call_count, 0 if forbidden else 1)
                    model.assert_not_called()
                    self.assertFalse(candidate["evidence"])

    def test_recovery_refuses_other_weeks_and_live_runs(self):
        from control_center import pipeline, store
        for prior in [{"week_id": "another", "kind": "prepare", "status": "failed"}, {"week_id": "week", "kind": "review", "status": "failed"}, {"week_id": "week", "kind": "prepare", "status": "running"}]:
            with patch.object(store, "get_run", return_value=prior):
                with self.assertRaisesRegex(ValueError, "same week"):
                    pipeline.recover_preparation_enrichment("new-run", "week", "0123456789abcdef")
        with self.assertRaisesRegex(ValueError, "valid failed"):
            pipeline.recover_preparation_enrichment("new-run", "week", "../outside")

    def test_evidence_refresh_uses_frozen_inputs_not_reviewed_row_values(self):
        from control_center import store, pipeline
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'benchmark').mkdir()
            (root / 'benchmark/portfolio.json').write_text('[]')
            with patch.object(store, "DATA", root), patch.object(store, "DB", root / "test.sqlite"), patch.object(store, "ARTIFACTS", root / "artifacts"), patch.object(pipeline, "DATA", root), patch.object(pipeline, "PROJECT", root):
                store.initialize()
                wid = pipeline.register_week("2026-09-16", "2026-09-22")
                original = deal()
                pipeline.save_deal(wid, original)
                manifest = pipeline.freeze_prepared(wid, [original])
                old_bytes = (root / manifest["path"]).read_bytes()
                store.execute("UPDATE weeks SET status='prepared' WHERE id=?", (wid,))
                changed = copy.deepcopy(original)
                changed["fields"]["amount"] = "$999 million"
                pipeline.save_deal(wid, changed)
                rid = store.new_run(wid, "enrich", "local")

                def enrich(batch, run_id):
                    self.assertEqual(batch[0]["fields"]["amount"], "$10 million")
                    item = {"id": original["id"], "fields": copy.deepcopy(batch[0]["fields"]), "missing": {}, "evidence": [{"url": "https://example.com/customers", "quote": "Construction teams use this product", "fields": ["description"]}]}
                    item["fields"]["description"] = "Construction software used by contractors"
                    return [item], {"model": "test", "usage": {}, "web_calls": 1}

                with patch.object(pipeline, "enrich_batch", enrich):
                    pipeline.refresh_prepared_evidence(rid, wid, ["Example"])
                revised, next_manifest = pipeline.prepared_deals(wid)
                self.assertNotEqual(manifest["sha256"], next_manifest["sha256"])
                self.assertEqual((root / manifest["path"]).read_bytes(), old_bytes)
                self.assertEqual(revised[0]["fields"]["amount"], "$10 million")
                self.assertEqual(revised[0]["evidence"][-1]["enrichment_version"], "application-context-v3")
                self.assertEqual(store.get_run(rid)["status"], "completed")
                self.assertFalse(store.query("SELECT * FROM decisions WHERE run_id=?", (rid,)))

    def test_enrichment_prompt_checks_actual_applications_and_market_scope(self):
        from control_center import pipeline
        candidate = deal()
        candidate["final"] = {"fit": "Yes", "reason": "Must not be supplied to research"}
        with patch.object(pipeline, "source_evidence", return_value=[]), patch.object(pipeline, "run_model", return_value=({"deals": [{"id": candidate["id"]}]}, {})) as model:
            pipeline.enrich_batch([candidate], "test")
        prompt = model.call_args.args[0]
        self.assertIn("Read beyond the headline/tagline", prompt)
        self.assertIn("foreign headquarters alone", prompt)
        self.assertIn("at most 25 quoted words per URL", prompt)
        self.assertNotIn("Must not be supplied to research", prompt)
        self.assertTrue(model.call_args.kwargs["research"])

    def test_enrichment_merges_one_page_without_repeating_quotes(self):
        from control_center.pipeline import apply_enrichment
        d = deal()
        item = {"fields": d["fields"], "missing": {}, "evidence": [{"url": "https://example.com/news", "quote": "word " * 30, "summary": "Factual context in own words", "fields": ["description"]}, {"url": "https://example.com/news", "quote": "word " * 6, "summary": "The funding includes debt", "fields": ["amount"]}]}
        apply_enrichment(d, item, "test")
        self.assertEqual(len(d["evidence"]), 1)
        self.assertEqual(len(d["evidence"][0]["quote"].split()), 25)
        self.assertEqual(d["evidence"][0]["source_entries_merged"], 2)
        self.assertTrue(d["evidence"][0]["quote_shortened"])
        self.assertIn("includes debt", d["evidence"][0]["summary"])
        self.assertEqual(d["evidence"][0]["fields"], ["description", "amount"])

    def test_enrichment_does_not_truncate_overlong_factual_context(self):
        from control_center.pipeline import apply_enrichment
        d = deal()
        item = {"fields": d["fields"], "missing": {}, "evidence": [{"url": "https://example.com/news", "quote": "Supported source excerpt", "summary": "word " * 151, "fields": ["description"]}]}
        with self.assertRaisesRegex(ValueError, "per-URL word limit"):
            apply_enrichment(d, item, "test")

    def test_feedback_query_with_active_deal_column(self):
        from control_center import store, pipeline
        from control_center.pipeline import register_week, review_inputs, save_deal
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'benchmark').mkdir()
            (root / 'benchmark/portfolio.json').write_text('[]')
            with patch.object(store, "DATA", root), patch.object(store, "DB", root / "test.sqlite"), patch.object(store, "ARTIFACTS", root / "artifacts"), patch.object(pipeline, "PROJECT", root):
                store.initialize()
                wid = register_week("2026-09-16", "2026-09-22")
                d = deal()
                save_deal(wid, d)
                store.execute("INSERT INTO feedback(id,deal_id,fit,reason,at) VALUES(?,?,?,?,?)", ("feedback", d["id"], "Yes", "Construction software directly serves contractors", store.now()))
                inputs = review_inputs(store.get_deals(wid))
                self.assertEqual(inputs[0]["past_corrections"][0]["fit"], "Yes")

    def test_prepared_snapshot_is_immutable_and_detects_tampering(self):
        from control_center import store, pipeline
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(store, "DATA", root), patch.object(store, "DB", root / "test.sqlite"), patch.object(store, "ARTIFACTS", root / "artifacts"), patch.object(pipeline, "DATA", root):
                store.initialize()
                wid = pipeline.register_week("2026-09-16", "2026-09-22")
                manifest = pipeline.freeze_prepared(wid, [deal()])
                initial, _ = pipeline.prepared_deals(wid)
                initial[0]["fields"]["amount"] = "$99 million"
                again, _ = pipeline.prepared_deals(wid)
                self.assertEqual(again[0]["fields"]["amount"], "$10 million")
                (root / manifest["path"]).write_text("[]")
                with self.assertRaisesRegex(ValueError, "changed"):
                    pipeline.prepared_deals(wid)

    def test_google_preflight_failure_does_not_start_model_work(self):
        from control_center import store, pipeline
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(store, "DATA", root), patch.object(store, "DB", root / "test.sqlite"), patch.object(store, "ARTIFACTS", root / "artifacts"):
                store.initialize()
                wid = pipeline.register_week("2026-09-16", "2026-09-22")
                store.execute("UPDATE weeks SET status='prepared' WHERE id=?", (wid,))
                rid = store.new_run(wid, "review", "test")
                with patch("control_center.google.Publisher") as publisher, patch.object(pipeline, "classify_batch") as classify:
                    publisher.return_value.snapshot.side_effect = RuntimeError("Google HTTP 403: permission denied")
                    pipeline.run_job(rid, wid, "review", "test")
                    classify.assert_not_called()
                self.assertEqual(store.get_run(rid)["status"], "failed")


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('control_center.auth.GOOGLE_ACCOUNT', 'reviewer@example.org'))
        self.enterContext(patch('control_center.google.GOOGLE_ACCOUNT', 'reviewer@example.org'))

    def test_authorization_accepts_only_verified_configured_identity(self):
        from control_center.auth import verified_account
        GOOGLE_ACCOUNT = 'reviewer@example.org'
        creds = MagicMock()
        with patch("control_center.auth.AuthorizedSession") as session:
            response = session.return_value.__enter__.return_value.get.return_value
            response.json.return_value = {"email": GOOGLE_ACCOUNT, "email_verified": True}
            self.assertIs(verified_account(creds), creds.with_account.return_value)
            creds.with_account.assert_called_once_with(GOOGLE_ACCOUNT)
            for profile in [{"email": "other@gmail.com", "email_verified": True}, {"email": GOOGLE_ACCOUNT, "email_verified": False}, {}]:
                response.json.return_value = profile
                with self.assertRaisesRegex(RuntimeError, "Existing credentials are unchanged"):
                    verified_account(creds)

    def test_gmail_identity_uses_existing_read_only_profile(self):
        from control_center.auth import verified_account
        GOOGLE_ACCOUNT = 'reviewer@example.org'
        with patch("control_center.auth.AuthorizedSession") as session:
            client = session.return_value.__enter__.return_value
            client.get.return_value.json.return_value = {"emailAddress": GOOGLE_ACCOUNT}
            verified_account(MagicMock(), gmail=True)
            self.assertEqual(client.get.call_args.args[0], "https://gmail.googleapis.com/gmail/v1/users/me/profile")

    def test_unknown_writer_account_stops_before_refresh_or_write(self):
        from control_center import google
        creds = MagicMock(account="")
        with patch.object(google.GOOGLE_TOKEN.__class__, "exists", return_value=True), patch.object(google.Credentials, "from_authorized_user_file", return_value=creds):
            with self.assertRaisesRegex(RuntimeError, "account is not verified"):
                google.credentials()
            creds.refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
