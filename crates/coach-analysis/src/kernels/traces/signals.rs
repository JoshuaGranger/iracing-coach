//! `_additional_trace_sources`: the optional distance-domain signals in the oracle's
//! registration order, each `[_finite(transform(value)) for value in raw]` of its channel; with
//! `_binary_column` and `_sideslip_column`.

use std::rc::Rc;

use super::super::error::KernelError;
use super::super::table::setup::{
    TIRES, platform_channel_aliases, shock_channel_aliases, tire_pressure_aliases,
    tire_temperature_aliases,
};
use super::super::table::{
    ColumnKey, Op, SteeringBasis, Table, View, apply_op_steps, apply_steps, normalized_unit,
    row_means, unit_steps,
};
use super::super::value::PyValue;
use super::{Col, dict, finite_after_transform};

/// How a signal's bin samples reduce to one representative.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Reducer {
    Median,
    Max,
    SignedPeak,
    Last,
}

/// One registered signal: its id, its normalised series, reducer and rounding digits.
pub(super) struct Signal {
    pub id: String,
    pub series: Col,
    pub reducer: Reducer,
    pub precision: i64,
}

/// The catalog (`additional_signal_catalog`) and the series behind it, in registration order.
pub(super) struct Sources {
    pub catalog: Vec<PyValue>,
    pub signals: Vec<Signal>,
}

/// A transform of `add`: the channel's normalised converted column and gaps.
#[derive(Clone, Copy)]
enum Transform {
    /// The recorded column, normalised (`table._finite_for`).
    Finite,
    /// `percent`: `fraction * 100.0`, no finite check.
    Percent,
    /// `binary`: `_binary_column` of the raw column.
    Binary,
    /// `degrees`: `math.degrees` unless the unit says degrees.
    Degrees,
    /// `speed_mph`: from mph, km/h or (anything else) m/s.
    SpeedMph,
    /// `gallons`: from gallons or litres.
    Gallons,
    /// `acceleration_g`: from g or m/s^2.
    AccelerationG,
    /// `zero_folded(factor)`: `(n or 0.0) * factor`.
    ZeroFolded(f64),
    /// `unit_transform(quantity)`: `_unit_steps(quantity, unit)`.
    Unit(&'static str),
}

use Reducer::{Last, Max, Median, SignedPeak};
use Transform::{
    AccelerationG, Binary, Degrees, Finite, Gallons, Percent, SpeedMph, Unit, ZeroFolded,
};

/// One `add(...)` call of the oracle with a fixed alias list.
struct Spec {
    id: &'static str,
    name: &'static str,
    unit: &'static str,
    category: &'static str,
    aliases: &'static [&'static str],
    transform: Transform,
    reducer: Reducer,
    precision: i64,
    description: &'static str,
}

#[allow(clippy::too_many_arguments)]
const fn spec(
    id: &'static str,
    name: &'static str,
    unit: &'static str,
    category: &'static str,
    aliases: &'static [&'static str],
    transform: Transform,
    reducer: Reducer,
    precision: i64,
    description: &'static str,
) -> Spec {
    Spec {
        id,
        name,
        unit,
        category,
        aliases,
        transform,
        reducer,
        precision,
        description,
    }
}

#[rustfmt::skip]
const CONTROLS: [Spec; 5] = [
    spec("clutch", "Clutch", "%", "Controls", &["Clutch"], Percent, Median, 3, "Recorded clutch position."),
    spec("abs-active", "ABS active", "on / off", "Controls", &["BrakeABSactive"], Binary, Max, 0, "Recorded ABS-active state."),
    spec("abs-cut", "ABS intervention", "%", "Controls", &["BrakeABScutPct"], Percent, Max, 2, "Recorded ABS brake-cut percentage."),
    spec("brake-bias", "Brake bias", "%", "Controls", &["dcBrakeBias"], Percent, Median, 2, "Recorded in-car brake-bias setting."),
    spec("steering-torque", "Steering torque", "Nm", "Controls", &["SteeringWheelTorque"], Finite, SignedPeak, 3, "Recorded steering-wheel torque."),
];

