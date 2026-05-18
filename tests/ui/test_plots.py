from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PyQt6")

from magscope.ui import plots as plots_module
from magscope.ui.plots import PlotWorker, TimeSeriesPlotBase, TracksTimeSeriesPlot


class FakeMutex:
    def __init__(self):
        self.lock_calls = 0
        self.unlock_calls = 0

    def lock(self) -> None:
        self.lock_calls += 1

    def unlock(self) -> None:
        self.unlock_calls += 1


class FakeAxisDirection:
    def __init__(self):
        self.inverted: bool | None = None
        self.formatters = []

    def set_inverted(self, value: bool) -> None:
        self.inverted = value

    def set_major_formatter(self, formatter) -> None:
        self.formatters.append(formatter)


class FakeLine:
    def __init__(self):
        self.xdata = None
        self.ydata = None

    def set_xdata(self, xdata) -> None:
        self.xdata = xdata

    def set_ydata(self, ydata) -> None:
        self.ydata = ydata


class FakeAxes:
    def __init__(self):
        self.xaxis = FakeAxisDirection()
        self.yaxis = FakeAxisDirection()
        self.facecolor = None
        self.margin_calls = []
        self.tick_params_calls = []
        self.xlabel = None
        self.ylabel = None
        self.xlim = None
        self.ylim = None
        self.autoscale_calls = 0
        self.autoscale_view_calls = 0
        self.relim_calls = 0
        self.plot_calls = []
        self.line = FakeLine()

    def set_facecolor(self, color) -> None:
        self.facecolor = color

    def margins(self, **kwargs) -> None:
        self.margin_calls.append(kwargs)

    def tick_params(self, **kwargs) -> None:
        self.tick_params_calls.append(kwargs)

    def set_xlabel(self, label: str) -> None:
        self.xlabel = label

    def set_ylabel(self, label: str) -> None:
        self.ylabel = label

    def plot(self, *args, **kwargs):
        self.plot_calls.append((args, kwargs))
        return (self.line,)

    def autoscale(self) -> None:
        self.autoscale_calls += 1

    def autoscale_view(self) -> None:
        self.autoscale_view_calls += 1

    def relim(self) -> None:
        self.relim_calls += 1

    def set_xlim(self, xmin=None, xmax=None) -> None:
        self.xlim = (xmin, xmax)

    def set_ylim(self, ymin=None, ymax=None) -> None:
        self.ylim = (ymin, ymax)


class FakeFigure:
    instances = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.layout_pads = None
        self.subplots_kwargs = None
        self.axes = []
        self.dpi_calls = []
        self.size_calls = []
        self.clear_calls = 0
        FakeFigure.instances.append(self)

    def set_constrained_layout_pads(self, **kwargs) -> None:
        self.layout_pads = kwargs

    def subplots(self, **kwargs):
        self.subplots_kwargs = kwargs
        self.axes = [FakeAxes() for _ in range(kwargs['nrows'])]
        return self.axes

    def set_dpi(self, dpi: float) -> None:
        self.dpi_calls.append(dpi)

    def set_size_inches(self, width: float, height: float) -> None:
        self.size_calls.append((width, height))

    def clear(self) -> None:
        self.clear_calls += 1


class FakeCanvas:
    def __init__(self, figure=None, width: int = 3, height: int = 2):
        self.figure = figure
        self.width = width
        self.height = height
        self.draw_calls = 0

    def draw(self) -> None:
        self.draw_calls += 1

    def get_width_height(self) -> tuple[int, int]:
        return self.width, self.height

    def buffer_rgba(self) -> bytes:
        return bytes(range(self.width * self.height * 4))


class FakeTracksBuffer:
    def __init__(self, data: np.ndarray):
        self.data = data
        self.peak_unsorted_calls = 0

    def peak_unsorted(self):
        self.peak_unsorted_calls += 1
        return self.data


class FakeTeardownCanvas:
    def __init__(self):
        self.calls = []

    def hide(self) -> None:
        self.calls.append('hide')
        raise RuntimeError('already deleted')

    def setParent(self, parent) -> None:
        self.calls.append(('setParent', parent))
        raise RuntimeError('already deleted')

    def close(self) -> None:
        self.calls.append('close')
        raise RuntimeError('already deleted')

    def deleteLater(self) -> None:
        self.calls.append('deleteLater')
        raise RuntimeError('already deleted')


class FakeTeardownFigure:
    def __init__(self):
        self.clear_calls = 0

    def clear(self) -> None:
        self.clear_calls += 1
        raise ValueError('clear failed')


