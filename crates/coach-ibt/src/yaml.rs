//! Session-info text: the in-house YAML subset of `ibt_reader._parse_iracing_yaml_subset`
//! (ibt_reader.py:400-517) and `_yaml_scalar` (:342-372). No general YAML.

use crate::IbtError;
use crate::literal::{Evaluated, evaluate};
use crate::matchers::{is_yaml_float, is_yaml_integer};
use crate::py;

/// Deepest block nesting accepted. Python's recursive parser raises `RecursionError` near its
/// default limit of 1000 frames; this bound keeps the port's stack use fixed.
const MAX_BLOCK_DEPTH: usize = 960;

#[derive(Debug, Clone, PartialEq)]
pub enum YamlNode {
    /// Document order; a repeated key keeps both entries, lookups take the last (Python dict).
    Map(Vec<(String, YamlNode)>),
    List(Vec<YamlNode>),
    Scalar(Scalar),
    /// Leaf of an evaluated quoted or flow literal (Python `ast.literal_eval` subset).
    Literal(YamlScalar),
    /// Key with no value and no block, or "- " with no block.
    Null,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Scalar {
    /// Text after "key:" or "- ", trimmed, quotes kept.
    pub raw: String,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
#[serde(untagged)]
pub enum YamlScalar {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
}

fn is_quoted(value: &str) -> bool {
    let first = value.chars().next();
    matches!(first, Some('\'' | '"')) && value.ends_with(first.unwrap_or('\''))
}

fn is_flow(value: &str) -> bool {
    value.starts_with(['[', '{']) && value.ends_with([']', '}'])
}

/// `value[1:-1]`: empty for a one-character value.
fn between_quotes(value: &str) -> &str {
    let mut chars = value.chars();
    chars.next();
    chars.next_back();
    chars.as_str()
}

impl Scalar {
    /// Port of `_yaml_scalar`; quoted text that `literal_eval` rejects is the text between the
    /// quotes, flow text it rejects is `Str(raw)`. A scalar never holds a value whose literal is a
    /// container (the parser builds `List`/`Map` nodes for those); for one built by hand the
    /// result is `Str(raw)`.
    pub fn typed(&self) -> YamlScalar {
        let value = py::strip(&self.raw);
        match value {
            "" | "~" | "null" | "Null" | "NULL" => return YamlScalar::Null,
            _ => {}
        }
        match value.to_lowercase().as_str() {
            "true" | "yes" | "on" => return YamlScalar::Bool(true),
            "false" | "no" | "off" => return YamlScalar::Bool(false),
            _ => {}
        }
        if is_quoted(value) || is_flow(value) {
            return match evaluate(value) {
                Ok(Some(Evaluated::Scalar(scalar))) => scalar,
                Ok(None) if is_quoted(value) => YamlScalar::Str(between_quotes(value).to_owned()),
                _ => YamlScalar::Str(value.to_owned()),
            };
        }
        if is_yaml_integer(value) {
            return match value.parse::<i64>() {
                Ok(number) => YamlScalar::Int(number),
                Err(_) => YamlScalar::Str(value.to_owned()),
            };
        }
        if is_yaml_float(value) {
            return match value.parse::<f64>() {
                Ok(number) if number.is_finite() => YamlScalar::Float(number),
                _ => YamlScalar::Str(value.to_owned()),
            };
        }
        YamlScalar::Str(value.to_owned())
    }

    /// `raw` without one pair of matching surrounding quotes.
    pub fn text(&self) -> &str {
        if is_quoted(&self.raw) {
            between_quotes(&self.raw)
        } else {
            &self.raw
        }
    }
}

/// The node `_yaml_scalar(text)` stands for: a container literal becomes `List`/`Map`, anything
/// else stays a `Scalar` (typed lazily). Unsupported literal results are an error.
fn value_node(text: &str, line: u32) -> Result<YamlNode, IbtError> {
    let value = py::strip(text);
    let scalar = || {
        YamlNode::Scalar(Scalar {
            raw: value.to_owned(),
        })
    };
    if !(is_quoted(value) || is_flow(value)) {
        return Ok(scalar());
    }
    match evaluate(value) {
        Ok(Some(Evaluated::Node(node))) => Ok(node),
        Ok(_) => Ok(scalar()),
        Err(reason) => Err(IbtError::Yaml {
            line,
            message: format!("unsupported flow literal ({reason}): {value}"),
        }),
    }
}

struct Token<'a> {
    indent: usize,
    content: &'a str,
    line: u32,
}

fn yaml_error(line: u32, message: impl Into<String>) -> IbtError {
    IbtError::Yaml {
        line,
        message: message.into(),
    }
}

/// `_split_yaml_pair`: the first ':' outside quotes splits key and value.
fn split_pair(content: &str, line: u32) -> Result<(String, &str), IbtError> {
    let mut quote: Option<char> = None;
    let mut escaped = false;
    for (index, c) in content.char_indices() {
        if escaped {
            escaped = false;
            continue;
        }
        if c == '\\' && quote == Some('"') {
            escaped = true;
            continue;
        }
        if c == '\'' || c == '"' {
            quote = match quote {
                None => Some(c),
                Some(open) if open == c => None,
                open => open,
            };
            continue;
        }
        if c == ':' && quote.is_none() {
            let key = py::strip(&content[..index]);
            if key.is_empty() {
                break;
            }
            let key = if is_quoted(key) {
                match evaluate(key) {
                    Ok(Some(Evaluated::Scalar(YamlScalar::Str(text)))) => text,
                    Ok(None) => between_quotes(key).to_owned(),
                    _ => {
                        return Err(yaml_error(line, format!("unsupported quoted key: {key}")));
                    }
                }
            } else {
                key.to_owned()
            };
            return Ok((key, py::strip(&content[index + 1..])));
        }
    }
    Err(yaml_error(
        line,
        format!("unsupported session YAML: {content:?}"),
    ))
}

struct Parser<'a> {
    tokens: Vec<Token<'a>>,
}

