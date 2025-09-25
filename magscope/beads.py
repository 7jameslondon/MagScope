from math import copysign, isnan
import numpy as np
from time import time

from magscope.processes import ManagerProcessBase

class BeadManager(ManagerProcessBase):
    def __init__(self):
        super().__init__()
        self.auto_center_thresholds: tuple[float, float] = (1.1, 10.0)  # pixels
        self.auto_center_interval: float = 1 # seconds
        self.auto_center_on: bool = False
        self._auto_center_last_time: float = time()

    def setup(self):
        pass

    def do_main_loop(self):
        pass

    def set_auto_center_on(self, value: bool):
        self._auto_center_on = value

    def center(self):
        """ Moves the bead ROIs to stay centered based on the latest position """
        width = self._settings['bead roi width']
        radius = width // 2
        flag = False
        tracks = self._tracks_buffer.peak_unsorted().copy()
        for bead in self.beads:
            tracks_sel = tracks[tracks[:, 4] == bead.id, :]
            if tracks_sel.shape[0] == 0:
                continue

            # Find the latest point from this bead
            ind = np.argmax(tracks_sel[:, 0])
            (t, x, y, roi_x, roi_y) = tracks_sel[ind,
                                                [0, 1, 2, 5, 6]].tolist()

            # Check if this position is recent
            if time() - t > 1.:
                continue

            # Check if bead has a valid position
            if isnan(x) or isnan(y):
                continue

            # Check if the beads have completed the last requested move
            if bead.requested_position is not None:
                if roi_x != bead.requested_position[
                        0] or roi_y != bead.requested_position[1]:
                    continue

            # Check if the bead needs to move in each axis
            nm_per_px = self._camera_type.nm_per_px / self._settings['magnification']
            dx = (x / nm_per_px) - radius - roi_x
            dy = (y / nm_per_px) - radius - roi_y
            if abs(dx) < self.auto_centering_thresholds[0]:
                dx = 0.
            if abs(dy) < self.auto_centering_thresholds[0]:
                dy = 0.
            dx = round(dx)
            dy = round(dy)

            # Limit movement to the upper threshold
            dx = copysign(min(abs(dx), self.auto_centering_thresholds[1]), dx)
            dy = copysign(min(abs(dy), self.auto_centering_thresholds[1]), dy)

            # Move the bead
            if abs(dx) > 0. or abs(dy) > 0.:
                flag = True
                bead.move(dx, dy)

        if flag:
            self.update_bead_rois()

    def _auto_center(self):
        if self._is_auto_centering:
            self.center()