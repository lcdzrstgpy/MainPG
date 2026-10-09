from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..domain.dimension_templates import (
    AXIS_FIELDS,
    AXIS_PREFIXES,
    GLOBAL_WORKSPACE,
    SEED_TEMPLATES,
    category_identity,
)
from .dimension_template_orm import (
    DimensionObservationRow,
    DimensionTemplateRefreshRow,
    DimensionTemplateRow,
)
from .orm import utc_now


_ABSOLUTE_LIMITS = {
    "length_cm": (0.05, 1000.0),
    "width_cm": (0.05, 1000.0),
    "height_cm": (0.05, 1000.0),
    "weight_g": (0.1, 500_000.0),
}
_OUTLIER_RATIO = 10.0


def _json_dict(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _positive_numbers(value: Mapping[str, Any] | None) -> dict[str, float]:
    result: dict[str, float] = {}
    for field in AXIS_FIELDS:
        candidate = (value or {}).get(field)
        if isinstance(candidate, bool):
            continue
        try:
            number = float(candidate)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0:
            result[field] = number
    return result


def _error_metrics(
    truth: Mapping[str, float],
    raw_estimate: Mapping[str, float],
    resolved_estimate: Mapping[str, float],
) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for field, actual in truth.items():
        field_metrics: dict[str, Any] = {"actual": actual}
        raw = raw_estimate.get(field)
        resolved = resolved_estimate.get(field)
        if raw is not None:
            raw_error = abs(raw - actual)
            field_metrics.update(
                raw_value=raw,
                raw_abs_error=raw_error,
                raw_pct_error=(raw_error / actual) * 100,
            )
        if resolved is not None:
            resolved_error = abs(resolved - actual)
            field_metrics.update(
                resolved_value=resolved,
                resolved_abs_error=resolved_error,
                resolved_pct_error=(resolved_error / actual) * 100,
            )
        if raw is not None and resolved is not None:
            field_metrics["resolution_improved"] = abs(resolved - actual) < abs(raw - actual)
        metrics[field] = field_metrics
    return metrics


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)) and float(value) > 0)
    if not ordered:
        return None
    position = (len(ordered) - 1) * quantile
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


