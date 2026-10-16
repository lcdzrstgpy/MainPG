from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from wh_local.modules.pod_customization.fission_registry import FissionRegistryService
from wh_local.modules.pod_customization.repository import PodCustomizationRepository, PodRepositoryError


def _candidate(suffix: str = "") -> dict:
    return {
        "capabilities": [
            {
                "candidate_id": f"cap-cotton{suffix}",
                "capability_type": "material_finish",
                "required_facts": ["material"],
                "optional_facts": [],
                "activation_conditions": [
                    {"field": "material", "operator": "eq", "value": "cotton"}
                ],
                "generated_constraints": ["Show the cotton weave clearly."],
                "forbidden_conditions": [],
                "compatible_templates": ["flat-lay"],
                "incompatible_capabilities": [],
                "generation_strategy": "grid",
                "prompt_slots": ["product_surface"],
            }
        ],
        "product_mappings": [
            {"product_type": "tote_bag", "capability_ids": [f"cap-cotton{suffix}"]}
        ],
        "rules": [
            {
                "rule_id": f"rule-cotton{suffix}",
                "predicate": {"field": "material", "operator": "eq", "value": "cotton"},
                "constraints": ["Keep the printed region unobstructed."],
                "capability_ids": [f"cap-cotton{suffix}"],
            }
        ],
    }


@pytest.fixture
def registry(tmp_path: Path) -> tuple[PodCustomizationRepository, FissionRegistryService]:
    repository = PodCustomizationRepository(tmp_path / "registry.sqlite3")
    return repository, FissionRegistryService(repository)


def test_valid_candidates_are_normalized_and_published_as_a_new_version(registry) -> None:
    repository, service = registry
    validated = service.validate_candidates(_candidate())

    assert len(validated.content_hash) == 64
    assert validated.to_payload()["capabilities"][0]["candidate_id"] == "cap-cotton"
    snapshot = service.publish_candidates(validated, analysis_id="analysis-1", input_sha256="a" * 64)

    assert snapshot.capability_version == 1
    assert snapshot.rule_version == 1
    assert snapshot.capabilities[0]["candidate_id"] == "cap-cotton"
    assert repository.get_fission_registry_snapshot is not None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["capabilities"][0].update(required_facts=["owner_user_id"]),
        lambda payload: payload["rules"][0]["predicate"].update(operator="exec"),
        lambda payload: payload["capabilities"][0].update(generated_constraints=["x" * 1201]),
        lambda payload: payload["rules"][0].update(sql="DROP TABLE users"),
        lambda payload: payload["capabilities"][0].update(code="__import__('os').system('id')"),
        lambda payload: payload["rules"][0].update(url="https://example.com/"),
    ],
)
def test_invalid_candidates_are_rejected_without_changing_current_pointer(registry, mutate) -> None:
    _repository, service = registry
    before = service.get_current_snapshot()
    payload = _candidate()
    mutate(payload)

    with pytest.raises((ValidationError, ValueError)):
        service.validate_candidates(payload)

    assert service.get_current_snapshot() == before


def test_duplicate_candidate_ids_are_rejected(registry) -> None:
    _repository, service = registry
    payload = _candidate()
    payload["capabilities"].append(payload["capabilities"][0].copy())

    with pytest.raises(ValueError, match="duplicate"):
        service.validate_candidates(payload)


def test_identical_publish_is_idempotent_and_history_is_immutable(registry, tmp_path: Path) -> None:
    repository, service = registry
    validated = service.validate_candidates(_candidate())
    first = service.publish_candidates(validated)
    service.publish_candidates(validated)
    second = service.get_current_snapshot()

    assert second.capability_version == first.capability_version == 1
    with sqlite3.connect(repository.database_path) as connection:
        row = connection.execute(
            "SELECT content_json FROM pod_customization_capability_registry_versions WHERE version = 1"
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE pod_customization_capability_registry_versions SET content_json = '{}' WHERE version = 1"
            )
    assert row is not None


def test_concurrent_publications_do_not_lose_versions(registry) -> None:
    _repository, service = registry
    candidates = [service.validate_candidates(_candidate(f"-{index}")) for index in range(6)]

    with ThreadPoolExecutor(max_workers=6) as pool:
        snapshots = list(pool.map(service.publish_candidates, candidates))

    assert sorted(snapshot.capability_version for snapshot in snapshots) == list(range(1, 7))
    assert service.get_current_snapshot().capability_version == 6


def test_three_consecutive_failures_roll_back_only_the_matching_versions(registry) -> None:
    _repository, service = registry
    service.publish_candidates(service.validate_candidates(_candidate()))

    assert service.rollback_after_failures(1, 1, [1, 2]) is False
    assert service.get_current_snapshot().capability_version == 1
    assert service.rollback_after_failures(2, 1, [1, 2, 3]) is False
    assert service.get_current_snapshot().capability_version == 1
    assert service.rollback_after_failures(1, 1, [1, 2, 3]) is True
    snapshot = service.get_current_snapshot()

    assert snapshot.capability_version == 0
    assert snapshot.rule_version == 0


