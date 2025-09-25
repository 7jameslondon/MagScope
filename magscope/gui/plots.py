from __future__ import annotations

from abc import abstractmethod
from datetime import datetime, timedelta
import time
import numpy as np
from PyQt6.QtWidgets import *
import pyqtgraph as pg
from typing import TYPE_CHECKING

from magscope.datatypes import MatrixBuffer

if TYPE_CHECKING:
    from magscope import WindowManager

pg.setConfigOption('background', '#1e1e1e')

class Plots:
    def __init__(self, manager):
        """ Called before the parent process is started """
        super().__init__()
        self.manager: WindowManager = manager
        self.layout: pg.GraphicsLayoutWidget
        self.plots: list[TimeSeriesPlotBase] = []

        # Add plots for bead tracks
        self.plots.append(TracksTimeSeriesPlot('X'))
        self.plots.append(TracksTimeSeriesPlot('Y'))
        self.plots.append(TracksTimeSeriesPlot('Z'))

    def setup(self):
        """ Called after the parent process is started """
        # Layout
        self.layout = pg.GraphicsLayoutWidget()
        self.layout.setEnabled(False)

        # Add plots to layout
        for plot in self.plots:
            plot_item = self.layout.addPlot()
            self.layout.nextRow()
            plot._set_plot_item(plot_item)

        # Set manager for each plot
        for plot in self.plots:
            plot._set_manager(self.manager)

        # Setup each plot
        for plot in self.plots:
            plot.setup()

    def get_ui(self):
        """ Returns the central widget for the GUI"""
        return self.layout

    def add_plot(self, plot: TimeSeriesPlotBase):
        """ Used to add plots before the process has started """
        if self.manager._running:
            raise Exception('Cannot add plots after the program has started')
        self.plots.append(plot)

    def update(self):
        """ Called by the parent process on a regular timer """
        for plot in self.plots:
            plot.update()


class TimeSeriesPlotBase:
    def __init__(self, buffer_name: str):
        self.plot_item: pg.PlotItem
        self.plot_data_item: pg.PlotDataItem
        self.buffer: MatrixBuffer
        self.buffer_name = buffer_name
        self.manager: WindowManager

    def setup(self):
        """ Called after the parent process is started """

        # Buffer
        self.buffer = MatrixBuffer(
            create=False,
            name='TracksBuffer',
            locks=self.manager.locks
        )

        # Plot
        self.plot_data_item = self.plot_item.plot()
        self.plot_data_item.setDownsampling(ds=None, auto=True, method='peak')

    def _set_manager(self, manager: WindowManager):
        self.manager = manager

    def _set_plot_item(self, plot_item: pg.PlotItem):
        self.plot_item = plot_item

    @abstractmethod
    def update(self):
        pass

class TracksTimeSeriesPlot(TimeSeriesPlotBase):
    def __init__(self, axis: str):
        super().__init__('TracksBuffer')
        self.axis = axis
        self.axis_index = ['X', 'Y', 'Z'].index(axis) + 1

    def update(self):
        # Get data from buffer
        data = self.buffer.peak_unsorted()
        t = data[:, 0]
        v = data[:, self.axis_index]

        # Remove nan/inf
        selection = np.isfinite(t)
        t = t[selection]
        v = v[selection]

        # Update the plot
        self.plot_data_item.setData(t, v)

class MyMotorTimeSeriesPlot:
    pass


# =========================================== #
# =========================================== #
# =========================================== #

class Plots2(QWidget):

    def __init__(self):
        super().__init__()

        # Layout
        self.setLayout(QGridLayout())
        self.plot_area = pg.GraphicsLayoutWidget()
        self.layout().addWidget(self.plot_area, 0, 0, 1, 2)

        # Plots
        self._plots = {}
        self._plots['X'] = TimeSeriesPlot(parent=self,
                                          row=len(self._plots),
                                          y_label='X (nm)',
                                          n_lines=1,
                                          bead=True)
        self._plots['Y'] = TimeSeriesPlot(parent=self,
                                          row=len(self._plots),
                                          y_label='Y (nm)',
                                          n_lines=1,
                                          bead=True)
        self._plots['Z'] = TimeSeriesPlot(parent=self,
                                          row=len(self._plots),
                                          y_label='Z (nm)',
                                          n_lines=1,
                                          bead=True)
        self.n_plots = len(self._plots)

        # Find bottom-most plot
        self.bottom_plot = None
        for plot in self._plots.values():
            if plot.row == self.n_plots - 1:
                self.bottom_plot = plot
        assert self.bottom_plot

        # Link axes
        for plot in self._plots.values():
            if plot.row < self.n_plots - 1:
                plot.plot.setXLink(self.bottom_plot.plot)

        # Set bottom plot x-label
        self.bottom_plot.plot.setLabel('bottom', 'Time (H:M:S)')
        self.bottom_plot.plot.getAxis('bottom').setStyle(showValues=True)

    def update_plots(self):
        # Beads - Get data
        new_data = self.app.txyzb_buf.read()
        self._plots['X'].plot_buf.write(new_data[:, [0, 1, 4]])
        self._plots['Y'].plot_buf.write(new_data[:, [0, 2, 4]])
        self._plots['Z'].plot_buf.write(new_data[:, [0, 3, 4]])

        # Motors - Get data
        for m, b in zip(['Rotary Motor', 'Objective Motor'],
                        [self.app.rot_mot_buf, self.app.obj_mot_buf]):
            new_data = b.read()
            self._plots[m].plot_buf.write(new_data[:, 0:3])

        # Linear Motor and Force - Get data
        new_data = self.app.lin_mot_buf.read()
        self._plots['Linear Motor'].plot_buf.write(new_data[:, 0:3])
        self._plots['Force'].plot_buf.write(new_data[:, 0:3])

        # Update all plots
        for m in self._plots.keys():
            self._plots[m].update_plot()


