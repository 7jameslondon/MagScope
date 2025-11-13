# do not change the order of imports
from magscope.gui.video_viewer import VideoViewer
from magscope.gui.plots import PlotWorker, TimeSeriesPlotBase
from magscope.gui.widgets import (
    CollapsibleGroupBox,
    LabeledCollapsibleGroupBox,
    LabeledLineEditWithValue,
    LabeledLineEdit,
    LabeledCheckbox,
    LabeledStepperLineEdit,
    GripSplitter,
    BeadGraphic,
    ResizableLabel,
)
from magscope.gui.controls import (
    AcquisitionPanel,
    BeadSelectionPanel,
    CameraPanel,
    StatusPanel,
    ControlPanelBase,
    HistogramPanel,
    ScriptPanel,
    PlotSettingsPanel)
from magscope.gui.windows import WindowManager, Controls