class DummyPlot(TimeSeriesPlotBase):
    def __init__(self, buffer_name: str = 'TracksBuffer', ylabel: str = 'X (nm)'):
        super().__init__(buffer_name, ylabel)
        self.setup_calls = 0
        self.update_calls = 0

    def setup(self):
        self.setup_calls += 1

    def update(self) -> None:
        self.update_calls += 1


def test_plot_worker_setup_creates_figure_and_wires_plots(monkeypatch):
    FakeFigure.instances = []
    monkeypatch.setattr(plots_module, 'Figure', FakeFigure)
    monkeypatch.setattr(plots_module, 'FigureCanvas', FakeCanvas)
    monkeypatch.setattr(plots_module, 'QMutex', FakeMutex)

    worker = PlotWorker()
    worker.plots = [
        DummyPlot(ylabel='X (nm)'),
        DummyPlot(ylabel='Y (nm)'),
        DummyPlot(ylabel='Z (nm)'),
    ]
    worker.set_locks({})

    worker.setup()

    figure = FakeFigure.instances[-1]
    assert worker.n_plots == 3
    assert worker.figure is figure
    assert worker.canvas.figure is figure
    assert worker.mutex.lock_calls == 0
    assert figure.kwargs['figsize'] == (5, 4)
    assert figure.kwargs['dpi'] == 100
    assert figure.kwargs['constrained_layout'] is True
    assert figure.layout_pads == {
        'w_pad': 0.02,
        'h_pad': 0.0,
        'hspace': 0.0,
        'wspace': 0.0,
    }
    assert figure.subplots_kwargs == {
        'nrows': 3,
        'ncols': 1,
        'sharex': True,
        'sharey': False,
    }
    assert [plot.axes for plot in worker.plots] == worker.axes
    assert all(plot.parent is worker for plot in worker.plots)
    assert [plot.setup_calls for plot in worker.plots] == [1, 1, 1]
    assert all(axis.facecolor == plots_module.PANEL_BACKGROUND_COLOR for axis in worker.axes)
    assert all(axis.margin_calls == [{'x': 0}] for axis in worker.axes)
    assert worker.axes[0].tick_params_calls == [
        {'axis': 'x', 'which': 'both', 'bottom': False, 'labelbottom': False}
    ]
    assert worker.axes[1].tick_params_calls == [
        {'axis': 'x', 'which': 'both', 'bottom': False, 'labelbottom': False}
    ]
    assert worker.axes[2].tick_params_calls == []
    assert worker.axes[2].xlabel == 'Time (h:m:s)'
    assert all(axis.xaxis.formatters for axis in worker.axes)


def test_plot_worker_run_stops_after_one_loop(monkeypatch):
    worker = PlotWorker()
    calls = []

    def do_main_loop() -> None:
        calls.append(worker._update_last_time)
        worker._stop()

    monkeypatch.setattr(plots_module, 'time', lambda: 123.0)
    worker.do_main_loop = do_main_loop

    worker.run()

    assert calls == [123.0]
    assert worker._is_running is False


def test_plot_worker_do_main_loop_returns_when_updates_disabled():
    worker = PlotWorker()
    worker.update_on = False


    class ExplodingCanvas:
        def draw(self):
            raise AssertionError('disabled updates should return before drawing')


    worker.canvas = ExplodingCanvas()
    worker.do_main_loop()


def test_plot_worker_do_main_loop_uses_one_tracks_snapshot_and_emits_qimage(qtbot, monkeypatch):
    snapshot = np.asarray([[1.0, 10.0, 0.0, 0.0, 7.0, 0.0, 0.0]], dtype=np.float64)
    canvas = FakeCanvas(width=3, height=2)
    seen_snapshots = []

    class SnapshotTracksPlot(TracksTimeSeriesPlot):
        def update(self) -> None:
            seen_snapshots.append(self.parent._tracks_snapshot)

    class SecondaryPlot:
        def __init__(self, parent):
            self.parent = parent

        def update(self) -> None:
            seen_snapshots.append(self.parent._tracks_snapshot)

    worker = PlotWorker()
    worker._update_last_time = 9.0
    worker.device_pixel_ratio = 1.75
    worker.canvas = canvas
    worker._recreate_figure_if_needed = lambda: seen_snapshots.append('recreated')
    track_plot = SnapshotTracksPlot('X')
    track_plot.parent = worker
    track_plot.buffer = FakeTracksBuffer(snapshot)
    worker.plots = [track_plot, SecondaryPlot(worker)]
    emitted_images = []
    worker.image_signal.connect(emitted_images.append)

    monkeypatch.setattr(plots_module, 'sleep', lambda _seconds: None)
    monkeypatch.setattr(plots_module, 'time', lambda: 10.0)

    worker.do_main_loop()

    assert seen_snapshots == ['recreated', snapshot, snapshot]
    assert track_plot.buffer.peak_unsorted_calls == 1
    assert worker._tracks_snapshot is None
    assert canvas.draw_calls == 1
    assert len(emitted_images) == 1
    assert emitted_images[0].width() == 3
    assert emitted_images[0].height() == 2
    assert emitted_images[0].devicePixelRatio() == pytest.approx(1.75)


