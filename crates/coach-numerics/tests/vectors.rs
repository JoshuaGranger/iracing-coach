//! Every vector CPython 3.12 produced (tools/gen_vectors.py) must come out bit for bit.

mod support;

use coach_numerics as py;
use py::Num;
use support::{Json, PLATFORM, Report, enc_bool, enc_float, enc_num, encode, float, load, num};

fn nums(list: &Json) -> Vec<Num> {
    list.arr().iter().map(num).collect()
}

fn floats(list: &Json) -> Vec<f64> {
    list.arr().iter().map(float).collect()
}

fn text(result: py::PyResult<String>) -> String {
    encode(result, |s| format!("s:{s}"))
}

#[test]
fn sum_builtin() {
    let mut report = Report::default();
    for case in load(PLATFORM, "sum") {
        let items = nums(case.get("items"));
        let start = num(case.get("start"));
        let got = encode(py::builtin_sum(&items, start), enc_num);
        report.check(
            || format!("sum({items:?}, {start:?})"),
            &got,
            case.get("out").str(),
        );
        if start.is_float() && items.iter().all(|x| x.is_float()) {
            let fs: Vec<f64> = items.iter().map(|x| x.to_f64()).collect();
            let fast = enc_float(py::sum_floats(&fs, start.to_f64()));
            report.check(
                || format!("sum_floats({fs:?})"),
                &fast,
                case.get("out").str(),
            );
        }
        if matches!(start, Num::Int(0)) && items.iter().all(|x| x.is_float()) {
            let fs: Vec<f64> = items.iter().map(|x| x.to_f64()).collect();
            let fast = enc_num(py::sum_floats_from_int_zero(&fs));
            report.check(
                || format!("sum_floats_from_int_zero({fs:?})"),
                &fast,
                case.get("out").str(),
            );
        }
    }
    report.finish("sum");
}

#[test]
fn fsum() {
    let mut report = Report::default();
    for case in load("common", "fsum") {
        let items = floats(case.get("items"));
        let got = encode(py::fsum(&items), enc_float);
        report.check(|| format!("fsum({items:?})"), &got, case.get("out").str());
    }
    report.finish("fsum");
}

#[test]
fn statistics() {
    let mut report = Report::default();
    for case in load("common", "stats") {
        let data = nums(case.get("data"));
        let name = case.get("fn").str();
        let got = match name {
            "fmean" => encode(py::fmean_nums(&data), enc_float),
            "mean" => encode(py::mean(&data), enc_num),
            "median" => encode(py::median(&data), enc_num),
            "variance" => encode(py::variance(&data), enc_num),
            "pvariance" => encode(py::pvariance(&data), enc_num),
            "stdev" => encode(py::stdev(&data), enc_float),
            "pstdev" => encode(py::pstdev(&data), enc_float),
            other => panic!("unknown statistics function {other}"),
        };
        report.check(|| format!("{name}({data:?})"), &got, case.get("out").str());
        if name == "fmean" && data.iter().all(|x| x.is_float()) {
            let fs: Vec<f64> = data.iter().map(|x| x.to_f64()).collect();
            report.check(
                || format!("fmean({fs:?})"),
                &encode(py::fmean(&fs), enc_float),
                case.get("out").str(),
            );
        }
        if name == "median" && data.iter().all(|x| x.is_float()) {
            let fs: Vec<f64> = data.iter().map(|x| x.to_f64()).collect();
            let got = encode(py::median_floats(&fs), enc_float);
            report.check(
                || format!("median_floats({fs:?})"),
                &got,
                case.get("out").str(),
            );
        }
    }
    report.finish("stats");
}

#[test]
fn round() {
    let mut report = Report::default();
    for case in load("common", "round") {
        let x = num(case.get("x"));
        let nd = case.get("nd");
        let nd = if nd.is_null() { None } else { Some(nd.int()) };
        let got = encode(py::round_num(x, nd), enc_num);
        report.check(
            || format!("round({x:?}, {nd:?})"),
            &got,
            case.get("out").str(),
        );
    }
    report.finish("round");
}

