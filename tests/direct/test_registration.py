"""Registration-time validation: identifiers, flight data, URLs, sources,
supersedes rules. All failures must be [EXPECTED] UserErrors and store nothing."""

import pytest

from tests.direct.flightattest_support import (
    AIRLINE_URL,
    DEP_PORT_URL,
    REGISTER_ARGS,
    SECOND_SENDER,
    TRACKER_URL,
    UserError,
    expect_user_error,
    new_contract,
    register_standard,
    set_sender,
)


def call(contract, **overrides):
    args = dict(REGISTER_ARGS)
    args.update(overrides)
    return contract.register_flight(**args)


# ------------------------------- happy path ------------------------------- #

def test_register_minimal_two_sources():
    _mod, c = new_contract()
    register_standard(c, source_urls=[AIRLINE_URL, TRACKER_URL], source_roles=["AIRLINE", "TRACKER"])
    flight = c.get_flight(REGISTER_ARGS["flight_id"])
    assert flight["found"] is True
    assert flight["flight_number"] == "ST221"
    assert flight["departure_airport"] == "SGN"
    assert flight["arrival_airport"] == "SFO"
    assert [s["source_id"] for s in flight["sources"]] == ["S1", "S2"]
    assert [s["priority"] for s in flight["sources"]] == [1, 2]
    assert flight["attestation_count"] == 0
    assert flight["policy_version"] == "PRIORITY_V2"


def test_register_max_four_sources():
    _mod, c = new_contract()
    urls = [AIRLINE_URL, DEP_PORT_URL, TRACKER_URL, "https://ops.civil-aviation.aero/notices/ST221"]
    roles = ["AIRLINE", "DEPARTURE_AIRPORT", "TRACKER", "AUTHORITY"]
    register_standard(c, source_urls=urls, source_roles=roles)
    flight = c.get_flight(REGISTER_ARGS["flight_id"])
    assert len(flight["sources"]) == 4
    assert flight["sources"][3]["role"] == "AUTHORITY"


def test_source_order_is_preserved_as_priority():
    _mod, c = new_contract()
    urls = [TRACKER_URL, AIRLINE_URL]
    register_standard(c, source_urls=urls, source_roles=["TRACKER", "AIRLINE"])
    flight = c.get_flight(REGISTER_ARGS["flight_id"])
    assert [s["url"] for s in flight["sources"]] == urls


def test_get_flight_unknown():
    _mod, c = new_contract()
    assert c.get_flight("nope") == {"found": False, "flight_id": "nope"}


# ------------------------------- identifiers ------------------------------ #

@pytest.mark.parametrize("bad", ["", "   ", "-abc", "_abc", "has space", "unicode-é", "x" * 65])
def test_bad_flight_ids_rejected(bad):
    _mod, c = new_contract()
    expect_user_error(lambda: call(c, flight_id=bad), "[EXPECTED]")


def test_duplicate_flight_id_rejected():
    _mod, c = new_contract()
    register_standard(c)
    expect_user_error(lambda: register_standard(c), "already registered")
    assert c.get_flight(REGISTER_ARGS["flight_id"])["found"] is True


def test_identifier_edge_shapes_accepted():
    _mod, c = new_contract()
    call(c, flight_id="A" + "x" * 62 + "9")  # exactly 64 chars
    call(c, flight_id="vn231_2026.03.10:leg-1")
    assert c.get_flight("vn231_2026.03.10:leg-1")["found"] is True


# ------------------------------ flight fields ----------------------------- #

@pytest.mark.parametrize("bad", ["", "x" * 121, "line\nbreak", "tab\there"])
def test_bad_carrier_rejected(bad):
    _mod, c = new_contract()
    expect_user_error(lambda: call(c, carrier=bad), "[EXPECTED]")


@pytest.mark.parametrize("bad", ["", "1ABC", "TOOLONG1234", "st221", "ABC12345", "AB 123", "AB1234X9"])
def test_bad_flight_numbers_rejected(bad):
    _mod, c = new_contract()
    expect_user_error(lambda: call(c, flight_number=bad), "flight_number")


@pytest.mark.parametrize("good", ["VN123", "UA2459", "ST221", "BA2490K", "VJ88"])
def test_flight_number_shapes_accepted(good):
    _mod, c = new_contract()
    call(c, flight_id="f-" + good, flight_number=good)
    assert c.get_flight("f-" + good)["flight_number"] == good


@pytest.mark.parametrize("bad", ["sgn", "SG", "SGNN", "S1N", ""])
def test_bad_iata_codes_rejected(bad):
    _mod, c = new_contract()
    expect_user_error(lambda: call(c, departure_airport=bad), "IATA")
    expect_user_error(lambda: call(c, arrival_airport=bad), "IATA")


def test_same_airports_rejected():
    _mod, c = new_contract()
    expect_user_error(lambda: call(c, arrival_airport="SGN"), "must differ")