impl Parser<'_> {
    fn deeper(&self, index: usize, indent: usize) -> Option<usize> {
        self.tokens
            .get(index)
            .filter(|token| token.indent > indent)
            .map(|token| token.indent)
    }

    fn block(
        &self,
        mut index: usize,
        indent: usize,
        depth: usize,
    ) -> Result<(YamlNode, usize), IbtError> {
        let Some(first) = self.tokens.get(index) else {
            return Ok((YamlNode::Map(Vec::new()), index));
        };
        if depth > MAX_BLOCK_DEPTH {
            return Err(yaml_error(first.line, "session YAML nests too deeply"));
        }
        let is_list = first.content.starts_with('-');
        let mut list = Vec::new();
        let mut map = Vec::new();
        while let Some(token) = self.tokens.get(index) {
            if token.indent < indent {
                break;
            }
            if token.indent > indent {
                return Err(yaml_error(token.line, "unexpected indentation"));
            }
            if is_list {
                if !token.content.starts_with('-') {
                    break;
                }
                let item_text = py::strip(&token.content[1..]);
                index += 1;
                if item_text.is_empty() {
                    let item = match self.deeper(index, indent) {
                        Some(child) => {
                            let (item, next) = self.block(index, child, depth + 1)?;
                            index = next;
                            item
                        }
                        None => YamlNode::Null,
                    };
                    list.push(item);
                    continue;
                }
                if !item_text.contains(':') {
                    let item = value_node(item_text, token.line)?;
                    if let Some(next) = self.tokens.get(index).filter(|t| t.indent > indent) {
                        return Err(yaml_error(
                            next.line,
                            "scalar list item has an unsupported nested block",
                        ));
                    }
                    list.push(item);
                    continue;
                }
                let (key, rest) = split_pair(item_text, token.line)?;
                let mut item_map = Vec::new();
                if !rest.is_empty() {
                    item_map.push((key, value_node(rest, token.line)?));
                } else if let Some(child) = self.deeper(index, indent) {
                    let (value, next) = self.block(index, child, depth + 1)?;
                    index = next;
                    item_map.push((key, value));
                } else {
                    item_map.push((key, YamlNode::Map(Vec::new())));
                }
                if let Some(child) = self.deeper(index, indent) {
                    let line = self.tokens[index].line;
                    let (extra, next) = self.block(index, child, depth + 1)?;
                    index = next;
                    match extra {
                        YamlNode::Map(entries) => item_map.extend(entries),
                        _ => {
                            return Err(yaml_error(
                                line,
                                "list mapping has a non-mapping continuation",
                            ));
                        }
                    }
                }
                list.push(YamlNode::Map(item_map));
            } else {
                if token.content.starts_with('-') {
                    break;
                }
                let (key, rest) = split_pair(token.content, token.line)?;
                index += 1;
                let value = if !rest.is_empty() {
                    value_node(rest, token.line)?
                } else {
                    match self.tokens.get(index) {
                        Some(next)
                            if next.indent > indent
                                || (next.indent == indent && next.content.starts_with('-')) =>
                        {
                            let (value, after) = self.block(index, next.indent, depth + 1)?;
                            index = after;
                            value
                        }
                        // A key with no inline value and no block is null, as in yaml.safe_load.
                        _ => YamlNode::Null,
                    }
                };
                map.push((key, value));
            }
        }
        let node = if is_list {
            YamlNode::List(list)
        } else {
            YamlNode::Map(map)
        };
        Ok((node, index))
    }
}