#[rustfmt::skip]
const VEHICLE: [Spec; 5] = [
    spec("vertical-g", "Vertical G", "g", "Vehicle", &["VertAccel"], AccelerationG, SignedPeak, 3, "Recorded vertical acceleration."),
    spec("pitch", "Pitch", "deg", "Vehicle", &["Pitch"], Degrees, SignedPeak, 3, "Recorded chassis pitch angle."),
    spec("roll", "Roll", "deg", "Vehicle", &["Roll"], Degrees, SignedPeak, 3, "Recorded chassis roll angle."),
    spec("pitch-rate", "Pitch rate", "deg/s", "Vehicle", &["PitchRate"], Degrees, SignedPeak, 3, "Recorded chassis pitch rate."),
    spec("roll-rate", "Roll rate", "deg/s", "Vehicle", &["RollRate"], Degrees, SignedPeak, 3, "Recorded chassis roll rate."),
];

#[rustfmt::skip]
const FUEL: [Spec; 2] = [
    spec("fuel-level", "Fuel level", "gal", "Fuel", &["FuelLevel"], Gallons, Median, 3, "Recorded fuel level."),
    spec("fuel-use-rate", "Fuel use rate", "kg/h", "Fuel", &["FuelUsePerHour"], Finite, Median, 3, "Recorded instantaneous engine fuel-use mass rate."),
];

#[rustfmt::skip]
const CONDITIONS_AND_RACE: [Spec; 17] = [
    spec("track-temperature", "Track temperature", "deg F", "Conditions", &["TrackTempCrew", "TrackTemp"], Unit("temperature"), Median, 1, "Recorded track temperature."),
    spec("air-temperature", "Air temperature", "deg F", "Conditions", &["AirTemp"], Unit("temperature"), Median, 1, "Recorded air temperature."),
    spec("wind-speed", "Wind speed", "mph", "Conditions", &["WindVel"], SpeedMph, Median, 1, "Recorded wind speed."),
    spec("humidity", "Relative humidity", "%", "Conditions", &["RelativeHumidity"], Percent, Median, 1, "Recorded relative humidity."),
    spec("fog", "Fog", "%", "Conditions", &["FogLevel"], Percent, Median, 1, "Recorded fog level."),
    spec("precipitation", "Precipitation", "%", "Conditions", &["Precipitation"], Percent, Median, 2, "Recorded precipitation level."),
    spec("air-pressure", "Air pressure", "inHg", "Conditions", &["AirPressure"], ZeroFolded(0.00029529983071445), Median, 2, "Recorded ambient air pressure."),
    spec("air-density", "Air density", "lb/ft3", "Conditions", &["AirDensity"], ZeroFolded(0.0624279606), Median, 4, "Recorded ambient air density."),
    spec("track-wetness", "Track wetness", "state", "Conditions", &["TrackWetness"], Finite, Last, 0, "Recorded iRacing categorical track-wetness state."),
    spec("track-usage", "Track usage", "%", "Conditions", &["TrackUsage"], Percent, Median, 1, "Recorded track-usage percentage."),
    spec("weather-wet", "Wet declared", "on / off", "Conditions", &["WeatherDeclaredWet"], Binary, Max, 0, "Recorded weather-declared-wet state."),
    spec("overall-position", "Overall position", "position", "Race", &["PlayerCarPosition"], Finite, Last, 0, "Recorded overall race position."),
    spec("class-position", "Class position", "position", "Race", &["PlayerCarClassPosition"], Finite, Last, 0, "Recorded class position."),
    spec("distance-ahead", "Distance ahead", "ft", "Race", &["CarDistAhead"], ZeroFolded(3.280839895), Median, 1, "Recorded distance to the car ahead."),
    spec("distance-behind", "Distance behind", "ft", "Race", &["CarDistBehind"], ZeroFolded(3.280839895), Median, 1, "Recorded distance to the car behind."),
    spec("on-pit-road", "On pit road", "on / off", "Race", &["OnPitRoad"], Binary, Max, 0, "Recorded pit-road state."),
    spec("track-surface", "Track surface", "state", "Race", &["PlayerTrackSurface"], Finite, Last, 0, "Recorded iRacing player track-location state."),
];

/// Builds the catalog and the series as the oracle's nested `register`/`add` do.
struct Builder<'t> {
    table: &'t Table,
    sources: Sources,
}

