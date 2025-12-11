from __future__ import annotations

from abc import ABCMeta, abstractmethod
from datetime import datetime
from time import sleep, time
from typing import TYPE_CHECKING

import matplotlib
import matplotlib.dates as mdates
import matplotlib.style as mplstyle
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt6.QtCore import QMutex, QObject, pyqtSignal
from PyQt6.QtGui import QImage

from magscope.datatypes import MatrixBuffer

if TYPE_CHECKING:
    from multiprocessing.synchronize import Lock as LockType

matplotlib.use('QtAgg')
mplstyle.use('dark_background')
mplstyle.use('fast')


class PlotWorker(QObject):
    image_signal = pyqtSignal(QImage)
    limits_signal = pyqtSignal(object)
    selected_bead_signal = pyqtSignal(int)
    reference_bead_signal = pyqtSignal(int)
    stop_signal = pyqtSignal()
    figure_size_signal = pyqtSignal(int, int)

    def __init__(self):
        """ Called before the parent process is started """
        super().__init__()
        self.axes: matplotlib.axes.Axes
        self.locks: dict[str, LockType]
        self.figure: Figure | None = None
        self.canvas: FigureCanvas
        self._is_running: bool = False
        self.plots = []
        self.limits: dict[str, tuple[float, float]] = {}
        self.selected_bead: int | None = 0
        self.reference_bead: int | None = None
        self.n_plots: int

        self.update_on: bool = True
        self._update_last_time: float

        self.fig_width = 5
        self.fig_height = 4
        self.dpi = 100

        # Connect internal signal to slot
        self.limits_signal.connect(self._set_limits)
        self.selected_bead_signal.connect(self._set_selected_bead)
        self.reference_bead_signal.connect(self._set_reference_bead)
        self.stop_signal.connect(self._stop)
        self.figure_size_signal.connect(self._update_figure_size)

        # Thread safety
        self.mutex: QMutex
        self.figure_size_changed = True

        # Add plots for bead tracks
        self.add_plot(TracksTimeSeriesPlot('X'))
        self.add_plot(TracksTimeSeriesPlot('Y'))
        self.add_plot(TracksTimeSeriesPlot('Z'))

    def setup(self):
        self.n_plots = len(self.plots)
        self.mutex = QMutex()

        # Create figure and axes
        self.figure = Figure(figsize=(self.fig_width, self.fig_height), dpi=self.dpi, facecolor='#1e1e1e')
        self.canvas = FigureCanvas(self.figure)
        self.axes = self.figure.subplots(nrows=self.n_plots, ncols=1, sharex=True, sharey=False)

        # Formating to make it look good
        self.figure.tight_layout()
        self.figure.subplots_adjust(hspace=0.08)
        for ax in self.axes:
            ax.set_facecolor('#1e1e1e')  # Set background color
            ax.margins(x=0)  # Set margins
        self.axes[-1].set_xlabel('Time (h:m:s)')
        self.axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))

        # Pass complex objects to each plot (self, axes, ect)
        for plot, ax in zip(self.plots, self.axes):
            plot.set_axes(ax)

        for plot in self.plots:
            plot.set_parent(self)

        for plot in self.plots:
            plot.setup()

    def run(self):
        self._is_running = True
        self._update_last_time = time()
        while self._is_running:
            self.do_main_loop()

    def do_main_loop(self):
        # Is plotting enabled?
        if not self.update_on:
            return

        # Wait for timer
        duration = time() - self._update_last_time
        sleep(10*duration)
        self._update_last_time = time()

        # Check if we need to recreate the figure
        self._recreate_figure_if_needed()

        # Update plots
        for plot in self.plots:
            plot.update()

        # Render figure to buffer
        self.canvas.draw()
        w, h = self.canvas.get_width_height()
        buf = np.frombuffer(self.canvas.buffer_rgba(), dtype=np.uint8).reshape((h, w, 4))

        # Convert numpy RGBA -> QImage
        img = QImage(buf.data, w, h, QImage.Format.Format_RGBA8888)

        # Emit figure as a buffer to the main GUI
        self.image_signal.emit(img.copy())

    def add_plot(self, plot: TimeSeriesPlotBase):
        """ Used to add plots before the process has started """
        self.plots.append(plot)

    def _set_limits(self, limits: dict[str, list[float, float]]):
        self.limits = limits

    def _set_selected_bead(self, bead: int):
        self.selected_bead = bead

    def _set_reference_bead(self, bead: int | None):
        self.reference_bead = bead

    def set_locks(self, locks: dict[str, LockType]):
        self.locks = locks

    def _stop(self):
        self._is_running = False

    def _update_figure_size(self, width: int, height: int):
        """Slot: update figure size based on QLabel dimensions."""
        if width > 0 and height > 0:
            self.mutex.lock()
            try:
                # Convert pixels to inches
                self.fig_width = max(1, width / self.dpi)
                self.fig_height = max(1, height / self.dpi)
                self.figure_size_changed = True
            finally:
                self.mutex.unlock()

    def _recreate_figure_if_needed(self):
        """Recreate figure and canvas if size changed."""
        self.mutex.lock()
        if self.figure_size_changed:
            self.figure.set_size_inches(self.fig_width, self.fig_height)
            self.figure_size_changed  = False
        self.mutex.unlock()


