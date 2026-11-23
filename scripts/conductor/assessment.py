from __future__ import annotations

from typing import Any

TIER_RANK = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}
SCOPES = {"trivial", "localized", "multi_module", "architecture"}
CONFIDENCES = {"low", "medium", "high"}
REQUIRED_BOOLEANS = (
    "behavior_change",
    "ui",
    "design_change",
    "new_capability",
    "data_model_change",
    "multi_page",
    "high_risk",
)


class AssessmentError(ValueError):
    """Raised when a semantic route assessment is incomplete or contradictory."""


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssessmentError(f"{name} must be a non-empty string")
    return value.strip()


def _text_list(value: Any, name: str, *, required: bool = True) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list):
        raise AssessmentError(f"{name} must be a list of strings")
    result = [str(item).strip() for item in value if str(item).strip()]
    if required and not result:
        raise AssessmentError(f"{name} must contain at least one item")
    return result


def derive_tier(scope: str, new_capability: bool, data_model_change: bool, multi_page: bool) -> str:
    if scope == "architecture":
        return "T3"
    if scope == "multi_module" or new_capability or data_model_change or multi_page:
        return "T2"
    if scope == "trivial":
        return "T0"
    return "T1"


def validate_assessment(
    payload: Any,
    *,
    task: str,
    heuristic_tier: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AssessmentError("assessment must be a JSON object")

    summary = _text(payload.get("summary"), "summary")
    scope = _text(payload.get("scope"), "scope")
    if scope not in SCOPES:
        raise AssessmentError(f"scope must be one of: {', '.join(sorted(SCOPES))}")

    flags: dict[str, bool] = {}
    for name in REQUIRED_BOOLEANS:
        value = payload.get(name)
        if not isinstance(value, bool):
            raise AssessmentError(f"{name} must be a boolean")
        flags[name] = value

    confidence = _text(payload.get("confidence"), "confidence")
    if confidence not in CONFIDENCES:
        raise AssessmentError("confidence must be one of: high, medium, low")
    if confidence == "low":
        raise AssessmentError(
            "confidence is low; ask the user a focused clarifying question before routing"
        )

    affected_areas = _text_list(payload.get("affected_areas"), "affected_areas")
    reasons = _text_list(payload.get("reasons"), "reasons")
    unknowns = _text_list(payload.get("unknowns"), "unknowns", required=False)

    if flags["design_change"] and not flags["ui"]:
        raise AssessmentError("design_change requires ui=true")
    if scope == "trivial" and flags["behavior_change"]:
        raise AssessmentError("scope=trivial cannot have behavior_change=true")

    tier = derive_tier(
        scope,
        flags["new_capability"],
        flags["data_model_change"],
        flags["multi_page"],
    )
    if flags["high_risk"]:
        tier = "T3"

    declared_tier = payload.get("declared_tier")
    if declared_tier is not None and declared_tier != tier:
        raise AssessmentError(
            f"declared_tier {declared_tier!r} conflicts with derived tier {tier}; omit declared_tier or fix the assessment"
        )

    if (
        heuristic_tier in TIER_RANK
        and TIER_RANK[tier] < TIER_RANK[heuristic_tier]
        and confidence != "high"
    ):
        raise AssessmentError(
            f"confidence must be high when downgrading heuristic tier {heuristic_tier} to {tier}"
        )

    return {
        "summary": summary,
        "scope": scope,
        "tier": tier,
        "confidence": confidence,
        **flags,
        "reuse_design": flags["ui"] and not flags["design_change"],
        "affected_areas": affected_areas,
        "reasons": reasons,
        "unknowns": unknowns,
        "task": task,
    }
