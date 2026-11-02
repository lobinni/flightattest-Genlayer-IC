# FlightAttest — Testing guide

Four layers, ordered by how much infrastructure they need. Layer 0 needs nothing but a
browser or the CLI; layer 1 runs anywhere with Python; layer 2 needs the GenVM tooling;
layer 3 needs GenLayer Studio.

## Layer 0 — the deployed Studio instance (read-only sanity)

The contract is live on GenLayer Studio at
`0xeE3d4EF77cf1176869Ca16116120e4C9fEB78f12`
([explorer](https://explorer-studio.genlayer.com/address/0xeE3d4EF77cf1176869Ca16116120e4C9fEB78f12);
canonical record: `deployments/studio.json`).

```bash
# 1. source integrity: the deployed contract must hash to the recorded digest
shasum -a 256 contracts/flight_attest.py   # == deployments/studio.json contract.source_sha256

# 2. schema: exactly 2 write + 3 view methods
genlayer schema 0xeE3d4EF77cf1176869Ca16116120e4C9fEB78f12

# 3. read-only call: an unknown flight id must answer {"found": false, ...}
genlayer call 0xeE3d4EF77cf1176869Ca16116120e4C9fEB78f12 \
  get_flight --args '["definitely-not-registered"]'
```

A write dry-run is covered by the call sheets in `samples/studio_calls.md`
(section "Attest against the deployed instance").

## Layer 1 — direct-mode suite (no chain, deterministic)

```bash
pip install -r requirements.txt
pytest tests/direct -q          # ~190 tests, runs in under a second
```

What it covers:

| File | Scope |
| --- | --- |
| `tests/direct/test_registration.py` | every registration validation rule, supersedes ACL, atomicity (a rejected call stores nothing) |
| `tests/direct/test_page_text.py` | the documented HTML -> visible text procedure, incl. unterminated blocks, entities, zero-width chars |
| `tests/direct/test_policy.py` | the PRIORITY_V2 truth table (control / fallback / blocking / conflicts / roles), digests, `_check_core` tamper detection, `_epoch_minutes` cross-checked against the stdlib as an independent oracle |
| `tests/direct/test_resolution.py` | full leader -> 3 validators consensus simulation: agreement, divergence, quote spoofing, injection immunity, retrieval classification, storage and views |

### How the harness works (`tests/direct/flightattest_support.py`)

- The real contract file is loaded with a **fake `genlayer` module**: `TreeMap`/`DynArray`/
  `Address`/`u32` shims, lazy storage, `gl.message`, `gl.message_raw`.
- `gl.nondet.web.get` is a scripted registry per node. Four consensus nodes exist
  (`leader`, `v1`, `v2`, `v3`) and `run_nondet_unsafe` actually executes the leader closure,
  then the validator closure on each validator with its **own** registry — pages can be
  diverged per node to prove consensus rejects them.
- `gl.nondet.exec_prompt` is a deterministic rule-based oracle: test pages embed a machine
  readable line `FA_OBS{"identity":"MATCH","status":"DELAYED",...}` whose values the oracle
  returns, mirroring what a real model should answer for that page. Excerpts are referenced
  by key (`"excerpt":"@DELAYED"`) and resolve to natural sentences placed in the page body,
  so the contract's literal-quote check stays meaningful.
- `llm_mode` can be switched per node to `raw_string` / `fenced` (string JSON variants the
  contract must still parse), `trash` (non-JSON -> `[LLM_ERROR]`) or `raise` (call failure),
  covering the failure semantics without an actual model.

Because the oracle is independent of the contract's prompt, the suite validates the whole
pipeline — retrieval classification, extraction, policy, consensus comparison, digests,
storage — end to end, deterministically.

## Layer 2 — lint + Studio-style consensus

```bash
genvm-lint check contracts/flight_attest.py    # schema validation, GenVM restrictions
gltest tests/integration/test_flight_attest.py -v -s
FLIGHTATTEST_LIVE_URLS='["https://a","https://b"]' \
  gltest tests/integration/test_flight_attest.py -k live -v -s
```

## Layer 3 — manual Studio run

Follow `samples/studio_calls.md`: it contains a copy-paste `register_flight` call, the
`attest_flight` call, the reads, and three static demo pages in `samples/pages/` you can
host anywhere to make the web fetches deterministic.

## Scenario matrix

`samples/scenarios.json` maps page configurations to expected outcomes:

| Scenario | Expected |
| --- | --- |
| all_sources_agree_on_schedule | ON_SCHEDULE / AGREEMENT / LIVE |
| airline_down_tracker_says_delayed | DELAYED via S2, fallback_used, FULL precision |
| tracker_disagrees_priority_rule_wins | ON_SCHEDULE / PRIORITY_RULE + STATUS_MISMATCH |
| airline_page_ambiguous_blocks_everything | INSUFFICIENT_EVIDENCE, blocking roles |
| all_pages_about_another_flight | FLIGHT_IDENTITY_UNCONFIRMED |
| everything_down | INSUFFICIENT_EVIDENCE / NO_USABLE_SOURCE |
| diversion_recorded_with_airport | DIVERTED to OAK, LIVE |

Each scenario in the JSON names the per-source page disposition; the direct-mode harness
replays the same dispositions, and the Studio pages replicate them with natural HTML.

## Verifying results by hand

Any stored attestation can be re-verified offline:

1. `get_attestation(request_id)` -> take every field except `result_digest`.
2. `canonical = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`.
3. `sha256(canonical.encode()).hexdigest()` must equal `result_digest`.
4. Each `findings[i].evidence_digest` is the same construction over the finding minus
   `evidence_digest`/`decision_role`.