def test_plot_worker_dispose_clears_resources_and_ignores_teardown_errors():
    worker = PlotWorker()
    canvas = FakeTeardownCanvas()
    figure = FakeTeardownFigure()
    worker._is_running = True
    worker.canvas = canvas
    worker.figure = figure
    worker.axes = [FakeAxes()]
    worker._tracks_snapshot = object()
    worker.plots = [DummyPlot()]

    worker.dispose()

    assert canvas.calls == ['hide', ('setParent', None), 'close', 'deleteLater']
    assert figure.clear_calls == 1
    assert worker._is_running is False
    assert worker.axes is None
    assert worker.canvas is None
    assert worker.figure is None
    assert worker._tracks_snapshot is None
    assert worker.plots == []


def test_plot_worker_update_figure_size_ignores_nonpositive_dimensions():
    worker = PlotWorker()
    worker.mutex = FakeMutex()
    worker.figure_size_changed = False

    worker._update_figure_size(0, 120, 2.0)
    worker._update_figure_size(120, 0, 2.0)

    assert worker.figure_size_changed is False
    assert worker.mutex.lock_calls == 0


def test_plot_worker_update_figure_size_scales_dimensions_and_clamps_ratio():
    worker = PlotWorker()
    worker.mutex = FakeMutex()
    worker.figure_size_changed = False

    worker._update_figure_size(320, 180, 0.5)

    assert worker.fig_width == pytest.approx(3.2)
    assert worker.fig_height == pytest.approx(1.8)
    assert worker.device_pixel_ratio == 1.0
    assert worker.figure_size_changed is True
    assert worker.mutex.lock_calls == 1
    assert worker.mutex.unlock_calls == 1


def test_plot_worker_recreate_figure_if_needed_updates_figure_once():
    worker = PlotWorker()
    worker.mutex = FakeMutex()
    worker.figure = FakeFigure()
    worker.fig_width = 3.2
    worker.fig_height = 1.8
    worker.dpi = 100
    worker.device_pixel_ratio = 2.0
    worker.figure_size_changed = True

    worker._recreate_figure_if_needed()
    worker._recreate_figure_if_needed()

    assert worker.figure.dpi_calls == [200.0]
    assert worker.figure.size_calls == [(3.2, 1.8)]
    assert worker.figure_size_changed is False
    assert worker.mutex.lock_calls == 2
    assert worker.mutex.unlock_calls == 2


def test_plot_worker_apply_time_axis_format_returns_before_setup():
    worker = PlotWorker()
    worker.axes = None

    worker._apply_time_axis_format()


def test_time_series_plot_base_setup_constructs_matrix_buffer_and_sets_ylabel(monkeypatch):
    created_buffers = []

    class FakeMatrixBuffer:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            created_buffers.append(self)

    class ConcretePlot(TimeSeriesPlotBase):
        def update(self) -> None:
            pass

    monkeypatch.setattr(plots_module, 'MatrixBuffer', FakeMatrixBuffer)
    locks = {'TracksBuffer': object()}
    plot = ConcretePlot('TracksBuffer', 'Position (nm)')
    plot.set_parent(SimpleNamespace(locks=locks))
    axes = FakeAxes()
    plot.set_axes(axes)

    plot.setup()

    assert plot.buffer is created_buffers[0]
    assert created_buffers[0].args == ()
    assert created_buffers[0].kwargs == {
        'create': False,
        'name': 'TracksBuffer',
        'locks': locks,
    }
    assert axes.ylabel == 'Position (nm)'


