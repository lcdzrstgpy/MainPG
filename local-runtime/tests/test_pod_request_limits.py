from __future__ import annotations

import asyncio
import unittest

from pydantic import ValidationError

from wh_local.modules.pod_customization.contracts import (
    BusinessFields,
    ManualTitleUpdate,
    SPEC_CARD_MAX_COLUMNS,
    SPEC_CARD_MAX_ROWS,
    validate_spec_card_cells,
)
from wh_local.modules.pod_customization.router import (
    POD_JSON_REQUEST_MAX_BYTES,
    PodRequestLimitMiddleware,
)


class PodRequestLimitTests(unittest.TestCase):
    def test_rejects_empty_spec_card_padding_beyond_column_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "规格卡最多"):
            validate_spec_card_cells(
                [[""] * (SPEC_CARD_MAX_COLUMNS + 1)],
                require_content=False,
            )

    def test_rejects_oversized_business_fields_and_manual_title(self) -> None:
        with self.assertRaises(ValidationError):
            BusinessFields(product_category="x" * 501)
        with self.assertRaises(ValidationError):
            BusinessFields(style_keywords=["keyword"] * 101)
        with self.assertRaises(ValidationError):
            ManualTitleUpdate(title="x" * 301)

    def test_accepts_documented_boundaries(self) -> None:
        fields = BusinessFields(
            product_category="x" * 500,
            style_keywords=["x" * 200] * 100,
            copy_restrictions="x" * 2000,
        )
        self.assertEqual(len(fields.style_keywords), 100)
        self.assertEqual(len(ManualTitleUpdate(title="x" * 300).title), 300)
        self.assertEqual(
            len(
                validate_spec_card_cells(
                    [[""] * SPEC_CARD_MAX_COLUMNS] * SPEC_CARD_MAX_ROWS,
                    require_content=False,
                )
            ),
            SPEC_CARD_MAX_ROWS,
        )

    def test_limits_chunked_pod_json_before_downstream_consumes_body(self) -> None:
        sent: list[dict[str, object]] = []
        chunks = [b"x" * POD_JSON_REQUEST_MAX_BYTES, b"x"]

        async def receive() -> dict[str, object]:
            body = chunks.pop(0) if chunks else b""
            return {"type": "http.request", "body": body, "more_body": bool(chunks)}

        async def downstream(_scope, receive, _send) -> None:
            await receive()
            await receive()

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/pod-customization/spec-card/preview",
            "headers": [(b"content-type", b"application/json")],
        }
        asyncio.run(PodRequestLimitMiddleware(downstream)(scope, receive, send))

        self.assertEqual(sent[0]["type"], "http.response.start")
        self.assertEqual(sent[0]["status"], 413)

    def test_allows_template_upload_budget_on_trailing_slash_redirect(self) -> None:
        sent: list[dict[str, object]] = []
        chunks = [b"x" * (POD_JSON_REQUEST_MAX_BYTES + 1)]

        async def receive() -> dict[str, object]:
            body = chunks.pop(0) if chunks else b""
            return {"type": "http.request", "body": body, "more_body": bool(chunks)}

        async def downstream(_scope, receive, send) -> None:
            await receive()
            await send({"type": "http.response.start", "status": 307, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/pod-customization/templates/",
            "headers": [(b"content-type", b"multipart/form-data; boundary=test")],
        }
        asyncio.run(PodRequestLimitMiddleware(downstream)(scope, receive, send))

        self.assertEqual(sent[0]["type"], "http.response.start")
        self.assertEqual(sent[0]["status"], 307)


if __name__ == "__main__":
    unittest.main()
