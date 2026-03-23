from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import time

import numpy as np

from magscope._logging import get_logger
from magscope.datatypes import MatrixBuffer, ZLUTSweepDataset
from magscope.hardware import FocusMotorBase
from magscope.ipc import register_ipc_command
from magscope.ipc_commands import (
    ArmZLUTSweepCaptureCommand,
    CancelGeneratedZLUTEvaluationCommand,
    CancelZLUTGenerationCommand,
    DisarmZLUTSweepCaptureCommand,
    LoadZLUTCommand,
    MoveFocusMotorAbsoluteCommand,
    ReportZLUTProfileLengthCommand,
    RequestZLUTProfileLengthCommand,
    SaveGeneratedZLUTCommand,
    SelectGeneratedZLUTBeadCommand,
    SetAcquisitionOnCommand,
    ShowErrorCommand,
    StartZLUTGenerationCommand,
    UpdateZLUTGenerationEvaluationCommand,
    UpdateZLUTGenerationProgressCommand,
    UpdateZLUTGenerationStateCommand,
    ZLUTSweepCaptureCompleteCommand,
)
from magscope.processes import ManagerProcessBase

logger = get_logger('zlut_generation')


@dataclass(frozen=True)
class GeneratedZLUTResult:
    bead_id: int
    zlut_array: np.ndarray