def test_tracks_time_series_plot_setup_creates_line(monkeypatch):
    class FakeMatrixBuffer:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(plots_module, 'MatrixBuffer', FakeMatrixBuffer)
    plot = TracksTimeSeriesPlot('X')
    axes = FakeAxes()
    plot.set_axes(axes)
    plot.set_parent(SimpleNamespace(locks={}))

    plot.setup()

    assert plot.line is axes.line
    assert axes.ylabel == 'X (nm)'
    assert axes.plot_calls == [(([], [], 'r'), {})]


def make_tracks_plot(axis_name: str, data: np.ndarray, *, selected=7, reference=None, limits=None):
    plot = TracksTimeSeriesPlot(axis_name)
    plot.axes = FakeAxes()
    plot.line = FakeLine()
    plot.buffer = FakeTracksBuffer(data)
    plot.parent = SimpleNamespace(
        selected_bead=selected,
        reference_bead=reference,
        limits={} if limits is None else limits,
        time_mode='absolute',
        relative_window_seconds=300,
        _tracks_snapshot=None,
    )
    return plot


def test_tracks_time_series_plot_treats_reference_minus_one_as_no_reference():
    plot = make_tracks_plot(
        'X',
        np.asarray(
            [
                [1.0, 10.0, 0.0, 0.0, 7.0, 0.0, 0.0],
                [1.0, 99.0, 0.0, 0.0, 8.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        ),
        reference=-1,
    )

    plot.update()

    assert plot.line.xdata == [datetime.fromtimestamp(1.0)]
    np.testing.assert_allclose(plot.line.ydata, np.asarray([10.0]))


def test_tracks_time_series_plot_relative_mode_with_no_selected_data_clears_line():
    plot = make_tracks_plot(
        'X',
        np.asarray([[1.0, 10.0, 0.0, 0.0, 8.0, 0.0, 0.0]], dtype=np.float64),
        selected=7,
    )
    plot.parent.time_mode = 'relative'

    plot.update()

    assert plot.line.xdata == []
    assert plot.line.ydata == []
    assert plot.axes.relim_calls == 1
    assert plot.axes.autoscale_view_calls == 1


def test_tracks_time_series_plot_expands_equal_axis_limits():
    plot = make_tracks_plot(
        'X',
        np.asarray([[1.0, 10.0, 0.0, 0.0, 7.0, 0.0, 0.0]], dtype=np.float64),
        limits={'Time': (1.0, 1.0), 'X (nm)': (10.0, 10.0)},
    )

    plot.update()

    assert plot.axes.xlim == (datetime.fromtimestamp(1.0), datetime.fromtimestamp(2.0))
    assert plot.axes.ylim == (10.0, 11.0)
    assert plot.axes.xaxis.inverted is None
    assert plot.axes.yaxis.inverted is None


def test_tracks_time_series_plot_resets_y_axis_inversion_when_limit_is_open():
    plot = make_tracks_plot(
        'X',
        np.asarray([[1.0, 10.0, 0.0, 0.0, 7.0, 0.0, 0.0]], dtype=np.float64),
        limits={'Time': (1.0, 2.0), 'X (nm)': (None, 20.0)},
    )

    plot.update()

    assert plot.axes.xaxis.inverted is None
    assert plot.axes.yaxis.inverted is False


def test_tracks_time_series_plot_relative_mode_without_window_uses_full_range():
    plot = make_tracks_plot(
        'X',
        np.asarray(
            [
                [4.0, 10.0, 0.0, 0.0, 7.0, 0.0, 0.0],
                [10.0, 20.0, 0.0, 0.0, 7.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        ),
    )
    plot.parent.time_mode = 'relative'
    plot.parent.relative_window_seconds = None

    plot.update()

    np.testing.assert_allclose(plot.line.xdata, np.asarray([0.0, 6.0]))
    assert plot.axes.xlim == (0, None)


def test_tracks_time_series_plot_filters_relative_mode_by_y_limits():
    plot = make_tracks_plot(
        'X',
        np.asarray(
            [
                [4.0, 10.0, 0.0, 0.0, 7.0, 0.0, 0.0],
                [10.0, 20.0, 0.0, 0.0, 7.0, 0.0, 0.0],
                [12.0, 30.0, 0.0, 0.0, 7.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        ),
        limits={'X (nm)': (15.0, 25.0)},
    )
    plot.parent.time_mode = 'relative'
    plot.parent.relative_window_seconds = 10.0

    plot.update()

    np.testing.assert_allclose(plot.line.xdata, np.asarray([8.0]))
    np.testing.assert_allclose(plot.line.ydata, np.asarray([20.0]))
    assert plot.axes.xlim == (0, 10.0)
    assert plot.axes.ylim == (15.0, 25.0)
