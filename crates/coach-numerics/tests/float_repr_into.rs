//! `float_repr_into` (port phase 3) is `float_repr` byte for byte: on every float of the `repr`
//! vectors (whose `float_repr` the vector test checks against CPython) and on random families
//! that reach every branch: arbitrary bit patterns, float32 values widened to double (the
//! dtoa ties), rounded decimals, dyadic ties, integers and powers of ten with neighbours.

use coach_numerics::{float_repr, float_repr_into};

fn check(x: f64, count: &mut usize) {
    let mut out = b"prefix".to_vec();
    float_repr_into(x, &mut out);
    assert_eq!(
        &out[6..],
        float_repr(x).as_bytes(),
        "float_repr_into({x:?}) bits {:016x}",
        x.to_bits()
    );
    *count += 1;
}

struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        // xorshift64*
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }

    fn unit(&mut self) -> f64 {
        (self.next() >> 11) as f64 / (1u64 << 53) as f64
    }
}

#[test]
fn equals_float_repr_on_the_repr_vectors() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("vectors/common/repr.json");
    let text = std::fs::read_to_string(path).unwrap();
    let mut count = 0;
    for field in text.split("\"x\":\"f:").skip(1) {
        let bits = u64::from_str_radix(&field[..16], 16).unwrap();
        check(f64::from_bits(bits), &mut count);
    }
    assert!(count > 3000, "{count}");
}

#[test]
fn equals_float_repr_on_random_families() {
    let mut rng = Rng(0x9E37_79B9_7F4A_7C15);
    let mut count = 0;
    for _ in 0..60_000 {
        check(f64::from_bits(rng.next()), &mut count);
        let wide = f64::from(f32::from_bits(rng.next() as u32));
        check(wide, &mut count);
        let telemetry = f64::from((rng.unit() * 1000.0 - 500.0) as f32);
        check(telemetry, &mut count);
        let digits = (rng.next() % 7) as i32;
        let scale = 10f64.powi(digits);
        check(
            ((rng.unit() * 2e4 - 1e4) * scale).round() / scale,
            &mut count,
        );
        let dyadic =
            ((rng.next() % 2001) as f64 - 1000.0) * 2f64.powi((rng.next() % 120) as i32 - 60);
        check(dyadic, &mut count);
        check((rng.next() % 1_000_000_000) as f64, &mut count);
    }
    for exponent in -325..=309 {
        let power = format!("1e{exponent}").parse::<f64>().unwrap();
        for x in [
            power,
            f64::from_bits(power.to_bits().wrapping_add(1)),
            f64::from_bits(power.to_bits().wrapping_sub(1)),
        ] {
            check(x, &mut count);
            check(-x, &mut count);
        }
    }
    for x in [
        0.0,
        -0.0,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NAN,
        f64::MIN_POSITIVE,
        f64::MAX,
        5e-324,
        1e16,
        1e15,
        9007199254740993.0,
        0.1,
        1e-4,
        1e-5,
    ] {
        check(x, &mut count);
    }
    assert!(count > 360_000, "{count}");
}