pub(crate) fn parse(text: &str) -> Result<YamlNode, IbtError> {
    let mut tokens = Vec::new();
    for (number, raw_line) in py::splitlines(text).into_iter().enumerate() {
        let line = u32::try_from(number + 1).unwrap_or(u32::MAX);
        let stripped = py::strip(raw_line);
        if stripped.is_empty() || py::lstrip(raw_line).starts_with('#') {
            continue;
        }
        if stripped == "---" || stripped == "..." {
            continue;
        }
        let after_leading = raw_line.trim_start_matches([' ', '\t']);
        let leading = &raw_line[..raw_line.len() - after_leading.len()];
        if leading.contains('\t') {
            return Err(yaml_error(
                line,
                "tabs are not allowed for session YAML indentation",
            ));
        }
        if stripped.starts_with(['!', '&', '*']) {
            return Err(yaml_error(
                line,
                "YAML tags, anchors, and aliases are not supported",
            ));
        }
        let indent = raw_line.len() - raw_line.trim_start_matches(' ').len();
        tokens.push(Token {
            indent,
            content: py::rstrip(&raw_line[indent..]),
            line,
        });
    }
    let Some(first) = tokens.first() else {
        return Ok(YamlNode::Map(Vec::new()));
    };
    let (root_indent, first_line) = (first.indent, first.line);
    let parser = Parser { tokens };
    let (root, index) = parser.block(0, root_indent, 1)?;
    if let Some(token) = parser.tokens.get(index) {
        return Err(yaml_error(token.line, "could not consume session YAML"));
    }
    if !matches!(root, YamlNode::Map(_)) {
        return Err(yaml_error(
            first_line,
            "embedded session YAML root must be a mapping",
        ));
    }
    Ok(root)
}

/// Python dict semantics over a block map: the last entry for a key.
pub(crate) fn map_get<'a>(entries: &'a [(String, YamlNode)], key: &str) -> Option<&'a YamlNode> {
    entries
        .iter()
        .rev()
        .find(|(name, _)| name == key)
        .map(|(_, node)| node)
}

/// Python dict view of a block map: each key once, at its first position, with its last value.
pub(crate) fn dict_view(entries: &[(String, YamlNode)]) -> Vec<(&str, &YamlNode)> {
    let mut view: Vec<(&str, &YamlNode)> = Vec::with_capacity(entries.len());
    for (key, node) in entries {
        match view
            .iter_mut()
            .find(|(existing, _)| *existing == key.as_str())
        {
            Some(slot) => slot.1 = node,
            None => view.push((key.as_str(), node)),
        }
    }
    view
}

