#!/usr/bin/env python3
"""A deliberately small JSON Schema validator for this repository's contracts.

`contracts/compatibility.json` declares `python_runtime_dependencies: []`, so
importing a third-party validator would make that statement false. This module
implements only the subset the repository's own contracts use, and refuses any
schema construct outside that subset rather than ignoring it.

Refusing unsupported keywords is the important part. A validator that silently
skips what it does not understand reports success for constraints it never
checked, which is the class of false evidence this workstream exists to remove.

Supported: `type` (including unions), `required`, `properties`, `const`,
`additionalProperties`, `items`, `enum`, `$ref` to `#/$defs/*`, and `$defs`.
"""

from __future__ import annotations

from typing import Any


SUPPORTED_KEYWORDS = frozenset(
    {
        "$comment",
        "$defs",
        "$ref",
        "$schema",
        "additionalProperties",
        "const",
        "description",
        "enum",
        "items",
        "properties",
        "required",
        "title",
        "type",
    }
)

_JSON_TYPES = {
    "array": list,
    "boolean": bool,
    "integer": int,
    "null": type(None),
    "number": (int, float),
    "object": dict,
    "string": str,
}


class UnsupportedSchema(Exception):
    """The schema uses a construct this validator does not implement."""


def _is_json_type(value: Any, name: str) -> bool:
    """Match JSON types, not Python's subclass relationships.

    `isinstance(True, int)` is True in Python, so a naive check would let `true`
    satisfy `integer` or `number`. JSON booleans are not numbers, and conflating
    them would let a wrong-typed payload pass a type check.
    """
    expected = _JSON_TYPES.get(name)
    if expected is None:
        raise UnsupportedSchema(f"unsupported type name: {name!r}")
    if name in {"integer", "number"} and isinstance(value, bool):
        return False
    if name == "boolean":
        return isinstance(value, bool)
    return isinstance(value, expected)


def _equal(left: Any, right: Any) -> bool:
    """JSON equality, where `true` is not 1 and `false` is not 0."""
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return left == right


def _assert_supported(schema: Any, pointer: str) -> None:
    if not isinstance(schema, dict):
        raise UnsupportedSchema(f"schema at {pointer} is not an object")
    unknown = sorted(set(schema) - SUPPORTED_KEYWORDS)
    if unknown:
        raise UnsupportedSchema(f"unsupported keyword(s) at {pointer}: {', '.join(unknown)}")


# Keywords that constrain nothing about the instance, so they may accompany
# `$ref` harmlessly. `$defs` is a definitions container, not a constraint, and a
# root schema legitimately holds both it and a `$ref`.
_ANNOTATION_KEYWORDS = frozenset(
    {"$ref", "$comment", "$schema", "$defs", "title", "description"}
)


def _resolve(schema: dict[str, Any], root: dict[str, Any], pointer: str) -> dict[str, Any]:
    reference = schema.get("$ref")
    if reference is None:
        return schema
    prefix = "#/$defs/"
    if not isinstance(reference, str) or not reference.startswith(prefix):
        raise UnsupportedSchema(f"unsupported $ref at {pointer}: {reference!r}")
    # Under draft 2020-12 a sibling of `$ref` still applies. This validator
    # resolves the reference and does not merge siblings, so a constraint placed
    # beside `$ref` would be silently discarded and report false conformance.
    # Refusing is the whole point of a bounded validator.
    siblings = sorted(set(schema) - _ANNOTATION_KEYWORDS)
    if siblings:
        raise UnsupportedSchema(
            f"$ref at {pointer} has sibling constraint(s) this validator does not merge: "
            + ", ".join(siblings)
        )
    name = reference[len(prefix) :]
    definitions = root.get("$defs")
    if not isinstance(definitions, dict) or name not in definitions:
        raise UnsupportedSchema(f"unresolvable $ref at {pointer}: {reference!r}")
    return definitions[name]


def _validate(value: Any, schema: dict[str, Any], root: dict[str, Any],
              pointer: str, errors: list[str]) -> None:
    _assert_supported(schema, pointer)
    schema = _resolve(schema, root, pointer)
    _assert_supported(schema, pointer)

    declared = schema.get("type")
    if declared is not None:
        names = declared if isinstance(declared, list) else [declared]
        if not any(_is_json_type(value, name) for name in names):
            errors.append(f"{pointer}: expected type {declared!r}")
            return

    if "const" in schema and not _equal(value, schema["const"]):
        errors.append(f"{pointer}: expected const {schema['const']!r}")

    if "enum" in schema:
        options = schema["enum"]
        if not isinstance(options, list):
            raise UnsupportedSchema(f"enum at {pointer} is not a list")
        if not any(_equal(value, option) for option in options):
            errors.append(f"{pointer}: value is not in enum")

    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{pointer}: missing required property {name!r}")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise UnsupportedSchema(f"properties at {pointer} is not an object")
        for name, child in properties.items():
            if name in value:
                _validate(value[name], child, root, f"{pointer}/{name}", errors)
        allow_extra = schema.get("additionalProperties", True)
        if allow_extra is False:
            for name in sorted(set(value) - set(properties)):
                errors.append(f"{pointer}: unexpected property {name!r}")
        elif allow_extra is not True:
            raise UnsupportedSchema(f"additionalProperties at {pointer} must be a boolean")

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate(item, schema["items"], root, f"{pointer}/{index}", errors)


def validate(value: Any, schema: dict[str, Any]) -> list[str]:
    """Return every validation error; an empty list means the value conforms.

    Raises `UnsupportedSchema` when the schema uses a construct outside the
    supported subset, so an unchecked constraint can never be mistaken for a
    satisfied one.
    """
    errors: list[str] = []
    _validate(value, schema, schema, "#", errors)
    return errors


def assert_valid(value: Any, schema: dict[str, Any], subject: str) -> None:
    errors = validate(value, schema)
    if errors:
        raise ValueError(f"{subject} does not conform:\n  " + "\n  ".join(errors))
