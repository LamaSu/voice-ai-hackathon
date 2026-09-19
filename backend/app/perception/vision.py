"""Gaze / head-pose telemetry from the browser (MediaPipe runs client-side; only numbers arrive)."""

from __future__ import annotations

from typing import Any

from app.state.engine import StateEngine


def apply_gaze(engine: StateEngine, data: dict[str, Any]) -> None:
    v = engine.state.vision
    v.enabled = True
    v.face_present = bool(data.get("face_present", False))
    v.looking_at_agent = bool(data.get("looking_at_agent", False))
    v.gaze_confidence = float(data.get("gaze_confidence", 0.0) or 0.0)
    v.gaze_x = float(data.get("gaze_x", 0.5) or 0.5)
    v.gaze_y = float(data.get("gaze_y", 0.5) or 0.5)
    v.head_yaw = float(data.get("head_yaw", 0.0) or 0.0)
    v.head_pitch = float(data.get("head_pitch", 0.0) or 0.0)
    v.head_roll = float(data.get("head_roll", 0.0) or 0.0)
    v.updated_at = engine.now()
