# Studio call sheets — FlightAttest

Copy-paste these into GenLayer Studio (or adapt for the `genlayer` CLI). All ids follow
`[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}`.

## 0. Use the deployed instance (no deploy needed)

The contract is already live on GenLayer Studio:

| | |
| --- | --- |
| Address | `0xeE3d4EF77cf1176869Ca16116120e4C9fEB78f12` |
| Explorer | https://explorer-studio.genlayer.com/address/0xeE3d4EF77cf1176869Ca16116120e4C9fEB78f12 |
| Record | `deployments/studio.json` (integrity hash + schema) |

Open the explorer link (or load the address in Studio) to call the methods below directly
on the live instance. Before trusting it, verify
`sha256(contracts/flight_attest.py) == deployments/studio.json -> contract.source_sha256`.

## 1. Deploy your own copy (optional)

- New contract, paste `contracts/flight_attest.py`, **no constructor arguments**.
- Verify the schema: `register_flight` + `attest_flight` (write), `get_flight` +
  `get_attestation` + `get_flight_attestation_id` (view).
- Then record your deployment exactly like `deployments/studio.json` (address, network,
  `shasum -a 256` of the contract file).

## 2. Register a flight

```jsonc
// register_flight
{
  "flight_id": "st221-2026-03-10",
  "carrier": "Skyline Air",
  "flight_number": "ST221",
  "departure_airport": "SGN",
  "arrival_airport": "SFO",
  "scheduled_departure": "2026-03-10T08:30:00Z",
  "source_urls": [
    "https://your-host.example-aero.com/airline_delayed.html",
    "https://your-host.example-aero.com/airport_cancelled.html",
    "https://your-host.example-aero.com/tracker_on_schedule.html"
  ],
  "source_roles": ["AIRLINE", "DEPARTURE_AIRPORT", "TRACKER"],
  "supersedes": ""
}
```

Host the three static pages from `samples/pages/` anywhere (GitHub Pages, an S3 bucket,
`python3 -m http.server` behind a tunnel, ...). Their content is stable, which makes the
consensus run reproducible. With the three demo pages above, the airline page is DELAYED,
the airport page says CANCELLED and the tracker says ON_SCHEDULE — the airline controls, so
the expected outcome is `DELAYED` with `method = PRIORITY_RULE` and a `STATUS_MISMATCH`
conflict on S2 + S3. Edit the pages to walk through the scenarios in `scenarios.json`.

If you have real public pages for a real flight, use those instead (2–4 compact,
flight-specific HTTPS pages; airline own page first).

## 3. Attest

```jsonc
// attest_flight  (write — runs the full web+LLM consensus)
{ "flight_id": "st221-2026-03-10", "request_id": "demo-0001" }
```

Returns the final status string. `request_id` is single-use, pick a fresh one each run.

## 4. Read back

```jsonc
// get_attestation (view)
{ "request_id": "demo-0001" }

// get_flight (view)
{ "flight_id": "st221-2026-03-10" }

// get_flight_attestation_id (view)
{ "flight_id": "st221-2026-03-10", "index": 1 }
```

## 5. Things to try

- Point one URL at a 404: the next source controls and `fallback_used` becomes true.
- Re-register a corrected flight with `"supersedes": "st221-2026-03-10"` (same account).
- Repeat the attestation with a new `request_id` after editing a page: the new result
  replaces nothing — it is appended to the flight's history.
- Recompute `result_digest` offline using the recipe in `docs/TESTING.md` § Verifying.

## CLI quick reference (deployed instance)

```bash
ADDR=0xeE3d4EF77cf1176869Ca16116120e4C9fEB78f12
genlayer schema $ADDR                                  # expect 2 write + 3 view methods
genlayer call $ADDR get_flight --args '["st221-2026-03-10"]'
genlayer call $ADDR get_attestation --args '["demo-0001"]'
genlayer call $ADDR get_flight_attestation_id --args '["st221-2026-03-10", 1]'
```

## CLI quick reference (redeploy your own)

```bash
npm install -g genlayer
genlayer network set <network>
genlayer deploy                       # uses contracts/flight_attest.py
```
