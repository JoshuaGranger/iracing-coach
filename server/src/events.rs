//! Server-sent events: one broadcast bus; each SSE stream filters by driver.

use serde_json::{Value, json};
use tokio::sync::broadcast;

#[derive(Debug, Clone)]
pub struct Event {
    /// `type` of the ServerEvent (`presence`, `recording_state`, `job_state`, `analysis_ready`,
    /// `library_changed`).
    pub kind: &'static str,
    /// Driver the event belongs to; `None` is delivered to every stream.
    pub customer_id: Option<i64>,
    pub payload: Value,
}

#[derive(Clone)]
pub struct EventBus {
    tx: broadcast::Sender<Event>,
}

impl Default for EventBus {
    fn default() -> Self {
        EventBus {
            tx: broadcast::channel(1024).0,
        }
    }
}

impl EventBus {
    pub fn subscribe(&self) -> broadcast::Receiver<Event> {
        self.tx.subscribe()
    }

    pub fn publish(&self, kind: &'static str, customer_id: Option<i64>, mut payload: Value) {
        payload["type"] = Value::String(kind.to_string());
        let _ = self.tx.send(Event {
            kind,
            customer_id,
            payload,
        });
    }

    pub fn recording_state(
        &self,
        sha256: &str,
        customer_id: Option<i64>,
        state: &str,
        reason: Option<&str>,
    ) {
        self.publish(
            "recording_state",
            customer_id,
            json!({"sha256": sha256, "customer_id": customer_id, "state": state, "reason": reason}),
        );
    }

    pub fn job_state(&self, job: Value, customer_id: Option<i64>, error_code: Option<&str>) {
        self.publish(
            "job_state",
            customer_id,
            json!({"job": job, "customer_id": customer_id, "error_code": error_code}),
        );
    }

    pub fn library_changed(&self, customer_id: i64, scope: &str) {
        self.publish(
            "library_changed",
            Some(customer_id),
            json!({"customer_id": customer_id, "scope": scope}),
        );
    }

    pub fn analysis_ready(
        &self,
        customer_id: i64,
        session_id: i64,
        analysis_id: &str,
        projection: u32,
    ) {
        self.publish(
            "analysis_ready",
            Some(customer_id),
            json!({
                "customer_id": customer_id,
                "session_id": session_id,
                "analysis_id": analysis_id,
                "projection_version": projection,
            }),
        );
    }
}
