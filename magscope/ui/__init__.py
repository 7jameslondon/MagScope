# do not change the order of imports
from magscope.ui.video_viewer import VideoViewer
from magscope.ui.plots import PlotWorker, TimeSeriesPlotBase
from magscope.ui.widgets import (BeadGraphic, CollapsibleGroupBox, GripSplitter, LabeledCheckbox,
                                 LabeledLineEdit, LabeledLineEditWithValue, LabeledStepperLineEdit,
                                 ResizableLabel)
from magscope.ui.controls import (AcquisitionPanel, BeadSelectionPanel, CameraPanel,
                                  ControlPanelBase, HistogramPanel, PlotSettingsPanel, ScriptPanel,
                                  StatusPanel, MagScopeSettingsPanel)
from magscope.ui.ui import Controls, UIManager