class TimeSeriesPlotBase(metaclass=ABCMeta):
    def __init__(self, buffer_name: str, ylabel: str):
        self.buffer: MatrixBuffer
        self.buffer_name = buffer_name
        self.parent: PlotWorker
        self.axes: matplotlib.axes.Axes
        self.ylabel = ylabel

    def setup(self):
        """ Called after the parent process is started """

        # Buffer
        self.buffer = MatrixBuffer(
            create=False,
            name=self.buffer_name,
            locks=self.parent.locks
        )

        # Format plot
        self.axes.set_ylabel(self.ylabel)

    def set_parent(self, parent: PlotWorker):
        self.parent = parent

    def set_axes(self, axes: matplotlib.axes.Axes):
        self.axes = axes

    @abstractmethod
    def update(self): pass


class TracksTimeSeriesPlot(TimeSeriesPlotBase):
    def __init__(self, axis_name: str):
        super().__init__('TracksBuffer', ylabel=axis_name+' (nm)')
        self.axis_name = axis_name
        self.axis_index = ['X', 'Y', 'Z'].index(axis_name) + 1
        self.line: matplotlib.lines.Line2D

    def setup(self):
        super().setup()
        self.line, = self.axes.plot([], [], 'r')

    def update(self):
        # Get selected and reference bead
        sel = self.parent.selected_bead
        ref = self.parent.reference_bead
        if ref == -1:
            ref = None

        # Get data from buffer
        data = self.buffer.peak_unsorted()
        t = data[:, 0]
        b = data[:, 4]
        v = data[:, self.axis_index]

        # Get selected bead values
        selection = b == sel
        t_sel = t[selection]
        v_sel = v[selection]

        # Subtract reference bead values
        if ref is None:
            t = t_sel
            v = v_sel
        else:
            # Get reference bead values
            selection = b == ref
            t_ref = t[selection]
            v_ref = v[selection]

            # Get values where selected bead and reference bead share the same timepoints
            t, index_sel, index_ref = np.intersect1d(t_sel, t_ref, assume_unique=True, return_indices=True)
            v = v_sel[index_sel] - v_ref[index_ref]

            # Correct for ZLUT upsidedown order
            if self.axis_name == 'Z':
                v *= -1

        # Remove nan/inf
        selection = np.isfinite(t)
        t = t[selection]
        v = v[selection]

        # Remove value outside of axis limits
        xmin = self.parent.limits.get('Time', (None, None))[0]
        xmax = self.parent.limits.get('Time', (None, None))[1]
        ymin = self.parent.limits.get(self.ylabel, (None, None))[0]
        ymax = self.parent.limits.get(self.ylabel, (None, None))[1]
        selection = ((xmin or -np.inf) <= t) & (t <= (xmax or np.inf)) & ((ymin or -np.inf) <= v) & (v <= (ymax or np.inf))
        t = t[selection]
        v = v[selection]

        # Convert time to timepoints
        t = [datetime.fromtimestamp(t_) for t_ in t]

        # Update the plot data
        self.line.set_xdata(t)
        self.line.set_ydata(v)

        # Prevent equal limits error
        if xmin is not None and xmin==xmax:
            xmax += 1
        if ymin is not None and ymin==ymax:
            ymax += 1

        # Prevent unintended axis inversion
        if xmin is None or xmax is None:
            self.axes.xaxis.set_inverted(False)
        if ymin is None or ymax is None:
            self.axes.yaxis.set_inverted(False)

        # Convert the x-limits to timestamps
        xmin, xmax = [datetime.fromtimestamp(t_) if t_ else None for t_ in (xmin, xmax)]

        # Update the axis limits
        self.axes.autoscale()
        self.axes.autoscale_view()
        self.axes.set_xlim(xmin=xmin, xmax=xmax)
        self.axes.set_ylim(ymin=ymin, ymax=ymax)
        self.axes.relim()