class DimensionTemplateRepository:
    def __init__(self, database: Any):
        self.database = database
        self._write_lock = threading.RLock()
        self._refresh_event = threading.Event()
        self._refresh_thread: threading.Thread | None = None
        self._background_enabled = False
        self._busy_check: Any = None
        self._refresh_debounce_seconds = 30.0
        self._busy_was_active = False
        self._idle_not_before_epoch = 0.0
        self.ensure_seed_templates()

    def configure_background_refresh(
        self,
        *,
        busy_check: Any = None,
        debounce_seconds: float = 30.0,
    ) -> None:
        database_name = str(getattr(getattr(self.database, "engine", None), "url", ""))
        if ":memory:" in database_name:
            return
        self._busy_check = busy_check
        self._refresh_debounce_seconds = max(1.0, float(debounce_seconds))
        self._background_enabled = True
        if self.pending_refresh_count():
            self.schedule_refresh()

    def pending_refresh_count(self) -> int:
        with self.database.sessions() as session:
            return len(session.scalars(select(DimensionTemplateRefreshRow.id)).all())

    def schedule_refresh(self) -> None:
        if not self._background_enabled:
            return
        with self._write_lock:
            if self._refresh_thread is None or not self._refresh_thread.is_alive():
                self._refresh_thread = threading.Thread(
                    target=self._refresh_loop,
                    name="dimension-template-refresh",
                    daemon=True,
                )
                self._refresh_thread.start()
        self._refresh_event.set()

    def ensure_seed_templates(self) -> None:
        now = utc_now()
        with self._write_lock, self.database.sessions.begin() as session:
            for seed in SEED_TEMPLATES:
                values: dict[str, Any] = {
                    "workspace_id": GLOBAL_WORKSPACE,
                    "category_key": seed.category_key,
                    "package_profile": seed.package_profile,
                    "created_at": now,
                    "updated_at": now,
                }
                for field, (minimum, maximum, default) in seed.bounds.items():
                    prefix = AXIS_PREFIXES[field]
                    values.update({
                        f"known_{prefix}_min": minimum,
                        f"known_{prefix}_max": maximum,
                        f"known_{prefix}_default": default,
                    })
                session.execute(
                    sqlite_insert(DimensionTemplateRow)
                    .values(**values)
                    .on_conflict_do_nothing(index_elements=["workspace_id", "category_key", "package_profile"])
                )

    def resolve(self, raw: Mapping[str, Any], title: str, *, workspace_id: str) -> dict[str, Any] | None:
        learned_key, profile, candidates = category_identity(raw, title)
        del learned_key
        with self.database.sessions() as session:
            for scope in (str(workspace_id), GLOBAL_WORKSPACE):
                rows = session.scalars(
                    select(DimensionTemplateRow).where(
                        DimensionTemplateRow.workspace_id == scope,
                        DimensionTemplateRow.package_profile.in_([profile, "generic"]),
                        DimensionTemplateRow.category_key.in_(candidates),
                    )
                ).all()
                by_key = {(row.category_key, row.package_profile): row for row in rows}
                for key in candidates:
                    row = by_key.get((key, profile)) or by_key.get((key, "generic"))
                    if row is not None:
                        result = self._template(row)
                        result["matched_category_key"] = key
                        return result
        return None

    def record_observation(
        self,
        *,
        workspace_id: str,
        observation_key: str,
        raw: Mapping[str, Any],
        title: str,
        values: Mapping[str, Any],
        provenance: Mapping[str, str],
        source_kind: str,
        estimate_context: Mapping[str, Any] | None = None,
        task_id: int = 0,
        product_draft_id: int = 0,
        variant_key: str = "",
    ) -> bool:
        clean: dict[str, float] = {}
        for field in AXIS_FIELDS:
            if isinstance(values.get(field), bool):
                continue
            try:
                number = float(values.get(field))
            except (TypeError, ValueError):
                continue
            if math.isfinite(number) and number > 0 and provenance.get(field) in {"source_confirmed", "manual_confirmed"}:
                clean[field] = number
        if not clean:
            return False
        category_key, profile, _candidates = category_identity(raw, title)
        with self._write_lock, self.database.sessions.begin() as session:
            old_identity: tuple[str, str] | None = None
            existing = session.scalar(
                select(DimensionObservationRow).where(
                    DimensionObservationRow.workspace_id == str(workspace_id),
                    DimensionObservationRow.observation_key == str(observation_key)[:255],
                )
            )
            if existing is None:
                merged_values = dict(clean)
                merged_provenance = {field: provenance[field] for field in clean}
                old_identity = None
            else:
                old_identity = (existing.category_key, existing.package_profile)
                merged_values = {
                    field: float(getattr(existing, field))
                    for field in AXIS_FIELDS
                    if getattr(existing, field) is not None
                }
                merged_values.update(clean)
                merged_provenance = _json_dict(existing.provenance_json)
                merged_provenance.update({field: provenance[field] for field in clean})

            raw_estimate = _positive_numbers(
                (estimate_context or {}).get("raw_estimate")
                if isinstance((estimate_context or {}).get("raw_estimate"), Mapping)
                else {}
            )
            resolved_estimate = _positive_numbers(estimate_context)
            quality = self._quality_map(
                session,
                str(workspace_id),
                category_key,
                profile,
                merged_values,
            )
            metrics = _error_metrics(merged_values, raw_estimate, resolved_estimate)
            serialized_provenance = json.dumps(merged_provenance, ensure_ascii=False, sort_keys=True)
            serialized_quality = json.dumps(quality, ensure_ascii=False, sort_keys=True)
            serialized_raw = json.dumps(raw_estimate, ensure_ascii=False, sort_keys=True)
            serialized_resolved = json.dumps(resolved_estimate, ensure_ascii=False, sort_keys=True)
            serialized_metrics = json.dumps(metrics, ensure_ascii=False, sort_keys=True)

            if existing is None:
                session.add(DimensionObservationRow(
                    workspace_id=str(workspace_id),
                    observation_key=str(observation_key)[:255],
                    category_key=category_key,
                    package_profile=profile,
                    source_kind=str(source_kind),
                    task_id=max(0, int(task_id)),
                    product_draft_id=max(0, int(product_draft_id)),
                    variant_key=str(variant_key),
                    provenance_json=serialized_provenance,
                    quality_json=serialized_quality,
                    raw_estimate_json=serialized_raw,
                    resolved_estimate_json=serialized_resolved,
                    error_metrics_json=serialized_metrics,
                    **{field: merged_values.get(field) for field in AXIS_FIELDS},
                ))
            else:
                changed = any(getattr(existing, field) != merged_values.get(field) for field in AXIS_FIELDS)
                changed = changed or existing.provenance_json != serialized_provenance
                changed = changed or existing.quality_json != serialized_quality
                changed = changed or existing.raw_estimate_json != serialized_raw
                changed = changed or existing.resolved_estimate_json != serialized_resolved
                changed = changed or existing.error_metrics_json != serialized_metrics
                if not changed:
                    return False
                for field in AXIS_FIELDS:
                    setattr(existing, field, merged_values.get(field))
                existing.category_key = category_key
                existing.package_profile = profile
                existing.source_kind = str(source_kind)
                existing.task_id = max(0, int(task_id))
                existing.product_draft_id = max(0, int(product_draft_id))
                existing.variant_key = str(variant_key)
                existing.provenance_json = serialized_provenance
                existing.quality_json = serialized_quality
                existing.raw_estimate_json = serialized_raw
                existing.resolved_estimate_json = serialized_resolved
                existing.error_metrics_json = serialized_metrics
                session.flush()
            if old_identity is not None and old_identity != (category_key, profile):
                self._mark_dirty(session, str(workspace_id), old_identity[0], old_identity[1])
            self._mark_dirty(session, str(workspace_id), category_key, profile)
        # Wake the worker only after the queue transaction is visible.  Waking it
        # inside the transaction can lose the notification on a fast machine.
        self.schedule_refresh()
        return True

    def delete_observation(self, *, workspace_id: str, observation_key: str) -> bool:
        with self._write_lock, self.database.sessions.begin() as session:
            row = session.scalar(
                select(DimensionObservationRow).where(
                    DimensionObservationRow.workspace_id == str(workspace_id),
                    DimensionObservationRow.observation_key == str(observation_key)[:255],
                )
            )
            if row is None:
                return False
            identity = (row.category_key, row.package_profile)
            session.delete(row)
            session.flush()
            self._mark_dirty(session, str(workspace_id), identity[0], identity[1])
        self.schedule_refresh()
        return True

    def _mark_dirty(
        self,
        session: Any,
        workspace_id: str,
        category_key: str,
        profile: str,
    ) -> None:
        not_before = time.time() + self._refresh_debounce_seconds
        session.execute(
            sqlite_insert(DimensionTemplateRefreshRow)
            .values(
                workspace_id=workspace_id,
                category_key=category_key,
                package_profile=profile,
                pending_changes=1,
                not_before_epoch=not_before,
                last_error="",
                updated_at=utc_now(),
            )
            .on_conflict_do_update(
                index_elements=["workspace_id", "category_key", "package_profile"],
                set_={
                    "pending_changes": DimensionTemplateRefreshRow.pending_changes + 1,
                    "not_before_epoch": not_before,
                    "last_error": "",
                    "updated_at": utc_now(),
                },
            )
        )

    def refresh_pending(self, *, force: bool = False, max_categories: int | None = None) -> int:
        refreshed = 0
        while max_categories is None or refreshed < max_categories:
            with self.database.sessions() as session:
                query = select(DimensionTemplateRefreshRow).order_by(
                    DimensionTemplateRefreshRow.not_before_epoch,
                    DimensionTemplateRefreshRow.id,
                )
                if not force:
                    query = query.where(DimensionTemplateRefreshRow.not_before_epoch <= time.time())
                pending = session.scalar(query.limit(1))
                identity = (
                    pending.workspace_id,
                    pending.category_key,
                    pending.package_profile,
                ) if pending is not None else None
            if identity is None:
                break
            try:
                with self._write_lock, self.database.sessions.begin() as session:
                    current = session.scalar(
                        select(DimensionTemplateRefreshRow).where(
                            DimensionTemplateRefreshRow.workspace_id == identity[0],
                            DimensionTemplateRefreshRow.category_key == identity[1],
                            DimensionTemplateRefreshRow.package_profile == identity[2],
                        )
                    )
                    if current is None:
                        continue
                    if not force and current.not_before_epoch > time.time():
                        continue
                    self._rebuild(session, identity[0], identity[1], identity[2])
                    session.delete(current)
            except Exception as exc:  # noqa: BLE001 - keep prior aggregate and retry later
                with self.database.sessions.begin() as session:
                    session.execute(
                        update(DimensionTemplateRefreshRow)
                        .where(
                            DimensionTemplateRefreshRow.workspace_id == identity[0],
                            DimensionTemplateRefreshRow.category_key == identity[1],
                            DimensionTemplateRefreshRow.package_profile == identity[2],
                        )
                        .values(
                            last_error=str(exc)[:1000],
                            not_before_epoch=time.time() + 60,
                            updated_at=utc_now(),
                        )
                    )
                break
            refreshed += 1
            time.sleep(0.01)
        return refreshed

    def _next_refresh_epoch(self) -> float | None:
        with self.database.sessions() as session:
            value = session.scalar(
                select(DimensionTemplateRefreshRow.not_before_epoch)
                .order_by(
                    DimensionTemplateRefreshRow.not_before_epoch,
                    DimensionTemplateRefreshRow.id,
                )
                .limit(1)
            )
            return float(value) if value is not None else None

    def _refresh_loop(self) -> None:
        while self._background_enabled:
            next_epoch = self._next_refresh_epoch()
            if next_epoch is None:
                self._refresh_event.wait()
                self._refresh_event.clear()
                continue
            busy = bool(callable(self._busy_check) and self._busy_check())
            if busy:
                self._busy_was_active = True
                self._refresh_event.wait(5)
                self._refresh_event.clear()
                continue
            if self._busy_was_active:
                self._busy_was_active = False
                # A long-running task may make the observation debounce expire.
                # Give a low-spec machine a real idle window after that task exits.
                self._idle_not_before_epoch = time.time() + self._refresh_debounce_seconds
            delay = max(next_epoch, self._idle_not_before_epoch) - time.time()
            if delay > 0:
                self._refresh_event.wait(min(delay, 5))
                self._refresh_event.clear()
                continue
            if self.refresh_pending(max_categories=1) == 0:
                self._refresh_event.wait(1)
                self._refresh_event.clear()

    def _quality_map(
        self,
        session: Any,
        workspace_id: str,
        category_key: str,
        profile: str,
        values: Mapping[str, float],
    ) -> dict[str, dict[str, Any]]:
        prior = session.scalar(
            select(DimensionTemplateRow).where(
                DimensionTemplateRow.workspace_id == GLOBAL_WORKSPACE,
                DimensionTemplateRow.category_key.in_([f"profile:{profile}", "fallback"]),
                DimensionTemplateRow.package_profile.in_([profile, "generic"]),
            ).order_by(DimensionTemplateRow.category_key.desc())
        )
        # Foreground writes must remain O(1).  Historical outlier comparison uses
        # the last completed exact aggregate instead of rescanning every sample.
        learned = session.scalar(
            select(DimensionTemplateRow).where(
                DimensionTemplateRow.workspace_id == workspace_id,
                DimensionTemplateRow.category_key == category_key,
                DimensionTemplateRow.package_profile == profile,
            )
        )
        quality: dict[str, dict[str, Any]] = {}
        for field, number in values.items():
            flags: list[str] = []
            absolute_min, absolute_max = _ABSOLUTE_LIMITS[field]
            if number < absolute_min or number > absolute_max:
                flags.append("absolute_limit")
            prefix = AXIS_PREFIXES[field]
            prior_min = getattr(prior, f"known_{prefix}_min", None) if prior is not None else None
            prior_max = getattr(prior, f"known_{prefix}_max", None) if prior is not None else None
            if prior_min and number < float(prior_min) / _OUTLIER_RATIO:
                flags.append("extreme_below_prior")
            if prior_max and number > float(prior_max) * _OUTLIER_RATIO:
                flags.append("extreme_above_prior")
            learned_count = int(getattr(learned, f"stat_{prefix}_sample_count", 0) or 0)
            median = getattr(learned, f"stat_{prefix}_p50", None) if learned is not None else None
            if learned_count >= 10 and median:
                median = float(median)
                if median and (number < median / _OUTLIER_RATIO or number > median * _OUTLIER_RATIO):
                    flags.append("extreme_vs_history")
            quality[field] = {
                "status": "quarantined" if flags else "accepted",
                "flags": sorted(set(flags)),
            }
        if all(field in values for field in AXIS_FIELDS):
            volume = values["length_cm"] * values["width_cm"] * values["height_cm"]
            density = values["weight_g"] / volume if volume > 0 else math.inf
            if density < 0.00005 or density > 25:
                entry = quality["weight_g"]
                entry["status"] = "quarantined"
                entry["flags"] = sorted(set([*entry["flags"], "implausible_package_density"]))
        return quality

    def _rebuild(self, session: Any, workspace_id: str, category_key: str, profile: str) -> None:
        observations = session.scalars(
            select(DimensionObservationRow).where(
                DimensionObservationRow.workspace_id == workspace_id,
                DimensionObservationRow.category_key == category_key,
                DimensionObservationRow.package_profile == profile,
            )
        ).all()
        provenance_by_id = {row.id: _json_dict(row.provenance_json) for row in observations}
        quality_by_id = {row.id: _json_dict(row.quality_json) for row in observations}
        metrics_by_id = {row.id: _json_dict(row.error_metrics_json) for row in observations}
        accepted_observations = [
            row
            for row in observations
            if any(
                quality_by_id[row.id].get(field, {}).get("status", "accepted") == "accepted"
                and getattr(row, field) is not None
                for field in AXIS_FIELDS
            )
        ]
        prior = session.scalar(
            select(DimensionTemplateRow).where(
                DimensionTemplateRow.workspace_id == GLOBAL_WORKSPACE,
                DimensionTemplateRow.category_key.in_([f"profile:{profile}", "fallback"]),
                DimensionTemplateRow.package_profile.in_([profile, "generic"]),
            ).order_by(DimensionTemplateRow.category_key.desc())
        )
        values: dict[str, Any] = {
            "workspace_id": workspace_id,
            "category_key": category_key,
            "package_profile": profile,
            "sample_count": len(accepted_observations),
            "source_confirmed_n": sum(
                any(
                    provenance_by_id[row.id].get(field) == "source_confirmed"
                    and quality_by_id[row.id].get(field, {}).get("status", "accepted") == "accepted"
                    for field in AXIS_FIELDS
                )
                for row in accepted_observations
            ),
            "manual_confirmed_n": sum(
                any(
                    provenance_by_id[row.id].get(field) == "manual_confirmed"
                    and quality_by_id[row.id].get(field, {}).get("status", "accepted") == "accepted"
                    for field in AXIS_FIELDS
                )
                for row in accepted_observations
            ),
            "quarantined_axis_count": sum(
                quality_by_id[row.id].get(field, {}).get("status") == "quarantined"
                for row in observations
                for field in AXIS_FIELDS
            ),
            "updated_at": utc_now(),
        }
        if prior is not None:
            for field, prefix in AXIS_PREFIXES.items():
                del field
                for suffix in ("min", "max", "default"):
                    values[f"known_{prefix}_{suffix}"] = getattr(prior, f"known_{prefix}_{suffix}")
        for field, prefix in AXIS_PREFIXES.items():
            # Prefer independently captured source values once they alone meet
            # the statistical threshold; otherwise combine all genuine values.
            source_values = [
                float(getattr(row, field)) for row in observations
                if getattr(row, field) is not None
                and provenance_by_id[row.id].get(field) == "source_confirmed"
                and quality_by_id[row.id].get(field, {}).get("status", "accepted") == "accepted"
            ]
            all_values = [
                float(getattr(row, field))
                for row in observations
                if getattr(row, field) is not None
                and quality_by_id[row.id].get(field, {}).get("status", "accepted") == "accepted"
            ]
            selected = source_values if len(source_values) >= 20 else all_values
            values[f"{prefix}_sample_count"] = len(all_values)
            values[f"stat_{prefix}_p10"] = _percentile(selected, 0.10)
            values[f"stat_{prefix}_p50"] = _percentile(selected, 0.50)
            values[f"stat_{prefix}_p90"] = _percentile(selected, 0.90)
        accuracy: dict[str, dict[str, Any]] = {}
        for field in AXIS_FIELDS:
            field_metrics = [
                metrics_by_id[row.id].get(field, {})
                for row in accepted_observations
                if quality_by_id[row.id].get(field, {}).get("status", "accepted") == "accepted"
            ]
            raw_abs = [float(item["raw_abs_error"]) for item in field_metrics if item.get("raw_abs_error") is not None]
            raw_pct = [float(item["raw_pct_error"]) for item in field_metrics if item.get("raw_pct_error") is not None]
            resolved_abs = [
                float(item["resolved_abs_error"])
                for item in field_metrics
                if item.get("resolved_abs_error") is not None
            ]
            resolved_pct = [
                float(item["resolved_pct_error"])
                for item in field_metrics
                if item.get("resolved_pct_error") is not None
            ]
            comparable = [item for item in field_metrics if "resolution_improved" in item]
            accuracy[field] = {
                "raw_count": len(raw_abs),
                "raw_mae": _mean(raw_abs),
                "raw_mape": _mean(raw_pct),
                "resolved_count": len(resolved_abs),
                "resolved_mae": _mean(resolved_abs),
                "resolved_mape": _mean(resolved_pct),
                "improved_count": sum(bool(item["resolution_improved"]) for item in comparable),
                "comparable_count": len(comparable),
                "improvement_rate": (
                    sum(bool(item["resolution_improved"]) for item in comparable) / len(comparable)
                    if comparable
                    else None
                ),
            }
        values["accuracy_json"] = json.dumps(accuracy, ensure_ascii=False, sort_keys=True)
        values.setdefault("created_at", utc_now())
        update_values = {key: value for key, value in values.items() if key not in {"workspace_id", "category_key", "package_profile", "created_at"}}
        session.execute(
            sqlite_insert(DimensionTemplateRow)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["workspace_id", "category_key", "package_profile"],
                set_=update_values,
            )
        )

    @staticmethod
    def _template(row: DimensionTemplateRow) -> dict[str, Any]:
        return {column.name: getattr(row, column.name) for column in row.__table__.columns}
