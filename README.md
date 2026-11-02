# FlightAttest — Verifiable Flight Status Attestation

A standalone [Intelligent Contract](https://docs.genlayer.com/developers/intelligent-contracts)
for [GenLayer](https://www.genlayer.com) that attests the **status of a scheduled flight**
from **2–4 pre-registered, prioritized web sources**, resolves disagreements with an
explicit deterministic policy (`PRIORITY_V2`), and stores a compact result that anyone can
re-check without trusting a narrative summary.

It is a resolution primitive, not a flight search engine: it never discovers sources, never
guarantees a flight operates, and does not establish legal truth. It evaluates the evidence
in the registered sources under written rules. **Contract-only repository** — no frontend,
backend, token, payment, betting or admin power is part of it. Everything needed to build,
lint, test and submit the contract lives in this folder.

## How it works

1. **Register** a flight leg once: carrier, flight number, departure/arrival airports (IATA),
   a scheduled departure with an explicit timezone, and 2–4 flight-specific HTTPS source URLs
   with a declared role. **List order is the priority order and can never change.** A
   correction is a new registration (optionally marked `supersedes` the old one); old results
   keep the policy they were produced under.
2. **Attest**: anyone calls `attest_flight(flight_id, request_id)`. The contract fetches
   **only the frozen URLs**, extracts one finding per source (a separate fixed prompt per
   source, so one page can never influence another), applies the policy, and stores the full
   result under the caller's unique `request_id`.
3. **Read** the result by `request_id`.

### Status model

| Status | Meaning |
| --- | --- |
| ON_SCHEDULE | affirmative, current evidence the flight is operating as planned (absence of a cancellation, or a stale page showing the timetable, is *not* evidence) |
| DELAYED | explicitly departing later; the new departure is recorded **only as stated** (a missing timezone is preserved as `TZ_AMBIGUOUS`, never guessed; a "delay" that is not provably later than the schedule is rejected) |
| DIVERTED | explicitly landing at a different airport; the IATA diversion airport is recorded (it must differ from the registered endpoints) |
| CANCELLED | explicit cancellation. A 404, an empty page or silence is **not** a cancellation |
| LANDED | explicit evidence it arrived. A departure time in the past is not evidence |
| CONFLICTING_SOURCES | the controlling source contradicts itself and the policy cannot decide |
| INSUFFICIENT_EVIDENCE | nothing defensible can be concluded |
| FLIGHT_IDENTITY_UNCONFIRMED | every retrieved page failed to match the registered flight |

Every result also carries a derived `operation_state`: `LIVE` (ON_SCHEDULE / DELAYED /
DIVERTED), `CLOSED` (CANCELLED / LANDED) or `UNDETERMINED` (inconclusive). These are flight
findings, not transaction outcomes: a transaction can succeed with `INSUFFICIENT_EVIDENCE`,
and a failed or undetermined transaction means *no result yet*.

### Policy `PRIORITY_V2` (summary)

Per source the outcome is one of `RETRIEVAL_FAILED`, `IDENTITY_UNCONFIRMED`,
`NO_EXPLICIT_STATUS`, `AMBIGUOUS`, `CONTRADICTORY` or `VALID_CLAIM`. The first source (in
priority order) that is not *skippable* decides: a valid claim **controls** the result; an
ambiguous or contradictory source **blocks** it (no fallback past it). Failed, wrong-flight
or status-less sources are skipped — that is the documented fallback, and it is recorded
(`fallback_used`, role `SKIPPED`). Every disagreeing lower-priority claim is preserved in
`conflicts` as `STATUS_MISMATCH`, `TIMING_MISMATCH` (two different delayed departures),
`DIVERSION_MISMATCH` (two different diversion airports) or `INTERNAL_CONTRADICTION`.
Full rules and tables: [docs/DESIGN.md](docs/DESIGN.md).

### What makes consensus meaningful

Validators do **not** compare final statuses. Each validator independently re-fetches every
source, re-extracts every finding and must obtain the **same retrieval class and HTTP
status, identity decision, status claim, new-departure date/time/timezone and diversion
airport for every source**. The final status, operation state, method, controlling source,
conflicts, decision roles and digests are derived by pure code from those findings and
recomputed by every node. Evidence quotes may be worded differently by different nodes but
each must be a substantive literal quote of that validator's *own* copy of the page. There
is no score, tolerance or free-text field that can influence the outcome.

## Public interface

| Method | Kind | Arguments | Returns |
| --- | --- | --- | --- |
| `register_flight` | write | flight_id, carrier, flight_number, departure_airport, arrival_airport, scheduled_departure (ISO-8601 with explicit timezone), source_urls: list[str], source_roles: list[str], supersedes ("" for none) | - |
| `attest_flight` | write | flight_id, request_id | final status string |
| `get_flight` | view | flight_id | config incl. ordered sources and attestation_count (found: false if absent) |
| `get_attestation` | view | request_id | the complete stored result (found: false if absent) |
| `get_flight_attestation_id` | view | flight_id, index (1-based) | request_id of the N-th attestation or "" |

Ids are `[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}`. Flight numbers are a 2–3 character airline
designator plus 1–4 digits (optional trailing letter), airports are uppercase IATA codes.
Source roles are `AIRLINE, DEPARTURE_AIRPORT, ARRIVAL_AIRPORT, TRACKER, AUTHORITY, OTHER`
and are **declared metadata only**: the contract cannot prove a URL belongs to the named
airline. Failed calls revert with a message prefix: `[EXPECTED]` (invalid call) or
`[LLM_ERROR]` (unusable model output).

An attestation contains: `request_id`, `flight_id`, `requester`, `attested_at`
(deterministic transaction time), `policy_version`, `final_status`, `operation_state`,
`method` (`AGREEMENT`/`PRIORITY_RULE`/`NONE`), `reason`, `controlling_source`,
`fallback_used`, `new_dep_*` (+ `new_dep_precision`), `diversion_airport`, `findings[]`
(per source: retrieval class, HTTP status, outcome, claim, new-departure parts, diversion
airport, short verbatim excerpt, evidence digest, decision role), `conflicts[]` and
`result_digest`. Digests are SHA-256 over a canonical JSON of the *normalized* evidence
record — they identify what was stored, not the raw page bytes (live pages differ between
requests, so a raw-byte hash cannot be consensus-checked).

`request_id` is an application key chosen by the caller (global, used once). It is **not**
the blockchain transaction id, which the contract never reads.

## Live deployment (GenLayer Studio)

| | |
| --- | --- |
| Network | GenLayer Studio |
| Address | `0xeE3d4EF77cf1176869Ca16116120e4C9fEB78f12` |
| Explorer | https://explorer-studio.genlayer.com/address/0xeE3d4EF77cf1176869Ca16116120e4C9fEB78f12 |
| Canonical record | [`deployments/studio.json`](deployments/studio.json) (network, address, schema, integrity hash) |

Verify that the deployed bytecode really comes from this repository before interacting:

```bash
shasum -a 256 contracts/flight_attest.py
# must equal the "source_sha256" field in deployments/studio.json
genlayer schema 0xeE3d4EF77cf1176869Ca16116120e4C9fEB78f12
# must list exactly: register_flight, attest_flight (write)
#                    get_flight, get_attestation, get_flight_attestation_id (view)
```

Read-only smoke check against the live instance:

```bash
genlayer call 0xeE3d4EF77cf1176869Ca16116120e4C9fEB78f12 \
  get_flight --args '["st221-2026-03-10"]'
# unknown id -> {"found": false, "flight_id": "st221-2026-03-10"}
```

## Getting started

Requirements: Python 3.10+ and internet access (GenVM runners download on first use).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

genvm-lint check contracts/flight_attest.py   # lint + schema validation (optional)
pytest tests/direct -q                         # direct-mode suite (mock GenVM)
```

### Interacting with the deployed instance (or redeploying)

1. The contract is already live on Studio — open
   https://explorer-studio.genlayer.com/address/0xeE3d4EF77cf1176869Ca16116120e4C9fEB78f12
   or load the address in Studio to call it directly. To redeploy your own copy instead:
   paste `contracts/flight_attest.py` into a new Studio contract (no constructor
   arguments), or use the CLI: `npm install -g genlayer`, then `genlayer network set
   <network>` and `genlayer deploy` (see `genlayer deploy --help`).
2. Confirm the schema (`genlayer schema <address>`): 2 write methods, 3 view methods.
3. Call `register_flight` with 2–4 real, compact, flight-specific HTTPS pages, then
   `attest_flight` with a fresh `request_id`, then `get_attestation`. Copy-paste call
   sheets (prefilled with the deployed address) and static demo pages:
   [samples/studio_calls.md](samples/studio_calls.md).
4. Automated checks: `gltest tests/integration/test_flight_attest.py -v -s`, and
   optionally with real pages
   `FLIGHTATTEST_LIVE_URLS='["https://...","https://..."]' gltest tests/integration/test_flight_attest.py -k live -s`.
5. Studio's web access depends on its own browser service; validate production web reads
   on the target network.

## Repository layout

```
contracts/flight_attest.py      The contract (single file, no constructor args)
deployments/studio.json         Canonical deployment record (address, schema, sha256)
tests/direct/                   Direct-mode tests: mock GenVM, scripted pages,
                                rule-based model oracle, 4 consensus nodes
tests/integration/              Studio / gltest consensus tests
samples/pages/                  Static demo pages for manual Studio runs
samples/scenarios.json          Scripted scenario matrix -> expected outcomes
samples/studio_calls.md         Copy-paste call sheets for the deployed Studio instance
docs/DESIGN.md                  Status model, policy, evidence, consensus, storage
docs/TESTING.md                 Test layers, commands, how the mock harness works
README.md                       This file
```

## Known limitations (short list)

- If every node's model is fooled the same way (for example by an injection) and cites a
  real sentence of the page, the result is consistent and is accepted. Defenses (per-source
  isolation, delimiters, fixed schema, literal-quote and independent re-derivation
  requirements) raise the cost; they are not immunity.
- A quote proves *presence* in the page, not that it *supports* the claim; that judgement
  is the models'.
- Raw HTML is evaluated: JavaScript-rendered content is invisible and CSS-hidden text is
  not detected. Prefer official, compact, machine-readable pages.
- If a higher-priority source is unreachable *for validators*, a lower-priority source may
  control. This is always flagged (`fallback_used`, role `SKIPPED`); consumers should weigh
  such results accordingly.
- Flight and request ids are first-come, there is no fee or rate limit, and each
  attestation costs validators web and LLM calls.
- Up to 240 characters per source of page text are stored publicly (the excerpt).

The complete discussion is in [docs/DESIGN.md](docs/DESIGN.md).