def test_publishing_new_candidates_merges_the_previous_registry(registry) -> None:
    _repository, service = registry
    service.publish_candidates(service.validate_candidates(_candidate()))
    second_payload = _candidate("-second")
    second_payload["product_mappings"].append(
        {"product_type": "mug", "capability_ids": ["cap-cotton-second"]}
    )
    snapshot = service.publish_candidates(service.validate_candidates(second_payload))

    assert {item["candidate_id"] for item in snapshot.capabilities} == {
        "cap-cotton",
        "cap-cotton-second",
    }
    assert {item["product_type"] for item in snapshot.product_mappings} == {"tote_bag", "mug"}


def test_preview_after_rollback_uses_next_append_only_version(registry) -> None:
    repository, service = registry
    service.publish_candidates(service.validate_candidates(_candidate()))
    service.publish_candidates(service.validate_candidates(_candidate("-second")))
    with repository._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository.rollback_fission_registry(connection, capability_version=1, rule_version=1)

    current, projected = service.preview_candidates(service.validate_candidates(_candidate("-third")))
    assert current.capability_version == 1
    assert current.rule_version == 1
    assert projected.capability_version == 3
    assert projected.rule_version == 3


def test_rollback_does_not_reactivate_a_rejected_registry_version(registry) -> None:
    repository, service = registry
    service.publish_candidates(service.validate_candidates(_candidate()))
    service.publish_candidates(service.validate_candidates(_candidate("-second")))
    with repository._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository.rollback_fission_registry(connection, capability_version=1, rule_version=1,
                                              failed_capability_version=2, failed_rule_version=2)
        with pytest.raises(PodRepositoryError, match="回滚"):
            repository.rollback_fission_registry(connection, capability_version=2, rule_version=2)


def test_variant_outcome_streak_ignores_non_quality_failures_and_resets_on_success(registry) -> None:
    _repository, service = registry
    service.publish_candidates(service.validate_candidates(_candidate()))

    assert service.record_variant_outcome(
        batch_id="batch-1", style_index=1, capability_version=1, rule_version=1,
        outcome="failed", failure_kind="authorization",
    ) is False
    assert service.record_variant_outcome(
        batch_id="batch-1", style_index=2, capability_version=1, rule_version=1,
        outcome="failed", failure_kind="image_generation",
    ) is False
    assert service.record_variant_outcome(
        batch_id="batch-1", style_index=3, capability_version=1, rule_version=1,
        outcome="succeeded",
    ) is False
    assert service.record_variant_outcome(
        batch_id="batch-1", style_index=4, capability_version=1, rule_version=1,
        outcome="failed", failure_kind="artifact_validation",
    ) is False
    assert service.record_variant_outcome(
        batch_id="batch-1", style_index=5, capability_version=1, rule_version=1,
        outcome="failed", failure_kind="artifact_validation",
    ) is False
    assert service.record_variant_outcome(
        batch_id="batch-1", style_index=6, capability_version=1, rule_version=1,
        outcome="failed", failure_kind="artifact_validation",
    ) is True
    assert service.record_variant_outcome(
        batch_id="batch-1", style_index=6, capability_version=1, rule_version=1,
        outcome="failed", failure_kind="artifact_validation",
    ) is False
    assert service.get_current_snapshot().capability_version == 0



def test_successive_rollbacks_skip_rejected_pairs(registry):
    _repository, service = registry
    first = service.publish_candidates(service.validate_candidates(_candidate()))
    second = service.publish_candidates(service.validate_candidates(_candidate('-second')))
    for index in range(1, 4):
        service.record_variant_outcome(batch_id='second', style_index=index,
            capability_version=second.capability_version, rule_version=second.rule_version,
            outcome='failed', failure_kind='artifact_validation')
    third = service.publish_candidates(service.validate_candidates(_candidate('-third')))
    for index in range(1, 4):
        service.record_variant_outcome(batch_id='third', style_index=index,
            capability_version=third.capability_version, rule_version=third.rule_version,
            outcome='failed', failure_kind='artifact_validation')
    assert service.get_current_snapshot() == first


def test_rejected_content_cannot_be_reactivated_or_previewed(registry):
    from wh_local.modules.pod_customization.repository import PodRepositoryError
    _repository, service = registry
    candidate = service.validate_candidates(_candidate())
    snapshot = service.publish_candidates(candidate)
    for index in range(1, 4):
        service.record_variant_outcome(batch_id='rejected', style_index=index,
            capability_version=snapshot.capability_version, rule_version=snapshot.rule_version,
            outcome='failed', failure_kind='artifact_validation')
    with pytest.raises(PodRepositoryError, match='回滚'):
        service.preview_candidates(candidate)
    with pytest.raises(PodRepositoryError, match='回滚'):
        service.publish_candidates(candidate)
    assert service.get_current_snapshot().capability_version == 0
