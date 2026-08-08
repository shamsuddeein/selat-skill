#!/usr/bin/env python3
"""
probe_check.py — api-failure-monitor probe engine

Probes an x402/MPP endpoint using `selat-pay --probe-only` to obtain
authentic 402 signals, live prices, rails, and latency WITHOUT settling any
payment. Defaults to UNHEALTHY / HALT_AND_REROUTE (circuit breaker OPEN) on
any unexpected response code, timeout, dead URL, or unparseable output.

Exit codes:
  0  — endpoint healthy (received expected 402 signal)
  1  — endpoint unhealthy or probe failed (circuit breaker tripped)
  2  — usage / argument error

Usage:
  python3 probe_check.py --url <endpoint> [--method GET|POST]
                         [--body '{"key":"val"}'] [--chain base]
                         [--timeout 10] [--dry-run] [--json]

Examples:
  python3 probe_check.py --url https://serpapi.mpp.tempo.xyz/search
  python3 probe_check.py --url https://hunter.mpp.paywithlocus.com/hunter/email-finder \\
      --method POST --body '{"domain":"stripe.com","first_name":"John","last_name":"Doe"}'
  python3 probe_check.py --url https://serpapi.mpp.tempo.xyz/search --dry-run
"""

import argparse
import json
import subprocess
import sys
import time


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CIRCUIT_OPEN   = "OPEN"
CIRCUIT_CLOSED = "CLOSED"
STATUS_HEALTHY   = "HEALTHY"
STATUS_UNHEALTHY = "UNHEALTHY"
ACTION_PROCEED          = "PROCEED"
ACTION_HALT_AND_REROUTE = "HALT_AND_REROUTE"

# selat-pay returns 402 for a live, payment-gated endpoint that is UP.
# Any other code — 200, 204, 404, 5xx, or no response — is treated as
# unhealthy so the circuit breaker opens.
EXPECTED_HTTP_CODE = 402


# ---------------------------------------------------------------------------
# Probe logic
# ---------------------------------------------------------------------------

def build_selat_pay_cmd(args: argparse.Namespace) -> list[str]:
    """Construct the selat-pay --probe-only command from parsed args."""
    cmd = [
        "selat-pay",
        args.method.upper(),
        args.url,
        "--chain", args.chain,
        "--probe-only",
    ]
    if args.body:
        cmd += ["--body", args.body]
    return cmd


def run_probe(
    url: str,
    method: str,
    body: str | None,
    chain: str,
    timeout: int,
) -> dict:
    """
    Shell out to `selat-pay --probe-only` and parse the result.

    Returns a result dict with keys:
      status, circuit_breaker, action, http_code, price, rail, latency_ms, error
    """
    cmd = ["selat-pay", method.upper(), url, "--chain", chain, "--probe-only"]
    if body:
        cmd += ["--body", body]

    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        latency_ms = round((time.monotonic() - t0) * 1000)
    except FileNotFoundError:
        # selat-pay binary not installed
        return _unhealthy(
            http_code=None,
            latency_ms=0,
            error="selat-pay binary not found — install selat-pay >= 0.3.1",
        )
    except subprocess.TimeoutExpired:
        latency_ms = timeout * 1000
        return _unhealthy(
            http_code=None,
            latency_ms=latency_ms,
            error=f"probe timed out after {timeout}s",
        )
    except Exception as exc:  # noqa: BLE001
        return _unhealthy(
            http_code=None,
            latency_ms=0,
            error=f"unexpected subprocess error: {exc}",
        )

    # -----------------------------------------------------------------------
    # Parse selat-pay output.  selat-pay --probe-only writes JSON to stdout
    # on success:
    #   {"http_code": 402, "price": "0.01", "rail": "x402", "latency_ms": 87}
    # On failure it may write a plain error string or partial JSON.
    # -----------------------------------------------------------------------
    raw_stdout = (result.stdout or "").strip()
    raw_stderr = (result.stderr or "").strip()

    parsed: dict = {}
    if raw_stdout:
        try:
            parsed = json.loads(raw_stdout)
        except json.JSONDecodeError:
            # Unparseable output → fail closed
            return _unhealthy(
                http_code=result.returncode,
                latency_ms=latency_ms,
                error=f"unparseable probe output: {raw_stdout[:200]}",
            )

    http_code = parsed.get("http_code")
    if http_code != EXPECTED_HTTP_CODE:
        return _unhealthy(
            http_code=http_code,
            latency_ms=latency_ms,
            error=(
                f"expected HTTP {EXPECTED_HTTP_CODE}, got {http_code!r}"
                + (f" — stderr: {raw_stderr[:200]}" if raw_stderr else "")
            ),
        )

    return {
        "status":          STATUS_HEALTHY,
        "circuit_breaker": CIRCUIT_CLOSED,
        "action":          ACTION_PROCEED,
        "http_code":       http_code,
        "price":           parsed.get("price"),
        "rail":            parsed.get("rail"),
        "latency_ms":      parsed.get("latency_ms", latency_ms),
        "error":           None,
    }


