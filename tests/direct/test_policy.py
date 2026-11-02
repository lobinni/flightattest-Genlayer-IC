"""The PRIORITY_V2 resolution policy as a pure function: truth tables for
outcomes, roles, conflicts, methods, fallbacks, derived operation states,
digests and structural self-checks (_check_core tamper detection)."""

import json
from datetime import datetime

import pytest

from tests.direct.flightattest_support import new_contract

M, _ = new_contract()  # pure functions only; no chain interaction

CTX = {
    "flight_id": "st221-2026-03-10",
    "request_id": "req-1",
    "policy_version": "PRIORITY_V2",
    "carrier": "Skyline Air",
    "flight_number": "ST221",
    "departure_airport": "SGN",
    "arrival_airport": "SFO",
    "scheduled_departure": "2026-03-10T08:30:00Z",
    "sources": [
        {"id": "S1", "url": "https://ops.skyline-air.aero/flights/ST221"},
        {"id": "S2", "url": "https://status.sgn-airport.aero/departures/ST221"},
        {"id": "S3", "url": "https://track.flightradar.io/live/ST221"},
    ],
}

LONG_QUOTE = "Flight ST221 now departs at 11:45 UTC due to late inbound aircraft."

URLS = {s["id"]: s["url"] for s in CTX["sources"]}


def mk_finding(source_id, outcome, claim="", date="", clock="", tz="", div="",
               retrieval="OK", http=200, excerpt=""):
    assert outcome != "RETRIEVAL_FAILED" or retrieval != "OK" or excerpt == ""
    return {
        "source_id": source_id,
        "url": URLS.get(source_id, "https://x.invalid.invalid/" + source_id),
        "retrieval": retrieval,
        "http_status": http,
        "outcome": outcome,
        "claim": claim,
        "new_dep_date": date,
        "new_dep_time": clock,
        "new_dep_tz": tz,
        "new_dep_precision": M._precision(date, clock, tz),
        "diversion_airport": div,
        "excerpt": excerpt,
        "evidence_digest": "",
        "decision_role": "UNUSABLE",
    }


def valid(source_id, claim, date="", clock="", tz="", div="", excerpt=LONG_QUOTE):
    return mk_finding(source_id, "VALID_CLAIM", claim, date, clock, tz, div, excerpt=excerpt)


def failed(source_id, retrieval="UNAVAILABLE", http=0):
    return mk_finding(source_id, "RETRIEVAL_FAILED", retrieval=retrieval, http=http)


def resolve(findings):
    return M._resolve(findings)


# ------------------------------ all skippable ------------------------------ #

def test_all_retrieval_failed_is_insufficient():
    out = resolve([failed("S1"), failed("S2")])
    assert out["final_status"] == "INSUFFICIENT_EVIDENCE"
    assert out["reason"] == "NO_USABLE_SOURCE"
    assert out["method"] == "NONE"
    assert out["operation_state"] == "UNDETERMINED"
    assert out["roles"] == ["SKIPPED", "SKIPPED"]


def test_all_retrieved_pages_wrong_flight_is_identity_unconfirmed():
    out = resolve([mk_finding("S1", "IDENTITY_UNCONFIRMED"), mk_finding("S2", "IDENTITY_UNCONFIRMED")])
    assert out["final_status"] == "FLIGHT_IDENTITY_UNCONFIRMED"
    assert out["reason"] == "IDENTITY_UNCONFIRMED_ALL_RETRIEVED"


def test_mix_of_wrong_flight_and_dead_source_still_identity_unconfirmed():
    # Every page that WAS retrieved failed the identity check: the rule cares
    # about retrieved pages only, dead sources cannot manufacture evidence.
    out = resolve([mk_finding("S1", "IDENTITY_UNCONFIRMED"), failed("S2")])
    assert out["final_status"] == "FLIGHT_IDENTITY_UNCONFIRMED"
    assert out["reason"] == "IDENTITY_UNCONFIRMED_ALL_RETRIEVED"


