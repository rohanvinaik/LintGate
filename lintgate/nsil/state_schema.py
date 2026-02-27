import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# Type aliases for better readability
GateStatus = Literal["pass", "fail", "degraded", "unknown"]
RiskLevel = Literal["low", "medium", "high", "critical", "unknown"]


# Protocol for compact serialization
class CompactSerializer(Protocol):
    def serialize_compact(
        self, output_format: Literal["structured_text", "json_flat", "kv_pairs"], budget: int
    ) -> str: ...

    @classmethod
    def from_compact(cls, data: str, output_format: Literal["json_flat", "kv_pairs"]) -> Any: ...


@dataclass(frozen=True)
class InferenceStateSnapshot:
    """
    A compact snapshot of the agent's inference state, designed for deterministic
    serialization and token budgeting.
    """

    gate_status: GateStatus = "unknown"
    blocking_findings: list[str] = field(default_factory=list)
    mutation_summary: dict[str, Any] = field(default_factory=dict)
    active_constraints: list[str] = field(default_factory=list)
    prediction_accuracy: float = 0.0
    risk_level: RiskLevel = "unknown"
    token_count: int = 0

    def serialize_compact(
        self,
        output_format: Literal["structured_text", "json_flat", "kv_pairs"] = "structured_text",
        budget: int = 4096,  # Default budget in characters
    ) -> str:
        """
        Serializes the snapshot into a compact, deterministic string representation.
        Applies budgeting to ensure output stays within character limits by prioritizing
        fields for truncation. Budget is measured in characters.
        """
        if budget <= 0:
            return ""

        # Deep copy and sort to ensure deterministic serialization of lists
        _blocking_findings = sorted(self.blocking_findings)
        _mutation_summary = dict(sorted(self.mutation_summary.items()))
        _active_constraints = sorted(self.active_constraints)

        # Initial data for serialization, before any truncation
        initial_serializable_data: dict[str, Any] = {
            "gate_status": self.gate_status,
            "risk_level": self.risk_level,
            "blocking_findings": _blocking_findings,
            "mutation_summary": _mutation_summary,
            "active_constraints": _active_constraints,
            "prediction_accuracy": self.prediction_accuracy,
        }

        # Field priority for removal (from least important to most important)
        # Fields listed earlier will be removed first if budget is exceeded.
        removal_priority = [
            "active_constraints",
            "mutation_summary",
            "blocking_findings",
        ]

        # Use a mutable copy of the initial data for budgeting decisions
        current_data_for_budgeting = initial_serializable_data.copy()

        # Iteratively remove fields based on priority until budget is met
        for field_name_to_remove in removal_priority:
            # Check current size in characters
            current_serialized = self._serialize_to_format(current_data_for_budgeting, format)
            if len(current_serialized) <= budget:
                break

            # If over budget, remove the current field
            if field_name_to_remove in current_data_for_budgeting:
                del current_data_for_budgeting[field_name_to_remove]

        # Calculate final serialized string for token count estimation
        final_serialized = self._serialize_to_format(current_data_for_budgeting, format)

        # Recalculate token_count based on the final serialized string (word count as proxy)
        current_data_for_budgeting["token_count"] = len(final_serialized.split())

        # Re-serialize with the correct token_count
        return self._serialize_to_format(current_data_for_budgeting, format)

    def _serialize_to_format(
        self, data: dict[str, Any], output_format: Literal["structured_text", "json_flat", "kv_pairs"]
    ) -> str:
        """Helper to serialize data dictionary to the specified format."""
        if format == "json_flat":
            # Ensure deterministic JSON serialization
            return json.dumps(data, sort_keys=True, separators=(",", ":"))
        elif format == "kv_pairs":
            # Deterministic key-value pair serialization
            pairs = []
            for k, v in sorted(data.items()):
                if isinstance(v, list):
                    pairs.append(
                        f"{k}=[{','.join(map(str, sorted(v)))}]"
                    )  # Ensure lists are sorted
                elif isinstance(v, dict):
                    # For nested dicts, convert to flat k=v pairs or just repr
                    pairs.append(f"{k}={json.dumps(v, sort_keys=True, separators=(',', ':'))}")
                else:
                    pairs.append(f"{k}={v}")
            return " ".join(pairs)
        elif format == "structured_text":
            lines = [
                f"Gate Status: {data.get('gate_status', 'unknown')}",
                f"Risk Level: {data.get('risk_level', 'unknown')}",
            ]
            if data.get("blocking_findings"):
                lines.append(
                    f"Blocking Findings: {', '.join(sorted(data['blocking_findings']))}"
                )  # Ensure lists are sorted
            if data.get("mutation_summary"):
                lines.append(
                    f"Mutation Summary: {json.dumps(data['mutation_summary'], sort_keys=True, indent=2)}"
                )
            if data.get("active_constraints"):
                lines.append(
                    f"Active Constraints: {', '.join(sorted(data['active_constraints']))}"
                )  # Ensure lists are sorted
            lines.append(f"Prediction Accuracy: {data.get('prediction_accuracy', 0.0):.2f}")
            lines.append(f"Token Count: {data.get('token_count', 0)}")
            return "\n".join(lines)
        else:
            raise ValueError(f"Unsupported format: {format}")

    @staticmethod
    def _parse_kv_pair_value(value: str) -> Any:
        """Helper to parse and convert a string value from a KV pair."""
        try:
            if value.startswith("[") and value.endswith("]"):
                if value[1:-1].strip() == "":
                    return []
                return [v.strip() for v in value[1:-1].split(",")]
            elif value.startswith("{") and value.endswith("}"):
                return json.loads(value)
            else:
                if "." in value:
                    return float(value)
                return int(value)
        except ValueError:
            return value

    @classmethod
    def from_compact(
        cls, data: str, output_format: Literal["json_flat", "kv_pairs"]
    ) -> "InferenceStateSnapshot":
        """
        Deserializes a compact string representation back into an InferenceStateSnapshot.
        """
        if format == "json_flat":
            parsed_data = json.loads(data)
        elif format == "kv_pairs":
            parsed_data = {}
            for item in data.split(" "):
                if "=" in item:
                    key, value_str = item.split("=", 1)
                    parsed_data[key] = cls._parse_kv_pair_value(value_str)

        else:
            raise ValueError(f"Unsupported format for from_compact: {format}")

        # Ensure all fields are present or default, and convert types as necessary
        # The .get() with default values handles cases where fields might be missing due to truncation
        return cls(
            gate_status=parsed_data.get("gate_status", "unknown"),
            blocking_findings=parsed_data.get("blocking_findings", []),
            mutation_summary=parsed_data.get("mutation_summary", {}),
            active_constraints=parsed_data.get("active_constraints", []),
            prediction_accuracy=float(parsed_data.get("prediction_accuracy", 0.0)),
            risk_level=parsed_data.get("risk_level", "unknown"),
            token_count=int(parsed_data.get("token_count", 0)),
        )