def _unhealthy(http_code, latency_ms, error: str) -> dict:
    """Return a fail-closed result."""
    return {
        "status":          STATUS_UNHEALTHY,
        "circuit_breaker": CIRCUIT_OPEN,
        "action":          ACTION_HALT_AND_REROUTE,
        "http_code":       http_code,
        "price":           None,
        "rail":            None,
        "latency_ms":      latency_ms,
        "error":           error,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="probe_check.py",
        description=(
            "Probe an x402/MPP endpoint using selat-pay --probe-only to "
            "obtain an authentic 402 health signal without settling a payment. "
            "Fails closed (UNHEALTHY / HALT_AND_REROUTE) on any unexpected "
            "result, timeout, or error."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--url",
        required=True,
        help="x402/MPP endpoint URL to probe (must be https://).",
    )
    parser.add_argument(
        "--method",
        default="GET",
        choices=["GET", "POST", "PUT", "PATCH", "DELETE"],
        help="HTTP method to probe (default: GET).",
    )
    parser.add_argument(
        "--body",
        default=None,
        help='JSON request body string, e.g. \'{"domain":"example.com"}\'.',
    )
    parser.add_argument(
        "--chain",
        default="base",
        help="Chain flag forwarded to selat-pay (default: base). "
             "Probing is chain-independent; this flag satisfies selat-pay's "
             "required --chain argument but does not settle on any chain.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="Probe timeout in seconds (default: 15). "
             "Triggers HALT_AND_REROUTE on expiry.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the selat-pay command that would be run, then exit 0. "
             "No network calls are made.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON to stdout (default is human-readable).",
    )

    return parser.parse_args(argv)


def emit(result: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2))
        return

    status = result["status"]
    cb     = result["circuit_breaker"]
    action = result["action"]
    code   = result.get("http_code")
    price  = result.get("price")
    rail   = result.get("rail")
    ms     = result.get("latency_ms")
    error  = result.get("error")

    icon = "✓" if status == STATUS_HEALTHY else "✗"
    print(f"{icon}  status={status}  circuit_breaker={cb}  action={action}")
    if code is not None:
        print(f"   http_code={code}")
    if price is not None:
        print(f"   price={price}")
    if rail is not None:
        print(f"   rail={rail}")
    if ms is not None:
        print(f"   latency_ms={ms}")
    if error:
        print(f"   error: {error}", file=sys.stderr)


def main(argv=None) -> int:
    args = parse_args(argv)

    # ------------------------------------------------------------------
    # Validate --url
    # ------------------------------------------------------------------
    if not args.url.startswith("https://"):
        print(
            f"error: --url must start with https:// (got: {args.url!r})",
            file=sys.stderr,
        )
        return 2

    # ------------------------------------------------------------------
    # --dry-run: show the command and exit cleanly
    # ------------------------------------------------------------------
    if args.dry_run:
        cmd_parts = [
            "selat-pay", args.method.upper(), args.url,
            "--chain", args.chain, "--probe-only",
        ]
        if args.body:
            cmd_parts += ["--body", args.body]
        print("dry-run: would execute:")
        print("  " + " ".join(cmd_parts))
        result = {
            "status":          "DRY_RUN",
            "circuit_breaker": "N/A",
            "action":          "N/A",
            "http_code":       None,
            "price":           None,
            "rail":            None,
            "latency_ms":      None,
            "error":           None,
        }
        emit(result, args.json_output)
        return 0

    # ------------------------------------------------------------------
    # Live probe
    # ------------------------------------------------------------------
    result = run_probe(
        url=args.url,
        method=args.method,
        body=args.body,
        chain=args.chain,
        timeout=args.timeout,
    )

    emit(result, args.json_output)

    return 0 if result["status"] == STATUS_HEALTHY else 1


if __name__ == "__main__":
    sys.exit(main())