/// What `register` writes into a catalog entry besides the series.
struct CatalogEntry<'a> {
    id: &'a str,
    name: &'a str,
    unit: &'a str,
    category: &'a str,
    evidence_type: &'a str,
    description: &'a str,
    source_channels: &'a [&'a str],
}

impl Builder<'_> {
    /// `register`: skipped for an empty table or a series with no present value.
    fn register(
        &mut self,
        entry: &CatalogEntry<'_>,
        values: Col,
        reducer: Reducer,
        precision: i64,
    ) {
        if values.is_empty() || values.all_missing() {
            return;
        }
        self.sources.signals.push(Signal {
            id: entry.id.to_owned(),
            series: values,
            reducer,
            precision,
        });
        let channels = entry.source_channels.iter().map(|&channel| channel.into());
        self.sources.catalog.push(dict([
            ("id", entry.id.into()),
            ("name", entry.name.into()),
            ("unit", entry.unit.into()),
            ("category", entry.category.into()),
            ("evidence_type", entry.evidence_type.into()),
            ("description", entry.description.into()),
            ("source_channels", PyValue::List(channels.collect())),
        ]));
    }

    /// `add`: the first present alias (recorded), transformed, registered as measured. Returns
    /// the resolved channel.
    #[allow(clippy::too_many_arguments)]
    fn add(
        &mut self,
        id: &str,
        name: &str,
        unit: &str,
        category: &str,
        aliases: &[&str],
        transform: Transform,
        reducer: Reducer,
        precision: i64,
        description: &str,
    ) -> Result<Option<String>, KernelError> {
        let Some(channel) = self.resolve(aliases) else {
            return Ok(None);
        };
        let values = self.transform(&channel, transform)?;
        let entry = CatalogEntry {
            id,
            name,
            unit,
            category,
            evidence_type: "measured",
            description,
            source_channels: &[&channel],
        };
        self.register(&entry, values, reducer, precision);
        Ok(Some(channel))
    }

    fn add_spec(&mut self, spec: &Spec) -> Result<Option<String>, KernelError> {
        self.add(
            spec.id,
            spec.name,
            spec.unit,
            spec.category,
            spec.aliases,
            spec.transform,
            spec.reducer,
            spec.precision,
            spec.description,
        )
    }

    /// `table.resolve(*aliases, default=None)`: records the first present alias.
    fn resolve(&self, aliases: &[&str]) -> Option<String> {
        self.table
            .resolve(aliases, PyValue::None)
            .0
            .map(str::to_owned)
    }

    fn finite_view(&self, channel: &str) -> Rc<View> {
        self.table
            .finite_for(&ColumnKey::Channel(channel.to_owned()))
    }

    fn finite(&self, channel: &str) -> Col {
        Col::View(self.finite_view(channel))
    }

    /// `_normalized_unit(table.unit(channel))`.
    fn unit(&self, channel: &str) -> String {
        normalized_unit(self.table.unit(Some(channel)))
    }

    /// `converted(channel, steps)`: the normalised column, converted and re-normalised.
    fn converted(&self, channel: &str, steps: Option<&[(Op, f64)]>) -> Col {
        let values = self.finite(channel);
        match steps {
            None => values,
            Some(steps) => finite_after_transform(values.map(|n| apply_op_steps(n, steps))),
        }
    }

    fn transform(&self, channel: &str, transform: Transform) -> Result<Col, KernelError> {
        Ok(match transform {
            Finite => self.finite(channel),
            Percent => {
                let key = ColumnKey::Channel(channel.to_owned());
                Col::View(self.table.fractions_for(&key)).map(|n| n * 100.0)
            }
            Binary => binary_column(self.table, channel)?,
            Degrees => {
                if self.unit(channel).contains("deg") {
                    self.finite(channel)
                } else {
                    finite_after_transform(self.finite(channel).map(coach_numerics::degrees))
                }
            }
            SpeedMph => match self.unit(channel).as_str() {
                "mph" | "mi/h" => self.converted(channel, None),
                "km/h" | "kph" => self.converted(channel, Some(&[(Op::Div, 1.609344)])),
                _ => self.converted(channel, Some(&[(Op::Mul, 2.236936)])),
            },
            Gallons => {
                if self.unit(channel).contains("gal") {
                    self.converted(channel, None)
                } else {
                    self.converted(channel, Some(&[(Op::Div, 3.785411784)]))
                }
            }
            AccelerationG => {
                if self.unit(channel) == "g" {
                    self.converted(channel, None)
                } else {
                    self.converted(channel, Some(&[(Op::Div, 9.80665)]))
                }
            }
            // A signed zero becomes +0.0 before the multiplication.
            ZeroFolded(factor) => finite_after_transform(self.finite(channel).map(|n| {
                let folded = if n == 0.0 { 0.0 } else { n };
                folded * factor
            })),
            Unit(quantity) => {
                let unit = self.unit(channel);
                self.converted(channel, unit_steps(quantity, &unit))
            }
        })
    }

    /// `average_channels`: per-sample means of the present converted temperatures.
    fn average_channels(
        &mut self,
        id: &str,
        name: &str,
        alias_groups: &[Vec<String>],
        description: &str,
    ) -> Result<(), KernelError> {
        let available: Vec<String> = alias_groups
            .iter()
            .filter_map(|aliases| self.resolve(&strs(aliases)))
            .collect();
        if available.is_empty() {
            return Ok(());
        }
        let mut columns: Vec<Rc<View>> = Vec::with_capacity(available.len());
        for channel in &available {
            let values = self.finite_view(channel);
            columns.push(match unit_steps("temperature", &self.unit(channel)) {
                None => values,
                Some(steps) => Rc::new(apply_steps(&values, steps)),
            });
        }
        let refs: Vec<&View> = columns.iter().map(Rc::as_ref).collect();
        let means = Col::View(Rc::new(row_means(&refs)?));
        let entry = CatalogEntry {
            id,
            name,
            unit: "deg F",
            category: "Tires",
            evidence_type: "measured",
            description,
            source_channels: &strs(&available),
        };
        self.register(&entry, means, Median, 2);
        Ok(())
    }
}

