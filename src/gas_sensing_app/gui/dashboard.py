# src/gas_sensing_app/gui/dashboard.py

import sys
import os
import time
import yaml
import queue

import shutil
from pathlib import Path

# Qt imports
from PyQt6.QtWidgets import (QMainWindow, 
                             QWidget, 
                             QVBoxLayout,
                             QHBoxLayout, 
                             QLabel, 
                             QPushButton, 
                             QTabWidget,
                             QTextEdit, 
                             QFileDialog, 
                             QSpinBox,
                             QDoubleSpinBox, 
                             QCheckBox, 
                             QGroupBox, 
                             QGridLayout, 
                             QFrame, 
                             QSplitter,
                             QComboBox)

from PyQt6.QtGui import QFontDatabase, QIcon
from PyQt6.QtCore import Qt, QTimer
import pyqtgraph as pg

# Core imports
from gas_sensing_app.core.logger import WriteStream
from gas_sensing_app.core.worker import ExperimentWorker

# Style imports
from gas_sensing_app.gui.styles import (load_theme,
                                        update_plot_theme,
                                        DEFAULT_THEME,
                                        Theme)

ASSETS_DIR = Path(__file__).parent.parent / "assets"

# ==============================================================================
# MAIN GUI WINDOW
# ==============================================================================
class GasSensingDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gas Sensing Dashboard")
        self.resize(1400, 850)
        
        # Resolve path to the assets directory cleanly
        icon_path = ASSETS_DIR / "icon.png"
        
        # Load and apply the stylesheet dynamically
        self.setStyleSheet(load_theme(DEFAULT_THEME))

        # Load the window icon properly
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(str(icon_path)))

        self.system_mono = QFontDatabase.systemFont(
                                QFontDatabase.SystemFont.FixedFont
                            )
        
        self.config_path = "config.yaml" 
        self.recipe_path = None
        self.log_file_obj = None  
        self.data_history = {"time": [], "resistance": [], "flows": [[], [], [], []], "shutter": []}
        self.console_queue = queue.Queue()

        self._create_dummy_files()
        self._setup_print_logging()

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # LEFT PANEL
        left_panel = QVBoxLayout()
        left_frame = QFrame()
        left_frame.setFrameShape(QFrame.Shape.StyledPanel)
        left_frame.setLayout(left_panel)
        
        theme_selector_layout = QVBoxLayout()
        theme_selector_layout.addWidget(QLabel("Change the theme:"))
        
        self.theme_selector = QComboBox()
        for theme in Theme:
            self.theme_selector.addItem(theme.value.capitalize(), theme)
        self.theme_selector.setObjectName("themeSelectorComboBox")
        self.theme_selector.currentIndexChanged.connect(
            self.change_theme
        )
        theme_selector_layout.addWidget(self.theme_selector)

