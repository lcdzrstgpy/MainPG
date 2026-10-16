from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .fission_specs import FissionRegistryCandidates, FissionRegistrySnapshot, ValidatedRegistryCandidates
from .repository import PodCustomizationRepository, RegistryVersionConflict


def _canonical_json(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, str):
            return " ".join(item.split())
        if isinstance(item, Mapping):
            return {str(key): normalize(value) for key, value in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(value) for value in item]
        return item

    return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class FissionRegistryService:
    """Validates untrusted candidates and manages the global append-only registry."""

    def __init__(self, repository: PodCustomizationRepository) -> None:
        self.repository = repository

    @staticmethod
    def validate_candidates(candidates: Mapping[str, Any] | FissionRegistryCandidates) -> ValidatedRegistryCandidates:
        parsed = candidates if isinstance(candidates, FissionRegistryCandidates) else FissionRegistryCandidates.model_validate(candidates)
        capability_ids = [item.candidate_id for item in parsed.capabilities]
        rule_ids = [item.rule_id for item in parsed.rules]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("duplicate capability candidate ID")
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("duplicate rule candidate ID")
        known_capabilities = set(capability_ids)
        for mapping in parsed.product_mappings:
            unknown = set(mapping.capability_ids) - known_capabilities
            if unknown:
                raise ValueError(f"product mapping references unknown capabilities: {sorted(unknown)}")
        for rule in parsed.rules:
            unknown = set(rule.capability_ids) - known_capabilities
            if unknown:
                raise ValueError(f"rule references unknown capabilities: {sorted(unknown)}")

        capabilities = sorted(
            (item.model_dump(mode="json") for item in parsed.capabilities), key=lambda item: item["candidate_id"]
        )
        product_mappings = sorted(
            (item.model_dump(mode="json") for item in parsed.product_mappings),
            key=lambda item: (item["product_type"], tuple(item["capability_ids"])),
        )
        rules = sorted((item.model_dump(mode="json") for item in parsed.rules), key=lambda item: item["rule_id"])
        payload = {
            "capabilities": capabilities,
            "product_mappings": product_mappings,
            "rules": rules,
        }
        content_hash = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        return ValidatedRegistryCandidates(
            capabilities=tuple(capabilities),
            product_mappings=tuple(product_mappings),
            rules=tuple(rules),
            content_hash=content_hash,
        )

    def publish_candidates(
        self,
        candidates: ValidatedRegistryCandidates,
        *,
        analysis_id: str = "",
        input_sha256: str = "",
    ) -> FissionRegistrySnapshot:
        if not isinstance(candidates, ValidatedRegistryCandidates):
            candidates = self.validate_candidates(candidates)
        capability_payload = {
            "capabilities": list(candidates.capabilities),
            "product_mappings": list(candidates.product_mappings),
        }
        rule_payload = {"rules": list(candidates.rules)}
        cap_hash = hashlib.sha256(_canonical_json(capability_payload).encode("utf-8")).hexdigest()
        rule_hash = hashlib.sha256(_canonical_json(rule_payload).encode("utf-8")).hexdigest()
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = PodCustomizationRepository.get_fission_registry_snapshot(connection)
            capability_payload = self._merge_capability_payload(
                {"capabilities": list(current["capabilities"]), "product_mappings": list(current["product_mappings"])},
                capability_payload,
            )
            rule_payload = self._merge_rule_payload(
                {"rules": list(current["rules"])},
                rule_payload,
            )
            cap_hash = hashlib.sha256(_canonical_json(capability_payload).encode("utf-8")).hexdigest()
            rule_hash = hashlib.sha256(_canonical_json(rule_payload).encode("utf-8")).hexdigest()
            capability_version = PodCustomizationRepository.append_fission_registry_version(
                connection,
                content_hash=cap_hash,
                content_json=_canonical_json(capability_payload),
                analysis_id=analysis_id,
                input_sha256=input_sha256,
                capability=True,
            )
            rule_version = PodCustomizationRepository.append_fission_registry_version(
                connection,
                content_hash=rule_hash,
                content_json=_canonical_json(rule_payload),
                analysis_id=analysis_id,
                input_sha256=input_sha256,
                capability=False,
            )
            PodCustomizationRepository.ensure_fission_registry_pair_allowed(connection, capability_version, rule_version)
            connection.execute(
                "INSERT INTO pod_customization_learning_events "
                "(analysis_id, input_sha256, candidate_json, capability_version, rule_version, status, reason) "
                "VALUES (?, ?, ?, ?, ?, 'published', '')",
                (analysis_id, input_sha256, _canonical_json(candidates.to_payload()), capability_version, rule_version),
            )
            snapshot = PodCustomizationRepository.get_fission_registry_snapshot(
                connection, capability_version=capability_version, rule_version=rule_version
            )
        return FissionRegistrySnapshot(**snapshot)

    def preview_candidates(
        self,
        candidates: ValidatedRegistryCandidates | Mapping[str, Any],
    ) -> tuple[FissionRegistrySnapshot, FissionRegistrySnapshot]:
        validated = candidates if isinstance(candidates, ValidatedRegistryCandidates) else self.validate_candidates(candidates)
        current = self.get_current_snapshot()
        capability_payload = self._merge_capability_payload(
            {"capabilities": list(current.capabilities), "product_mappings": list(current.product_mappings)},
            {"capabilities": list(validated.capabilities), "product_mappings": list(validated.product_mappings)},
        )
        rule_payload = self._merge_rule_payload(
            {"rules": list(current.rules)}, {"rules": list(validated.rules)}
        )
        capability_version = current.capability_version
        rule_version = current.rule_version
        current_capability_payload = {
            "capabilities": list(current.capabilities),
            "product_mappings": list(current.product_mappings),
        }
        if current.capability_version == 0 or _canonical_json(capability_payload) != _canonical_json(current_capability_payload):
            capability_hash = hashlib.sha256(_canonical_json(capability_payload).encode("utf-8")).hexdigest()
            with self.repository._connect() as connection:
                row = connection.execute(
                    "SELECT version FROM pod_customization_capability_registry_versions WHERE content_hash = ?",
                    (capability_hash,),
                ).fetchone()
                maximum = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM pod_customization_capability_registry_versions"
                ).fetchone()
            capability_version = int(row[0]) if row else int(maximum[0]) + 1
        current_rule_payload = {"rules": list(current.rules)}
        if current.rule_version == 0 or _canonical_json(rule_payload) != _canonical_json(current_rule_payload):
            rule_hash = hashlib.sha256(_canonical_json(rule_payload).encode("utf-8")).hexdigest()
            with self.repository._connect() as connection:
                row = connection.execute(
                    "SELECT version FROM pod_customization_rule_registry_versions WHERE content_hash = ?",
                    (rule_hash,),
                ).fetchone()
                maximum = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM pod_customization_rule_registry_versions"
                ).fetchone()
            rule_version = int(row[0]) if row else int(maximum[0]) + 1
        with self.repository._connect() as connection:
            PodCustomizationRepository.ensure_fission_registry_pair_allowed(connection, capability_version, rule_version)
        projected = FissionRegistrySnapshot(
            capabilities=tuple(capability_payload["capabilities"]),
            product_mappings=tuple(capability_payload["product_mappings"]),
            rules=tuple(rule_payload["rules"]),
            capability_version=capability_version,
            rule_version=rule_version,
        )
        return current, projected

    @staticmethod
    def _merge_capability_payload(current: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
        capabilities = {str(item["candidate_id"]): dict(item) for item in current.get("capabilities", [])}
        capabilities.update({str(item["candidate_id"]): dict(item) for item in incoming.get("capabilities", [])})
        mappings: dict[str, dict[str, Any]] = {
            str(item["product_type"]): dict(item) for item in current.get("product_mappings", [])
        }
        for item in incoming.get("product_mappings", []):
            key = str(item["product_type"])
            previous = mappings.get(key, {})
            merged_ids = list(dict.fromkeys([*previous.get("capability_ids", []), *item.get("capability_ids", [])]))
            mappings[key] = {**previous, **dict(item), "capability_ids": merged_ids}
        return {
            "capabilities": sorted(capabilities.values(), key=lambda item: item["candidate_id"]),
            "product_mappings": sorted(mappings.values(), key=lambda item: item["product_type"]),
        }

    @staticmethod
    def _merge_rule_payload(current: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
        rules = {str(item["rule_id"]): dict(item) for item in current.get("rules", [])}
        rules.update({str(item["rule_id"]): dict(item) for item in incoming.get("rules", [])})
        return {"rules": sorted(rules.values(), key=lambda item: item["rule_id"])}

    def get_current_snapshot(self) -> FissionRegistrySnapshot:
        with self.repository._connect() as connection:
            snapshot = PodCustomizationRepository.get_fission_registry_snapshot(connection)
        return FissionRegistrySnapshot(**snapshot)

    def record_variant_outcome(
        self,
        *,
        batch_id: str,
        style_index: int,
        capability_version: int,
        rule_version: int,
        outcome: str,
        failure_kind: str = "",
    ) -> bool:
        """Record one terminal variant result and roll back after three quality failures.

        The unique event key makes worker retries idempotent. Billing, authorization,
        cancellation, infrastructure, and title failures are retained for audit but
        never contribute to the quality-failure streak.
        """
        outcome_value = str(outcome).strip().casefold()
        if outcome_value not in {"succeeded", "completed", "failed", "cancelled"}:
            raise ValueError("variant outcome must be succeeded, completed, failed, or cancelled")
        failure_value = str(failure_kind or "").strip().casefold()
        quality_failure = outcome_value == "failed" and failure_value in {
            "image_generation",
            "artifact_validation",
        }
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT event_id FROM pod_customization_learning_events "
                "WHERE batch_id = ? AND style_index = ? AND capability_version = ? AND rule_version = ?",
                (batch_id, int(style_index), int(capability_version), int(rule_version)),
            ).fetchone()
            if existing is not None:
                return False
            connection.execute(
                "INSERT INTO pod_customization_learning_events "
                "(batch_id, style_index, capability_version, rule_version, status, outcome, failure_kind, reason) "
                "VALUES (?, ?, ?, ?, 'variant_outcome', ?, ?, '')",
                (batch_id, int(style_index), int(capability_version), int(rule_version), outcome_value, failure_value),
            )
            if not quality_failure:
                return False
            current = PodCustomizationRepository.get_fission_registry_snapshot(connection)
            if (
                current["capability_version"] != int(capability_version)
                or current["rule_version"] != int(rule_version)
            ):
                return False
            rows = connection.execute(
                "SELECT outcome, failure_kind FROM pod_customization_learning_events "
                "WHERE capability_version = ? AND rule_version = ? "
                "AND status = 'variant_outcome' ORDER BY event_id DESC",
                (int(capability_version), int(rule_version)),
            ).fetchall()
            streak = 0
            for row in rows:
                if row[0] in {"succeeded", "completed"}:
                    break
                if row[0] == "failed" and row[1] in {"image_generation", "artifact_validation"}:
                    streak += 1
            if streak < 3:
                return False
            previous = connection.execute(
                "SELECT capability_version, rule_version FROM pod_customization_learning_events "
                "WHERE status = 'published' AND NOT (capability_version = ? AND rule_version = ?) "
                "AND NOT EXISTS (SELECT 1 FROM pod_customization_learning_events rejected "
                "WHERE rejected.status='rolled_back' AND rejected.capability_version=pod_customization_learning_events.capability_version "
                "AND rejected.rule_version=pod_customization_learning_events.rule_version) "
                "ORDER BY event_id DESC LIMIT 1",
                (int(capability_version), int(rule_version)),
            ).fetchone()
            rollback_capability = int(previous[0]) if previous else 0
            rollback_rule = int(previous[1]) if previous else 0
            failed_styles = [
                int(item[0])
                for item in connection.execute(
                    "SELECT style_index FROM pod_customization_learning_events "
                    "WHERE capability_version = ? AND rule_version = ? "
                    "AND status = 'variant_outcome' AND outcome = 'failed' "
                    "AND failure_kind IN ('image_generation', 'artifact_validation') "
                    "ORDER BY event_id",
                    (int(capability_version), int(rule_version)),
                ).fetchall()
            ]
            PodCustomizationRepository.rollback_fission_registry(
                connection,
                capability_version=rollback_capability,
                rule_version=rollback_rule,
                failed_style_indices=failed_styles,
                failed_capability_version=capability_version,
                failed_rule_version=rule_version,
            )
            return True

    def rollback_after_failures(
        self,
        capability_version: int,
        rule_version: int,
        failed_style_indices: Sequence[int],
    ) -> bool:
        normalized = sorted({int(index) for index in failed_style_indices if int(index) > 0})
        if not normalized:
            return False
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = PodCustomizationRepository.get_fission_registry_snapshot(connection)
            if (
                current["capability_version"] != int(capability_version)
                or current["rule_version"] != int(rule_version)
            ):
                return False
            prior_rows = connection.execute(
                "SELECT candidate_json FROM pod_customization_learning_events "
                "WHERE capability_version = ? AND rule_version = ? AND status = 'style_failures'",
                (int(capability_version), int(rule_version)),
            ).fetchall()
            observed: set[int] = set(normalized)
            for row in prior_rows:
                try:
                    payload = json.loads(row[0])
                    observed.update(int(index) for index in payload.get("failed_style_indices", []))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
            connection.execute(
                "INSERT INTO pod_customization_learning_events "
                "(candidate_json, capability_version, rule_version, status, reason) VALUES (?, ?, ?, 'style_failures', '')",
                (_canonical_json({"failed_style_indices": normalized}), capability_version, rule_version),
            )
            if len(observed) < 3:
                return False
            failed = sorted(observed)
            prior = connection.execute(
                "SELECT capability_version, rule_version FROM pod_customization_learning_events "
                "WHERE status = 'published' AND NOT (capability_version = ? AND rule_version = ?) "
                "AND NOT EXISTS (SELECT 1 FROM pod_customization_learning_events rejected "
                "WHERE rejected.status='rolled_back' AND rejected.capability_version=pod_customization_learning_events.capability_version "
                "AND rejected.rule_version=pod_customization_learning_events.rule_version) "
                "ORDER BY event_id DESC LIMIT 1",
                (int(capability_version), int(rule_version)),
            ).fetchone()
            rollback_capability = int(prior[0]) if prior else 0
            rollback_rule = int(prior[1]) if prior else 0
            PodCustomizationRepository.rollback_fission_registry(
                connection,
                capability_version=rollback_capability,
                rule_version=rollback_rule,
                failed_style_indices=failed,
                failed_capability_version=capability_version,
                failed_rule_version=rule_version,
            )
            return True


__all__ = [
    "FissionRegistryService",
    "FissionRegistrySnapshot",
    "LearnedCapabilityCandidate",
    "LearnedRuleCandidate",
    "ValidatedRegistryCandidates",
    "RegistryVersionConflict",
]

from .fission_specs import LearnedCapabilityCandidate, LearnedRuleCandidate