def test_no_status_and_wrong_flight_is_insufficient():
    out = resolve([mk_finding("S1", "NO_EXPLICIT_STATUS"), mk_finding("S2", "IDENTITY_UNCONFIRMED")])
    assert out["final_status"] == "INSUFFICIENT_EVIDENCE"
    assert out["reason"] == "NO_USABLE_SOURCE"


def test_pages_without_status_are_skippable_then_insufficient():
    out = resolve([mk_finding("S1", "NO_EXPLICIT_STATUS"), mk_finding("S2", "IDENTITY_UNCONFIRMED")])
    assert out["final_status"] == "INSUFFICIENT_EVIDENCE"


# ------------------------------ controlling -------------------------------- #

@pytest.mark.parametrize(
    "claim,state",
    [("ON_SCHEDULE", "LIVE"), ("DELAYED", "LIVE"), ("DIVERTED", "LIVE"), ("CANCELLED", "CLOSED"), ("LANDED", "CLOSED")],
)
def test_first_valid_claim_controls(claim, state):
    args = {}
    if claim == "DELAYED":
        args = {"date": "2026-03-10", "clock": "11:45", "tz": "Z"}
    if claim == "DIVERTED":
        args = {"div": "OAK"}
    out = resolve([valid("S1", claim, **args), mk_finding("S2", "NO_EXPLICIT_STATUS")])
    assert out["final_status"] == claim
    assert out["operation_state"] == state
    assert out["method"] == "AGREEMENT"
    assert out["controlling_source"] == "S1"
    assert out["fallback_used"] is False
    assert out["roles"][0] == "CONTROLLING"


def test_delayed_claim_propagates_new_departure():
    out = resolve([valid("S1", "DELAYED", date="2026-03-10", clock="11:45", tz="Z"), failed("S2")])
    assert out["final_status"] == "DELAYED"
    assert (out["new_dep_date"], out["new_dep_time"], out["new_dep_tz"]) == ("2026-03-10", "11:45", "Z")
    assert out["new_dep_precision"] == "FULL"


def test_diverted_claim_propagates_airport():
    out = resolve([valid("S1", "DIVERTED", div="OAK"), failed("S2")])
    assert out["final_status"] == "DIVERTED"
    assert out["diversion_airport"] == "OAK"
    assert out["operation_state"] == "LIVE"


def test_non_controlling_claims_clear_payloads():
    out = resolve([valid("S1", "CANCELLED"), failed("S2")])
    assert out["new_dep_date"] == out["new_dep_time"] == out["new_dep_tz"] == ""
    assert out["new_dep_precision"] == "NONE"
    assert out["diversion_airport"] == ""


# -------------------------------- fallback --------------------------------- #

@pytest.mark.parametrize("skippable", ["RETRIEVAL_FAILED", "IDENTITY_UNCONFIRMED", "NO_EXPLICIT_STATUS"])
def test_skippable_outcomes_allow_fallback(skippable):
    first = failed("S1") if skippable == "RETRIEVAL_FAILED" else mk_finding("S1", skippable)
    out = resolve([first, valid("S2", "LANDED")])
    assert out["final_status"] == "LANDED"
    assert out["controlling_source"] == "S2"
    assert out["fallback_used"] is True
    assert out["roles"] == ["SKIPPED", "CONTROLLING"]


def test_double_fallback_through_two_dead_sources_with_four():
    out = resolve([
        failed("S1"),
        mk_finding("S2", "NO_EXPLICIT_STATUS"),
        valid("S3", "ON_SCHEDULE"),
        failed("S4"),
    ])
    assert out["final_status"] == "ON_SCHEDULE"
    assert out["controlling_source"] == "S3"
    assert out["fallback_used"] is True
    assert out["roles"] == ["SKIPPED", "SKIPPED", "CONTROLLING", "UNUSABLE"]


# --------------------------------- blocking -------------------------------- #

