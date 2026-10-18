"""Responsive view. All experiment actions belong to DashboardController."""

from dataclasses import replace

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gas_sensing_app.gui.styles.theme_manager import DEFAULT_THEME, Theme, load_theme

from .widgets.plots import MeasurementPlots


def number(value, minimum, maximum, decimals=4):
    widget = QDoubleSpinBox()
    widget.setDecimals(decimals)
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    return widget


class Dashboard(QMainWindow):
    close_requested = pyqtSignal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.running = False
        self.setWindowTitle("Gas Sensing — acquisition and gate control")
        self.setStyleSheet(load_theme(DEFAULT_THEME))
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1400, screen.width()), min(950, screen.height()))
        self.status_label = QLabel(
            "Ready · Mock" if config.hardware.use_mock else "Ready · Real instruments"
        )
        self.status_label.setWordWrap(True)
        self.readout = QLabel("R: — Ω    V: — V    I: — A")
        self.readout.setWordWrap(True)
        self.stop_btn = QPushButton("STOP — apply failure gas profile")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setObjectName("stopButton")
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.addWidget(self.status_label)
        outer.addWidget(self.readout)
        outer.addWidget(self.stop_btn)
        self.splitter = QSplitter()
        outer.addWidget(self.splitter, 1)
        self.setCentralWidget(root)
        controls = QWidget()
        layout = QVBoxLayout(controls)
        self.controls_scroll = QScrollArea()
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setWidget(controls)
        self.splitter.addWidget(self.controls_scroll)
        self.settings_group = QGroupBox("Measurement")
        form = QFormLayout(self.settings_group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.instrument = QComboBox()
        self.instrument.addItems(["2400", "2700"])
        self.instrument.setCurrentText(config.measurement.instrument)
        self.measurement_mode = QComboBox()
        self.wires = QComboBox()
        self.wires.addItems(["2", "4"])
        self.wires.setCurrentText(str(config.measurement.wire_mode))
        self.slot = QSpinBox()
        self.slot.setRange(1, 2)
        self.slot.setValue(config.measurement.slot)
        self.first_channel, self.last_channel = QSpinBox(), QSpinBox()
        for control, value in (
            (self.first_channel, config.measurement.first_channel),
            (self.last_channel, config.measurement.last_channel),
        ):
            control.setRange(1, 20)
            control.setValue(value)
        self.bias = number(config.measurement.bias_voltage, -200, 200)
        self.compliance = number(config.measurement.compliance, 0.000000001, 1.05, 9)
        self.interval = number(config.measurement.interval_s, 0.01, 86400, 2)
        for label, widget in (
            ("Instrument", self.instrument),
            ("Function", self.measurement_mode),
            ("Terminals", self.wires),
            ("Card slot", self.slot),
            ("First channel", self.first_channel),
            ("Last channel", self.last_channel),
            ("Drain bias (V)", self.bias),
            ("Current limit (A)", self.compliance),
            ("Sample interval (s)", self.interval),
        ):
            form.addRow(label, widget)
        self.capability_note = QLabel()
        self.capability_note.setWordWrap(True)
        form.addRow(self.capability_note)
        settings_tabs = QTabWidget()
        settings_tabs.addTab(self.settings_group, "Measurement")
        self.instrument.currentTextChanged.connect(self.instrument_changed)
        self.wires.currentTextChanged.connect(self.topology_changed)
        self.measurement_mode.currentIndexChanged.connect(self.topology_changed)
        self.instrument_changed()
        index = self.measurement_mode.findData(config.measurement.mode)
        self.measurement_mode.setCurrentIndex(max(0, index))
        self.gate_group = QGroupBox("EA-PS gate")
        gate_form = QFormLayout(self.gate_group)
        gate_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.gate_enabled = QCheckBox("Enable gate")
        self.gate_enabled.setChecked(config.gate.enabled)
        self.gate_mode = QComboBox()
        self.gate_mode.addItems(["fixed", "sweep"])
        self.gate_mode.setCurrentText(config.gate.mode)
        gate_form.addRow(self.gate_enabled)
        gate_form.addRow("Mode", self.gate_mode)
        self.gate_inputs = {}
        for key, label, low, high, decimals in (
            ("voltage", "Fixed gate (V)", 0, config.gate.max_voltage, 4),
            ("start", "Sweep start (V)", 0, config.gate.max_voltage, 4),
            ("stop", "Sweep end (V)", 0, config.gate.max_voltage, 4),
            ("step", "Sweep step (V)", 0.0001, 65, 4),
            ("dwell_s", "Settling per point (s)", 0.01, 86400, 2),
            ("current_limit", "Gate current limit (A)", 0.000000001, 3, 9),
        ):
            widget = number(getattr(config.gate, key), low, high, decimals)
            self.gate_inputs[key] = widget
            gate_form.addRow(label, widget)
        note = QLabel(
            "Gas flows stay fixed for each complete sweep. "
            "Real EA communication awaits hardware integration."
        )
        note.setWordWrap(True)
        gate_form.addRow(note)
        settings_tabs.addTab(self.gate_group, "Gate")
        layout.addWidget(settings_tabs)
        self.control_tabs = QTabWidget()
        manual = QWidget()
        manual_layout = QVBoxLayout(manual)
        gas_form = QFormLayout()
        self.flow_inputs = {}
        for channel, maximum in config.hardware.mfc_controller.range_sccm.items():
            self.flow_inputs[channel] = number(0, 0, maximum, 3)
            gas_form.addRow(
                f"{config.hardware.mfc_controller.gas_names[channel]} (sccm)",
                self.flow_inputs[channel],
            )
        manual_layout.addLayout(gas_form)
        self.shutter_checkbox = QCheckBox("Open shutter")
        self.shutter_checkbox.setEnabled(config.hardware.shutter.enabled)
        manual_layout.addWidget(self.shutter_checkbox)
        self.start_manual_btn = QPushButton("Start manual session")
        self.apply_manual_btn = QPushButton("Apply gases at next sweep boundary")
        self.apply_manual_btn.setEnabled(False)
        manual_layout.addWidget(self.start_manual_btn)
        manual_layout.addWidget(self.apply_manual_btn)
        recipe = QWidget()
        recipe_layout = QVBoxLayout(recipe)
        self.load_recipe_btn = QPushButton("Load recipe")
        self.recipe_label = QLabel("No recipe selected")
        self.recipe_label.setWordWrap(True)
        self.start_recipe_btn = QPushButton("Run recipe")
        self.start_recipe_btn.setEnabled(False)
        for widget in (self.load_recipe_btn, self.recipe_label, self.start_recipe_btn):
            recipe_layout.addWidget(widget)
        self.control_tabs.addTab(manual, "Manual")
        self.control_tabs.addTab(recipe, "Recipe")
        layout.addWidget(self.control_tabs)
        self.theme_selector = QComboBox()
        for theme in Theme:
            self.theme_selector.addItem(theme.value, theme)
        layout.addWidget(self.theme_selector)
        self.console_display = QPlainTextEdit()
        self.console_display.setReadOnly(True)
        self.console_display.setMaximumBlockCount(1000)
        self.console_display.setMaximumHeight(150)
        layout.addWidget(self.console_display)
        layout.addStretch()
        self.plots = MeasurementPlots(config.hardware.mfc_controller.gas_names)
        self.plot_scroll = QScrollArea()
        self.plot_scroll.setWidgetResizable(True)
        self.plot_scroll.setWidget(self.plots)
        self.splitter.addWidget(self.plot_scroll)
        self.splitter.setSizes([400, 1000])
        self.splitter.setChildrenCollapsible(False)

    def instrument_changed(self):
        self.measurement_mode.clear()
        self.measurement_mode.addItem("Resistance", "resistance")
        if self.instrument.currentText() == "2400":
            self.measurement_mode.addItem("Constant drain voltage", "constant_voltage")
        else:
            self.measurement_mode.addItem("Voltage", "voltage")
        self.topology_changed()

    def topology_changed(self):
        mux = self.instrument.currentText() == "2700"
        four = self.wires.currentText() == "4"
        for control in (self.slot, self.first_channel, self.last_channel):
            control.setEnabled(mux)
        for control in (self.first_channel, self.last_channel):
            control.setMaximum(10 if four else 20)
        bias = not mux and self.measurement_mode.currentData() == "constant_voltage"
        self.bias.setEnabled(bias)
        self.compliance.setEnabled(bias)
        self.capability_note.setText(
            "7706: current unavailable. Four terminals pair channel n with n+10. "
            "Voltage uses two terminals."
            if mux
            else "2400: V, I and R read from the instrument; gate is a separate supply."
        )

    def settings(self):
        measurement = replace(
            self.config.measurement,
            instrument=self.instrument.currentText(),
            mode=self.measurement_mode.currentData(),
            wire_mode=int(self.wires.currentText()),
            slot=self.slot.value(),
            first_channel=self.first_channel.value(),
            last_channel=self.last_channel.value(),
            bias_voltage=self.bias.value(),
            compliance=self.compliance.value(),
            interval_s=self.interval.value(),
        )
        gate = replace(
            self.config.gate,
            enabled=self.gate_enabled.isChecked(),
            mode=self.gate_mode.currentText(),
            **{key: widget.value() for key, widget in self.gate_inputs.items()},
        )
        measurement.validate()
        gate.points()
        return measurement, gate

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "splitter"):
            orientation = (
                Qt.Orientation.Vertical
                if self.width() < 900
                else Qt.Orientation.Horizontal
            )
            if self.splitter.orientation() != orientation:
                self.splitter.setOrientation(orientation)
                self.splitter.setSizes([350, 600])

    def closeEvent(self, event):
        if self.running:
            event.ignore()
            self.close_requested.emit()
        else:
            event.accept()
