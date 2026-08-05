"""Local filesystem exports for persisted, redacted quote snapshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.styles import Font

from .contracts import redact_sensitive
from .quote_normalizer import QuoteItem, QuotePreview
from .repository import QuoteRunRecord


@dataclass(frozen=True)
class ExportedQuoteRun:
    run_id: str
    workbook_path: Path
    endpoint_report_path: Path


_COLUMNS = (
    "skc_id",
    "sku_id",
    "sku_true_id",
    "sku_identifier_kind",
    "sku_merchant_code",
    "sku_attribute_set",
    "sku_attribute_text",
    "skc_attribute_text",
    "product_attribute_summary",
    "spu_or_goods_id",
    "site",
    "status",
    "original_declared_price_cny",
    "adjusted_declared_price_cny",
    "new_declared_price_cny",
    "product_title",
    "main_image_url",
    "extra_image_urls",
    "source_endpoint",
    "capture_method",
    "captured_at",
    "evidence_sources",
    "source_confidence",
    "authenticity_status",
    "completeness_score",
    "missing_fields",
    "conflict_fields",
    "network_evidence_count",
    "dom_evidence_count",
    "source_http_statuses",
)


def export_quote_snapshot(
    *, output_root: str | Path, run: QuoteRunRecord, preview: QuotePreview
) -> ExportedQuoteRun:
    """Write the two local export artifacts under a contained output root."""
    if not isinstance(run, QuoteRunRecord):
        raise TypeError("run must be QuoteRunRecord")
    if not isinstance(preview, QuotePreview):
        raise TypeError("preview must be QuotePreview")
    directory = _export_directory(Path(output_root), run.run_id)
    directory.mkdir(parents=True, exist_ok=True)
    workbook_path = directory / "normalized_quotes.xlsx"
    report_path = directory / "endpoint_report.md"
    _write_workbook(workbook_path, preview.quotes)
    _atomic_write_text(report_path, _endpoint_report(run, preview))
    return ExportedQuoteRun(
        run_id=run.run_id,
        workbook_path=workbook_path.resolve(),
        endpoint_report_path=report_path.resolve(),
    )


def _export_directory(output_root: Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run_id cannot escape the output root")
    root = output_root.resolve(strict=False)
    directory = (root / "quote-runs" / run_id).resolve(strict=False)
    if not directory.is_relative_to(root):
        raise ValueError("export path must stay below the output root")
    return directory


def _write_workbook(path: Path, quotes: Iterable[QuoteItem]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Normalized Quotes"
    worksheet.append(list(_COLUMNS))
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
    for quote in quotes:
        values = asdict(quote)
        worksheet.append([_cell_value(values.get(column)) for column in _COLUMNS])
    worksheet.freeze_panes = "A2"
    for column in worksheet.columns:
        letter = column[0].column_letter
        worksheet.column_dimensions[letter].width = min(
            48, max(12, max(len(str(cell.value or "")) for cell in column) + 2)
        )
    _atomic_save_workbook(workbook, path)


def _cell_value(value: Any) -> Any:
    safe = redact_sensitive(value)
    if isinstance(safe, list):
        safe = " | ".join(str(item) for item in safe)
    if isinstance(safe, str) and safe.startswith(("=", "+", "-", "@")):
        return f"'{safe}"
    return safe


def _endpoint_report(run: QuoteRunRecord, preview: QuotePreview) -> str:
    endpoints = sorted({str(redact_sensitive(quote.source_endpoint)) for quote in preview.quotes if quote.source_endpoint})
    lines = [
        "# Endpoint report",
        "",
        f"- Run ID: `{redact_sensitive(run.run_id)}`",
        f"- Captured at: `{redact_sensitive(run.captured_at)}`",
        f"- Quotes: {len(preview.quotes)}",
        "",
        "## Observed read-only endpoints",
        "",
    ]
    lines.extend(f"- `{endpoint}`" for endpoint in endpoints)
    if not endpoints:
        lines.append("- No endpoint was recorded in the persisted snapshot.")
    return "\n".join(lines) + "\n"


def _atomic_save_workbook(workbook: Workbook, path: Path) -> None:
    with NamedTemporaryFile(dir=path.parent, suffix=".xlsx", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        workbook.save(temporary_path)
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _atomic_write_text(path: Path, text: str) -> None:
    with NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as temporary:
        temporary.write(text)
        temporary_path = Path(temporary.name)
    try:
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
