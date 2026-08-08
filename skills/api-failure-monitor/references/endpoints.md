# x402 / MPP Endpoint Health Reference

Decision matrix for interpreting probe results from `selat-pay --probe-only`.

## Why OPTIONS Probes Are Wrong for x402

```
$ curl -X OPTIONS -o /dev/null -w '%{http_code}' https://serpapi.mpp.tempo.xyz/search
204
$ curl -o /dev/null -w '%{http_code}' https://serpapi.mpp.tempo.xyz/search
402
```

A CORS preflight (`OPTIONS`) always returns `204` on any CORS-enabled host —
including hosts that would `404` the moment you pay and send a real request.
The 402 payment challenge is only visible on the real HTTP method.
`selat-pay --probe-only` uses the correct method and sees the real 402.

## HTTP Status Decision Matrix

| Code received | Meaning | Circuit breaker | Action |
|---|---|---|---|
| `402` | Live payment challenge — endpoint healthy | `CLOSED` | `PROCEED` |
| `200` | Response without payment challenge — may be a stale route or proxy error | `OPEN` | `HALT_AND_REROUTE` |
| `204` | CORS preflight or empty response — endpoint state unknown | `OPEN` | `HALT_AND_REROUTE` |
| `404` | Path not found | `OPEN` | `HALT_AND_REROUTE` |
| `429` | Rate limited | `OPEN` | `HALT_AND_REROUTE` |
| `5xx` | Server error | `OPEN` | `HALT_AND_REROUTE` |
| timeout | Network unreachable or endpoint unresponsive | `OPEN` | `HALT_AND_REROUTE` |
| unparseable | `selat-pay` returned non-JSON or error text | `OPEN` | `HALT_AND_REROUTE` |
| binary not found | `selat-pay` not installed | `OPEN` | `HALT_AND_REROUTE` |

## Known Probe-Verified Endpoints

All entries below should be re-verified with `selat-pay --probe-only` before use.
Prices fluctuate; treat these as reference values only.

| Endpoint | Method | Rail | Approx. Price |
|---|---|---|---|
| `https://serpapi.mpp.tempo.xyz/search` | GET | x402 / routed | ~$0.01 |
| `https://hunter.mpp.paywithlocus.com/hunter/email-finder` | POST | routed | ~$0.10 |
| `https://hunter.mpp.paywithlocus.com/hunter/email-verifier` | POST | routed | ~$0.05 |
| `https://apollo.mpp.paywithlocus.com/apollo/people-enrichment` | POST | routed | ~$0.10 |
| `https://clado.mpp.paywithlocus.com/clado/contacts` | POST | routed | ~$0.045 |
| `https://hunter.mpp.paywithlocus.com/hunter/company-enrichment` | POST | routed | ~$0.15 |

## Probe Command Template

```bash
selat-pay <METHOD> <URL> --chain base --probe-only [--body '<json>']
```

`--chain base` satisfies selat-pay's required flag. The probe is
chain-independent and free — no settlement occurs.

## False-Healthy Risk

The original PR #34 submission used `urllib` with `OPTIONS` and treated any
`2xx` response as healthy. This produced false-healthy results:

```
# Dead endpoint — urllib OPTIONS returned 204, probe said HEALTHY:
python3 probe_check_old.py --url https://x402.tavily.com/this-endpoint-does-not-exist
{"status": "HEALTHY", "circuit_breaker": "CLOSED", "http_code": 204}
```

The refactored `probe_check.py` uses `selat-pay --probe-only` and treats `204`
as `OPEN` / `HALT_AND_REROUTE`, so dead paths fail closed.
