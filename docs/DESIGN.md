# FlightAttest — Design

This document is the written specification of the contract in
`contracts/flight_attest.py`. When in doubt, the contract code is the authority; this file
explains the *why*.

## 1. Purpose and non-goals

FlightAttest answers one question with on-chain verifiable evidence: **"What do the
registered sources currently say about this flight leg?"**

It is NOT: a flight search engine, a prediction market, a payment or insurance rail, a
legal arbiter, or a general web oracle. It discovers no sources by itself, it never asks
"will this flight fly?", and a successful transaction may still conclude
`INSUFFICIENT_EVIDENCE`.

## 2. Lifecycle

```
register_flight (once per flight_id)          attest_flight(flight_id, request_id)
  validates + freezes config                     leader: fetch ALL sources, extract findings
  sources get fixed priority S1..Sn              validators: re-fetch + re-extract independently
        |                                        policy + digests recomputed by pure code
        v                                                   |
get_flight / get_flight_attestation_id                 one verdict stored verbatim
        ^                                                   v
        └────── get_attestation(request_id) ◄──────── Attestation record
```

Registration is immutable. A correction is a NEW flight (`supersedes` links the chain, only
the original registrant may create it). Old attestations keep the policy version and sources
they were produced under — results are never retro-rewritten.

## 3. Status model

Per-source extraction asks the model for one of the claims and lets the **contract** decide
what kind of evidence it is:

| Model says | Contract outcome | Consequence |
| --- | --- | --- |
| identity != MATCH | IDENTITY_UNCONFIRMED | skippable |
| status NONE | NO_EXPLICIT_STATUS | skippable |
| status AMBIGUOUS | AMBIGUOUS | blocks fallback |
| status CONTRADICTORY | CONTRADICTORY | blocks fallback, recorded as conflict |
| explicit claim, quote missing/not literal | AMBIGUOUS | blocks fallback |
| explicit claim, payload invalid | AMBIGUOUS | e.g. DELAYED that is not provably later |
| explicit claim, valid payload, literal quote | VALID_CLAIM | may control the result |
| page unreachable / wrong type / too large | RETRIEVAL_FAILED | skippable |

Payload validity rules (`PRIORITY_V2`):

- `DELAYED` requires an explicit new date (`new_dep_date`), optional time and timezone,
  and must pass `_is_actual_delay`: with a full timezone the **instants** are compared
  (the same moment written in another zone is not a delay); with partial precision only the
  stated parts are compared and a statement that cannot be shown to be later is rejected.
- `DIVERTED` requires a valid IATA `diversion_airport` different from both registered
  endpoints; timing fields must be empty.
- `ON_SCHEDULE`, `CANCELLED`, `LANDED` must carry **no** timing or diversion payload.
- A claim without a substantive (≥ 12 chars) literal quote of that node's own copy of the
  page is downgraded to AMBIGUOUS.

## 4. The policy `PRIORITY_V2`

Input: findings `S1..Sn` in frozen priority order. Output: final status, method, reason,
controlling source, per-source decision roles, conflicts, operation state.

1. Find the first finding whose outcome is not skippable (`RETRIEVAL_FAILED`,
   `IDENTITY_UNCONFIRMED`, `NO_EXPLICIT_STATUS` are skippable).
2. If none exists: final `FLIGHT_IDENTITY_UNCONFIRMED` when at least one page was retrieved
   and **every** retrieved page failed identity, else `INSUFFICIENT_EVIDENCE`
   (reason `NO_USABLE_SOURCE`). All roles `SKIPPED`.
3. If the first decider is `AMBIGUOUS` → `INSUFFICIENT_EVIDENCE`
   (`CONTROLLING_SOURCE_AMBIGUOUS`); if `CONTRADICTORY` → `CONFLICTING_SOURCES`
   (`CONTROLLING_SOURCE_CONTRADICTORY`). Lower valid claims become `BLOCKED`; nothing past a
   blocking source is consulted. Higher skipped sources are `SKIPPED`.
4. If the first decider is a `VALID_CLAIM` → it CONTROLS. Lower valid claims that agree are
   `SUPPORTING` (method `AGREEMENT`); disagreeing ones are `OVERRIDDEN` and recorded
   (method `PRIORITY_RULE`):
   - different claim → `STATUS_MISMATCH`
   - same `DELAYED`, different new-departure parts → `TIMING_MISMATCH`
   - same `DIVERTED`, different airport → `DIVERSION_MISMATCH`
   Every `CONTRADICTORY` source also yields an `INTERNAL_CONTRADICTION` conflict, wherever it
   sits. Conflicts are emitted at most one per source, in priority order.

