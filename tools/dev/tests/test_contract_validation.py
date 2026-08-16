"""Tests for the bounded contract validator.

The validator exists because `compatibility.json` declares
`python_runtime_dependencies: []`, so a third-party validator would make that
statement false. A hand-written validator only earns trust if it refuses what it
does not implement, so the refusal cases below matter as much as the acceptance
cases: a validator that silently skips an unknown keyword reports success for a
constraint it never checked.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "tools" / "contract_validation.py"
SPEC = importlib.util.spec_from_file_location("contract_validation_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
cv = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cv
SPEC.loader.exec_module(cv)


class JsonTypeTests(unittest.TestCase):
    """JSON types, not Python's subclass relationships."""

    def test_boolean_is_not_an_integer_or_number(self) -> None:
        # isinstance(True, int) is True in Python. If the validator relied on
        # that, `true` would satisfy an integer field and a wrong-typed payload
        # would pass a type check.
        for declared in ("integer", "number"):
            with self.subTest(type=declared):
                self.assertTrue(cv.validate(True, {"type": declared}))
                self.assertTrue(cv.validate(False, {"type": declared}))

    def test_boolean_does_not_satisfy_a_numeric_const(self) -> None:
        self.assertTrue(cv.validate(True, {"const": 1}))
        self.assertTrue(cv.validate(False, {"const": 0}))
        self.assertFalse(cv.validate(1, {"const": 1}))
        self.assertFalse(cv.validate(0, {"const": 0}))

    def test_numbers_do_not_satisfy_a_boolean_const(self) -> None:
        self.assertTrue(cv.validate(1, {"const": True}))
        self.assertFalse(cv.validate(True, {"const": True}))

    def test_boolean_does_not_satisfy_a_numeric_enum(self) -> None:
        self.assertTrue(cv.validate(True, {"enum": [0, 1]}))
        self.assertFalse(cv.validate(1, {"enum": [0, 1]}))

    def test_integer_is_accepted_as_number_but_float_is_not_an_integer(self) -> None:
        self.assertFalse(cv.validate(3, {"type": "number"}))
        self.assertFalse(cv.validate(3.5, {"type": "number"}))
        self.assertTrue(cv.validate(3.5, {"type": "integer"}))

    def test_null_and_union_types(self) -> None:
        self.assertFalse(cv.validate(None, {"type": ["string", "null"]}))
        self.assertFalse(cv.validate("x", {"type": ["string", "null"]}))
        self.assertTrue(cv.validate(1, {"type": ["string", "null"]}))


class UnsupportedConstructTests(unittest.TestCase):
    """Refuse what is not implemented rather than passing it silently."""

    def test_unknown_keyword_is_refused(self) -> None:
        for keyword in ("minimum", "maxLength", "pattern", "oneOf", "allOf", "not", "$dynamicRef"):
            with self.subTest(keyword=keyword):
                with self.assertRaises(cv.UnsupportedSchema):
                    cv.validate(1, {"type": "integer", keyword: 1})

    def test_unknown_type_name_is_refused(self) -> None:
        with self.assertRaises(cv.UnsupportedSchema):
            cv.validate(1, {"type": "int"})

    def test_unresolvable_or_unsupported_ref_is_refused(self) -> None:
        with self.assertRaises(cv.UnsupportedSchema):
            cv.validate({}, {"$ref": "other-file.schema.json"})
        with self.assertRaises(cv.UnsupportedSchema):
            cv.validate({}, {"$ref": "#/$defs/missing", "$defs": {}})

    def test_non_boolean_additional_properties_is_refused(self) -> None:
        with self.assertRaises(cv.UnsupportedSchema):
            cv.validate({"a": 1}, {"type": "object", "additionalProperties": {"type": "string"}})

    def test_nested_unsupported_keyword_is_refused(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "string", "minLength": 2}}}
        with self.assertRaises(cv.UnsupportedSchema):
            cv.validate({"a": "xy"}, schema)


class ObjectAndArrayTests(unittest.TestCase):
    def test_required_and_additional_properties(self) -> None:
        closed = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}},
                  "additionalProperties": False}
        self.assertFalse(cv.validate({"a": "x"}, closed))
        self.assertTrue(cv.validate({}, closed))
        self.assertTrue(cv.validate({"a": "x", "b": 1}, closed))

    def test_additional_properties_true_accepts_unknown_fields(self) -> None:
        open_schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}},
                       "additionalProperties": True}
        self.assertFalse(cv.validate({"a": "x", "unknown": {"kept": True}}, open_schema))

    def test_items_are_validated_and_errors_are_pointed(self) -> None:
        schema = {"type": "array", "items": {"type": "integer"}}
        errors = cv.validate([1, "two", 3], schema)
        self.assertEqual(len(errors), 1)
        self.assertIn("#/1", errors[0])

    def test_ref_to_defs_resolves(self) -> None:
        schema = {
            "$defs": {"leaf": {"type": "string"}},
            "type": "object",
            "required": ["a"],
            "properties": {"a": {"$ref": "#/$defs/leaf"}},
        }
        self.assertFalse(cv.validate({"a": "x"}, schema))
        self.assertTrue(cv.validate({"a": 1}, schema))

    def test_every_error_is_reported_not_just_the_first(self) -> None:
        schema = {"type": "object", "required": ["a", "b", "c"], "properties": {}}
        self.assertEqual(len(cv.validate({}, schema)), 3)

    def test_assert_valid_raises_with_the_subject_named(self) -> None:
        with self.assertRaisesRegex(ValueError, "envelope does not conform"):
            cv.assert_valid({}, {"type": "object", "required": ["a"]}, "envelope")


if __name__ == "__main__":
    unittest.main()
