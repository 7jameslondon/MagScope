# AGENTS.md

## Project
Safe motor integration for `Fishel Lab MagScope and MagTrack`.

## Goal
Add PI objective + Zaber linear + Zaber rotary motor support as a local extension package that plugs into MagScope via `scope.add_hardware(...)` and `scope.add_control(...)`, with strict software safety defaults.

## Locked Safety Rules (Do Not Relax)
1. No auto-home and no auto-zero on startup.
2. All move commands are rejected unless motors are armed.
3. Arming remains active until explicitly disarmed.
4. Every move must pass all safety checks before sending to hardware:
   - objective/linear hard limits
   - rotary hard-limit check disabled (unbounded by position)
   - session window limits
   - speed caps
5. Stop-all must be available through UI and script/IPC commands.
6. Safety failures must not touch hardware; emit fault telemetry.
7. `test_mode` defaults to `true` during phase-1 bring-up.

## Required Package Layout
Create `magscope_motors/` with:
- `manager.py`
- `commands.py`
- `control_panel.py`
- `beadlock_ext.py`
- `adapters/pi_objective.py`
- `adapters/zaber_linear.py`
- `adapters/zaber_rotary.py`

## Required Interfaces
1. `MotorManager(HardwareManagerBase)` controls all three axes in one manager process.
2. `MotorControlPanel(ControlPanelBase)` provides:
   - connect/disconnect
   - arm/disarm
   - status/fault display
   - full move/control options from the previous motor UI (no functional regression)
   - stop-all
3. GUI presentation must match current MagScope panel style conventions (layout, spacing, control patterns, naming) while adding motor functionality.
4. `MotorAwareBeadLockManager(BeadLockManager)` overrides `do_z_lock` to send objective relative moves through motor safety gating.

## GUI Requirements (Motor Panels)
1. Add three motor panels in the motor control UI:
   - objective panel
   - linear panel
   - rotary panel
2. Each panel must expose both:
   - current live position (`actual_position`)
   - commanded target position (`target_position`)
3. Panel controls should include all options from the previous version for that motor type (step/relative move, absolute move where supported, speed/limits where previously available, enable/disable state indicators).
4. Shared controls (connect/disconnect, arm/disarm, stop-all) must remain globally visible and consistent with existing UI patterns.
5. Fault and safety-block messages must be visible at panel level and in shared status area.

## Required Commands (commands.py)
Implement dataclasses for IPC/script control:
- `SetMotorArmedCommand`
- `ConnectMotorsCommand`
- `DisconnectMotorsCommand`
- `StopAllMotorsCommand`
- objective/linear/rotary move commands
- `SetSessionSafetyWindowCommand`
- `UpdateMotorStatusCommand`
- `UpdateMotorFaultCommand`

## Settings Contract (settings.yaml + motors_settings.yaml)
Keep MagScope core settings and motor settings split:

`settings.yaml` (MagScope core keys only, valid for `MagScopeSettings`):
```yaml
ROI: 28
magnification: 10
tracks max datapoints: 1000000
video buffer n images: 40
video buffer n stacks: 5
video processors n: 3
xy-lock default interval: 10
xy-lock default max: 10
xy-lock default window: 10
z-lock default interval: 10
z-lock default max: 1000
```

`motors_settings.yaml` (motor extension keys):
```yaml
enabled: true
require_arm: true
discovery_mode: settings_then_fallback_scan
objective:
  model: E-709
  serial_number: null
  min_nm: <required>
  max_nm: <required>
linear:
  model: X-LSQ075A-E01
  port: COM8
  min_mm: <required>
  max_mm: <required>
rotary:
  model: X-NMS17-E01
  port: COM9
  min_turns: <optional/ignored by safety hard-limit check>
  max_turns: <optional/ignored by safety hard-limit check>
test_mode: true
test_caps:
  linear_max_speed_mm_s: 0.5
  rotary_max_speed_turns_s: 0.5
session_window:
  enabled: true
  objective_nm: 10000
  linear_mm: 1.0
  rotary_turns: 1.0
```

## Implementation Order
1. Build adapter interfaces and PI/Zaber adapter implementations using v2 behavior as reference (not copy-paste).
2. Implement `MotorManager` connect/disconnect/fetch with reconnect handling and telemetry.
3. Add centralized `SafetyGuard` used by every move/velocity command path.
4. Implement motor UI with three per-axis panels, preserving current style while restoring all prior control options.
5. Add telemetry buffer and optional acquisition-time file dumps.
6. Implement `MotorAwareBeadLockManager.do_z_lock` with small relative objective moves only.
7. Wire into `main.py`:
   - replace beadlock manager
   - register motor manager
   - register motor control panel
8. Ensure PI dependency note is present (`pipython` required).
9. Add tests and dry-run mocks before physical motor testing.

## Plot Integration Plan
1. Extend motor status telemetry to carry, per axis:
   - timestamp
   - actual_position
   - target_position
   - optional velocity/state/fault
2. Add plot panels for all 3 motors (objective, linear, rotary) in the existing plot area style.
3. For each motor plot, render two traces:
   - actual current position
   - commanded target position
4. Keep units axis-specific and explicit:
   - objective: nm
   - linear: mm
   - rotary: turns
5. Align timestamps with acquisition/lock timeline so motor traces can be compared with tracking/lock behavior.
6. Add trace toggles and legend labels to avoid clutter while keeping both traces available by default.
7. Persist short rolling buffers for responsive UI and optional full-resolution dumps to file when acquisition logging is enabled.

## Required Test Scenarios
1. Startup disconnected when hardware absent, no crash.
2. Connect by configured IDs; fallback scan when configured port missing.
3. Move blocked when not armed.
4. Armed state persists until explicit disarm.
5. Objective/linear hard-limit violation blocked.
6. Session-window violation blocked.
7. Speed cap violation blocked.
8. Valid small move succeeds; status and telemetry update emitted.
9. Stop-all interrupts active motion.
10. Z-lock emits objective move commands that still pass safety checks.
11. GUI shows 3 motor panels and preserves current style conventions.
12. GUI exposes all prior motor controls with no missing options.
13. Each motor plot shows both actual and target traces with correct units.
14. Plot timestamps are monotonic and aligned with acquisition timeline.
15. Safety-rejected commands update target/fault telemetry without false actual-position changes.

## Definition of Done
1. All required command types exist and are handled.
2. All move paths go through one shared safety gate.
3. UI includes 3 styled motor panels with full prior control parity.
4. Plotting includes both actual and target position traces for all 3 motors.
5. `main.py` integration works with motors disabled/enabled.
6. Required tests pass in dry-run mode.
7. Hardware tests executed only after dry-run pass and manual safety review.

## Deferred Work
1. Force clamp/ramp automation.
2. Z-LUT motor scan automation.
