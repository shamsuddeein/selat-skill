# api-failure-monitor

> **Runtime circuit-breaker preflight for x402 and MPP endpoints.**
> Probes a target URL through `selat-pay --probe-only` — the same path a real
> payment would take — so a genuine HTTP 402 payment challenge is the health
> signal. Any other outcome opens the circuit breaker and the pipeline halts
> before spending USDC.

---

## Table of Contents

- [Why This Exists](#why-this-exists)
- [How It Works](#how-it-works)
- [Requirements](#requirements)
- [Install](#install)
- [Quick Start](#quick-start)
- [CLI Reference](#cli-reference)
- [Output Schema](#output-schema)
- [Circuit-Breaker Decision Matrix](#circuit-breaker-decision-matrix)
- [Exit Codes](#exit-codes)
- [Examples](#examples)
- [Evals](#evals)
- [Files](#files)
- [Known Probe-Verified Endpoints](#known-probe-verified-endpoints)
- [FAQ](#faq)
- [License](#license)

---

## Why This Exists

Every paid skill in `selat-skills` burns USDC the moment it starts calling
endpoints. If an upstream is down, rate-limited, or has drifted to a dead
path, a multi-step pipeline will **pay for every failure** before giving up.

`api-failure-monitor` addresses this with a free pre-flight probe:

```
+-------------------------+
|   api-failure-monitor   |  <- probe (free, non-settling)
|   selat-pay --probe-only|
+------------+------------+
             |
    +--------+--------+
    |                 |
  402 OK           anything else
  PROCEED          HALT_AND_REROUTE
    |                 |
    v                 v
  Run paid         Abort pipeline
  pipeline         (circuit breaker OPEN)
```

The probe is **free** — `selat-pay --probe-only` reads the 402 challenge but
never signs or settles a payment, so no wallet balance is consumed.

---

## How It Works

The old approach used raw `urllib` with an `OPTIONS` request and treated any
`2xx` as healthy. This is **wrong for x402**:

```bash
# OPTIONS returns 204 on any CORS-enabled host, including dead endpoints:
$ curl -X OPTIONS -o /dev/null -w '%{http_code}' https://serpapi.mpp.tempo.xyz/search
204

# Only the real method shows the payment challenge:
$ curl -o /dev/null -w '%{http_code}' https://serpapi.mpp.tempo.xyz/search
402
```

`probe_check.py` shells out to `selat-pay --probe-only`, which uses the real
HTTP method and sees the real 402 challenge. Only HTTP `402` is healthy. Every
other code — `200`, `204`, `404`, `429`, `5xx`, timeout, missing binary, or
unparseable output — opens the circuit breaker immediately.

---

## Requirements

| Dependency  | Version  | Notes                                               |
|-------------|----------|-----------------------------------------------------|
| Python      | 3.11+    | Standard library only — no `pip install` needed     |
| `selat-pay` | >= 0.3.1 | Must be on `PATH` — verify with `selat-pay --version` |

No API keys, no wallet balance, no funded account — probing is always free.

---

## Install

```bash
selat skill install api-failure-monitor
```

Or run the script directly from a clone:

```bash
git clone https://github.com/SELAT-AI/selat-skills.git
cd selat-skills
python3 skills/api-failure-monitor/scripts/probe_check.py --help
```

---

## Quick Start

**Probe a GET endpoint:**

```bash
python3 scripts/probe_check.py \
  --url https://serpapi.mpp.tempo.xyz/search
```

**Probe a POST endpoint with a JSON body:**

```bash
python3 scripts/probe_check.py \
  --url https://hunter.mpp.paywithlocus.com/hunter/email-finder \
  --method POST \
  --body '{"domain":"stripe.com","first_name":"John","last_name":"Doe"}'
```

**Machine-readable JSON output:**

```bash
python3 scripts/probe_check.py \
  --url https://serpapi.mpp.tempo.xyz/search \
  --json
```

**Dry-run — print the command without making a network call:**

```bash
python3 scripts/probe_check.py \
  --url https://serpapi.mpp.tempo.xyz/search \
  --dry-run
```

---

## CLI Reference

```
usage: probe_check.py [-h] --url URL [--method {GET,POST,PUT,PATCH,DELETE}]
                      [--body BODY] [--chain CHAIN] [--timeout TIMEOUT]
                      [--dry-run] [--json]
```

| Flag        | Required | Default | Description                                                                                             |
|-------------|----------|---------|---------------------------------------------------------------------------------------------------------|
| `--url`     | **yes**  | —       | x402/MPP endpoint to probe. Must start with `https://`.                                                 |
| `--method`  | no       | `GET`   | HTTP method forwarded to `selat-pay --probe-only`.                                                      |
| `--body`    | no       | —       | JSON body string for POST probes, e.g. `'{"key":"val"}'`.                                               |
| `--chain`   | no       | `base`  | Chain flag for `selat-pay`. Probe is chain-independent and free; satisfies `--chain` but never settles. |
| `--timeout` | no       | `15`    | Probe timeout in seconds. Triggers `HALT_AND_REROUTE` on expiry.                                        |
| `--dry-run` | no       | —       | Print the `selat-pay` command; exit 0 without any network call.                                         |
| `--json`    | no       | —       | Emit machine-readable JSON to stdout instead of human-readable text.                                    |

---

## Output Schema

### Human-readable (default)

**Healthy:**

```
check  status=HEALTHY  circuit_breaker=CLOSED  action=PROCEED
   http_code=402
   price=0.01
   rail=x402
   latency_ms=87
```

**Unhealthy:**

```
error  status=UNHEALTHY  circuit_breaker=OPEN  action=HALT_AND_REROUTE
   http_code=404
   error: expected HTTP 402, got 404
```

### JSON (`--json`)

| Field            | Type            | Values                                | Description                                          |
|------------------|-----------------|---------------------------------------|------------------------------------------------------|
| `status`         | string          | `HEALTHY` / `UNHEALTHY` / `DRY_RUN`  | Overall endpoint health.                             |
| `circuit_breaker`| string          | `CLOSED` / `OPEN` / `N/A`            | Circuit breaker state.                               |
| `action`         | string          | `PROCEED` / `HALT_AND_REROUTE` / `N/A` | Recommended pipeline action.                       |
| `http_code`      | integer or null | —                                     | HTTP status returned by `selat-pay --probe-only`.    |
| `price`          | string or null  | e.g. `"0.01"`                         | Live USDC price from the endpoint (healthy only).    |
| `rail`           | string or null  | `x402`, `routed`, etc.                | Payment rail detected (healthy only).                |
| `latency_ms`     | integer or null | —                                     | Round-trip probe latency in milliseconds.            |
| `error`          | string or null  | —                                     | Error detail when unhealthy; `null` when healthy.    |

**Healthy JSON:**

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

**Unhealthy JSON:**

```json
{
  "status": "UNHEALTHY",
  "circuit_breaker": "OPEN",
  "action": "HALT_AND_REROUTE",
  "http_code": 404,
  "price": null,
  "rail": null,
  "latency_ms": 312,
  "error": "expected HTTP 402, got 404"
}
```

---

## Circuit-Breaker Decision Matrix

| Response                        | `http_code`  | Circuit breaker | Action              |
|---------------------------------|--------------|-----------------|---------------------|
| Live payment challenge          | `402`        | `CLOSED`        | `PROCEED`           |
| Success without payment gate    | `200`        | `OPEN`          | `HALT_AND_REROUTE`  |
| CORS preflight / empty response | `204`        | `OPEN`          | `HALT_AND_REROUTE`  |
| Not found                       | `404`        | `OPEN`          | `HALT_AND_REROUTE`  |
| Rate limited                    | `429`        | `OPEN`          | `HALT_AND_REROUTE`  |
| Server error                    | `5xx`        | `OPEN`          | `HALT_AND_REROUTE`  |
| Network timeout                 | —            | `OPEN`          | `HALT_AND_REROUTE`  |
| Non-JSON / unparseable output   | —            | `OPEN`          | `HALT_AND_REROUTE`  |
| `selat-pay` not on PATH         | —            | `OPEN`          | `HALT_AND_REROUTE`  |
| Any other unexpected code       | anything     | `OPEN`          | `HALT_AND_REROUTE`  |

The rule is strict: **only HTTP 402 is healthy.** False-healthy is the
direction that costs money; everything unknown fails closed.

---

## Exit Codes

| Code | Meaning                                                            |
|------|--------------------------------------------------------------------|
| `0`  | Endpoint healthy — 402 received, circuit breaker `CLOSED`.         |
| `1`  | Endpoint unhealthy — circuit breaker `OPEN`. Pipeline should halt. |
| `2`  | Usage or argument error (e.g. `--url` missing or not `https://`).  |

Shell usage:

```bash
python3 scripts/probe_check.py --url https://serpapi.mpp.tempo.xyz/search \
  && echo "healthy — proceeding with paid pipeline" \
  || echo "unhealthy — aborting"
```

---

## Examples

### Pre-flight gate before a paid skill run

```bash
# 1. Probe the endpoint (free — no USDC spent)
python3 scripts/probe_check.py \
  --url https://hunter.mpp.paywithlocus.com/hunter/email-finder \
  --method POST \
  --chain base \
  --json
# exit 0 -> proceed; exit 1 -> abort

# 2. Only if exit 0, run the paid pipeline
selat skill run lead-enrichment --params '{"domain":"stripe.com"}'
```

### Batch probe multiple endpoints

```bash
for url in \
  "https://serpapi.mpp.tempo.xyz/search" \
  "https://apollo.mpp.paywithlocus.com/apollo/people-enrichment" \
  "https://clado.mpp.paywithlocus.com/clado/contacts"
do
  echo -n "Probing $url ... "
  python3 scripts/probe_check.py --url "$url" --json \
    | python3 -c "import sys,json; r=json.load(sys.stdin); print(r['status'], r.get('price',''))"
done
```

### Dry-run for CI or documentation

```bash
python3 scripts/probe_check.py \
  --url https://serpapi.mpp.tempo.xyz/search \
  --dry-run

# Output:
# dry-run: would execute:
#   selat-pay GET https://serpapi.mpp.tempo.xyz/search --chain base --probe-only
```

### Extend timeout for slow upstreams

```bash
python3 scripts/probe_check.py \
  --url https://apollo.mpp.paywithlocus.com/apollo/people-enrichment \
  --method POST \
  --timeout 30 \
  --json
```

### Parse the action field in a script

```bash
result=$(python3 scripts/probe_check.py --url "$URL" --json)
action=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin)['action'])")

if [ "$action" != "PROCEED" ]; then
  echo "Circuit breaker OPEN — halting."
  exit 1
fi
```

---

## Evals

`evals/evals.json` contains six evaluation cases:

| Eval ID                              | Type          | What it tests                                                          |
|--------------------------------------|---------------|------------------------------------------------------------------------|
| `trigger-probe-before-pipeline`      | trigger       | Selects `api-failure-monitor`; uses `selat-pay --probe-only`, not OPTIONS |
| `trigger-circuit-break-on-dead-endpoint` | trigger  | Non-402 responses and timeouts both open the circuit breaker           |
| `trigger-latency-and-price-telemetry`| trigger       | `price`, `rail`, `latency_ms` come from parsing `selat-pay` JSON stdout |
| `trigger-dry-run`                    | trigger       | `--dry-run` prints the command, exits 0, makes no network call         |
| `no-trigger-general-enrichment`      | **no-trigger**| A plain enrichment prompt selects an enrichment skill, not this one    |
| `no-trigger-general-health-check`    | **no-trigger**| "Is Google.com up?" does not invoke `probe_check.py`                   |

The two no-trigger cases guard against over-triggering. This skill is for
**x402/MPP payment-gated endpoints**, not general ping or reachability checks.

---

## Files

```
skills/api-failure-monitor/
├── README.md                   <- this file
├── SKILL.md                    <- SOP frontmatter + operational docs
├── manifest.json               <- selat-skill/v1 recipe (no payment steps)
├── scripts/
│   └── probe_check.py          <- probe engine (shells out to selat-pay --probe-only)
├── evals/
│   └── evals.json              <- trigger + no-trigger eval assertions
└── references/
    └── endpoints.md            <- HTTP decision matrix + known probe-verified endpoints
```

### `probe_check.py` call flow

```
parse_args()
  validates --url starts with https://
  --dry-run: prints selat-pay command and exits 0

main()
  run_probe(url, method, body, chain, timeout)
    subprocess.run(["selat-pay", METHOD, URL, "--chain", CHAIN, "--probe-only"])
    FileNotFoundError  ->  _unhealthy("selat-pay binary not found")
    TimeoutExpired     ->  _unhealthy("probe timed out after Ns")
    stdout not JSON    ->  _unhealthy("unparseable probe output")
    http_code != 402   ->  _unhealthy("expected 402, got N")
    http_code == 402   ->  HEALTHY / CLOSED / PROCEED

_unhealthy()  ->  always UNHEALTHY / OPEN / HALT_AND_REROUTE
```

---

## Known Probe-Verified Endpoints

Prices fluctuate — re-probe before committing a budget.

| Endpoint                                                         | Method | Rail          | Approx. Price |
|------------------------------------------------------------------|--------|---------------|---------------|
| `https://serpapi.mpp.tempo.xyz/search`                          | GET    | x402 / routed | ~$0.01        |
| `https://hunter.mpp.paywithlocus.com/hunter/email-finder`       | POST   | routed        | ~$0.10        |
| `https://hunter.mpp.paywithlocus.com/hunter/email-verifier`     | POST   | routed        | ~$0.05        |
| `https://hunter.mpp.paywithlocus.com/hunter/company-enrichment` | POST   | routed        | ~$0.15        |
| `https://apollo.mpp.paywithlocus.com/apollo/people-enrichment`  | POST   | routed        | ~$0.10        |
| `https://clado.mpp.paywithlocus.com/clado/contacts`             | POST   | routed        | ~$0.045       |

See [`references/endpoints.md`](references/endpoints.md) for the full HTTP
status decision matrix and false-healthy risk notes.

---

## FAQ

**Does probing consume any USDC?**
No. `selat-pay --probe-only` reads the 402 challenge without signing or
submitting a transaction. No wallet balance is consumed.

**Why not use `curl -I` or `HEAD`?**
Same problem as `OPTIONS` — x402 payment challenges are only issued on the
real HTTP method (GET, POST, etc.). `HEAD` and `OPTIONS` return CORS
preflights on any CORS-enabled host, including completely dead endpoints.

**Why is `--chain base` required if the probe never settles?**
`selat-pay` requires the `--chain` flag even in `--probe-only` mode. Passing
`--chain base` satisfies the argument; no settlement occurs.

**What happens if `selat-pay` is not installed?**
`probe_check.py` catches `FileNotFoundError` and returns:
`UNHEALTHY / HALT_AND_REROUTE` with error
`"selat-pay binary not found — install selat-pay >= 0.3.1"`. Exit code `1`.

**Can I use this in CI/CD?**
Yes — the script is non-interactive, emits structured JSON to stdout, and uses
meaningful exit codes (`0` healthy, `1` unhealthy, `2` usage error). See
[Exit Codes](#exit-codes) and [Examples](#examples).

**Why does the manifest have `"method": "GET"` for a skill that supports POST?**
The `selat-skill/v1` schema validator requires a literal HTTP method string in
the step definition — template values like `${method}` fail enum validation.
The literal `"GET"` satisfies the schema. The actual runtime method is
controlled by `--method ${method}` in the step `args`, which accepts any
method the caller provides via the `method` param.

**Why is there no `maxAmount` in the manifest?**
This skill does not settle payments. `maxAmount: "0.00"` (as in the original
submission) is rejected by `selat skill validate` as a non-positive number.
The correct fix is to remove the payment step — and the field — entirely.

---

## License

Apache-2.0 — see [`../../LICENSE`](../../LICENSE).

**Author:** deen
**Skill version:** 1.1
**Rail:** none (script-only — no payment settlement)
**Requires:** Python 3.11+, `selat-pay >= 0.3.1`