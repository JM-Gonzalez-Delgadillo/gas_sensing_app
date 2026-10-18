"""Four time-linked plots with bounded live history per sensor."""

import math
from collections import deque

import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSplitter


class MeasurementPlots(QSplitter):
    def __init__(self, gas_names, parent=None):
        super().__init__(Qt.Orientation.Vertical, parent)
        self.gas_names = gas_names
        self.plots = {}
        self.curves = {}
        self.history = {}
        for key, title, unit in (
            ("resistance_ohm", "Resistance", "Ω"),
            ("voltage_V", "Voltage", "V"),
            ("current_A", "Current", "A"),
            ("flows", "Gas flows", "sccm"),
        ):
            plot = pg.PlotWidget()
            plot.setProperty("title", title)
            plot.setTitle(title)
            plot.setLabel("left", title, units=unit)
            plot.getAxis("left").setWidth(85)
            plot.setLabel("bottom", "Elapsed time", units="s")
            plot.showGrid(x=True, y=True, alpha=0.2)
            plot.addLegend(colCount=2, offset=(8, 8), labelTextSize="8pt")
            plot.setMinimumHeight(160)
            self.plots[key] = plot
            self.addWidget(plot)
        for key, plot in self.plots.items():
            if key != "flows":
                plot.setXLink(self.plots["flows"])
        self.setChildrenCollapsible(False)
        self.setSizes([200] * 4)

    def clear(self):
        for plot in self.plots.values():
            plot.clear()
            plot.plotItem.legend.clear()
        self.curves.clear()
        self.history.clear()

    def append(self, data):
        values = [
            (key, data["device"], data[key])
            for key in ("resistance_ohm", "voltage_V", "current_A")
        ]
        values += [
            ("flows", self.gas_names[ch], value) for ch, value in data["flows"].items()
        ]
        if data["gate_setpoint_V"] is not None:
            values += [
                ("voltage_V", "Gate setpoint", data["gate_setpoint_V"]),
                ("voltage_V", "Gate readback", data["gate_voltage_V"]),
            ]
        for key, label, value in values:
            identity = (key, label)
            if identity not in self.curves:
                color = pg.intColor(len(self.curves), hues=16)
                self.curves[identity] = self.plots[key].plot(
                    name=label, pen=pg.mkPen(color, width=1.5), connect="finite"
                )
                count = sum(1 for curve_key, _ in self.curves if curve_key == key)
                self.plots[key].setMinimumHeight(max(160, 95 + ((count + 1) // 2) * 22))
                self.history[identity] = (deque(maxlen=10000), deque(maxlen=10000))
            times, samples = self.history[identity]
            times.append(data["elapsed_s"])
            samples.append(value if value is not None else math.nan)
            self.curves[identity].setData(list(times), list(samples))