class TimeSeriesPlot:

    def __init__(self,
                 *,
                 parent,
                 row,
                 y_label,
                 n_lines,
                 text_decimals=0,
                 force_converter=None,
                 bead=False):
        self._parent = parent
        self.row = row
        self.n_lines = n_lines
        self.text_decimals = text_decimals
        self._force_converter = force_converter
        self._bead = bead

        self.plot = self._parent.plot_area.addPlot(
            row=row,
            col=0,
            size=1,
            width=1,
            antialias=False,
            clipToView=True,
            connect='finite',
            axisItems={
                'left':
                pg.AxisItem('left',
                            siPrefixEnableRanges=((None, None), (None, None))),
                'bottom':
                CustomDateAxisItem()
            })
        self.plot.getAxis('bottom').setStyle(showValues=False)
        self.plot.setLabel('left', y_label)

        if n_lines == 2:
            self.line_g = self.plot.plot(pen=pg.mkPen(color='g'))
        self.line_r = self.plot.plot(pen=pg.mkPen(color='r'))

        self.text_item = pg.LabelItem(color='r')
        self.text_item.setParentItem(self.plot)
        self.text_item.anchor(itemPos=(1, 0),
                              parentPos=(1, 0),
                              offset=(-10, 10))

    def update_plot(self):
        # Get data
        n = self._parent.app.control_window.plot_settings_panel.max_datapoints.lineedit.text(
        )
        if n == '':
            n = None
        else:
            n = int(n)
        data = self.plot_buf.read(n)

        # Max duration
        t = self._parent.app.control_window.plot_settings_panel.max_duration.lineedit.text(
        )
        if t != '':
            t = time.time() - int(t)
            data = data[data[:, 0] >= t, :]

        # Bead plots
        if self._bead:
            b = self._parent.app.control_window.plot_settings_panel.selected_bead.lineedit.text(
            )
            r = self._parent.app.control_window.plot_settings_panel.reference_bead.lineedit.text(
            )
            b = None if b == '' else int(b)
            r = None if r == '' else int(r)

            data_b = data[b == data[:, 2], 0:2]
            if r is None:
                data = data_b
            else:
                data_r = data[r == data[:, 2], 0:2]
                _, ind_b, ind_r = np.intersect1d(data_b[:, 0],
                                                 data_r[:, 0],
                                                 assume_unique=True,
                                                 return_indices=True)
                data = np.hstack(
                    (data_b[ind_b,
                            0:1], data_b[ind_b, 1:2] - data_r[ind_r, 1:2]))

        # Force plot
        if self._force_converter is not None:
            if self._force_converter.is_loaded():
                for i in range(1, data.shape[1]):
                    data[:, i] = self._force_converter.motor2force(data[:, i])
            else:
                data[:, 1:] = data[:, 1:] * np.nan

        # Convert absolute time to relative time
        if self._parent.app.control_window.plot_settings_panel.relative_time.checkbox.isChecked(
        ):
            t = time.time()
            data[:, 0] -= t

        # A nan value in the first position causes a memory leak.
        # So the leading nan values are removed. Other nan values are okay.
        # Just not in the first position.
        if self.n_lines == 1:
            while data.shape[0] > 0 and np.isnan(data[0, 1]):
                data = data[1:, :]
        else:
            while data.shape[0] > 0 and (np.isnan(data[0, 1])
                                         or np.isnan(data[0, 2])):
                data = data[1:, :]

        # Update lines
        self.line_r.setData(data[:, 0], data[:, 1])
        if self.n_lines == 2:
            self.line_g.setData(data[:, 0], data[:, 2])

        # Update text
        if data.shape[0] > 0:
            last_value = data[-1, 1]
            if np.isfinite(last_value):
                if self.text_decimals == 0:
                    last_value = int(last_value)
                else:
                    last_value = round(last_value, self.text_decimals)
            else:
                last_value = np.nan
        else:
            last_value = np.nan
        self.text_item.setText(str(last_value))


class CustomDateAxisItem(pg.DateAxisItem):

    def tickStrings(self, values, scale, spacing):
        strings = []
        for value in values:
            if value > 0:
                dt = datetime.fromtimestamp(value)
                strings.append(dt.strftime('%I:%M:%S'))
            else:
                h = int(-value // 3600)
                m = int((-value % 3600) // 60)
                s = int(-value % 60)
                strings.append(f'-{h}:{m:02d}:{s:02d}')
        return strings
