"""Integration flow for GenLayer Studio / gltest (real consensus, mocked or
live web access depending on the Studio browser service).

Run inside Studio or with the gltest runner:

    gltest tests/integration/test_flight_attest.py -v -s

Optionally point at real pages (they must be stable HTTPS pages describing
the same flight):

    FLIGHTATTEST_LIVE_URLS='["https://airline...","https://airport..."]' \
        gltest tests/integration/test_flight_attest.py -k live -v -s

These tests exercise real multi-validator consensus. The deterministic core
(policy, digests, validation) is covered by tests/direct with strict mocks.

A reference instance is live on GenLayer Studio at
0xeE3d4EF77cf1176869Ca16116120e4C9fEB78f12 (see deployments/studio.json for the
integrity hash and expected schema). The tests below deploy their own copy; to
sanity-check the deployed one instead, run the Layer-0 read-only commands in
docs/TESTING.md.
"""

import json
import os

import pytest

gltest = pytest.importorskip("gltest", reason="gltest runner only available inside GenLayer tooling")

from gltest import get_contract_factory  # noqa: E402
from gltest.assertions import tx_execution_succeeded  # noqa: E402

FLIGHT_ID = "st221-2026-03-10"
REQUEST_ID = "studio-demo-0001"

DEMO_ARGS = {
    "flight_id": FLIGHT_ID,
    "carrier": "Skyline Air",
    "flight_number": "ST221",
    "departure_airport": "SGN",
    "arrival_airport": "SFO",
    "scheduled_departure": "2026-03-10T08:30:00Z",
    "source_urls": [
        "https://ops.skyline-air.aero/flights/ST221",
        "https://status.sgn-airport.aero/departures/ST221",
        "https://track.flightradar.io/live/ST221",
    ],
    "source_roles": ["AIRLINE", "DEPARTURE_AIRPORT", "TRACKER"],
    "supersedes": "",
}


def _factory():
    return get_contract_factory("FlightAttest")


def test_register_and_query():
    contract = _factory().deploy()
    assert tx_execution_succeeded(contract.register_flight(**DEMO_ARGS))
    flight = contract.get_flight(FLIGHT_ID)
    assert flight["found"] is True
    assert flight["policy_version"] == "PRIORITY_V2"
    assert len(flight["sources"]) == 3
    assert flight["attestation_count"] == 0


def test_register_rejects_bad_airport_pair():
    contract = _factory().deploy()
    args = dict(DEMO_ARGS)
    args["arrival_airport"] = "SGN"
    with pytest.raises(Exception):
        contract.register_flight(**args)


@pytest.mark.skipif(
    os.environ.get("FLIGHTATTEST_LIVE_URLS") is None,
    reason="live mode: set FLIGHTATTEST_LIVE_URLS to a JSON list of 2-4 real pages",
)
def test_live_attestation_roundtrip():
    urls = json.loads(os.environ["FLIGHTATTEST_LIVE_URLS"])
    assert len(urls) >= 2
    contract = _factory().deploy()
    args = dict(DEMO_ARGS)
    args["flight_id"] = "live-demo"
    args["source_urls"] = urls
    args["source_roles"] = ["AIRLINE", "OTHER", "OTHER", "OTHER"][: len(urls)]
    assert tx_execution_succeeded(contract.register_flight(**args))
    status = contract.attest_flight("live-demo", "live-req-0001")
    assert status in (
        "ON_SCHEDULE", "DELAYED", "DIVERTED", "CANCELLED", "LANDED",
        "CONFLICTING_SOURCES", "INSUFFICIENT_EVIDENCE", "FLIGHT_IDENTITY_UNCONFIRMED",
    )
    result = contract.get_attestation("live-req-0001")
    assert result["found"] is True
    assert result["policy_version"] == "PRIORITY_V2"
    assert len(result["result_digest"]) == 64
    assert len(result["findings"]) == len(urls)
    assert contract.get_flight_attestation_id("live-demo", 1) == "live-req-0001"
