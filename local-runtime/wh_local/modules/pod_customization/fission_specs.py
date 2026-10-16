from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from .replica_specs import SOURCE_FACT_FIELDS, TARGET_FACT_FIELDS


FACT_FIELDS = frozenset((*SOURCE_FACT_FIELDS, *TARGET_FACT_FIELDS,
                         "artwork_style", "artwork_elements", "artwork_composition",
                         "selling_points", "source_selling_points", "manufacturing_process"))
PREDICATE_OPERATORS = frozenset({"eq", "in", "contains", "gt", "gte", "exists"})
MAX_CONSTRAINT_LENGTH = 1200
MAX_CANDIDATES_PER_KIND = 200


class _RegistryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RegistryPredicate(_RegistryModel):
    field: str = Field(
        min_length=1,
        max_length=100,
        validation_alias=AliasChoices("field", "fact_field"),
    )
    operator: str = Field(min_length=1, max_length=20)
    value: Any = None

    @field_validator("field")
    @classmethod
    def fact_field_only(cls, value: str) -> str:
        if value not in FACT_FIELDS:
            raise ValueError(f"unknown fact field: {value}")
        return value

    @field_validator("operator")
    @classmethod
    def supported_operator_only(cls, value: str) -> str:
        if value not in PREDICATE_OPERATORS:
            raise ValueError(f"unknown predicate operator: {value}")
        return value

    @model_validator(mode="after")
    def validate_value_shape(self) -> "RegistryPredicate":
        _validate_untrusted_value(self.value)
        if self.operator == "in" and not isinstance(self.value, (list, tuple)):
            raise ValueError("in predicate value must be a list")
        if self.operator in {"gt", "gte"} and (
            isinstance(self.value, bool) or not isinstance(self.value, (int, float))
        ):
            raise ValueError(f"{self.operator} predicate value must be numeric")
        if self.operator == "exists" and self.value is not None and not isinstance(self.value, bool):
            raise ValueError("exists predicate value must be boolean")
        return self


class LearnedCapabilityCandidate(_RegistryModel):
    candidate_id: str = Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,99}$",
        validation_alias=AliasChoices("candidate_id", "capability_id"),
    )
    capability_type: str = Field(min_length=1, max_length=100)
    required_facts: tuple[str, ...] = Field(default=(), max_length=32)
    optional_facts: tuple[str, ...] = Field(default=(), max_length=32)
    activation_conditions: tuple[RegistryPredicate, ...] = Field(default=(), max_length=32)
    generated_constraints: tuple[str, ...] = Field(default=(), max_length=32)
    forbidden_conditions: tuple[str, ...] = Field(default=(), max_length=32)
    compatible_templates: tuple[str, ...] = Field(default=(), max_length=32)
    incompatible_capabilities: tuple[str, ...] = Field(default=(), max_length=32)
    generation_strategy: Literal["grid", "independent_panels"] = "grid"
    prompt_slots: tuple[str, ...] = Field(default=(), max_length=32)

    @property
    def capability_id(self) -> str:
        return self.candidate_id

    @field_validator("required_facts", "optional_facts")
    @classmethod
    def validate_fact_lists(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if value not in FACT_FIELDS:
                raise ValueError(f"unknown fact field: {value}")
        return tuple(sorted(set(values)))

    @field_validator("generated_constraints", "forbidden_conditions")
    @classmethod
    def validate_constraints(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        for value in values:
            _validate_untrusted_text(value)
            if len(value) > MAX_CONSTRAINT_LENGTH:
                raise ValueError(f"constraint exceeds {MAX_CONSTRAINT_LENGTH} characters")
            cleaned.append(value)
        return tuple(sorted(set(cleaned)))


class ProductMappingCandidate(_RegistryModel):
    product_type: str = Field(min_length=1, max_length=100)
    capability_ids: tuple[str, ...] = Field(min_length=1, max_length=100)


class LearnedRuleCandidate(_RegistryModel):
    rule_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,99}$")
    predicate: RegistryPredicate
    constraints: tuple[str, ...] = Field(default=(), max_length=32)
    capability_ids: tuple[str, ...] = Field(default=(), max_length=100)

    @field_validator("constraints")
    @classmethod
    def validate_constraints(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        for value in values:
            _validate_untrusted_text(value)
            if len(value) > MAX_CONSTRAINT_LENGTH:
                raise ValueError(f"constraint exceeds {MAX_CONSTRAINT_LENGTH} characters")
            cleaned.append(value)
        return tuple(sorted(set(cleaned)))


class FissionRegistryCandidates(_RegistryModel):
    capabilities: tuple[LearnedCapabilityCandidate, ...] = Field(default=(), max_length=MAX_CANDIDATES_PER_KIND)
    product_mappings: tuple[ProductMappingCandidate, ...] = Field(default=(), max_length=MAX_CANDIDATES_PER_KIND)
    rules: tuple[LearnedRuleCandidate, ...] = Field(default=(), max_length=MAX_CANDIDATES_PER_KIND)


class ValidatedRegistryCandidates(_RegistryModel):
    capabilities: tuple[dict[str, Any], ...]
    product_mappings: tuple[dict[str, Any], ...]
    rules: tuple[dict[str, Any], ...]
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    def to_payload(self) -> dict[str, Any]:
        return {
            "capabilities": [dict(item) for item in self.capabilities],
            "product_mappings": [dict(item) for item in self.product_mappings],
            "rules": [dict(item) for item in self.rules],
        }


class FissionRegistrySnapshot(_RegistryModel):
    capabilities: tuple[dict[str, Any], ...] = ()
    product_mappings: tuple[dict[str, Any], ...] = ()
    rules: tuple[dict[str, Any], ...] = ()
    capability_version: int = 0
    rule_version: int = 0


def _validate_untrusted_text(value: str) -> None:
    lowered = value.casefold()
    prohibited = (
        "__import__",
        "import ",
        "exec(",
        "eval(",
        "lambda ",
        "def ",
        "class ",
        "subprocess",
        "system(",
        "open(",
        "sql",
        "python",
        "javascript",
        "shell",
        "drop table",
        "delete from",
        "insert into",
        "update ",
        "select ",
        "http://",
        "https://",
        ";",
        "```",
    )
    if any(token in lowered for token in prohibited):
        raise ValueError("constraint contains executable or external-action content")


def _validate_untrusted_value(value: Any) -> None:
    if isinstance(value, str):
        if len(value) > MAX_CONSTRAINT_LENGTH:
            raise ValueError(f"predicate value exceeds {MAX_CONSTRAINT_LENGTH} characters")
        _validate_untrusted_text(value)
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, (list, tuple)):
        if len(value) > 100:
            raise ValueError("predicate list value exceeds 100 items")
        for item in value:
            _validate_untrusted_value(item)
        return
    raise ValueError("predicate value must contain only JSON scalar or list values")