class ZLUTGenerationManager(ManagerProcessBase):
    def __init__(self):
        super().__init__()
        self._active = False
        self._cancel_requested = False
        self._current_step_index = 0
        self._dataset: ZLUTSweepDataset | None = None
        self._focus_buffer: MatrixBuffer | None = None
        self._focus_motor_name: str | None = None
        self._generated_zluts: dict[int, GeneratedZLUTResult] = {}
        self._last_progress_emit = 0.0
        self._phase = 'idle'
        self._previous_acquisition_on = False
        self._profile_length: int | None = None
        self._profiles_per_bead = 0
        self._requested_range: tuple[float, float, float] | None = None
        self._selected_bead_id: int | None = None
        self._step_capture_complete = False
        self._steps = np.zeros((0,), dtype=np.float64)

    def setup(self):
        self._focus_motor_name = self._discover_focus_motor_name()
        if self._focus_motor_name is not None:
            self._focus_buffer = MatrixBuffer(
                create=False,
                locks=self.locks,
                name=self._focus_motor_name,
            )

    def do_main_loop(self):
        if not self._active:
            return

        if self._cancel_requested:
            self._cancel_session()
            return

        if self._phase == 'moving':
            self._advance_when_in_position()
        elif self._phase == 'capturing':
            self._advance_after_capture()

        self._maybe_send_progress()

    def quit(self):
        self._cleanup_runtime_state(destroy_dataset=True)
        super().quit()

    @register_ipc_command(StartZLUTGenerationCommand)
    def start_generation(self, start_nm: float, step_nm: float, stop_nm: float):
        if self._active or self._phase == 'evaluating':
            self._send_state(
                'Generation already running.',
                detail='Cancel the current sweep before starting another one.',
                running=self._active,
                can_cancel=self._active,
                phase=self._phase,
            )
            return

        self._refresh_bead_roi_cache()

        try:
            self._prepare_session(start_nm, step_nm, stop_nm)
        except Exception as exc:
            reason = str(exc).strip() or repr(exc)
            logger.warning('Could not start Z-LUT generation: %s', reason)
            self.send_ipc(ShowErrorCommand(text='Could not start Z-LUT generation', details=reason))
            self._send_state('Generation failed to start.', detail=reason, phase='idle')
            self._cleanup_runtime_state(destroy_dataset=True)
            return

        self._send_state(
            'Waiting for a processed frame to measure profile length.',
            detail='Z-LUT generation is preparing shared memory and capture settings.',
            running=True,
            can_cancel=True,
            phase='waiting_profile_length',
        )
        self._send_progress(force=True)
        self.send_ipc(SetAcquisitionOnCommand(True))
        self.send_ipc(RequestZLUTProfileLengthCommand())

    @register_ipc_command(CancelZLUTGenerationCommand)
    def cancel_generation(self):
        if self._phase == 'evaluating':
            self.cancel_evaluation()
            return
        if not self._active:
            return
        self._cancel_requested = True
        self._send_state(
            'Canceling Z-LUT generation...',
            running=True,
            can_cancel=False,
            phase=self._phase,
        )

    @register_ipc_command(ReportZLUTProfileLengthCommand)
    def report_profile_length(self, profile_length: int | None = None):
        if not self._active or self._phase != 'waiting_profile_length':
            return
        if profile_length is None or int(profile_length) <= 0:
            self._fail_session('Could not determine profile length from the current tracking output.')
            return

        self._profile_length = int(profile_length)
        self._create_dataset()
        self._current_step_index = 0
        self._step_capture_complete = False
        self._issue_move_for_current_step()

    @register_ipc_command(ZLUTSweepCaptureCompleteCommand)
    def handle_capture_complete(self, step_index: int, written_count: int, error: str | None = None):
        if not self._active or self._phase != 'capturing':
            return
        if step_index != self._current_step_index:
            return
        if error:
            self._fail_session(error)
            return
        if written_count <= 0:
            self._fail_session('Sweep capture completed without any profiles being written.')
            return
        self._step_capture_complete = True

    @register_ipc_command(SelectGeneratedZLUTBeadCommand)
    def select_generated_bead(self, bead_id: int):
        if self._phase != 'evaluating':
            return
        bead_id = int(bead_id)
        if bead_id not in self._generated_zluts:
            self._fail_evaluation(f'Generated Z-LUT bead {bead_id} is not available.')
            return
        self._selected_bead_id = bead_id
        self._send_evaluation_state(active=True)

    @register_ipc_command(SaveGeneratedZLUTCommand)
    def save_generated_zlut(self, filepath: str, bead_id: int):
        if self._phase != 'evaluating':
            return

        bead_id = int(bead_id)
        result = self._generated_zluts.get(bead_id)
        if result is None:
            self._fail_evaluation(f'Generated Z-LUT bead {bead_id} is not available.')
            return

        path = Path(filepath).expanduser()
        if not path.parent.exists():
            self._fail_evaluation(f'Directory does not exist: {path.parent}')
            return

        try:
            np.savetxt(path, result.zlut_array)
        except Exception as exc:
            reason = str(exc).strip() or repr(exc)
            self._fail_evaluation(f'Failed to save generated Z-LUT: {reason}')
            return

        self.send_ipc(LoadZLUTCommand(filepath=str(path)))
        self._send_state(
            'Generated Z-LUT saved and loaded.',
            detail=f'Saved bead {bead_id} to {path}',
            running=False,
            can_cancel=False,
            phase='complete',
        )
        self._cleanup_runtime_state(destroy_dataset=True)

    @register_ipc_command(CancelGeneratedZLUTEvaluationCommand)
    def cancel_evaluation(self):
        if self._phase != 'evaluating':
            return
        self._send_state(
            'Generated Z-LUT discarded.',
            detail='The temporary sweep dataset has been cleared without loading a new Z-LUT.',
            running=False,
            can_cancel=False,
            phase='idle',
        )
        self._cleanup_runtime_state(destroy_dataset=True)

    def _prepare_session(self, start_nm: float, step_nm: float, stop_nm: float) -> None:
        self._cleanup_runtime_state(destroy_dataset=True)
        self._focus_motor_name = self._focus_motor_name or self._discover_focus_motor_name()
        if self._focus_motor_name is None:
            raise RuntimeError('No FocusMotorBase hardware is registered.')
        if self._focus_buffer is None:
            self._focus_buffer = MatrixBuffer(create=False, locks=self.locks, name=self._focus_motor_name)
        if self.video_buffer is None:
            raise RuntimeError('Video buffer is not available.')
        if self._bead_roi_ids.size == 0 or self._bead_roi_values.shape[0] == 0:
            raise RuntimeError('At least one bead ROI must be selected before generating a Z-LUT.')

        steps = self._build_steps(start_nm, step_nm, stop_nm)
        self._active = True
        self._cancel_requested = False
        self._current_step_index = 0
        self._dataset = None
        self._generated_zluts = {}
        self._last_progress_emit = 0.0
        self._phase = 'waiting_profile_length'
        self._previous_acquisition_on = bool(self._acquisition_on)
        self._profile_length = None
        self._profiles_per_bead = int(self.video_buffer.n_images)
        self._requested_range = (float(start_nm), float(step_nm), float(stop_nm))
        self._selected_bead_id = None
        self._step_capture_complete = False
        self._steps = steps
        self._send_evaluation_state(active=False)

    def _create_dataset(self) -> None:
        if self._profile_length is None:
            raise RuntimeError('Profile length must be known before creating the dataset.')
        n_steps = int(self._steps.size)
        n_beads = int(self._bead_roi_ids.size)
        capacity = n_steps * n_beads * self._profiles_per_bead
        self._reset_dataset(destroy=True)
        self._dataset = ZLUTSweepDataset.create(
            locks=self.locks,
            capacity=capacity,
            profile_length=self._profile_length,
            n_steps=n_steps,
            n_beads=n_beads,
            profiles_per_bead=self._profiles_per_bead,
        )
        self._dataset.set_state(ZLUTSweepDataset.STATE_CAPTURING)

    def _issue_move_for_current_step(self) -> None:
        if self._current_step_index >= self._steps.size:
            self._complete_session()
            return
        target_z = float(self._steps[self._current_step_index])
        self._phase = 'moving'
        self._step_capture_complete = False
        self.send_ipc(DisarmZLUTSweepCaptureCommand())
        self.send_ipc(SetAcquisitionOnCommand(False))
        self.send_ipc(MoveFocusMotorAbsoluteCommand(z=target_z))
        self._send_state(
            f'Moving focus motor to step {self._current_step_index + 1} of {self._steps.size}.',
            detail=f'Target Z: {target_z:.3f} nm',
            running=True,
            can_cancel=True,
            phase='moving',
        )

    def _advance_when_in_position(self) -> None:
        focus_state = self._latest_focus_state()
        if focus_state is None:
            return
        current_z, target_z, is_moving = focus_state
        requested_z = float(self._steps[self._current_step_index])
        if is_moving:
            return
        if not np.isclose(target_z, requested_z) or not np.isclose(current_z, requested_z):
            return

        self._phase = 'capturing'
        self._step_capture_complete = False
        self.send_ipc(SetAcquisitionOnCommand(True))
        self.send_ipc(
            ArmZLUTSweepCaptureCommand(
                step_index=self._current_step_index,
                motor_z_value=float(current_z),
            )
        )
        self._send_state(
            f'Capturing step {self._current_step_index + 1} of {self._steps.size}.',
            detail=f'Motor position: {current_z:.3f} nm',
            running=True,
            can_cancel=True,
            phase='capturing',
        )

    def _advance_after_capture(self) -> None:
        if not self._step_capture_complete:
            return
        self.send_ipc(SetAcquisitionOnCommand(False))
        self._step_capture_complete = False
        self._current_step_index += 1
        if self._current_step_index >= self._steps.size:
            self._complete_session()
            return
        self._issue_move_for_current_step()

    def _complete_session(self) -> None:
        if self._dataset is None:
            self._fail_session('Z-LUT sweep dataset is unavailable at completion time.')
            return

        self._dataset.set_state(ZLUTSweepDataset.STATE_COMPLETE)
        self.send_ipc(DisarmZLUTSweepCaptureCommand())
        self.send_ipc(SetAcquisitionOnCommand(self._previous_acquisition_on))
        self._active = False
        self._cancel_requested = False
        self._phase = 'processing'
        self._send_progress(force=True)
        self._send_state(
            'Sweep capture complete. Processing generated Z-LUTs...',
            detail='Averaging captured profiles for evaluation.',
            running=True,
            can_cancel=False,
            phase='processing',
        )

        try:
            self._build_generated_zluts()
        except Exception as exc:
            reason = str(exc).strip() or repr(exc)
            self._fail_session(f'Failed to process captured sweep data: {reason}')
            return

        self._phase = 'evaluating'
        self._send_evaluation_state(active=True)
        self._send_state(
            'Review the generated Z-LUT.',
            detail='Select a bead, then save and load the generated Z-LUT or cancel to discard it.',
            running=False,
            can_cancel=False,
            phase='evaluating',
        )

    def _cancel_session(self) -> None:
        self.send_ipc(DisarmZLUTSweepCaptureCommand())
        self.send_ipc(SetAcquisitionOnCommand(self._previous_acquisition_on))
        self._send_state('Z-LUT generation canceled.', running=False, can_cancel=False, phase='idle')
        self._cleanup_runtime_state(destroy_dataset=True)

    def _fail_session(self, reason: str) -> None:
        logger.warning('Z-LUT generation failed: %s', reason)
        self.send_ipc(ShowErrorCommand(text='Z-LUT generation failed', details=reason))
        self.send_ipc(DisarmZLUTSweepCaptureCommand())
        self.send_ipc(SetAcquisitionOnCommand(self._previous_acquisition_on))
        self._send_state('Z-LUT generation failed.', detail=reason, running=False, can_cancel=False, phase='idle')
        self._cleanup_runtime_state(destroy_dataset=True)

    def _fail_evaluation(self, reason: str) -> None:
        logger.warning('Z-LUT evaluation failed: %s', reason)
        self.send_ipc(ShowErrorCommand(text='Generated Z-LUT evaluation failed', details=reason))
        self._send_state(
            'Generated Z-LUT evaluation failed.',
            detail=reason,
            running=False,
            can_cancel=False,
            phase='evaluating',
        )

    def _cleanup_runtime_state(self, *, destroy_dataset: bool) -> None:
        self._active = False
        self._cancel_requested = False
        self._current_step_index = 0
        self._generated_zluts = {}
        self._last_progress_emit = 0.0
        self._phase = 'idle'
        self._profile_length = None
        self._profiles_per_bead = 0
        self._requested_range = None
        self._selected_bead_id = None
        self._step_capture_complete = False
        self._steps = np.zeros((0,), dtype=np.float64)
        self._reset_dataset(destroy=destroy_dataset)
        self._send_evaluation_state(active=False)

    def _reset_dataset(self, *, destroy: bool) -> None:
        if self._dataset is None:
            return
        dataset = self._dataset
        self._dataset = None
        if destroy:
            try:
                dataset.destroy()
            except Exception:
                logger.exception('Failed to destroy Z-LUT sweep dataset')
        else:
            dataset.close()

    def _latest_focus_state(self) -> tuple[float, float, bool] | None:
        if self._focus_buffer is None:
            return None
        data = self._focus_buffer.peak_sorted()
        if data.size == 0:
            return None
        finite_rows = np.isfinite(data[:, 0])
        if not np.any(finite_rows):
            return None
        timestamp, current_z, target_z, is_moving = data[finite_rows][-1, :]
        _ = timestamp
        return float(current_z), float(target_z), bool(round(is_moving))

    def _maybe_send_progress(self) -> None:
        self._send_progress(force=False)

    def _send_progress(self, *, force: bool) -> None:
        now = time()
        if not force and (now - self._last_progress_emit) < 0.1:
            return
        self._last_progress_emit = now
        capture_count = 0
        capture_capacity = 0
        if self._dataset is not None:
            capture_count = self._dataset.get_count()
            capture_capacity = self._dataset.get_capacity()
        motor_z_value = None
        focus_state = self._latest_focus_state()
        if focus_state is not None:
            motor_z_value = focus_state[0]
        display_step = self._current_step_index
        if self._active and self._steps.size > 0:
            display_step = min(self._current_step_index + 1, int(self._steps.size))
        self.send_ipc(
            UpdateZLUTGenerationProgressCommand(
                current_step=display_step,
                total_steps=int(self._steps.size),
                capture_count=capture_count,
                capture_capacity=capture_capacity,
                motor_z_value=motor_z_value,
            )
        )

    def _send_state(
        self,
        status: str,
        *,
        detail: str | None = None,
        running: bool = False,
        can_cancel: bool = False,
        phase: str = 'idle',
    ) -> None:
        self.send_ipc(
            UpdateZLUTGenerationStateCommand(
                status=status,
                detail=detail,
                running=running,
                can_cancel=can_cancel,
                phase=phase,
            )
        )

    def _send_evaluation_state(self, *, active: bool) -> None:
        self.send_ipc(
            UpdateZLUTGenerationEvaluationCommand(
                active=active,
                bead_ids=sorted(self._generated_zluts),
                selected_bead_id=self._selected_bead_id,
            )
        )

    def _build_generated_zluts(self) -> None:
        if self._dataset is None:
            raise RuntimeError('Z-LUT sweep dataset is not available.')

        snapshot = self._dataset.peak()
        valid_rows = snapshot['valid_flags'] != 0
        if not np.any(valid_rows):
            raise RuntimeError('No valid captured sweep profiles are available.')

        bead_ids = snapshot['bead_ids'][valid_rows]
        step_indices = snapshot['step_indices'][valid_rows]
        motor_z_values = snapshot['motor_z_values'][valid_rows]
        profiles = snapshot['profiles'][valid_rows]

        expected_steps = np.arange(int(self._dataset.n_steps), dtype=np.uint32)
        unique_steps = np.unique(step_indices)
        if unique_steps.shape != expected_steps.shape or not np.array_equal(unique_steps, expected_steps):
            raise RuntimeError('Sweep capture is missing one or more step indices.')

        generated: dict[int, GeneratedZLUTResult] = {}
        for bead_id in np.unique(bead_ids):
            bead_mask = bead_ids == bead_id
            bead_step_indices = step_indices[bead_mask]
            bead_profiles = profiles[bead_mask]
            bead_motor_z = motor_z_values[bead_mask]

            averaged_profiles: list[np.ndarray] = []
            z_references: list[float] = []
            for step_index in expected_steps:
                step_mask = bead_step_indices == step_index
                if not np.any(step_mask):
                    raise RuntimeError(
                        f'Bead {int(bead_id)} is missing captured data for step {int(step_index)}.'
                    )
                step_profiles = bead_profiles[step_mask]
                step_motor_z = bead_motor_z[step_mask]
                averaged_profiles.append(np.nanmean(step_profiles, axis=0))
                z_references.append(float(np.nanmean(step_motor_z)))

            averaged_matrix = np.asarray(averaged_profiles, dtype=np.float64)
            zlut_array = np.vstack((np.asarray(z_references, dtype=np.float64), averaged_matrix.T))
            generated[int(bead_id)] = GeneratedZLUTResult(bead_id=int(bead_id), zlut_array=zlut_array)

        if not generated:
            raise RuntimeError('No generated Z-LUT candidates were produced from the sweep dataset.')

        self._generated_zluts = generated
        self._selected_bead_id = min(generated)

    def _discover_focus_motor_name(self) -> str | None:
        focus_motor_names: list[str] = []
        for name, hardware_type in self.hardware_types.items():
            try:
                if issubclass(hardware_type, FocusMotorBase):
                    focus_motor_names.append(name)
            except TypeError:
                continue
        if len(focus_motor_names) == 1:
            return focus_motor_names[0]
        return None

    @staticmethod
    def _build_steps(start_nm: float, step_nm: float, stop_nm: float) -> np.ndarray:
        start = float(start_nm)
        step = float(step_nm)
        stop = float(stop_nm)
        if np.isclose(step, 0.0):
            raise ValueError('Step size must be non-zero.')
        if np.isclose(start, stop):
            return np.asarray([start], dtype=np.float64)

        delta = stop - start
        if np.sign(delta) != np.sign(step):
            raise ValueError('Step size direction must point from start toward stop.')

        intervals = delta / step
        rounded_intervals = int(round(intervals))
        if rounded_intervals < 0 or not np.isclose(start + rounded_intervals * step, stop):
            raise ValueError('Stop position must land exactly on the requested step grid.')

        return start + step * np.arange(rounded_intervals + 1, dtype=np.float64)