#[test]
fn repr_and_json() {
    let mut report = Report::default();
    for case in load("common", "repr") {
        let x = float(case.get("x"));
        report.check(
            || format!("repr({x:?})"),
            &format!("s:{}", py::float_repr(x)),
            case.get("repr").str(),
        );
        report.check(
            || format!("json({x:?})"),
            &text(py::json_float(x, true)),
            case.get("json").str(),
        );
        report.check(
            || format!("json strict({x:?})"),
            &text(py::json_float(x, false)),
            case.get("json_strict").str(),
        );
    }
    report.finish("repr");
}

#[test]
fn format() {
    let mut report = Report::default();
    for case in load("common", "format") {
        let x = num(case.get("x"));
        let spec = case.get("spec").str();
        report.check(
            || format!("format({x:?}, {spec:?})"),
            &text(py::format_num(x, spec)),
            case.get("out").str(),
        );
    }
    report.finish("format");
}

fn math_case(name: &str, args: &[Num]) -> String {
    let f = |i: usize| args[i].to_f64();
    match name {
        "floor" => encode(py::floor_num(args[0]), |i| format!("i:{i}")),
        "ceil" => encode(py::ceil_num(args[0]), |i| format!("i:{i}")),
        "sqrt" => encode(py::sqrt(f(0)), enc_float),
        "degrees" => enc_float(py::degrees(f(0))),
        "radians" => enc_float(py::radians(f(0))),
        "hypot" => enc_float(py::hypot_n(
            &args.iter().map(|a| a.to_f64()).collect::<Vec<_>>(),
        )),
        "isclose" => encode(py::isclose(f(0), f(1), f(2), f(3)), enc_bool),
        "gcd" => {
            let (Num::Int(a), Num::Int(b)) = (args[0], args[1]) else {
                panic!("gcd of ints")
            };
            encode(py::gcd(a, b), |i| format!("i:{i}"))
        }
        "atan2" => enc_float(py::atan2(f(0), f(1))),
        "sin" => encode(py::sin(f(0)), enc_float),
        "cos" => encode(py::cos(f(0)), enc_float),
        "pow" => encode(py::pow_num(args[0], args[1]), enc_num),
        other => panic!("unknown math function {other}"),
    }
}

#[test]
fn math() {
    let mut report = Report::default();
    for case in load("common", "math") {
        let args = nums(case.get("args"));
        let name = case.get("fn").str();
        report.check(
            || format!("{name}({args:?})"),
            &math_case(name, &args),
            case.get("out").str(),
        );
    }
    report.finish("math");
}

#[test]
fn libm() {
    let mut report = Report::default();
    for case in load(PLATFORM, "libm") {
        let args = nums(case.get("args"));
        let name = case.get("fn").str();
        report.check(
            || format!("{name}({args:?})"),
            &math_case(name, &args),
            case.get("out").str(),
        );
    }
    report.finish("libm");
}

#[test]
fn arithmetic() {
    let mut report = Report::default();
    for case in load("common", "arith") {
        let op = case.get("op").str();
        let a = num(case.get("a"));
        let b = if case.has("b") {
            num(case.get("b"))
        } else {
            Num::Int(0)
        };
        let got = match op {
            "add" => encode(py::add(a, b), enc_num),
            "sub" => encode(py::sub(a, b), enc_num),
            "mul" => encode(py::mul(a, b), enc_num),
            "truediv" => encode(py::truediv(a, b), enc_float),
            "floordiv" => encode(py::floordiv(a, b), enc_num),
            "mod" => encode(py::modulo(a, b), enc_num),
            "lt" => enc_bool(py::py_lt(a, b)),
            "le" => enc_bool(py::py_le(a, b)),
            "gt" => enc_bool(py::py_gt(a, b)),
            "ge" => enc_bool(py::py_ge(a, b)),
            "eq" => enc_bool(py::py_eq(a, b)),
            "neg" => encode(py::neg(a), enc_num),
            "abs" => encode(py::abs(a), enc_num),
            "int" => encode(py::int_from_float(a.to_f64()), |i| format!("i:{i}")),
            "float" => enc_float(a.to_f64()),
            "divmod" => {
                let want = case.get("out");
                let got = match py::divmod(a, b) {
                    Ok((q, r)) => format!("[{},{}]", enc_num(q), enc_num(r)),
                    Err(e) => format!("e:{}", e.kind.name()),
                };
                let want = match want {
                    Json::Arr(pair) => format!("[{},{}]", pair[0].str(), pair[1].str()),
                    other => other.str().to_owned(),
                };
                report.check(|| format!("divmod({a:?}, {b:?})"), &got, &want);
                continue;
            }
            other => panic!("unknown op {other}"),
        };
        report.check(
            || format!("{op}({a:?}, {b:?})"),
            &got,
            case.get("out").str(),
        );
    }
    report.finish("arith");
}