fn strs(names: &[String]) -> Vec<&str> {
    names.iter().map(String::as_str).collect()
}

/// `_binary_column(table._raw_column(channel))`: a missing cell stays `None`, text through
/// `_bool`, anything else `bool(v)`, as 0.0 or 1.0.
pub(super) fn binary_column(table: &Table, channel: &str) -> Result<Col, KernelError> {
    let raw = table.raw(&ColumnKey::Channel(channel.to_owned()))?;
    let levels = (0..raw.len())
        .map(|index| {
            if !raw.is_present(index) {
                return Ok(f64::NAN);
            }
            Ok(if raw.boolean(index)? { 1.0 } else { 0.0 })
        })
        .collect::<Result<Vec<f64>, KernelError>>()?;
    Ok(Col::Derived(Rc::new(levels)))
}

/// `_sideslip_column(longitudinal, lateral)`: chassis sideslip in degrees, `None` below 5 m/s,
/// at or below 0.5 m/s forward, for a missing sample or a non-finite angle. `atan2` is the C
/// library's, as CPython calls it (platform family, `numerics.md`).
pub(super) fn sideslip_column(longitudinal: &Col, lateral: &Col) -> Col {
    let length = longitudinal.len().min(lateral.len());
    Col::Derived(Rc::new(
        (0..length)
            .map(|index| match (longitudinal.at(index), lateral.at(index)) {
                (Some(forward), Some(sideways))
                    if !(coach_numerics::hypot(forward, sideways) < 5.0 || forward <= 0.5) =>
                {
                    let angle = coach_numerics::degrees(coach_numerics::atan2(sideways, forward));
                    if angle.is_finite() { angle } else { f64::NAN }
                }
                _ => f64::NAN,
            })
            .collect(),
    ))
}

