## Safe Motor Integration Plan for `Fishel Lab MagScope and MagTrack`

### Summary
Implement motor support in the `c:\Users\magne\Documents\Fishel Lab MagScope and MagTrack` workspace as a local extension package that plugs into current MagScope (`scope.add_hardware`, `scope.add_control`).  
Use your `v2` `motors.py` patterns for PI/Zaber device behavior, but add strict software safety controls by default to protect sensitive microscope hardware.

### Public API and Interface Additions
1. Add package `magscope_motors/` with:
   `manager.py`, `adapters/pi_objective.py`, `adapters/zaber_linear.py`, `adapters/zaber_rotary.py`, `commands.py`, `control_panel.py`, `beadlock_ext.py`.
2. Add IPC/script command dataclasses in `magscope_motors/commands.py`:
   `SetMotorArmedCommand`, `ConnectMotorsCommand`, `DisconnectMotorsCommand`, `StopAllMotorsCommand`, objective/linear/rotary move commands, `SetSessionSafetyWindowCommand`, `UpdateMotorStatusCommand`, `UpdateMotorFaultCommand`.
3. Add `MotorManager(HardwareManagerBase)` as one process controlling all 3 axes.
4. Add `MotorControlPanel(ControlPanelBase)` minimal UI:
   connect/disconnect, arm/disarm, status, basic move controls, stop-all.
5. Add `MotorAwareBeadLockManager(BeadLockManager)` implementing `do_z_lock` by sending objective move commands.

### Settings Contract (added to `settings.yaml`)
1. `motors.enabled: true`
2. `motors.require_arm: true`
3. `motors.arm_timeout_sec: 30`
4. `motors.discovery_mode: settings_then_fallback_scan`
5. `motors.objective.model: E-709`
6. `motors.objective.serial_number: null`
7. `motors.objective.min_nm` and `motors.objective.max_nm` (hard limits)
8. `motors.linear.model: X-LSQ075A-E01`
9. `motors.linear.port: COM8`
10. `motors.linear.min_mm` and `motors.linear.max_mm` (hard limits)
11. `motors.rotary.model: X-NMS17-E01`
12. `motors.rotary.port: COM9`
13. `motors.rotary.min_turns` and `motors.rotary.max_turns` (hard limits)
14. `motors.test_mode: true`
15. `motors.test_caps.objective_max_step_nm: 1000`
16. `motors.test_caps.linear_max_step_mm: 0.2`
17. `motors.test_caps.linear_max_speed_mm_s: 0.5`
18. `motors.test_caps.rotary_max_step_turns: 0.2`
19. `motors.test_caps.rotary_max_speed_turns_s: 0.5`
20. `motors.session_window.enabled: true`
21. `motors.session_window.objective_nm: 10000`
22. `motors.session_window.linear_mm: 1.0`
23. `motors.session_window.rotary_turns: 1.0`

### Safety Behavior (locked decisions)
1. No auto-home and no auto-zero on startup.
2. All move commands rejected unless armed.
3. Arming auto-expires after 30 seconds and requires re-arm.
4. Every move validated against:
   static hard limits, session window limits, per-command step caps, and speed caps.
5. Stop-all command available and exposed in UI/script.
6. If any safety check fails, command is not sent to hardware and a fault update is emitted.
7. Test mode defaults to ON for initial bring-up.

### Implementation Steps
1. Create adapter interfaces and implement PI/Zaber adapters using `v2` connection/query/move logic.
2. Implement `MotorManager.connect/disconnect/fetch` with robust reconnect handling and telemetry.
3. Implement command handlers with centralized `SafetyGuard` validation.
4. Implement telemetry buffer and optional acquisition-time file dumps.
5. Implement `MotorAwareBeadLockManager.do_z_lock` to command small objective relative moves only through safety gate.
6. Wire in `main.py`:
   replace beadlock manager, add motor manager, add motor panel.
7. Add dependency note: `pipython` is required for PI objective motor and is currently missing in this venv.
8. Add tests and dry-run mocks before physical motor testing.

### Test Cases and Scenarios
1. Manager starts disconnected with no crash when hardware is absent.
2. Connect succeeds with configured IDs; fallback scan works when configured port missing.
3. Move blocked when not armed.
4. Move blocked after arm timeout expires.
5. Out-of-range hard-limit move blocked.
6. Out-of-window session-limit move blocked.
7. Over-step and over-speed test-cap moves blocked.
8. Valid small move passes and updates status/telemetry.
9. Stop-all interrupts active motion.
10. Z-lock path emits objective move commands that still pass safety checks.

### Assumptions and Defaults
1. Development and file writing happen only in `Fishel Lab MagScope and MagTrack`.
2. Legacy `v2` motor logic is reference behavior, not copied verbatim.
3. One combined motor manager process is preferred over separate motor processes.
4. Force clamp/ramp and Z-LUT motor scan automation are deferred until safe phase-1 integration is validated.