def test_ambiguous_source_blocks_fallback():
    out = resolve([mk_finding("S1", "AMBIGUOUS"), valid("S2", "ON_SCHEDULE")])
    assert out["final_status"] == "INSUFFICIENT_EVIDENCE"
    assert out["reason"] == "CONTROLLING_SOURCE_AMBIGUOUS"
    assert out["roles"] == ["BLOCKING", "BLOCKED"]


def test_contradictory_source_blocks_and_is_recorded():
    out = resolve([mk_finding("S1", "CONTRADICTORY"), valid("S2", "LANDED")])
    assert out["final_status"] == "CONFLICTING_SOURCES"
    assert out["reason"] == "CONTROLLING_SOURCE_CONTRADICTORY"
    assert out["conflicts"] == [{"kind": "INTERNAL_CONTRADICTION", "source_id": "S1", "claim": ""}]
    assert out["roles"] == ["BLOCKING", "BLOCKED"]


def test_skipped_before_blocking_roles():
    out = resolve([failed("S1"), mk_finding("S2", "AMBIGUOUS"), valid("S3", "ON_SCHEDULE")])
    assert out["final_status"] == "INSUFFICIENT_EVIDENCE"
    assert out["roles"] == ["SKIPPED", "BLOCKING", "BLOCKED"]
    assert out["fallback_used"] is False


# -------------------------------- conflicts -------------------------------- #

def test_lower_priority_status_disagreement_recorded():
    out = resolve([valid("S1", "ON_SCHEDULE"), valid("S2", "CANCELLED")])
    assert out["final_status"] == "ON_SCHEDULE"
    assert out["method"] == "PRIORITY_RULE"
    assert out["roles"] == ["CONTROLLING", "OVERRIDDEN"]
    assert out["conflicts"] == [{"kind": "STATUS_MISMATCH", "source_id": "S2", "claim": "CANCELLED"}]


def test_delayed_timing_disagreement_recorded():
    out = resolve([
        valid("S1", "DELAYED", date="2026-03-10", clock="11:45", tz="Z"),
        valid("S2", "DELAYED", date="2026-03-10", clock="12:30", tz="Z"),
    ])
    assert out["final_status"] == "DELAYED"
    assert (out["new_dep_time"]) == "11:45"
    assert out["conflicts"] == [{"kind": "TIMING_MISMATCH", "source_id": "S2", "claim": "DELAYED"}]


def test_delayed_same_timing_supports():
    out = resolve([
        valid("S1", "DELAYED", date="2026-03-10", clock="11:45", tz="Z"),
        valid("S2", "DELAYED", date="2026-03-10", clock="11:45", tz="Z"),
    ])
    assert out["method"] == "AGREEMENT"
    assert out["roles"] == ["CONTROLLING", "SUPPORTING"]
    assert out["conflicts"] == []


def test_diversion_airport_disagreement_recorded():
    out = resolve([valid("S1", "DIVERTED", div="OAK"), valid("S2", "DIVERTED", div="SJC")])
    assert out["diversion_airport"] == "OAK"
    assert out["conflicts"] == [{"kind": "DIVERSION_MISMATCH", "source_id": "S2", "claim": "DIVERTED"}]


def test_diversion_same_airport_supports():
    out = resolve([valid("S1", "DIVERTED", div="OAK"), valid("S2", "DIVERTED", div="OAK")])
    assert out["roles"] == ["CONTROLLING", "SUPPORTING"]
    assert out["conflicts"] == []


def test_internal_contradiction_of_lower_source_preserved():
    out = resolve([valid("S1", "ON_SCHEDULE"), mk_finding("S2", "CONTRADICTORY")])
    assert out["final_status"] == "ON_SCHEDULE"
    assert out["conflicts"] == [{"kind": "INTERNAL_CONTRADICTION", "source_id": "S2", "claim": ""}]
    assert out["roles"] == ["CONTROLLING", "UNUSABLE"]


def test_conflicts_ordered_by_priority():
    out = resolve([
        valid("S1", "ON_SCHEDULE"),
        mk_finding("S2", "CONTRADICTORY"),
        valid("S3", "CANCELLED"),
    ])
    assert [c["source_id"] for c in out["conflicts"]] == ["S2", "S3"]


