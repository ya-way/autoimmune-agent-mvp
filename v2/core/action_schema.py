from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


def _match_type(value: Any, type_name: str) -> bool:
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "object":
        return isinstance(value, dict)
    return True


@dataclass
class ActionSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    category: str
    safe_for_benchmark: bool
    safe_for_ask: bool
    callable: Callable[..., Any] = field(repr=False, compare=False)

    def validate_args(self, args: dict[str, Any]) -> tuple[bool, str]:
        if not isinstance(args, dict):
            return False, "args must be a JSON object"
        required = self.input_schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if key not in args:
                    return False, f"missing required arg: {key}"
        props = self.input_schema.get("properties", {})
        if isinstance(props, dict):
            for key, rule in props.items():
                if key not in args:
                    continue
                if not isinstance(rule, dict):
                    continue
                expected = str(rule.get("type", "")).strip()
                if expected and not _match_type(args[key], expected):
                    return False, f"arg {key} expects type {expected}"
                if expected == "array" and isinstance(args[key], list):
                    min_items = rule.get("minItems")
                    if isinstance(min_items, int) and len(args[key]) < min_items:
                        return False, f"arg {key} expects at least {min_items} items"
                    max_items = rule.get("maxItems")
                    if isinstance(max_items, int) and len(args[key]) > max_items:
                        return False, f"arg {key} expects at most {max_items} items"
                    items_rule = rule.get("items", {})
                    if isinstance(items_rule, dict) and str(items_rule.get("type", "")).strip() == "object":
                        required_item_fields = items_rule.get("required", [])
                        item_props = items_rule.get("properties", {})
                        for idx, item in enumerate(args[key]):
                            if not isinstance(item, dict):
                                return False, f"arg {key}[{idx}] expects object"
                            if isinstance(required_item_fields, list):
                                for req_field in required_item_fields:
                                    if req_field not in item:
                                        return False, f"arg {key}[{idx}] missing required field {req_field}"
                            if isinstance(item_props, dict):
                                for prop_name, prop_rule in item_props.items():
                                    if prop_name not in item or not isinstance(prop_rule, dict):
                                        continue
                                    expected_prop_type = str(prop_rule.get("type", "")).strip()
                                    if expected_prop_type and not _match_type(item[prop_name], expected_prop_type):
                                        return False, f"arg {key}[{idx}].{prop_name} expects type {expected_prop_type}"
        return True, ""