@pytest.mark.parametrize(
    "bad",
    [
        "2026-03-10",                    # no time
        "2026-03-10 08:30:00Z",          # space instead of T
        "2026-03-10T08:30",              # no timezone
        "2026-03-10T24:00:00Z",          # invalid hour
        "2026-02-30T08:30:00Z",          # invalid day
        "1999-03-10T08:30:00Z",          # out of supported range
        "2026-03-10T08:30:00+15:00",     # offset out of range
        "next friday",
    ],
)
def test_bad_scheduled_departure_rejected(bad):
    _mod, c = new_contract()
    expect_user_error(lambda: call(c, scheduled_departure=bad), "scheduled_departure")


@pytest.mark.parametrize("good", ["2026-03-10T08:30:00Z", "2026-03-10T19:30+11:00", "2026-03-10T01:30:00-07:00"])
def test_scheduled_departure_shapes_accepted(good):
    _mod, c = new_contract()
    fid = "good-start-" + str(abs(hash(good)) % 100000)
    call(c, flight_id=fid, scheduled_departure=good)


# ----------------------------------- URLs --------------------------------- #

@pytest.mark.parametrize(
    "bad",
    [
        "http://ops.skyline-air.aero/x",        # not https
        "https://localhost/x",
        "https://internal.corp/x",
        "ftp://ops.skyline-air.aero/x",
        "https:///no-host",
        "https://ops.skyline-air.aero/x y",     # whitespace
        "not a url",
        "https://singlelabel/x",
        "https://bad_tld.example/x",
        "x" * 400,
    ],
)
def test_bad_urls_rejected(bad):
    _mod, c = new_contract()
    expect_user_error(lambda: call(c, source_urls=[AIRLINE_URL, bad]), "[EXPECTED]")


def test_duplicate_url_rejected_case_insensitively():
    _mod, c = new_contract()
    expect_user_error(
        lambda: call(c, source_urls=[AIRLINE_URL, AIRLINE_URL.upper()], source_roles=["AIRLINE", "AIRLINE"]),
        "duplicate",
    )


# ---------------------------------- sources ------------------------------- #

@pytest.mark.parametrize("n", [0, 1, 5])
def test_source_count_enforced(n):
    _mod, c = new_contract()
    urls = [AIRLINE_URL, DEP_PORT_URL, TRACKER_URL, "https://a.civil-aviation.aero/x", "https://b.civil-aviation.aero/y"][:n]
    roles = (["AIRLINE", "DEPARTURE_AIRPORT", "TRACKER", "AUTHORITY", "OTHER"])[:n]
    expect_user_error(lambda: call(c, source_urls=urls, source_roles=roles), "sources are required")


def test_role_cardinality_mismatch():
    _mod, c = new_contract()
    expect_user_error(
        lambda: call(c, source_urls=[AIRLINE_URL, TRACKER_URL], source_roles=["AIRLINE"]),
        "one role per source",
    )


def test_unknown_role_rejected():
    _mod, c = new_contract()
    expect_user_error(
        lambda: call(c, source_urls=[AIRLINE_URL, TRACKER_URL], source_roles=["AIRLINE", "PILOT"]),
        "source role",
    )


def test_all_roles_accepted():
    _mod, c = new_contract()
    urls = ["https://s1.alpha.aero/a", "https://s2.beta.aero/b", "https://s3.gamma.aero/c", "https://s4.delta.aero/d"]
    roles = ["AIRLINE", "DEPARTURE_AIRPORT", "ARRIVAL_AIRPORT", "AUTHORITY"]
    call(c, source_urls=urls, source_roles=roles)
    assert c.get_flight(REGISTER_ARGS["flight_id"])["sources"][2]["role"] == "ARRIVAL_AIRPORT"


# -------------------------------- supersedes ------------------------------- #

def test_supersede_chain_by_owner():
    _mod, c = new_contract()
    register_standard(c)
    call(c, flight_id="st221-2026-03-10b", supersedes="st221-2026-03-10")
    flight = c.get_flight("st221-2026-03-10b")
    assert flight["supersedes"] == "st221-2026-03-10"


def test_supersede_requires_existing_target():
    _mod, c = new_contract()
    expect_user_error(lambda: call(c, flight_id="x2", supersedes="ghost"), "not found")


def test_supersede_only_by_original_registrant():
    _mod, c = new_contract()
    register_standard(c)
    set_sender(SECOND_SENDER)
    expect_user_error(lambda: call(c, flight_id="v2", supersedes=REGISTER_ARGS["flight_id"]), "only the original registrant")


def test_failed_registration_stores_nothing():
    _mod, c = new_contract()
    expect_user_error(lambda: call(c, carrier=""), "[EXPECTED]")
    assert c.get_flight(REGISTER_ARGS["flight_id"])["found"] is False