/// `_additional_trace_sources(table, completeness=..., steering=basis)`.
pub(super) fn additional_trace_sources(
    table: &Table,
    steering: &SteeringBasis,
) -> Result<Sources, KernelError> {
    let mut b = Builder {
        table,
        sources: Sources {
            catalog: Vec::new(),
            signals: Vec::new(),
        },
    };
    for spec in &CONTROLS {
        b.add_spec(spec)?;
    }
    if steering.road_wheel() {
        let description = format!(
            "Recorded steering-wheel angle before dividing by the {} steering ratio. \
             It depends on the ratio; Steering (road wheel) is the comparable value.",
            steering.ratio_text.as_deref().unwrap_or("None")
        );
        b.add(
            "steering-wheel-angle",
            "Steering wheel angle",
            "deg",
            "Controls",
            &["SteeringWheelAngle"],
            Degrees,
            SignedPeak,
            2,
            &description,
        )?;
    }
    for spec in &VEHICLE {
        b.add_spec(spec)?;
    }

    let speed_channel = b.resolve(&["Speed"]);
    for tire in TIRES {
        let lower = tire.to_lowercase();
        let wheel_channel = b.add(
            &format!("{lower}-wheel-speed"),
            &format!("{tire} wheel speed"),
            "mph",
            "Tires",
            &[&format!("{tire}speed")],
            SpeedMph,
            Median,
            3,
            "Recorded individual wheel speed.",
        )?;
        if let (Some(wheel), Some(speed)) = (&wheel_channel, &speed_channel) {
            // ratio * 100.0 within +/-200 %, else a gap (_wheel_ratio_column).
            let ratios = table.wheel_ratios(
                &ColumnKey::Channel(speed.clone()),
                &ColumnKey::Channel(wheel.clone()),
            );
            let entry = CatalogEntry {
                id: &format!("{lower}-wheel-slip"),
                name: &format!("{tire} wheel slip"),
                unit: "%",
                category: "Tires",
                evidence_type: "derived",
                description: "Wheel speed relative to recorded vehicle speed; positive is spin \
                              and negative is lock.",
                source_channels: &[wheel, speed],
            };
            b.register(
                &entry,
                Col::View(ratios).map(|ratio| ratio * 100.0),
                SignedPeak,
                2,
            );
        }
        b.add(
            &format!("{lower}-pressure"),
            &format!("{tire} pressure"),
            "psi",
            "Tires",
            &strs(&tire_pressure_aliases(tire, false)),
            Unit("pressure"),
            Median,
            2,
            "Recorded live tire pressure; it does not establish tire wear.",
        )?;
        let groups = |positions: [&str; 3]| -> Vec<Vec<String>> {
            positions
                .iter()
                .map(|position| tire_temperature_aliases(tire, position))
                .collect()
        };
        b.average_channels(
            &format!("{lower}-carcass-temp"),
            &format!("{tire} carcass temperature"),
            &groups(["CL", "CM", "CR"]),
            "Average of the recorded inner, middle, and outer carcass temperatures.",
        )?;
        b.average_channels(
            &format!("{lower}-surface-temp"),
            &format!("{tire} surface temperature"),
            &groups(["L", "M", "R"]),
            "Average of the recorded inner, middle, and outer surface temperatures.",
        )?;
    }

    for spec in &FUEL {
        b.add_spec(spec)?;
    }
    let platform = platform_channel_aliases();
    let platform_aliases = |name: &str| -> Vec<String> {
        platform
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, aliases)| aliases.clone())
            .unwrap_or_default()
    };
    b.add(
        "center-front-ride-height",
        "Center-front ride height",
        "in",
        "Chassis",
        &strs(&platform_aliases("center_front_splitter")),
        Unit("distance"),
        SignedPeak,
        3,
        "Recorded center-front or splitter ride height.",
    )?;
    for tire in TIRES {
        let lower = tire.to_lowercase();
        b.add(
            &format!("{lower}-ride-height"),
            &format!("{tire} ride height"),
            "in",
            "Chassis",
            &strs(&platform_aliases(tire)),
            Unit("distance"),
            SignedPeak,
            3,
            "Recorded corner ride height.",
        )?;
        b.add(
            &format!("{lower}-shock-deflection"),
            &format!("{tire} shock deflection"),
            "in",
            "Chassis",
            &strs(&shock_channel_aliases(tire, "deflection")),
            Unit("distance"),
            SignedPeak,
            3,
            "Recorded shock or damper deflection.",
        )?;
        b.add(
            &format!("{lower}-shock-velocity"),
            &format!("{tire} shock velocity"),
            "in/s",
            "Chassis",
            &strs(&shock_channel_aliases(tire, "velocity")),
            Unit("velocity"),
            SignedPeak,
            3,
            "Recorded shock or damper velocity.",
        )?;
    }
    for spec in &CONDITIONS_AND_RACE {
        b.add_spec(spec)?;
    }
    Ok(b.sources)
}