# REPLACED OLD LIGHT STYLES WITH HIGH-CONTRAST LABELS:
        self.status_label = QLabel("Status: Idle / Ready") 
        self.status_label.setObjectName("statusLabel")            
        self.status_label.setProperty("textColor", "primary")
        self.status_label.setProperty("fontWeight", "normal")
        self.refresh_style(self.status_label)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter) 

        res_indicator_layout = QVBoxLayout()
        res_indicator_layout.addWidget(QLabel("Current Resistance:"))

        self.res_display = QLabel("--- Ω") 
        self.res_display.setObjectName("resistanceLabel")
        self.res_display.setFont(self.system_mono)
        self.res_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        res_indicator_layout.addWidget(self.res_display)

        # ==============================================================================
        # --- KEITHLEY 2400 SMU CONTROL PANEL ---
        # ==============================================================================
        keithley_group = QGroupBox("SMU Measurement Mode")
        keithley_layout = QGridLayout(keithley_group)
        
        keithley_layout.addWidget(QLabel("Mode:"), 0, 0)
        self.smu_mode_selector = QComboBox()
        self.smu_mode_selector.addItems(["Resistance (Auto-Ohms)", "Constant Voltage (Chronoamperometry)"])
        self.smu_mode_selector.currentIndexChanged.connect(self._toggle_smu_controls)
        keithley_layout.addWidget(self.smu_mode_selector, 0, 1, 1, 2)
        
        self.bias_label = QLabel("Bias Voltage (V):")
        self.bias_val = QDoubleSpinBox()
        self.bias_val.setRange(-20.0, 20.0)
        self.bias_val.setSingleStep(0.1)
        self.bias_val.setValue(1.0)
        
        self.comp_label = QLabel("Compliance (A):")
        self.comp_val = QDoubleSpinBox()
        self.comp_val.setDecimals(4)
        self.comp_val.setRange(0.0001, 1.05)
        self.comp_val.setSingleStep(0.001)
        self.comp_val.setValue(0.0100)

        keithley_layout.addWidget(self.bias_label, 1, 0)
        keithley_layout.addWidget(self.bias_val, 1, 1)
        keithley_layout.addWidget(self.comp_label, 2, 0)
        keithley_layout.addWidget(self.comp_val, 2, 1) 

        self._toggle_smu_controls()

        # ==============================================================================
        # --- KEITHLEY 2700 CONTROL PANEL ---
        # ==============================================================================  
        mux_group = QGroupBox("Multiplexer Settings (2700 / 7706)")
        mux_layout = QGridLayout(mux_group)
        
        self.use_mux_cb = QCheckBox("Enable 20-Channel Array")
        self.use_mux_cb.toggled.connect(self._toggle_mux_controls)
        mux_layout.addWidget(self.use_mux_cb, 0, 0, 1, 2)
        
        mux_layout.addWidget(QLabel("Operation Mode:"), 1, 0)
        self.mux_mode_selector = QComboBox()
        self.mux_mode_selector.addItems([
            "Mode 1: Standalone (DMM Res)", 
            "Mode 2: Switch Matrix (SMU Measures)", 
            "Mode 3: Shunt Scan (Global Bias)"
        ])
        self.mux_mode_selector.currentIndexChanged.connect(self._toggle_mux_controls)
        mux_layout.addWidget(self.mux_mode_selector, 1, 1)
        
        ch_layout = QHBoxLayout()
        self.mux_start_val = QSpinBox()
        self.mux_start_val.setRange(101, 120)
        self.mux_start_val.setValue(101)
        self.mux_end_val = QSpinBox()
        self.mux_end_val.setRange(101, 120)
        self.mux_end_val.setValue(120)
        
        ch_layout.addWidget(QLabel("Start Ch:"))
        ch_layout.addWidget(self.mux_start_val)
        ch_layout.addWidget(QLabel("End Ch:"))
        ch_layout.addWidget(self.mux_end_val)
        mux_layout.addLayout(ch_layout, 2, 0, 1, 2)
        
        self.shunt_label = QLabel("Shunt Res (Ω):")
        self.shunt_val = QDoubleSpinBox()
        self.shunt_val.setRange(1.0, 1000000.0)
        self.shunt_val.setDecimals(1)
        self.shunt_val.setValue(1000.0)
        
        mux_layout.addWidget(self.shunt_label, 3, 0)
        mux_layout.addWidget(self.shunt_val, 3, 1)
        
        self._toggle_mux_controls()


        # TAB LAYOUT CONTROLS
        self.control_tabs = QTabWidget()
        
        # TAB 1: AUTOMATED RECIPE
        recipe_tab = QWidget()
        recipe_layout = QVBoxLayout(recipe_tab)
        self.load_recipe_btn = QPushButton("Load YAML Recipe...")
        self.load_recipe_btn.setObjectName("loadRecipeButton")
        self.load_recipe_btn.clicked.connect(self.select_recipe)
        self.start_recipe_btn = QPushButton("RUN RECIPE")
        self.start_recipe_btn.setObjectName("runRecipeButton")
        self.start_recipe_btn.setEnabled(False) 
        self.start_recipe_btn.clicked.connect(self.start_recipe_mode)
        recipe_layout.addWidget(QLabel("Recipe Automation Controls:"))
        recipe_layout.addWidget(self.load_recipe_btn)
        recipe_layout.addSpacing(10)
        recipe_layout.addWidget(self.start_recipe_btn)
        recipe_layout.addStretch()

        # TAB 2: MANUAL OVERRIDE CONTROL
        manual_tab = QWidget()
        manual_layout = QVBoxLayout(manual_tab)
        self.start_manual_btn = QPushButton("START MANUAL SESSION")
        self.start_manual_btn.setObjectName("manualSessionButton")
        self.start_manual_btn.clicked.connect(self.start_manual_mode)

        input_group = QGroupBox("Manual Target Adjustments")
        grid = QGridLayout(input_group)
        
        grid.addWidget(QLabel("MFC 1 (sccm):"), 0, 0)
        self.mfc1_val = QSpinBox(); self.mfc1_val.setRange(0, 2000); grid.addWidget(self.mfc1_val, 0, 1)
        grid.addWidget(QLabel("MFC 2 (sccm):"), 1, 0)
        self.mfc2_val = QSpinBox(); self.mfc2_val.setRange(0, 2000); grid.addWidget(self.mfc2_val, 1, 1)
        grid.addWidget(QLabel("MFC 3 (sccm):"), 2, 0)
        self.mfc3_val = QSpinBox(); self.mfc3_val.setRange(0, 500); grid.addWidget(self.mfc3_val, 2, 1)
        grid.addWidget(QLabel("MFC 4 (sccm):"), 3, 0)
        self.mfc4_val = QSpinBox(); self.mfc4_val.setRange(0, 500); grid.addWidget(self.mfc4_val, 3, 1)
        
        # CHANGED: Replaced the angular numeric QSpinBox with a clean operational checkbox
        grid.addWidget(QLabel("Shutter Control:"), 4, 0)
        self.shutter_checkbox = QCheckBox("Open Shutter")
        self.shutter_checkbox.setObjectName("openShutterCheckBox")
        grid.addWidget(self.shutter_checkbox, 4, 1)

        self.apply_manual_btn = QPushButton("Apply Setpoint Changes")
        self.apply_manual_btn.setObjectName("applySetpointButton")
        self.apply_manual_btn.setEnabled(False)
        self.apply_manual_btn.clicked.connect(self.apply_manual_changes)

        manual_layout.addWidget(self.start_manual_btn)
        manual_layout.addWidget(input_group)
        manual_layout.addWidget(self.apply_manual_btn)
        manual_layout.addStretch()

        self.stop_btn = QPushButton("STOP ENGINE (Emergency)")
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_experiment)

        console_layout = QVBoxLayout()
        console_layout.addWidget(QLabel("Live System Console Log:"))
        
        # Live System Console Log 
        self.console_display = QTextEdit()
        self.console_display.setReadOnly(True)
        
        self.console_display.setObjectName("consoleLogText")
        self.console_display.setFont(self.system_mono)
        console_layout.addWidget(self.console_display)

        self.control_tabs.addTab(recipe_tab, "Automated Recipe")
        self.control_tabs.addTab(manual_tab, "Manual Overrides")

        left_panel.addWidget(self.status_label)
        left_panel.addLayout(res_indicator_layout)
        left_panel.addWidget(keithley_group) 
        left_panel.addWidget(mux_group)
        left_panel.addSpacing(10)
        left_panel.addWidget(self.control_tabs)
        left_panel.addWidget(self.stop_btn)
        left_panel.addSpacing(10)
        left_panel.addLayout(console_layout, stretch=1)        
        left_panel.addLayout(theme_selector_layout)

        # RIGHT PANEL: GRAPH MATRIX
        graph_splitter = QSplitter(Qt.Orientation.Vertical)
        

        self.res_plot = pg.PlotWidget()
        self.res_plot.setProperty("title", "1. Sensor Resistance Data Loop (R vs. t)")
        self.res_plot.setTitle(self.res_plot.property("title"))
        self.res_plot.showGrid(x=True, y=True)
        self.res_curve = self.res_plot.plot(pen=pg.mkPen(color='#3498db', width=2))
        self.res_plot.getAxis('bottom').setStyle(showValues=False)
        graph_splitter.addWidget(self.res_plot)

        mfc_container = QWidget()
        mfc_box_layout = QVBoxLayout(mfc_container)
        mfc_box_layout.setContentsMargins(0,0,0,0)

        self.mfc_plot = pg.PlotWidget()
        self.mfc_plot.setProperty("title", "2. MFC Gas Flows & Shutter Profiles")
        self.mfc_plot.setTitle(self.mfc_plot.property("title"))
        self.mfc_plot.showGrid(x=True, y=True)
        self.mfc_plot.setLabel('left', 'Gas Flow', units='sccm')
        self.mfc_plot.setLabel('bottom', 'Elapsed Time', units='s')
        
        self.res_plot.setXLink(self.mfc_plot)
        
        # Secondary Y-Axis for Shutter State Tracking
        self.shutter_view = pg.ViewBox()
        self.mfc_plot.scene().addItem(self.shutter_view)
        self.mfc_plot.getAxis('right').linkToView(self.shutter_view)
        self.shutter_view.setXLink(self.mfc_plot)
        
        # CHANGED: The right axis label now scales cleanly from 0 (Closed) to 1 (Open)
        self.mfc_plot.getAxis('right').setLabel('Shutter State', units='1=Open, 0=Closed')
        self.shutter_view.setYRange(-0.1, 1.1, padding=0) # Keeps the binary square waves clean
        
        self.shutter_curve = pg.PlotCurveItem(
            pen=pg.mkPen(color=(142, 68, 173, 200), width=1.5, style=Qt.PenStyle.DashLine),
            fillLevel=0.0, brush=pg.mkBrush(142, 68, 173, 35)
        )
        self.shutter_view.addItem(self.shutter_curve)
        self.mfc_plot.getViewBox().sigResized.connect(self.sync_secondary_axis_views)

        mfc_colors = ['#e67e22', '#f1c40f', '#1abc9c', '#2ecc71']
        self.mfc_curves = [self.mfc_plot.plot(pen=pg.mkPen(color=c, width=1.8)) for c in mfc_colors]
        mfc_box_layout.addWidget(self.mfc_plot)

        legend_layout = QHBoxLayout()
        legend_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        items_to_toggle = [
            ("MFC 1", mfc_colors[0], self.mfc_curves[0]), ("MFC 2", mfc_colors[1], self.mfc_curves[1]),
            ("MFC 3", mfc_colors[2], self.mfc_curves[2]), ("MFC 4", mfc_colors[3], self.mfc_curves[3]),
            ("Shutter State", "#8e44ad", self.shutter_curve)
        ]
        for name, color, plot_item in items_to_toggle:
            cb = QCheckBox(name); cb.setChecked(True)
            cb.setProperty("plotCheckBox", True)
            cb.setStyleSheet(f"color: {color};")
            self.refresh_style(cb)
            cb.stateChanged.connect(lambda state, item=plot_item: item.setVisible(bool(state)))
            legend_layout.addWidget(cb)
            
        mfc_box_layout.addLayout(legend_layout)
        graph_splitter.addWidget(mfc_container)

        graph_splitter.setSizes([350, 650])
        main_layout.addWidget(left_frame, 2) 
        main_layout.addWidget(graph_splitter, 4) 

        self.queue_timer = QTimer(self)
        self.queue_timer.timeout.connect(self.flush_console_queue)
        self.queue_timer.start(100) 

    def _toggle_smu_controls(self):
        is_constant_v = (self.smu_mode_selector.currentIndex() == 1)
        self.bias_label.setVisible(is_constant_v)
        self.bias_val.setVisible(is_constant_v)
        self.comp_label.setVisible(is_constant_v)
        self.comp_val.setVisible(is_constant_v)

    def _toggle_mux_controls(self):
        mux_enabled = self.use_mux_cb.isChecked()
        self.mux_mode_selector.setEnabled(mux_enabled)
        self.mux_start_val.setEnabled(mux_enabled)
        self.mux_end_val.setEnabled(mux_enabled)   
        # The shunt resistor controls are only relevant in Mode 3 (Shunt Scan)
        is_shunt_mode = mux_enabled and (self.mux_mode_selector.currentIndex() == 2)
        self.shunt_label.setVisible(is_shunt_mode)
        self.shunt_val.setVisible(is_shunt_mode) 

    def sync_secondary_axis_views(self):
        self.shutter_view.setGeometry(self.mfc_plot.getViewBox().sceneBoundingRect())
        self.shutter_view.linkedViewChanged(self.mfc_plot.getViewBox(), pg.ViewBox.XAxis)

    def _setup_print_logging(self):
        try:
            with open(self.config_path, 'r') as f: config = yaml.safe_load(f)
            console_cfg = config.get('logging', {}).get('console_log', {})
            if console_cfg.get('enabled', False):
                log_dir = console_cfg.get('folder', 'console_logs')
                prefix = console_cfg.get('filename_prefix', 'system_console')
                os.makedirs(log_dir, exist_ok=True)
                self.log_file_obj = open(os.path.join(log_dir, f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.log"), 'a', encoding='utf-8')
        except Exception as e:
            sys.__stdout__.write(f"Failed setting up print redirection: {e}\n")

        sys.stdout = WriteStream(sys.stdout, self.log_file_obj, self.console_queue)
        sys.stderr = WriteStream(sys.stderr, self.log_file_obj, self.console_queue)

    def flush_console_queue(self):
        text_to_append = ""
        while not self.console_queue.empty():
            try: text_to_append += self.console_queue.get_nowait()
            except queue.Empty: break
        if text_to_append:
            cursor = self.console_display.textCursor(); cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertText(text_to_append); self.console_display.setTextCursor(cursor)
            self.console_display.ensureCursorVisible()

    def select_recipe(self):
        file, _ = QFileDialog.getOpenFileName(self, "Select YAML Recipe", "", "YAML Files (*.yaml)")
        if file:
            self.recipe_path = file
            self.status_label.setText(f"Loaded: {os.path.basename(file)}")
            self.status_label.setProperty("textColor", "success")
            self.status_label.setProperty("fontWeight", "bold")
            self.refresh_style(self.status_label)
            self.start_recipe_btn.setEnabled(True)

    def prepare_data_arrays(self):
        self.data_history = {"time": [], "resistance": [], "flows": [[], [], [], []], "shutter": []}
        self.res_curve.setData([], [])
        self.shutter_curve.setData([], [])
        for curve in self.mfc_curves: curve.setData([], [])

    def start_recipe_mode(self):
        self.prepare_data_arrays()
        
        meas_mode = 'constant_voltage' if self.smu_mode_selector.currentIndex() == 1 else 'resistance'
        mux_modes_map = {0: 'standalone', 1: 'switch_matrix', 2: 'shunt_scan'}
        
        self.worker = ExperimentWorker(
            self.config_path, 
            self.recipe_path, 
            mode='recipe',
            measurement_mode=meas_mode,
            bias_voltage=self.bias_val.value(),
            compliance=self.comp_val.value(),
            use_mux=self.use_mux_cb.isChecked(),
            mux_mode=mux_modes_map[self.mux_mode_selector.currentIndex()],
            mux_start_ch=self.mux_start_val.value(),
            mux_end_ch=self.mux_end_val.value(),
            shunt_res=self.shunt_val.value()
        )
        self.connect_and_start_worker()
        
        self._lock_ui_hardware_controls(True)
        self.control_tabs.setTabEnabled(1, False) 
        self.start_recipe_btn.setEnabled(False)
        self.load_recipe_btn.setEnabled(False)

    def start_manual_mode(self):
        self.prepare_data_arrays()
        
        meas_mode = 'constant_voltage' if self.smu_mode_selector.currentIndex() == 1 else 'resistance'
        
        self.worker = ExperimentWorker(
            self.config_path, 
            mode='manual',
            measurement_mode=meas_mode,
            bias_voltage=self.bias_val.value(),
            compliance=self.comp_val.value()
        )
        self.connect_and_start_worker()

        self.smu_mode_selector.setEnabled(False)
        self.bias_val.setEnabled(False)
        self.comp_val.setEnabled(False)

        self.control_tabs.setTabEnabled(0, False) 
        self.start_manual_btn.setEnabled(False)
        self.apply_manual_btn.setEnabled(True)
        self.apply_manual_changes()

    def connect_and_start_worker(self):
        self.worker.data_sig.connect(self.update_live_data)
        self.worker.status_sig.connect(self.update_status)
        self.worker.error_sig.connect(self.handle_worker_error)
        self.worker.finished_sig.connect(self.on_experiment_finished)
        self.stop_btn.setEnabled(True)
        self.worker.start()

    def apply_manual_changes(self):
        if hasattr(self, 'worker') and self.worker.isRunning():
            flow_targets = {
                1: self.mfc1_val.value(), 2: self.mfc2_val.value(),
                3: self.mfc3_val.value(), 4: self.mfc4_val.value()
            }
            # CHANGED: Extracts the manual targets directly as a clean boolean state flag
            shutter_open_target = self.shutter_checkbox.isChecked()
            self.worker.update_manual_setpoints(shutter_open_target, flow_targets)
            print(f"[Manual Mode Update]: Staging Shutter_Open={shutter_open_target}, Flows={list(flow_targets.values())}")

    def update_live_data(self, data):
        self.res_display.setText(f"{data['resistance']:.4e} Ω")
        elapsed_t = len(self.data_history['time'])
        self.data_history['time'].append(elapsed_t)
        
        self.data_history['resistance'].append(data['resistance'])
        self.res_curve.setData(self.data_history['time'], self.data_history['resistance'])
        
        self.data_history['shutter'].append(data['shutter'])
        self.shutter_curve.setData(self.data_history['time'], self.data_history['shutter'])
        
        for i in range(4):
            self.data_history['flows'][i].append(data['flows'][i])
            self.mfc_curves[i].setData(self.data_history['time'], self.data_history['flows'][i])

    def update_status(self, text): self.status_label.setText(text)
    def handle_worker_error(self, err): print(f"[CRITICAL WARNING]: {err}"); self.stop_experiment()
    def stop_experiment(self):
        if hasattr(self, 'worker') and self.worker.isRunning(): self.worker.is_running = False

    def _lock_ui_hardware_controls(self, locked: bool):
        self.smu_mode_selector.setDisabled(locked)
        self.bias_val.setDisabled(locked)
        self.comp_val.setDisabled(locked)
        
        self.use_mux_cb.setDisabled(locked)
        self.mux_mode_selector.setDisabled(locked)
        self.mux_start_val.setDisabled(locked)
        self.mux_end_val.setDisabled(locked)
        self.shunt_val.setDisabled(locked)
        
    def on_experiment_finished(self, log_path):
        self.stop_btn.setEnabled(False)
        self.apply_manual_btn.setEnabled(False)

        self.smu_mode_selector.setEnabled(True)
        self.bias_val.setEnabled(True)
        self.comp_val.setEnabled(True)

        self.control_tabs.setTabEnabled(0, True)
        self.control_tabs.setTabEnabled(1, True)
        self.start_manual_btn.setEnabled(True)
        if self.recipe_path: self.start_recipe_btn.setEnabled(True)
        self.load_recipe_btn.setEnabled(True)
        print(f"Hardware loop cleanly halted. Session log saved to: {log_path}")

    
    def closeEvent(self, event):
    #Intercepts window close to ensure background threads are safely killed.
        if hasattr(self, 'worker') and self.worker.isRunning():
            print("Application closing. Halting hardware worker thread cleanly...")
            self.worker.is_running = False  # Signal thread loop to terminate
            self.worker.wait()              # Block main thread until worker exits safely
        
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
        if self.log_file_obj:
            self.log_file_obj.close()
        event.accept()
    
    def _create_dummy_files(self):
        """
        Ensures the local lab computer runtime workspace folders and files exist.
        Copies raw templates from the assets folder to preserve structural comments.
        """
        # Define template files paths
        template_config = ASSETS_DIR / "config.template.yaml"
        template_recipe = ASSETS_DIR / "recipe.template.yaml"
        
        # 1. Check and copy live config.yaml fallback
        if not os.path.exists(self.config_path):
            if template_config.exists():
                shutil.copy(template_config, self.config_path)
                print("[System Info] Generated local 'config.yaml' from master assets template.")
            else:
                # Critical safety net fallback if assets folder is completely missing
                print("[Critical Warning] 'assets/config.template.yaml' not found! Falling back to safe hardcoded defaults.")
                fallback_config = {
                    'hardware': {
                        'use_mock': True,
                        'keithley_2400': {'port': 'MOCK_PORT', 'auto_range': False, 'manual_range': 200000, 'four_wire': True},
                        'shutter': {'port': 'MOCK_SHUTTER', 'baud_rate': 115200, 'open_angle': 90, 'closed_angle': 0},
                        'mfc_controller': {'port': 'MOCK_MFC', 'range_sccm': {1: 1000, 2: 1000, 3: 200, 4: 200}}
                    },
                    'logging': {
                        'data_log': {'folder': 'data', 'filename_prefix': 'sensing_run'},
                        'console_log': {'enabled': True, 'folder': 'logs', 'filename_prefix': 'log'}
                    }
                }
                with open(self.config_path, 'w') as f:
                    yaml.dump(fallback_config, f)

        # 2. Extract operational workspace directories dynamically from config.yaml
        try:
            with open(self.config_path, 'r') as f:
                cfg = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"[Error] Failed to read config.yaml layout: {e}")
            cfg = {}

        # Safely fall back to default string folders if parameters are missing inside config.yaml
        data_dir = cfg.get('logging', {}).get('data_log', {}).get('folder', 'data')
        log_dir = cfg.get('logging', {}).get('console_log', {}).get('folder', 'logs')
        recipe_dir = "recipes"

        # 3. Create all dynamic storage directories safely (skips if they already exist)
        for folder in [data_dir, log_dir, recipe_dir]:
            Path(folder).mkdir(parents=True, exist_ok=True)

        # 4. Seed the user recipes workspace folder with a base profile if it is empty
        self.recipe_path = os.path.join(recipe_dir, "dummy_recipe.yaml")
        if not os.path.exists(self.recipe_path):
            if template_recipe.exists():
                shutil.copy(template_recipe, self.recipe_path)
                print(f"[System Info] Seeded recipes workspace with: {os.path.basename(self.recipe_path)}")
            else:
                # Quick programmatic fallback array if the assets/ recipe template is missing
                fallback_recipe = {
                    'steps': [
                        {'name': 'Purge_Phase', 'duration': 10, 'shutter_open': False, 'mfc_flows': {1: 120, 2: 10}},
                        {'name': 'Expose_Gas', 'duration': 20, 'shutter_open': True, 'mfc_flows': {1: 100, 2: 30}},
                        {'name': 'Recovery_Phase', 'duration': 15, 'shutter_open': False, 'mfc_flows': {1: 120, 2: 10}}
                    ]
                }
                with open(self.recipe_path, 'w') as f:
                    yaml.dump(fallback_recipe, f)
                    
    def refresh_style(self,widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        
    def change_theme(self,index):
        theme = self.theme_selector.itemData(index)
        self.setStyleSheet(
            load_theme(theme)
        )
        update_plot_theme(self.mfc_plot, theme)
        update_plot_theme(self.res_plot, theme)