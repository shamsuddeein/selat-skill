---
name: api-failure-monitor
description: Use this skill when the user wants to probe an x402 or MPP endpoint for health before spending USDC on a multi-step pipeline — e.g. "check if the endpoint is up before paying", "probe the API before running the skill", "is the payment endpoint healthy?", "circuit-break the pipeline if the upstream is down", "pre-flight check before launching the enrichment run". Uses selat-pay --probe-only to obtain authentic 402 signals, live prices, rails, and latency without settling a payment. Defaults to UNHEALTHY / HALT_AND_REROUTE on any unexpected result.
license: Apache-2.0
compatibility: Requires Python 3.11+ and selat-pay >= 0.3.1 on PATH. No API keys or wallet balance needed — probing is free.
metadata:
  author: deen
  version: "1.1"
  rail: none
  kind: script
---

# api-failure-monitor

Runtime circuit-breaker preflight for x402 and MPP endpoints. Probes a target
URL through `selat-pay --probe-only` — the same mechanism the real payment
would use — so a genuine 402 payment challenge is the health signal. Any other
outcome opens the circuit breaker and the pipeline halts before spending USDC.

## When To Use

Run this skill before any multi-step paid pipeline when you want to verify the
upstream is alive and returning a valid payment challenge. Use it when:

- you are about to run an expensive enrichment or data-sourcing skill;
- an endpoint has been flaky or is known to return stale CORS preflights;
- you want latency and live price telemetry before committing a budget;
- a circuit-breaker policy is required before a treasury spend.

Do **not** use raw `OPTIONS` or `HEAD` requests to check x402 health — they
return 204 CORS preflights on any CORS-enabled host, including hosts that
would 404 on a real paid call.

## Workflow

1. Install: `selat skill install api-failure-monitor`
2. Run the probe script directly:

```bash
python3 scripts/probe_check.py \
  --url https://serpapi.mpp.tempo.xyz/search \
  --method GET \
  --chain base
```

3. Check the exit code and JSON output:
   - Exit `0` + `"status": "HEALTHY"` → circuit breaker `CLOSED` → proceed.
   - Exit `1` + `"status": "UNHEALTHY"` → circuit breaker `OPEN` → halt and reroute.

4. For a POST endpoint with a body:

```bash
python3 scripts/probe_check.py \
  --url https://hunter.mpp.paywithlocus.com/hunter/email-finder \
  --method POST \
  --body '{"domain":"stripe.com","first_name":"John","last_name":"Doe"}' \
  --chain base
```

## Available Scripts

| Script | Purpose |
|---|---|
| `scripts/probe_check.py` | Probe engine — shells out to `selat-pay --probe-only` and emits a JSON circuit-breaker decision |

Run `python3 scripts/probe_check.py --help` for full flag reference.

## Inputs And Outputs

| Flag | Required | Default | Description |
|---|---|---|---|
| `--url` | yes | — | x402/MPP endpoint URL (must be `https://`) |
| `--method` | no | `GET` | HTTP method forwarded to `selat-pay` |
| `--body` | no | — | JSON body string for POST probes |
| `--chain` | no | `base` | Chain flag for `selat-pay` (probe is chain-independent and free) |
| `--timeout` | no | `15` | Probe timeout in seconds |
| `--dry-run` | no | — | Print the command that would run; no network call |
| `--json` | no | — | Emit machine-readable JSON instead of human-readable output |

**Output fields:**

| Field | Values | Description |
|---|---|---|
| `status` | `HEALTHY` / `UNHEALTHY` | Endpoint health |
| `circuit_breaker` | `CLOSED` / `OPEN` | Circuit state |
| `action` | `PROCEED` / `HALT_AND_REROUTE` | Recommended pipeline action |
| `http_code` | integer or null | HTTP status code returned by probe |
| `price` | string or null | Live price quoted by the endpoint |
| `rail` | string or null | Payment rail (x402, routed, etc.) |
| `latency_ms` | integer or null | Round-trip probe latency |
| `error` | string or null | Error detail when unhealthy |

## Gotchas

- **OPTIONS probes are wrong for x402.** A `curl -X OPTIONS` returns `204`
  on any CORS-enabled host, including dead endpoints. `selat-pay --probe-only`
  uses the real request method and sees the real 402 challenge.
- **Fail closed.** Any response that is not HTTP 402 opens the circuit breaker:
  200, 204, 404, 500, timeouts, binary/unparseable output, and missing binaries
  all produce `UNHEALTHY` / `HALT_AND_REROUTE`.
- **`--chain` is required by selat-pay but the probe is free.** Passing
  `--chain base` satisfies the CLI flag; no settlement occurs on any chain.
- **Probing is not a payment.** `selat-pay --probe-only` reads a free,
  non-settling 402 quote. It does not consume wallet balance.

## Validation

Static check:

```bash
selat skill validate ./skills/api-failure-monitor
```

Script self-test (no network):

```bash
python3 scripts/probe_check.py --help
python3 scripts/probe_check.py \
  --url https://serpapi.mpp.tempo.xyz/search --dry-run
```

Live free probe (no payment settled):

```bash
python3 scripts/probe_check.py \
  --url https://serpapi.mpp.tempo.xyz/search \
  --chain base --json
```

A healthy endpoint prints exit `0` and:

```json
{
  "status": "HEALTHY",
  "circuit_breaker": "CLOSED",
  "action": "PROCEED",
  "http_code": 402,
  "price": "0.01",
  "rail": "x402",
  "latency_ms": 87,
  "error": null
}
```

## References

- `manifest.json` — skill metadata (no payment steps; this skill is script-only).
- [`scripts/probe_check.py`](scripts/probe_check.py) — probe engine source.
- [`references/endpoints.md`](references/endpoints.md) — known x402/MPP endpoints and decision matrix.
- [`../../references/agent-skill-authoring-sop.md`](../../references/agent-skill-authoring-sop.md) — authoring standard.
- selat-pay — https://github.com/SELAT-AI/selat-pay
