//! One SDK tick as the collector hands it to the engine.

use std::sync::Arc;

use crate::session::SessionContext;

/// One per new tick, reusing the allocation. Every SDK field is the value as read through the
/// `coach_ibt::VarTable` reader named in the contract's table 4.3; nothing is validated here.
/// Missing = `None`; arrays are empty when missing. A present non-finite value stays `Some(NaN)`
/// (array elements keep NaN at their index); fallbacks select by presence, as C# `??` does.
#[derive(Debug, Clone, Default)]
pub struct Sample {
    // Collector-provided.
    /// Collector wall clock right after the row copy; the engine's only clock.
    pub captured_unix_ms: u64,
    /// Status bit 0 set and the tick advanced within the last 2 s.
    pub connected: bool,
    /// Newest buffer's tick (SDK i32 reinterpreted).
    pub tick: u32,
    /// Header `tick_rate`, saturating.
    pub tick_rate: u16,
    pub session: Option<Arc<SessionContext>>,
    // SDK variables.
    pub player_car_idx: Option<i32>,
    pub player_car_position: Option<i32>,
    pub player_car_class_position: Option<i32>,
    pub car_idx_position: Vec<i32>,
    pub car_idx_class_position: Vec<i32>,
    pub car_idx_lap: Vec<i32>,
    pub car_idx_f2_time: Vec<f32>,
    pub car_idx_last_lap_time: Vec<f32>,
    pub session_flags: Option<u32>,
    pub player_car_tow_time: Option<f64>,
    pub lap: Option<i32>,
    pub session_laps_remain_ex: Option<f64>,
    pub session_laps_remain: Option<f64>,
    pub lap_last_lap_time: Option<f64>,
    pub fuel_level: Option<f64>,
    pub fuel_level_pct: Option<f64>,
    pub track_temp_crew: Option<f64>,
    pub track_temp: Option<f64>,
    pub air_temp: Option<f64>,
    pub dc_brake_bias: Option<f64>,
    pub on_pit_road: Option<bool>,
    pub pit_repair_left: Option<f64>,
    pub pit_opt_repair_left: Option<f64>,
    pub steering_wheel_angle: Option<f64>,
    pub throttle: Option<f64>,
    pub brake: Option<f64>,
    pub brake_abs_active: Option<bool>,
    pub gear: Option<i32>,
    pub rpm: Option<f64>,
    pub yaw_rate: Option<f64>,
    pub lat_accel: Option<f64>,
    pub long_accel: Option<f64>,
    pub speed: Option<f64>,
    pub lap_dist_pct: Option<f64>,
    pub lat: Option<f64>,
    pub lon: Option<f64>,
    pub session_time: Option<f64>,
    pub session_num: Option<i32>,
    pub session_unique_id: Option<i64>,
    pub player_car_my_incident_count: Option<i32>,
    pub player_incidents: Option<i32>,
    pub is_on_track: Option<bool>,
    pub is_in_garage: Option<bool>,
}

impl Sample {
    /// All `None`/empty, capacity kept.
    pub fn clear(&mut self) {
        let mut car_idx_position = std::mem::take(&mut self.car_idx_position);
        let mut car_idx_class_position = std::mem::take(&mut self.car_idx_class_position);
        let mut car_idx_lap = std::mem::take(&mut self.car_idx_lap);
        let mut car_idx_f2_time = std::mem::take(&mut self.car_idx_f2_time);
        let mut car_idx_last_lap_time = std::mem::take(&mut self.car_idx_last_lap_time);
        car_idx_position.clear();
        car_idx_class_position.clear();
        car_idx_lap.clear();
        car_idx_f2_time.clear();
        car_idx_last_lap_time.clear();
        *self = Sample {
            car_idx_position,
            car_idx_class_position,
            car_idx_lap,
            car_idx_f2_time,
            car_idx_last_lap_time,
            ..Sample::default()
        };
    }

    /// Fills every SDK field from one row (the collector's copy of the newest buffer). The
    /// collector-provided fields (`captured_unix_ms`, `connected`, `tick`, `tick_rate`, `session`)
    /// are left as they are.
    pub fn read_row(&mut self, vars: &coach_ibt::VarTable, row: &[u8]) {
        self.player_car_idx = vars.i32("PlayerCarIdx", row);
        self.player_car_position = vars.i32("PlayerCarPosition", row);
        self.player_car_class_position = vars.i32("PlayerCarClassPosition", row);
        vars.i32_array("CarIdxPosition", row, &mut self.car_idx_position);
        vars.i32_array("CarIdxClassPosition", row, &mut self.car_idx_class_position);
        vars.i32_array("CarIdxLap", row, &mut self.car_idx_lap);
        vars.f32_array("CarIdxF2Time", row, &mut self.car_idx_f2_time);
        vars.f32_array("CarIdxLastLapTime", row, &mut self.car_idx_last_lap_time);
        self.session_flags = vars.u32("SessionFlags", row);
        self.player_car_tow_time = vars.f64("PlayerCarTowTime", row);
        self.lap = vars.i32("Lap", row);
        self.session_laps_remain_ex = vars.f64("SessionLapsRemainEx", row);
        self.session_laps_remain = vars.f64("SessionLapsRemain", row);
        self.lap_last_lap_time = vars.f64("LapLastLapTime", row);
        self.fuel_level = vars.f64("FuelLevel", row);
        self.fuel_level_pct = vars.f64("FuelLevelPct", row);
        self.track_temp_crew = vars.f64("TrackTempCrew", row);
        self.track_temp = vars.f64("TrackTemp", row);
        self.air_temp = vars.f64("AirTemp", row);
        self.dc_brake_bias = vars.f64("dcBrakeBias", row);
        self.on_pit_road = vars.bool("OnPitRoad", row);
        self.pit_repair_left = vars.f64("PitRepairLeft", row);
        self.pit_opt_repair_left = vars.f64("PitOptRepairLeft", row);
        self.steering_wheel_angle = vars.f64("SteeringWheelAngle", row);
        self.throttle = vars.f64("Throttle", row);
        self.brake = vars.f64("Brake", row);
        // C# `ReadBool(...) ?? (ReadDouble(...) is {} v ? v > .5 : null)`; NaN compares false.
        self.brake_abs_active = vars
            .bool("BrakeABSactive", row)
            .or_else(|| vars.f64("BrakeABSactive", row).map(|value| value > 0.5));
        self.gear = vars.i32("Gear", row);
        self.rpm = vars.f64("RPM", row);
        self.yaw_rate = vars.f64("YawRate", row);
        self.lat_accel = vars.f64("LatAccel", row);
        self.long_accel = vars.f64("LongAccel", row);
        self.speed = vars.f64("Speed", row);
        self.lap_dist_pct = vars.f64("LapDistPct", row);
        self.lat = vars.f64("Lat", row);
        self.lon = vars.f64("Lon", row);
        self.session_time = vars.f64("SessionTime", row);
        self.session_num = vars.i32("SessionNum", row);
        self.session_unique_id = vars.i64("SessionUniqueID", row);
        self.player_car_my_incident_count = vars.i32("PlayerCarMyIncidentCount", row);
        self.player_incidents = vars.i32("PlayerIncidents", row);
        self.is_on_track = vars.bool("IsOnTrack", row);
        self.is_in_garage = vars.bool("IsInGarage", row);
    }
}
