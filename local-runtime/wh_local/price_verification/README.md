# Price verification: local, read-only evidence capture

This module connects a locally running host to the companion browser extension
to collect evidence for price verification. It is workspace-isolated and
strictly read-only: the connector may inspect price-quote and sourcing data,
but must never accept a Temu quote, change a price, create an order, add an
item to a cart, or make any other platform write.

## Start the local bridge

Run the host only on the loopback interface with a locally trusted TLS
certificate; do not expose the bridge on a LAN or public address:

```bash
cd local-runtime
/Applications/anaconda3/bin/python3.12 -m uvicorn wh_local.app.main:app \
  --host 127.0.0.1 --port 8000 \
  --ssl-keyfile /absolute/path/to/loopback-key.pem \
  --ssl-certfile /absolute/path/to/loopback-cert.pem
```

Use the exact scheme and port served by the host in the extension. The bundled
extension declares loopback access for `https://127.0.0.1/*` and
`https://localhost/*`, and accepts only HTTPS loopback URLs. Trust the local
certificate in Edge before pairing. Never point this connector at a remote
bridge or an HTTP URL.

## Pair Microsoft Edge

1. In the authenticated local host, request a pairing code with
   `POST /api/v1/price-verification/plugin/pairing-codes`. The host resolves
   the current workspace before issuing it.
2. Open `edge://extensions`, enable **Developer mode**, choose **Load
   unpacked**, and select
   `local-runtime/wh_local/price_verification/plugin/extension`.
3. Open the **Price Verification Read-only Connector** extension popup. Enter
   the loopback bridge URL and the newly issued pairing code, then select
   **Connect**.

Pairing codes are short-lived (ten minutes), single-use, and are exchanged
only with the loopback bridge. The extension clears the entered code after the
connection attempt. If pairing fails or expires, issue a new code; do not
reuse a code or replace it with a business bearer token.

## Read-only Temu acceptance boundary

The connector may observe a Temu price-quote page and capture its already
presented network/DOM evidence. Human acceptance remains in Temu and is
outside this workflow. The extension and host must not click an acceptance
control, submit a confirmation, or call a Temu endpoint that accepts,
updates, saves, creates, deletes, or otherwise changes platform state.

The same boundary applies to sourcing: collect discovery evidence only. Keep
credentials in the local host process configuration; do not paste them into
the extension or capture payloads.

## Verification boundary

Python tests use saved fixtures under
`local-runtime/tests/price_verification/fixtures`. Fixture normalization is a
pure local operation and must not open network connections. The browser tests
exercise the packaged extension code without calling Temu, 1688, or any other
external service.

Run the module test suite from `local-runtime`:

```bash
/Applications/anaconda3/bin/python3.12 -m pytest tests/price_verification -q
node --test tests/price_verification/plugin/*.test.mjs
```

Any implementation or test that needs real Temu/1688 traffic, platform writes,
or a non-loopback bridge is out of scope for this module and must not be added.