fn indices(list: &Json) -> String {
    let items: Vec<String> = list.arr().iter().map(|j| j.int().to_string()).collect();
    items.join(",")
}

#[test]
fn ordering() {
    let mut report = Report::default();
    for case in load("common", "order") {
        let data = nums(case.get("data"));
        let name = case.get("fn").str();
        let lt = |i: usize, j: usize| py::py_lt(data[i], data[j]);
        let got = match name {
            "sorted" => {
                let reverse = case.get("reverse").bool();
                let perm = py::sort_permutation(data.len(), &mut |i, j| lt(i, j), reverse);
                perm.iter()
                    .map(usize::to_string)
                    .collect::<Vec<_>>()
                    .join(",")
            }
            "min" => py::min_index_by(data.len(), lt).unwrap().to_string(),
            "max" => py::max_index_by(data.len(), |i, j| py::py_gt(data[i], data[j]))
                .unwrap()
                .to_string(),
            "bisect_left" => py::bisect_left(&data, num(case.get("x"))).to_string(),
            "bisect_right" => py::bisect_right(&data, num(case.get("x"))).to_string(),
            "heap" => {
                let mut heap: Vec<usize> = Vec::new();
                let mut popped = Vec::new();
                let heap_lt = |a: &usize, b: &usize| py::py_lt(data[*a], data[*b]);
                for op in case.get("ops").arr() {
                    let op = op.arr();
                    match op[0].str() {
                        "push" => py::heap_push(&mut heap, op[1].int() as usize, heap_lt),
                        "pop" => popped.push(py::heap_pop(&mut heap, heap_lt).unwrap()),
                        "replace" => {
                            popped.push(
                                py::heap_replace(&mut heap, op[1].int() as usize, heap_lt).unwrap(),
                            );
                        }
                        other => panic!("unknown heap op {other}"),
                    }
                }
                popped
                    .iter()
                    .map(usize::to_string)
                    .collect::<Vec<_>>()
                    .join(",")
            }
            other => panic!("unknown order function {other}"),
        };
        let want = match case.get("out") {
            Json::Int(i) => i.to_string(),
            list => indices(list),
        };
        report.check(|| format!("{name} of {} items", data.len()), &got, &want);
        if name == "sorted" && !case.get("reverse").bool() {
            let sorted = py::sorted_nums(&data, false);
            let order: Vec<Num> = case
                .get("out")
                .arr()
                .iter()
                .map(|j| data[j.int() as usize])
                .collect();
            let same = sorted.iter().zip(&order).all(|(a, b)| a.identical(*b));
            report.check(|| "sorted_nums".to_owned(), &enc_bool(same), "b:1");
        }
    }
    report.finish("order");
}

#[test]
fn parse() {
    let mut report = Report::default();
    for case in load("common", "parse") {
        let t = case.get("text").str();
        report.check(
            || format!("float({t:?})"),
            &encode(py::parse_float(t), enc_float),
            case.get("float").str(),
        );
        report.check(
            || format!("int({t:?})"),
            &encode(py::parse_int(t), |i| format!("i:{i}")),
            case.get("int").str(),
        );
    }
    report.finish("parse");
}
