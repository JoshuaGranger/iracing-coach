//! Field-path pruning for the shrink ladder: keeps exactly the paths of an `x-consumed-fields` list.

use std::collections::BTreeMap;

use serde_json::{Map, Value};

#[derive(Debug, Default, Clone)]
pub(crate) struct Spec {
    keep: bool,
    keys: BTreeMap<String, Spec>,
    any: Option<Box<Spec>>,
    items: Option<Box<Spec>>,
}

impl Spec {
    pub(crate) fn parse(paths: &[&str]) -> Spec {
        let mut root = Spec::default();
        for path in paths {
            let mut node = &mut root;
            for segment in path.split('.') {
                let mut name = segment;
                let mut depth = 0;
                while let Some(stripped) = name.strip_suffix("[]") {
                    name = stripped;
                    depth += 1;
                }
                node = if name == "*" {
                    node.any.get_or_insert_with(Default::default)
                } else {
                    node.keys.entry(name.to_string()).or_default()
                };
                for _ in 0..depth {
                    node = node.items.get_or_insert_with(Default::default);
                }
            }
            node.keep = true;
        }
        root
    }

    /// The value with every field outside the spec dropped. A value whose shape the spec does not
    /// describe is kept whole: only what the list proves unread is removed.
    pub(crate) fn apply(&self, value: &Value) -> Value {
        if self.keep {
            return value.clone();
        }
        match value {
            Value::Object(map) => {
                let mut out = Map::new();
                for (key, child) in map {
                    if let Some(spec) = self.keys.get(key).or(self.any.as_deref()) {
                        out.insert(key.clone(), spec.apply(child));
                    }
                }
                Value::Object(out)
            }
            Value::Array(items) => match &self.items {
                Some(spec) => Value::Array(items.iter().map(|item| spec.apply(item)).collect()),
                None => value.clone(),
            },
            other => other.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn keeps_listed_paths_only() {
        let spec = Spec::parse(&["a.b", "c[].d", "e[][]", "f.*.g", "h"]);
        let value = json!({
            "a": {"b": 1, "x": 2}, "c": [{"d": 1, "y": 2}], "e": [[1, 2], [3]],
            "f": {"p": {"g": 1, "z": 2}, "q": {"g": 3}}, "h": {"deep": [1]}, "drop": 5
        });
        assert_eq!(
            spec.apply(&value),
            json!({
                "a": {"b": 1}, "c": [{"d": 1}], "e": [[1, 2], [3]],
                "f": {"p": {"g": 1}, "q": {"g": 3}}, "h": {"deep": [1]}
            })
        );
    }

    #[test]
    fn trailing_star_keeps_whole_children() {
        let spec = Spec::parse(&["m.*"]);
        let value = json!({"m": {"a": {"x": 1}, "b": [1]}, "n": 1});
        assert_eq!(spec.apply(&value), json!({"m": {"a": {"x": 1}, "b": [1]}}));
    }

    #[test]
    fn unexpected_shapes_are_kept() {
        let spec = Spec::parse(&["a.b"]);
        assert_eq!(spec.apply(&json!({"a": 3})), json!({"a": 3}));
        assert_eq!(spec.apply(&json!({"a": [1]})), json!({"a": [1]}));
    }
}