impl YamlNode {
    /// Dotted lookup with list indices.
    pub(crate) fn lookup(&self, path: &str) -> Option<&YamlNode> {
        path.split('.').try_fold(self, |node, segment| match node {
            YamlNode::Map(entries) => map_get(entries, segment),
            YamlNode::List(items) => segment.parse::<usize>().ok().and_then(|i| items.get(i)),
            _ => None,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scalar(raw: &str) -> YamlScalar {
        Scalar {
            raw: raw.to_owned(),
        }
        .typed()
    }

    #[test]
    fn scalar_typing_follows_yaml_scalar() {
        assert_eq!(scalar("~"), YamlScalar::Null);
        assert_eq!(scalar("NULL"), YamlScalar::Null);
        assert_eq!(scalar("nUlL"), YamlScalar::Str("nUlL".into()));
        assert_eq!(scalar("On"), YamlScalar::Bool(true));
        assert_eq!(scalar("no"), YamlScalar::Bool(false));
        assert_eq!(scalar("-12"), YamlScalar::Int(-12));
        assert_eq!(scalar("012"), YamlScalar::Str("012".into()));
        assert_eq!(
            scalar("99999999999999999999"),
            YamlScalar::Str("99999999999999999999".into())
        );
        assert_eq!(scalar("1.50"), YamlScalar::Float(1.5));
        assert_eq!(scalar("1."), YamlScalar::Float(1.0));
        assert_eq!(scalar(".5"), YamlScalar::Float(0.5));
        assert_eq!(scalar("1e999"), YamlScalar::Str("1e999".into()));
        assert_eq!(scalar(".5e3"), YamlScalar::Str(".5e3".into()));
        assert_eq!(scalar("1.e3"), YamlScalar::Float(1000.0));
        assert_eq!(scalar("'a b'"), YamlScalar::Str("a b".into()));
        assert_eq!(scalar("'it's'"), YamlScalar::Str("it's".into()));
        assert_eq!(scalar("'"), YamlScalar::Str(String::new()));
        assert_eq!(scalar("[a, b]"), YamlScalar::Str("[a, b]".into()));
        assert_eq!(scalar("-2.8 deg"), YamlScalar::Str("-2.8 deg".into()));
        assert_eq!(
            Scalar {
                raw: "\"25.0 psi\"".into()
            }
            .text(),
            "25.0 psi"
        );
        assert_eq!(Scalar { raw: "'".into() }.text(), "");
        assert_eq!(
            Scalar {
                raw: "25.0 psi".into()
            }
            .text(),
            "25.0 psi"
        );
    }

    #[test]
    fn block_structure() {
        let text = "A:\n B: 1\n C:\nD:\n- x\n- K: 1\n  L: 2\n-\n- M:\nE: [1, 2]\nF: {'IsFixedSetup': False}\n";
        let root = parse(text).expect("parses");
        assert_eq!(
            root.lookup("A.B"),
            Some(&YamlNode::Scalar(Scalar { raw: "1".into() }))
        );
        assert_eq!(root.lookup("A.C"), Some(&YamlNode::Null));
        assert_eq!(
            root.lookup("D.1.L"),
            Some(&YamlNode::Scalar(Scalar { raw: "2".into() }))
        );
        assert_eq!(root.lookup("D.2"), Some(&YamlNode::Null));
        assert_eq!(root.lookup("D.3.M"), Some(&YamlNode::Map(Vec::new())));
        assert_eq!(
            root.lookup("E"),
            Some(&YamlNode::List(vec![
                YamlNode::Literal(YamlScalar::Int(1)),
                YamlNode::Literal(YamlScalar::Int(2))
            ]))
        );
        assert_eq!(
            root.lookup("F.IsFixedSetup"),
            Some(&YamlNode::Literal(YamlScalar::Bool(false)))
        );
    }

    #[test]
    fn errors_carry_python_lines() {
        let line = |text: &str| match parse(text) {
            Err(IbtError::Yaml { line, .. }) => line,
            other => panic!("{text:?}: {other:?}"),
        };
        assert_eq!(line("A: 1\n\tB: 2\n"), 2);
        assert_eq!(line("A: 1\n&x: 2\n"), 2);
        assert_eq!(line("A: 1\n  B: 2\n"), 2);
        assert_eq!(line("- a\n"), 1);
        assert_eq!(line("A:\n- a\n  b: 1\n"), 3);
        assert_eq!(line("A: 1\nnot a pair\n"), 2);
        assert_eq!(line("A: {1, 2}\n"), 1);
    }
}