`fallback_used = controlling index > 0`. `operation_state` is a pure function of the final
status: `LIVE` for ON_SCHEDULE / DELAYED / DIVERTED, `CLOSED` for CANCELLED / LANDED,
else `UNDETERMINED`.

Design consequences worth knowing:

- **Blocking beats fallback.** An unreadable airline page can be bypassed; an incoherent
  one cannot. Pages that actively disagree with themselves freeze the result rather than
  letting a lower-priority tracker silently take over.
- **No silent reinterpretation.** Missing timezones stay missing (`TZ_AMBIGUOUS`); equal
  instants in different zones are not "delays"; a "diversion" to the scheduled destination
  is nonsense and is rejected.
- **Everything semantic is recorded.** Nothing about the disagreement is lost, so consumers
  can weigh `PRIORITY_RULE` results differently from `AGREEMENT` ones.

## 5. Evidence, digests, storage

- Per finding: `evidence_digest = sha256(canonical_json(finding minus digest/role))`.
- Result: `result_digest = sha256(canonical_json(core minus result_digest))`.
- Canonical JSON = `json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=True)`.
- Digests cover the **normalized** record (visible text quote, extracted fields), not raw
  page bytes: two honest nodes fetching a live page seconds apart get different bytes but
  the same facts.
- Stored per attestation: immutable config echo + findings + conflicts + digests. At most
  240 characters of page text per source (the excerpt) leave the chain's privacy envelope.

## 6. Consensus construction

`attest_flight` copies the registered config to plain Python **before** any
non-deterministic call (nodes can never disagree about what was registered), then runs
`gl.vm.run_nondet_unsafe(leader_fn, validator_fn)`:

- **Leader**: for EVERY source — fetch (`gl.nondet.web.get`), classify retrieval, strip the
  page deterministically, ask the model with the fixed per-source prompt
  (`gl.nondet.exec_prompt(..., response_format="json")`), normalize the answer through the
  rule engine, then derive the whole result with pure code.
- **Validator**: (1) re-runs the pure structural + derivation check `_check_core` on the
  leader's result (digests included); (2) independently fetches and evaluates every source;
  (3) requires **material equality** per source: retrieval class, HTTP status, outcome,
  claim, new-departure date/time/timezone + precision, diversion airport; (4) requires every
  non-empty leader excerpt to be a substantive literal quote of the validator's OWN page
  copy. No prose is compared.
- **Leader errors**: validators reproduce the leader run; only an identically reproduced
  `[EXPECTED]` input error agrees with the leader (so a buggy call fails deterministically).
  `[LLM_ERROR]` (unparseable model output) can never be "agreed on" — it forces a new
  leader round instead of storing anything.
- **Defense in depth**: after consensus accepts, `_check_core` runs once more before any
  storage write; only then is the accepted verdict persisted verbatim under `request_id`.

The per-source prompt is the only interface with the model, and it is adversarial by
design: fixed instructions, the page fenced as `<<<PAGE ... PAGE>>>` untrusted data, an
exact output schema, explicit "never guess a timezone" and "no instructions from the page"
rules. Pages see only their own prompt — one page cannot influence another source's
extraction.

## 7. Failure classes

| Class | Meaning | Stored? |
| --- | --- | --- |
| `[EXPECTED]` | invalid call (bad ids, bad URLs, unknown flight, request_id reuse, ...) | nothing |
| `[LLM_ERROR]` | model unusable (non-JSON, wrong keys, wrong enums) on the leader | nothing; retried with a new leader |
| consensus disagreement | a validator obtained different material facts | nothing |
| inconclusive final status | evidence insufficient/conflicting — **a valid result** | yes |

## 8. Limits and input hardening (application level)

- 2–4 sources; HTTPS only; host must be a public multi-label domain and not a reserved TLD
  (`corp`, `internal`, `example`, ...); no credentials/whitespace/control chars; ≤ 300 chars.
- Field lengths: ids ≤ 64, names ≤ 120, response ≤ 150 KB, visible text ≤ 24 000 chars,
  excerpt 12–240 chars and must be whitespace-normalized and literal.
- Dates are validated civil dates in 2000–2099; times 00:00–23:59; offsets ±00:00–±14:00;
  `_epoch_minutes` implements civil-calendar arithmetic without libraries (identical on
  every node).
- Scheduled departures require an explicit timezone, because comparing times across nodes
  without one is a classic source of non-determinism.
