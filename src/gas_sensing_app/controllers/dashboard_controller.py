"""Single owner of run state, GUI actions and worker lifecycle."""

from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFileDialog

from gas_sensing_app.config.config import load_failure_flows
from gas_sensing_app.core.recipe import load_recipe
from gas_sensing_app.core.worker import ExperimentWorker
from gas_sensing_app.gui.styles.theme_manager import load_theme, update_plot_theme
from gas_sensing_app.hardware.factory import HardwareFactory


class DashboardController:
    def __init__(self, window, config):
        self.window, self.config = window, config
        self.worker = None
        self.recipe_path = None
        self.closing = False
        self.console_log = None
        console = config.logging.console_log
        if console.enabled:
            folder = Path(config.base_dir) / console.folder
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
            self.console_log = (folder / f"{console.filename_prefix}_{stamp}.log").open(
                "x", encoding="utf-8"
            )
        window.theme_selector.currentIndexChanged.connect(self.change_theme)
        window.load_recipe_btn.clicked.connect(self.select_recipe)
        window.start_recipe_btn.clicked.connect(lambda: self.start("recipe"))
        window.start_manual_btn.clicked.connect(lambda: self.start("manual"))
        window.apply_manual_btn.clicked.connect(self.apply_manual)
        window.stop_btn.clicked.connect(self.stop)
        window.close_requested.connect(self.close_requested)
        window.destroyed.connect(self.close_log)

    def log(self, text):
        self.window.console_display.appendPlainText(text)
        if self.console_log:
            self.console_log.write(f"{datetime.now(timezone.utc).isoformat()} {text}\n")
            self.console_log.flush()

    def close_log(self):
        if self.console_log:
            self.console_log.close()
            self.console_log = None

    def change_theme(self):
        theme = self.window.theme_selector.currentData()
        self.window.setStyleSheet(load_theme(theme))
        for plot in self.window.plots.plots.values():
            update_plot_theme(plot, theme)

    def select_recipe(self):
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Select recipe", self.config.base_dir, "YAML (*.yaml *.yml)"
        )
        if path:
            self.recipe_path = path
            self.window.recipe_label.setText(path)
            self.window.start_recipe_btn.setEnabled(True)

    def start(self, mode):
        if self.window.running:
            return
        try:
            measurement, gate = self.window.settings()
            load_failure_flows(self.config)
            stages = (
                load_recipe(self.recipe_path, self.config, gate)
                if mode == "recipe"
                else None
            )
            for profile in [s.gate for s in stages] if stages else [gate]:
                HardwareFactory(self.config).validate(profile)
            worker = ExperimentWorker(self.config, measurement, gate, stages)
            if mode == "manual":
                worker.update_manual_setpoints(
                    self.window.shutter_checkbox.isChecked(), self.flow_targets()
                )
        except Exception as exc:
            self.error(str(exc))
            return
        self.worker = worker
        self.window.plots.clear()
        self.window.running = True
        self.window.settings_group.setEnabled(False)
        self.window.gate_group.setEnabled(False)
        self.window.start_manual_btn.setEnabled(False)
        self.window.start_recipe_btn.setEnabled(False)
        self.window.load_recipe_btn.setEnabled(False)
        self.window.stop_btn.setEnabled(True)
        self.window.apply_manual_btn.setEnabled(mode == "manual")
        self.window.control_tabs.setTabEnabled(0, mode == "manual")
        self.window.control_tabs.setTabEnabled(1, mode == "recipe")
        worker.data_sig.connect(self.update_data)
        worker.status_sig.connect(self.status)
        worker.error_sig.connect(self.error)
        worker.finished.connect(self.finished)
        self.status("Starting")
        worker.start()

    def flow_targets(self):
        return {ch: widget.value() for ch, widget in self.window.flow_inputs.items()}

    def apply_manual(self):
        if self.worker and self.window.running:
            try:
                self.worker.update_manual_setpoints(
                    self.window.shutter_checkbox.isChecked(), self.flow_targets()
                )
                self.log("Gas update queued for next complete sweep boundary")
            except Exception as exc:
                self.error(str(exc))

    def update_data(self, data):
        self.window.plots.append(data)

        def fmt(key):
            return "—" if data[key] is None else f"{data[key]:.5g}"

        self.window.readout.setText(
            f"Sensor {data['device']} · R: {fmt('resistance_ohm')} Ω · "
            f"V: {fmt('voltage_V')} V · I: {fmt('current_A')} A\n"
            f"{data['measurement_status']}"
        )

    def status(self, text):
        self.window.status_label.setText(text)
        self.log(text)

    def error(self, text):
        self.window.status_label.setText(f"Error: {text}")
        self.log(f"ERROR: {text}")

    def stop(self):
        if self.worker and self.window.running:
            self.worker.stop()
            self.window.stop_btn.setEnabled(False)
            self.status("Stopping sources and applying failure gas profile…")

    def close_requested(self):
        self.closing = True
        self.stop()

    def finished(self):
        worker = self.worker
        self.window.running = False
        self.window.settings_group.setEnabled(True)
        self.window.gate_group.setEnabled(True)
        self.window.start_manual_btn.setEnabled(True)
        self.window.start_recipe_btn.setEnabled(bool(self.recipe_path))
        self.window.load_recipe_btn.setEnabled(True)
        self.window.stop_btn.setEnabled(False)
        self.window.apply_manual_btn.setEnabled(False)
        for index in (0, 1):
            self.window.control_tabs.setTabEnabled(index, True)
        if worker.engine.log_path:
            self.log(f"CSV: {worker.engine.log_path}")
        if worker.engine.errors:
            self.window.status_label.setText(worker.engine.errors[-1])
        self.worker = None
        worker.deleteLater()
        if self.closing:
            self.close_log()
            QTimer.singleShot(0, self.window.close)
