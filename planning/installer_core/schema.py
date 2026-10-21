"""Fail-closed validation of TrueNAS middleware positional request schemas.

Adapted from the HAOS installer validator. This is a deliberately bounded
middleware-schema subset, not a general JSON Schema implementation.
"""
from __future__ import annotations

import math
import re
from typing import Any


class SchemaError(ValueError):
    """Payload or middleware schema does not match the supported contract."""


MAX_SCHEMA_DEPTH = 64


def _json_equal(left: Any, right: Any) -> bool:
    # In JSON, true is not the integer 1 even though Python considers them equal.
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _resolve_ref(ref: Any, root: Any) -> Any:
    if not isinstance(ref, str) or not ref.startswith("#/") or root is None:
        raise KeyError("only local JSON pointer references are supported")
    target = root
    for part in ref[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        target = target[key]
    return target


SUPPORTED_SCHEMA_KEYWORDS = frozenset({
    "$ref", "type", "const", "enum", "oneOf", "anyOf", "allOf", "properties",
    "required", "additionalProperties", "items", "additionalItems", "minItems",
    "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "minimum",
    "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
})
UNSUPPORTED_SCHEMA_KEYWORDS = frozenset({
    "format", "not", "if", "then", "else", "patternProperties", "propertyNames",
    "dependencies", "dependentSchemas", "contains", "minContains", "maxContains",
    "minProperties", "maxProperties", "unevaluatedProperties", "unevaluatedItems",
    "prefixItems", "contentEncoding", "contentMediaType", "contentSchema", "nullable",
    "readOnly", "writeOnly",
})
_SCHEMA_ANNOTATIONS = frozenset({
    "_name_", "_required_", "_attrs_order_", "title", "description", "default",
    "example", "examples", "deprecated", "$comment", "$id", "$schema", "discriminator",
})


def normalize_schema_contract(schema: Any, path: str = "argument", _depth: int = 0) -> dict[str, Any]:
    """Return a constraint-only schema shape, rejecting unsupported keywords."""
    if _depth > MAX_SCHEMA_DEPTH:
        raise SchemaError(f"{path}: middleware schema exceeds the nesting limit")
    if not isinstance(schema, dict):
        raise SchemaError(f"{path}: middleware schema node is malformed")
    unsupported = sorted(set(schema) & UNSUPPORTED_SCHEMA_KEYWORDS)
    if unsupported:
        raise SchemaError(f"{path}: unsupported middleware schema keyword {unsupported[0]!r}")
    unknown = sorted(set(schema) - SUPPORTED_SCHEMA_KEYWORDS - _SCHEMA_ANNOTATIONS)
    if unknown:
        raise SchemaError(f"{path}: unrecognized middleware schema keyword {unknown[0]!r}")

    normalized: dict[str, Any] = {}
    for keyword in sorted(set(schema) & SUPPORTED_SCHEMA_KEYWORDS):
        value = schema[keyword]
        if keyword == "properties":
            if not isinstance(value, dict):
                raise SchemaError(f"{path}: middleware properties schema is malformed")
            normalized[keyword] = {
                name: normalize_schema_contract(child, f"{path}.{keyword}.{name}", _depth + 1)
                for name, child in value.items()
            }
        elif keyword in {"items", "additionalItems", "additionalProperties"} and isinstance(value, dict):
            normalized[keyword] = normalize_schema_contract(value, f"{path}.{keyword}", _depth + 1)
        elif keyword == "items" and isinstance(value, list):
            normalized[keyword] = [
                normalize_schema_contract(child, f"{path}.{keyword}[{index}]", _depth + 1)
                for index, child in enumerate(value)
            ]
        elif keyword == "additionalItems" and isinstance(value, list):
            raise SchemaError(f"{path}: middleware additionalItems definition is malformed")
        elif keyword in {"items", "additionalItems", "additionalProperties"} and type(value) is bool:
            normalized[keyword] = value
        elif keyword in {"items", "additionalItems", "additionalProperties"}:
            raise SchemaError(f"{path}: middleware {keyword} definition is malformed")
        elif keyword in {"oneOf", "anyOf", "allOf"}:
            if not isinstance(value, list) or not value:
                raise SchemaError(f"{path}: middleware {keyword} definition is malformed")
            normalized[keyword] = [
                normalize_schema_contract(child, f"{path}.{keyword}[{index}]", _depth + 1)
                for index, child in enumerate(value)
            ]
        elif keyword == "required":
            if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
                raise SchemaError(f"{path}: middleware required list is malformed")
            if len(set(value)) != len(value):
                raise SchemaError(f"{path}: middleware required list contains duplicates")
            normalized[keyword] = sorted(value)
        elif keyword == "type":
            choices = value if isinstance(value, list) else [value]
            valid_types = {"object", "array", "string", "integer", "number", "boolean", "null"}
            if not choices or any(not isinstance(kind, str) or kind not in valid_types for kind in choices):
                raise SchemaError(f"{path}: middleware type definition is malformed")
            normalized[keyword] = value
        elif keyword == "enum":
            if not isinstance(value, list):
                raise SchemaError(f"{path}: middleware enum is malformed")
            normalized[keyword] = value
        elif keyword == "$ref":
            if not isinstance(value, str) or not value.startswith("#/"):
                raise SchemaError(f"{path}: unresolved or unsupported middleware schema reference")
            normalized[keyword] = value
        elif keyword == "pattern":
            if not isinstance(value, str):
                raise SchemaError(f"{path}: middleware pattern is malformed")
            try:
                re.compile(value)
            except re.error as exc:
                raise SchemaError(f"{path}: middleware pattern is malformed") from exc
            normalized[keyword] = value
        elif keyword in {"minItems", "maxItems", "minLength", "maxLength"}:
            if type(value) is not int or value < 0:
                raise SchemaError(f"{path}: middleware {keyword} bound is malformed")
            normalized[keyword] = value
        elif keyword in {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise SchemaError(f"{path}: middleware {keyword} bound is malformed")
            if keyword == "multipleOf" and value <= 0:
                raise SchemaError(f"{path}: middleware multipleOf bound must be positive")
            normalized[keyword] = value
        elif keyword == "uniqueItems":
            if type(value) is not bool:
                raise SchemaError(f"{path}: middleware uniqueItems value is malformed")
            normalized[keyword] = value
        else:
            normalized[keyword] = value
    if "_name_" in schema:
        if not isinstance(schema["_name_"], str):
            raise SchemaError(f"{path}: middleware argument name is malformed")
        normalized["_name_"] = schema["_name_"]
    if "_required_" in schema:
        if type(schema["_required_"]) is not bool:
            raise SchemaError(f"{path}: middleware required marker is malformed")
        normalized["_required_"] = schema["_required_"]
    for minimum, maximum in (("minItems", "maxItems"), ("minLength", "maxLength")):
        if minimum in normalized and maximum in normalized and normalized[minimum] > normalized[maximum]:
            raise SchemaError(f"{path}: middleware {minimum}/{maximum} bounds are contradictory")
    if "minimum" in normalized and "maximum" in normalized and normalized["minimum"] > normalized["maximum"]:
        raise SchemaError(f"{path}: middleware minimum/maximum bounds are contradictory")
    if "discriminator" in schema:
        discriminator = schema["discriminator"]
        if not isinstance(discriminator, dict) or not isinstance(discriminator.get("propertyName"), str):
            raise SchemaError(f"{path}: middleware discriminator is malformed")
        mapping = discriminator.get("mapping", {})
        if not isinstance(mapping, dict) or any(not isinstance(key, str) or not isinstance(ref, str) for key, ref in mapping.items()):
            raise SchemaError(f"{path}: middleware discriminator mapping is malformed")
        normalized["discriminator"] = {
            "propertyName": discriminator["propertyName"],
            "mapping": dict(sorted(mapping.items())),
        }
    return normalized


def normalize_method_contract(metadata: Any, method: str) -> dict[str, Any]:
    """Normalize accepted request schemas and local definitions for comparison."""
    accepts = _accepted_args(metadata, method)
    normalized: dict[str, Any] = {
        "accepts": [normalize_schema_contract(item, f"{method}.accepts[{index}]") for index, item in enumerate(accepts)]
    }
    if not isinstance(metadata, dict):
        raise SchemaError(f"runtime schema for {method} is missing or malformed")
    for key in ("$defs", "definitions"):
        definitions = metadata.get(key)
        if definitions is not None:
            if not isinstance(definitions, dict):
                raise SchemaError(f"runtime schema for {method} has malformed {key}")
            normalized[key] = {
                name: normalize_schema_contract(item, f"{method}.{key}.{name}")
                for name, item in definitions.items()
            }
    return normalized


def schema_errors(
    value: Any,
    schema: Any,
    path: str = "argument",
    root: Any = None,
    _depth: int = 0,
    _ref_stack: tuple[str, ...] = (),
) -> list[str]:
    """Return payload errors for the supported middleware-schema subset."""
    if _depth > MAX_SCHEMA_DEPTH:
        return [f"{path}: middleware schema exceeds the nesting limit"]
    if not isinstance(schema, dict):
        return [f"{path}: middleware schema node is malformed"]
    unsupported = sorted(set(schema) & UNSUPPORTED_SCHEMA_KEYWORDS)
    if unsupported:
        return [f"{path}: unsupported middleware schema keyword {unsupported[0]!r}"]
    unknown = sorted(set(schema) - SUPPORTED_SCHEMA_KEYWORDS - _SCHEMA_ANNOTATIONS)
    if unknown:
        return [f"{path}: unrecognized middleware schema keyword {unknown[0]!r}"]
    root = schema if root is None else root
    if "$ref" in schema:
        reference = schema["$ref"]
        if reference in _ref_stack:
            return [f"{path}: circular middleware schema reference"]
        try:
            target = _resolve_ref(reference, root)
        except (KeyError, TypeError):
            return [f"{path}: unresolved middleware schema reference"]
        reference_errors = schema_errors(value, target, path, root, _depth + 1, _ref_stack + (reference,))
        if reference_errors:
            return reference_errors
    if "const" in schema and not _json_equal(value, schema["const"]):
        return [f"{path}: expected constant {schema['const']!r}"]
    if "enum" in schema:
        if not isinstance(schema["enum"], list):
            return [f"{path}: middleware enum is malformed"]
        if not any(_json_equal(value, option) for option in schema["enum"]):
            return [f"{path}: value is not allowed by middleware enum"]
    for keyword in ("oneOf", "anyOf", "allOf"):
        if keyword in schema and (not isinstance(schema[keyword], list) or not schema[keyword]):
            return [f"{path}: middleware {keyword} definition is malformed"]
    if "oneOf" in schema:
        matches = [variant for variant in schema["oneOf"] if not schema_errors(value, variant, path, root, _depth + 1, _ref_stack)]
        if len(matches) != 1:
            return [f"{path}: must match exactly one middleware variant; matched {len(matches)}"]
    if "anyOf" in schema and not any(
        not schema_errors(value, variant, path, root, _depth + 1, _ref_stack) for variant in schema["anyOf"]
    ):
        return [f"{path}: does not match any middleware alternative"]
    if "allOf" in schema:
        composition_errors = []
        for variant in schema["allOf"]:
            composition_errors.extend(schema_errors(value, variant, path, root, _depth + 1, _ref_stack))
        if composition_errors:
            return composition_errors

    expected = schema.get("type")
    if expected is not None:
        choices = expected if isinstance(expected, list) else [expected]
        def valid_type(kind: Any) -> bool:
            return (
                (kind == "object" and isinstance(value, dict))
                or (kind == "array" and isinstance(value, list))
                or (kind == "string" and isinstance(value, str))
                or (kind == "integer" and type(value) is int)
                or (kind == "number" and type(value) in (int, float) and math.isfinite(value))
                or (kind == "boolean" and type(value) is bool)
                or (kind == "null" and value is None)
            )
        if not isinstance(choices, list) or not any(valid_type(kind) for kind in choices):
            return [f"{path}: expected type {expected!r}, got {type(value).__name__}"]

    errors: list[str] = []
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return [f"{path}: middleware properties schema is malformed"]
        required = set(schema.get("required", []))
        required.update(key for key, spec in properties.items() if isinstance(spec, dict) and spec.get("_required_") is True)
        errors.extend(f"{path}.{key}: required field is missing" for key in sorted(required - value.keys()))
        extras = value.keys() - properties.keys()
        additional = schema.get("additionalProperties", True)
        if additional is False:
            errors.extend(f"{path}.{key}: field is not accepted" for key in sorted(extras))
        elif isinstance(additional, dict):
            for key in extras:
                errors.extend(schema_errors(value[key], additional, f"{path}.{key}", root, _depth + 1, _ref_stack))
        for key, item in value.items():
            if key in properties:
                errors.extend(schema_errors(item, properties[key], f"{path}.{key}", root, _depth + 1, _ref_stack))
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            errors.append(f"{path}: array length is outside middleware bounds")
        if schema.get("uniqueItems") is True:
            for index, item in enumerate(value):
                if any(_json_equal(item, earlier) for earlier in value[:index]):
                    errors.append(f"{path}[{index}]: duplicate array item")
                    break
        items = schema.get("items")
        if items is False and value:
            errors.append(f"{path}: array items are not accepted")
        elif isinstance(items, dict):
            for index, item in enumerate(value):
                errors.extend(schema_errors(item, items, f"{path}[{index}]", root, _depth + 1, _ref_stack))
        elif isinstance(items, list):
            for index, item_schema in enumerate(items[:len(value)]):
                errors.extend(schema_errors(value[index], item_schema, f"{path}[{index}]", root, _depth + 1, _ref_stack))
            if len(value) > len(items):
                additional = schema.get("additionalItems", True)
                if additional is False:
                    errors.append(f"{path}: additional array items are not accepted")
                elif isinstance(additional, dict):
                    for index in range(len(items), len(value)):
                        errors.extend(schema_errors(value[index], additional, f"{path}[{index}]", root, _depth + 1, _ref_stack))
    elif isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            errors.append(f"{path}: string length is outside middleware bounds")
        pattern = schema.get("pattern")
        if pattern is not None:
            try:
                matched = re.search(pattern, value) is not None
            except (TypeError, re.error):
                return [f"{path}: middleware pattern is malformed"]
            if not matched:
                errors.append(f"{path}: string does not match middleware pattern")
    elif type(value) in (int, float):
        if not math.isfinite(value):
            return [f"{path}: number must be finite"]
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            errors.append(f"{path}: number is outside middleware bounds")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: number is below the exclusive minimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: number is above the exclusive maximum")
        divisor = schema.get("multipleOf")
        if divisor is not None and (divisor <= 0 or not math.isclose(value / divisor, round(value / divisor), rel_tol=1e-9, abs_tol=1e-9)):
            errors.append(f"{path}: number is not an allowed multiple")
    return errors


def _accepted_args(metadata: Any, method: str) -> list[Any]:
    if not isinstance(metadata, dict):
        raise SchemaError(f"runtime schema for {method} is missing or malformed")
    public = metadata.get("accepts")
    private = metadata.get("__accepts__")
    if public is not None and private is not None and public != private:
        raise SchemaError(f"runtime schema for {method} has conflicting accepts definitions")
    accepts = public if public is not None else private
    if not isinstance(accepts, list):
        raise SchemaError(f"runtime schema for {method} has no accepted-arguments definition")
    return accepts


def validate_method_request(methods: Any, method: str, params: Any) -> None:
    """Raise SchemaError unless positional ``params`` match the method schema."""
    if not isinstance(methods, dict) or not isinstance(method, str):
        raise SchemaError("runtime methods or method name is malformed")
    if not isinstance(params, list):
        raise SchemaError(f"request for {method} must use a positional parameter list")
    metadata = methods.get(method)
    normalize_method_contract(metadata, method)
    accepts = _accepted_args(metadata, method)
    if len(params) > len(accepts):
        raise SchemaError(f"request for {method} has too many positional arguments")
    metadata = methods[method]
    for index, schema in enumerate(accepts):
        if not isinstance(schema, dict):
            raise SchemaError(f"runtime schema for {method} argument {index} is malformed")
        if index >= len(params):
            if schema.get("_required_") is True:
                raise SchemaError(f"request for {method} omits required argument {schema.get('_name_', index)!r}")
            continue
        errors = schema_errors(params[index], schema, f"{method}[{schema.get('_name_', index)}]", metadata)
        if errors:
            raise SchemaError(errors[0])


class SchemaCheckedClient:
    """Permit only explicitly allowlisted calls; schema-check allowlisted writes.

    Schema validation is not authorization. The default empty allowlist blocks
    every call until the caller deliberately names permitted methods. These
    guards apply only through this wrapper; code holding the underlying client
    can bypass them and must treat it as trusted.
    """

    def __init__(
        self,
        client: Any,
        methods: dict[str, Any] | None = None,
        allowed_read_methods: set[str] | frozenset[str] = frozenset(),
        allowed_write_methods: set[str] | frozenset[str] = frozenset(),
    ):
        self.client = client
        self.runtime_methods = methods
        self.allowed_read_methods = self._validated_allowlist(allowed_read_methods, "read")
        self.allowed_write_methods = self._validated_allowlist(allowed_write_methods, "write")

    @staticmethod
    def _validated_allowlist(methods: set[str] | frozenset[str], kind: str) -> frozenset[str]:
        if not isinstance(methods, (set, frozenset)) or any(
            not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]*\Z", name)
            for name in methods
        ):
            raise SchemaError(f"allowed {kind} methods must be a set of valid middleware method names")
        return frozenset(methods)

    def call_read(self, method: str, params: list[Any] | None = None) -> Any:
        if not isinstance(method, str) or method not in self.allowed_read_methods:
            raise SchemaError("middleware read method is not explicitly allowlisted")
        payload = [] if params is None else params
        if not isinstance(payload, list):
            raise SchemaError("middleware parameters must be a positional list")
        return self.client.call(method, payload)

    def call_write(self, method: str, params: list[Any] | None = None) -> Any:
        if not isinstance(method, str) or method not in self.allowed_write_methods:
            raise SchemaError("middleware write method is not explicitly allowlisted")
        if self.runtime_methods is None:
            methods = self.client.call("core.get_methods", [])
            if not isinstance(methods, dict):
                raise SchemaError("TrueNAS did not return runtime method schemas before write")
            self.runtime_methods = methods
        payload = [] if params is None else params
        validate_method_request(self.runtime_methods, method, payload)
        return self.client.call(method, payload)

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