# ----------------------- time helpers (independent oracle) ------------------ #

def ref_minutes(date, clock, tz):
    dt = datetime.fromisoformat(date + "T" + clock + ":00" + tz)
    return int(dt.timestamp() // 60)


@pytest.mark.parametrize(
    "date,clock,tz",
    [
        ("1970-01-01", "00:00", "Z"),
        ("2000-02-29", "23:59", "+11:00"),
        ("2024-12-31", "23:59", "-11:00"),
        ("2026-03-10", "08:30", "Z"),
        ("2027-07-04", "12:00", "+05:45"),
    ],
)
def test_epoch_minutes_matches_stdlib(date, clock, tz):
    assert M._epoch_minutes(date, clock, tz) == ref_minutes(date, clock, tz)


def test_delay_must_be_strictly_later_instant_same_day():
    sched = "2026-03-10T08:30:00Z"
    assert M._is_actual_delay(sched, "2026-03-10", "09:45", "Z") is True
    assert M._is_actual_delay(sched, "2026-03-10", "08:30", "Z") is False
    assert M._is_actual_delay(sched, "2026-03-10", "07:00", "Z") is False


def test_delay_instant_compared_across_timezones():
    sched = "2026-03-10T08:30:00Z"
    # 17:45 at +09:00 is 08:45Z: only 15 minutes later, still a delay.
    assert M._is_actual_delay(sched, "2026-03-10", "17:45", "+09:00") is True
    # 17:30 at +09:00 is 08:30Z exactly: the same instant, not a delay.
    assert M._is_actual_delay(sched, "2026-03-10", "17:30", "+09:00") is False


def test_delay_date_only():
    sched = "2026-03-10T08:30:00Z"
    assert M._is_actual_delay(sched, "2026-03-11", "", "") is True
    assert M._is_actual_delay(sched, "2026-03-10", "", "") is False
    assert M._is_actual_delay(sched, "2026-03-09", "", "") is False


def test_delay_tz_ambiguous_same_day():
    sched = "2026-03-10T08:30:00Z"
    assert M._is_actual_delay(sched, "2026-03-10", "10:00", "") is True
    assert M._is_actual_delay(sched, "2026-03-10", "08:00", "") is False


# ------------------------------- digests ----------------------------------- #

def test_canonical_bytes_sorted_and_compact():
    assert M._canonical_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_evidence_digest_stable_and_sensitive():
    f = valid("S1", "ON_SCHEDULE")
    d1 = M._evidence_digest(f)
    assert isinstance(d1, str) and len(d1) == 64
    f2 = dict(f)
    f2["http_status"] = 201
    assert M._evidence_digest(f2) != d1
    # role is excluded from the digest
    f3 = dict(f)
    f3["decision_role"] = "CONTROLLING"
    assert M._evidence_digest(f3) == d1


def test_build_core_recomputes_digests_and_is_deterministic():
    findings = [valid("S1", "ON_SCHEDULE"), failed("S2", "HTTP_ERROR", 404), valid("S3", "ON_SCHEDULE")]
    core1 = M._build_core(CTX, findings)
    core2 = M._build_core(CTX, findings)
    assert core1 == core2
    assert core1["result_digest"] == core2["result_digest"]
    assert all(f["evidence_digest"] for f in core1["findings"])
    assert M._check_core(core1, CTX) is True


# --------------------------- _check_core tampering -------------------------- #

# _check_core tests use a 2-source variant of the context (findings length
# must equal the registered source count).
CTX2 = dict(CTX)
CTX2["sources"] = CTX["sources"][:2]


def build(**mutation):
    findings = [valid("S1", "DELAYED", date="2026-03-10", clock="11:45", tz="Z"), valid("S2", "ON_SCHEDULE")]
    core = M._build_core(CTX2, findings)
    core.update(mutation)
    return core


def test_check_core_accepts_fresh_result():
    assert M._check_core(build(), CTX2) is True


@pytest.mark.parametrize(
    "mutation",
    [
        {"fallback_used": True},
        {"method": "AGREEMENT"},
        {"final_status": "CANCELLED"},
        {"operation_state": "CLOSED"},
        {"controlling_source": "S2"},
        {"new_dep_time": "12:00"},
        {"request_id": "other"},
        {"flight_id": "other"},
        {"policy_version": "PRIORITY_V1"},
        {"result_digest": "0" * 64},
    ],
)
def test_check_core_rejects_derived_tampering(mutation):
    assert M._check_core(build(**mutation), CTX2) is False


def test_check_core_rejects_finding_tampering():
    core = build()
    tampered = [dict(f) for f in core["findings"]]
    tampered[1]["claim"] = "DELAYED"
    core["findings"] = tampered
    assert M._check_core(core, CTX2) is False


def test_check_core_rejects_extra_keys():
    assert M._check_core(build(notes="hi"), CTX2) is False


def test_check_core_rejects_inconsistent_retrieval_shape():
    findings = [valid("S1", "ON_SCHEDULE"), mk_finding("S2", "VALID_CLAIM", "CANCELLED", retrieval="UNAVAILABLE", http=503)]
    core = M._build_core(CTX2, findings)
    assert M._check_core(core, CTX2) is False


def test_check_core_rejects_delay_that_is_not_a_delay():
    findings = [valid("S1", "DELAYED", date="2026-03-10", clock="08:30", tz="Z"), failed("S2")]
    core = M._build_core(CTX2, findings)
    assert M._check_core(core, CTX2) is False


def test_check_core_rejects_diversion_to_registered_airport():
    findings = [valid("S1", "DIVERTED", div="SFO"), failed("S2")]
    core = M._build_core(CTX2, findings)
    assert M._check_core(core, CTX2) is False


def test_check_core_rejects_url_spoofing():
    findings = [valid("S1", "ON_SCHEDULE"), failed("S2")]
    core = M._build_core(CTX2, findings)
    core["findings"][0]["url"] = "https://attacker.io/x"
    assert M._check_core(core, CTX2) is False


# ----------------------------- misc pure pieces ---------------------------- #

@pytest.mark.parametrize(
    "date,clock,tz,prec",
    [("", "", "", "NONE"), ("2026-03-11", "", "", "DATE_ONLY"), ("2026-03-11", "09:00", "", "TZ_AMBIGUOUS"), ("2026-03-11", "09:00", "Z", "FULL")],
)
def test_precision_mapping(date, clock, tz, prec):
    assert M._precision(date, clock, tz) == prec


def test_quote_rules():
    text = M._norm_ws("Flight ST221 is diverting and will land at OAK instead of SFO.")
    assert M._quote_is_substantive("land at OAK instead of SFO", text) is True
    assert M._quote_is_substantive("too short", text) is False
    assert M._quote_is_substantive("not in the page at all here", text) is False
    assert M._quote_is_substantive(text, text) is True  # whole short page tolerated


def test_delay_fields_ok():
    assert M._delay_fields_ok("2026-03-11", "09:00", "Z") is True
    assert M._delay_fields_ok("2026-03-11", "", "") is True
    assert M._delay_fields_ok("2026-13-11", "09:00", "Z") is False
    assert M._delay_fields_ok("2026-03-11", "25:00", "Z") is False
    assert M._delay_fields_ok("2026-03-11", "", "Z") is False      # tz without time
    assert M._delay_fields_ok("2026-03-11", "09:00", "+24:00") is False


def test_operation_state_mapping():
    assert M._operation_state("ON_SCHEDULE") == "LIVE"
    assert M._operation_state("CANCELLED") == "CLOSED"
    assert M._operation_state("INSUFFICIENT_EVIDENCE") == "UNDETERMINED"


def test_policy_version_constant():
    assert M.POLICY_VERSION == "PRIORITY_V2"
    assert json.dumps(M.FINAL_STATUSES)  # serializable
