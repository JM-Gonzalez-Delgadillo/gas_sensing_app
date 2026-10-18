import csv
from pathlib import Path

from PyQt6.QtCore import Qt

from gas_sensing_app.config.config import Config
from gas_sensing_app.controllers.dashboard_controller import DashboardController
from gas_sensing_app.gui.dashboard import Dashboard


def test_manual_window_lifecycle_and_linked_plots(qtbot, tmp_path):
    path = tmp_path / "config.yaml"
    template = (
        Path(__file__).parents[1] / "src/gas_sensing_app/assets/config.template.yaml"
    )
    path.write_text(template.read_text())
    (tmp_path / "failure_gases.yaml").write_text("mfc_flows: {1: 10}")
    config = Config(path).load()
    window = Dashboard(config)
    controller = DashboardController(window, config)
    qtbot.addWidget(window)
    window.resize(640, 480)
    window.show()
    qtbot.waitUntil(lambda: window.splitter.orientation() == Qt.Orientation.Vertical)
    assert window.width() == 640
    for key in ("resistance_ohm", "voltage_V", "current_A"):
        assert (
            window.plots.plots[key].getViewBox().linkedView(0)
            is window.plots.plots["flows"].getViewBox()
        )
    window.interval.setValue(0.02)
    controller.start("manual")
    worker = controller.worker
    qtbot.waitUntil(lambda: bool(window.plots.history), timeout=3000)
    window.close()
    assert controller.closing
    qtbot.waitUntil(lambda: controller.worker is None, timeout=3000)
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=3000)
    with open(worker.engine.log_path) as stream:
        rows = list(csv.DictReader(stream))
    assert rows and rows[0]["current_A"]
    assert not worker.engine.meter.enabled
    assert worker.engine.mfc.current_flows[1] == 10
    controller.close_log()
