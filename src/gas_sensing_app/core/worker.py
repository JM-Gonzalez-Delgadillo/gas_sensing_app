import csv
import os
import time

import yaml
from PyQt6.QtCore import QMutex, QThread, pyqtSignal

from gas_sensing_app.hardware import (
    AeraMFCManager,
    Keithley2400,
    Keithley2700,
    ShutterController,
)

# ==============================================================================
# II. BACKGROUND WORKER (Processes State-Based Instructions)
# ==============================================================================
# src/gas_sensing_app/core/worker.py


class ExperimentWorker(QThread):
    data_sig = pyqtSignal(dict)
    status_sig = pyqtSignal(str)
    error_sig = pyqtSignal(str)
    finished_sig = pyqtSignal(str)

    def __init__(
        self,
        config_path=None,
        recipe_path=None,
        mode="recipe",
        measurement_mode="resistance",
        bias_voltage=1.0,
        compliance=0.01,
        use_mux=False,
        mux_mode="standalone",
        mux_start_ch=101,
        mux_end_ch=120,
        shunt_res=1000.0,
    ):
        """Initialize the background worker for data acquisition

        Args:
            config_path: Path to the hardware configuration YAML.
            recipe_path: Path to the recipe YAML (if mode=='recipe').
            mode: 'recipe' for automated runs, 'manual' for live dashboard control.
            measurement_mode: 'resistance' for Auto-Ohms, 'constant_voltage' for Chronoamperometry.
            bias_voltage: The voltage to apply if in 'constant_voltage' mode.
            compliance: Current compliance limit in Amperes.
            use_mux (bool): Flag to activate the Keithley 2700 multiplexer.
            mux_mode (str): 'standalone' (Mode 1), 'switch_matrix' (Mode 2), or 'shunt_scan' (Mode 3).
            mux_start_ch (int): Starting channel of the MUX card.
            mux_end_ch (int): Ending channel of the MUX card.
            shunt_res (float): Resistance value of the series shunt (in Ohms) for Mode 3.
        """

        self.mutex = QMutex()
        super().__init__()

        # 1. FIX: Save the incoming mode parameter so line 55 can read it!
        self.mode = mode
        self.measurement_mode = measurement_mode
        self.bias_voltage = float(bias_voltage)
        self.compliance = float(compliance)

        self.use_mux = use_mux
        self.mux_mode = mux_mode
        self.mux_start_ch = int(mux_start_ch)
        self.mux_end_ch = int(mux_end_ch)
        self.shunt_res = float(shunt_res)

        # 2. FIX: Initialize this flag so your step loop doesn't crash on 'self.is_running'
        self.is_running = True
        self.manual_update_pending = False
        self.target_shutter_open = False
        self.target_flows = {}

        # If no explicit absolute path is passed, calculate relative to this code file
        if config_path is None:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            # Move up two levels: from core/ to gas_sensing_app/ to project_root/
            self.config_path = os.path.abspath(
                os.path.join(current_dir, "..", "..", "..", "config.yaml")
            )
        else:
            self.config_path = config_path

        # Do the same for the recipe file to prevent it from spawning randomly
        if recipe_path is None:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            self.recipe_path = os.path.abspath(
                os.path.join(current_dir, "..", "..", "..", "dummy_recipe.yaml")
            )
        else:
            self.recipe_path = recipe_path

    def update_manual_setpoints(self, shutter_open, flow_dict):
        self.mutex.lock()
        self.target_shutter_open = shutter_open
        self.target_flows = flow_dict.copy()  # Create a decoupled snapshot copy
        self.manual_update_pending = True
        self.mutex.unlock()

    def run(self):  # noqa: C901
        print(f"Starting Background Worker Thread [{self.mode.upper()} MODE]...")
        self.status_sig.emit("Initializing Hardware...")

        # Initialize references to None to avoid UnboundLocalErrors
        meter = None
        meter_mux = None
        shutter = None
        mfc = None

        try:
            with open(self.config_path, "r") as f:
                config = yaml.safe_load(f)
            hw = config["hardware"]
            data_cfg = config.get("logging", {}).get("data_log", {})
            log_dir = data_cfg.get("folder", "data")
            os.makedirs(log_dir, exist_ok=True)

            if self.mode == "recipe":
                with open(self.recipe_path, "r") as f:
                    recipe = yaml.safe_load(f)
                prefix = data_cfg.get("filename_prefix", "gas_experiment")
                self.log_path = os.path.join(
                    log_dir, f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
                )
            else:
                self.log_path = os.path.join(
                    log_dir, f"manual_session_{time.strftime('%Y%m%d_%H%M%S')}.csv"
                )

        except Exception as e:
            self.error_sig.emit(f"Configuration/File Error: {str(e)}")
            return

        # Instantiate hardware using configuration bounds
        k2400_cfg = hw["keithley_2400"]
        shutter_cfg = hw["shutter"]

        meter = Keithley2400(k2400_cfg["port"])

        if self.use_mux:
            k2700_cfg = hw.get("keithley_2700", {"port": "GPIB0::16::INSTR"})
            meter_mux = Keithley2700(k2700_cfg["port"])

        shutter = ShutterController(
            shutter_cfg["port"],
            shutter_cfg.get("baud_rate", 115200),
            open_angle=shutter_cfg.get("open_angle", 90),
            closed_angle=shutter_cfg.get("closed_angle", 0),
        )
        shutter.set_speed(0)
        mfc = AeraMFCManager(
            hw["mfc_controller"]["port"], ranges=hw["mfc_controller"]["range_sccm"]
        )

        try:
            if not self.use_mux:
                if self.measurement_mode == "constant_voltage":
                    self.status_sig.emit("Setting up Constant Voltage Mode...")
                    meter.setup_constant_voltage_mode(
                        self.bias_voltage, self.compliance
                    )
                else:
                    self.status_sig.emit("Setting up Constant Resistance Mode...")
                    k2400_cfg = self.hw.get("keithley_2400", {})
                    four_wire_flag = k2400_cfg.get("four_wire", False)
                    wire_mode = 4 if four_wire_flag else 2
                    meter.setup_resistance_mode(wire_mode=wire_mode)

                # Turn on output for continuous measurement
                meter.turn_output_on()
            else:
                # Multiplexed Array Operations
                self.status_sig.emit(
                    f"Setting up Sensor Array: {self.mux_mode.replace('_', ' ').title()}"
                )

                if self.mux_mode == "standalone":
                    # Mode 1: DMM scans resistance internally
                    meter_mux.setup_internal_resistance_scan(
                        self.mux_start_ch, self.mux_end_ch
                    )
                    # Turn off 2400 output as it's not needed here
                    meter.turn_output_off()

                elif self.mux_mode == "shunt_scan":
                    # Mode 3: Global bias via SMU, DMM scans shunt voltages
                    meter_mux.setup_shunt_voltage_scan(
                        self.mux_start_ch, self.mux_end_ch
                    )
                    meter.setup_constant_voltage_mode(
                        self.bias_voltage, self.compliance
                    )
                    meter.turn_output_on()

                elif self.mux_mode == "switch_matrix":
                    # Mode 2: DMM routes signals, SMU measures
                    # Important: Force 2-wire mode on SMU because the 7706 routes 2-wire circuits
                    if self.measurement_mode == "constant_voltage":
                        meter.setup_constant_voltage_mode(
                            self.bias_voltage, self.compliance
                        )
                    else:
                        meter.setup_resistance_mode(wire_mode=2)
                    meter.turn_output_on()

            shutter.set_open(False)  # Start safely closed
            experiment_start = (
                time.time()
            )  # Capture the absolute start time of the experiment sequence

            with open(self.log_path, "w", newline="") as csvfile:
                writer = csv.writer(csvfile)
                headers = ["Timestamp", "Elapsed_Seconds", "Mode_Step", "Tick"]
                if self.use_mux:
                    for ch in range(self.mux_start_ch, self.mux_end_ch + 1):
                        headers.extend([f"Res_{ch}", f"Cur_{ch}"])
                else:
                    headers.extend(["Resistance", "Current"])

                headers.extend(["MFC1", "MFC2", "MFC3", "MFC4", "Shutter_Open"])
                writer.writerow(headers)

                # ----------------- ACQUISITION FUNCTION -----------------
                def acquire_reading():
                    """
                    Executes the measurement sequence based on the active hardware topology.
                    Returns a dictionary mapping channels to their respective resistance and current.
                    """
                    results = {}

                    if not self.use_mux:
                        if self.measurement_mode == "constant_voltage":
                            curr = meter.read_current()
                            res = (
                                self.bias_voltage / curr if curr != 0 else float("inf")
                            )
                        else:
                            res = meter.read_resistance_direct()
                            curr = 0.0
                        results["single"] = {"res": res, "curr": curr}
                    else:
                        if self.mux_mode == "standalone":
                            scan_data = meter_mux.execute_scan()
                            for ch, val in scan_data:
                                results[ch] = {"res": val, "curr": 0.0}

                        elif self.mux_mode == "shunt_scan":
                            scan_data = meter_mux.execute_scan()
                            for ch, v_shunt in scan_data:
                                curr = v_shunt / self.shunt_res
                                # R_sensor = V_sensor / I = (V_bias - V_shunt) / I
                                res = (
                                    (self.bias_voltage - v_shunt) / curr
                                    if curr != 0
                                    else float("inf")
                                )
                                results[ch] = {"res": res, "curr": curr}

                        elif self.mux_mode == "switch_matrix":
                            for ch in range(self.mux_start_ch, self.mux_end_ch + 1):
                                meter_mux.close_single_relay(ch)
                                if self.measurement_mode == "constant_voltage":
                                    curr = meter.read_current()
                                    res = (
                                        self.bias_voltage / curr
                                        if curr != 0
                                        else float("inf")
                                    )
                                else:
                                    res = meter.read_resistance_direct()
                                    curr = 0.0
                                results[ch] = {"res": res, "curr": curr}

                    return results

                # ----------------- MODE A: AUTOMATED RECIPE -----------------
                if self.mode == "recipe":
                    for step in recipe["steps"]:
                        if not self.is_running:
                            break

                        is_open = bool(step.get("shutter_open", False))
                        print(f"Transitioning to Phase Step: {step['name']}")
                        shutter.set_open(is_open)

                        # 1. Extract raw flows dict
                        raw_flows = step.get("mfc_flows", {})

                        # 2. Defensively normalize all keys to integers
                        # to handle user string inputs
                        normalized_flows = {
                            int(k): float(v) for k, v in raw_flows.items()
                        }

                        # 3. Explicitly loop through all 4 hardware channels
                        # to prevent sticky flows
                        for ch in [1, 2, 3, 4]:
                            target_flow = normalized_flows.get(
                                ch, 0.0
                            )  # Safe fallback to 0.0 if omitted
                            mfc.set_flow(ch, target_flow)

                        self.status_sig.emit(
                            f"Step: {step['name']} ({step['duration']}s)"
                        )

                        for second in range(step["duration"]):
                            if not self.is_running:
                                break
                            loop_start = time.time()

                            # Read based on selected mode
                            data_dict = acquire_reading()

                            f1, f2 = mfc.get_actual_flow(1), mfc.get_actual_flow(2)
                            f3, f4 = mfc.get_actual_flow(3), mfc.get_actual_flow(4)

                            curr_time = time.strftime(
                                "%Y-%m-%d %H:%M:%S"
                            )  # Full calendar date + time format
                            elapsed_seconds = int(
                                time.time() - experiment_start
                            )  # Calculate exact seconds elapsed from the start

                            row = [curr_time, elapsed_seconds, step["name"], second + 1]
                            if not self.use_mux:
                                row.extend(
                                    [
                                        data_dict["single"]["res"],
                                        data_dict["single"]["curr"],
                                    ]
                                )
                                ui_res = data_dict["single"]["res"]
                            else:
                                for ch in range(self.mux_start_ch, self.mux_end_ch + 1):
                                    ch_data = data_dict.get(
                                        ch, {"res": 0.0, "curr": 0.0}
                                    )
                                    row.extend([ch_data["res"], ch_data["curr"]])
                                # Fallback for legacy UI graph (plot channel 101 by default)
                                ui_res = data_dict.get(self.mux_start_ch, {"res": 0.0})[
                                    "res"
                                ]

                            row.extend([f1, f2, f3, f4, int(is_open)])

                            writer.writerow(row)
                            csvfile.flush()

                            self.data_sig.emit(
                                {
                                    "timestamp": curr_time,
                                    "resistance": ui_res,  # Legacy support for dashboard graph
                                    "current": 0.0,
                                    "flows": [f1, f2, f3, f4],
                                    "shutter": int(is_open),
                                    "mux_data": data_dict,  # Full array data payload
                                }
                            )
                            time.sleep(max(0, 1.0 - (time.time() - loop_start)))

                # ----------------- MODE B: MANUAL CONTROL OVERRIDE -----------------
                elif self.mode == "manual":
                    self.status_sig.emit("Live Manual Override Active")
                    tick = 0
                    while self.is_running:
                        loop_start = time.time()
                        tick += 1

                        if self.manual_update_pending:
                            self.mutex.lock()
                            local_shutter = self.target_shutter_open
                            local_flows = self.target_flows.copy()
                            self.manual_update_pending = False
                            self.mutex.unlock()
                            shutter.set_open(local_shutter)
                            for ch_idx, flow in local_flows.items():
                                mfc.set_flow(ch_idx, flow)

                        data_dict = acquire_reading()

                        f1, f2 = mfc.get_actual_flow(1), mfc.get_actual_flow(2)
                        f3, f4 = mfc.get_actual_flow(3), mfc.get_actual_flow(4)

                        curr_time = time.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )  # Full calendar date + time format
                        elapsed_seconds = int(
                            time.time() - experiment_start
                        )  # Calculate exact seconds elapsed from the start

                        row = [curr_time, elapsed_seconds, "MANUAL", tick]
                        if not self.use_mux:
                            row.extend(
                                [
                                    data_dict["single"]["res"],
                                    data_dict["single"]["curr"],
                                ]
                            )
                            ui_res = data_dict["single"]["res"]
                        else:
                            for ch in range(self.mux_start_ch, self.mux_end_ch + 1):
                                ch_data = data_dict.get(ch, {"res": 0.0, "curr": 0.0})
                                row.extend([ch_data["res"], ch_data["curr"]])
                            ui_res = data_dict.get(self.mux_start_ch, {"res": 0.0})[
                                "res"
                            ]

                        row.extend([f1, f2, f3, f4, int(self.target_shutter_open)])

                        writer.writerow(row)
                        csvfile.flush()

                        self.data_sig.emit(
                            {
                                "timestamp": curr_time,
                                "resistance": ui_res,
                                "current": 0.0,
                                "flows": [f1, f2, f3, f4],
                                "shutter": int(self.target_shutter_open),
                                "mux_data": data_dict,
                            }
                        )
                        time.sleep(max(0, 1.0 - (time.time() - loop_start)))

        except Exception as e:
            self.error_sig.emit(f"Runtime Hardware Error: {str(e)}")

        finally:
            print("Cleaning up resources, moving hardware to safe state.")
            self.status_sig.emit("Finished / Safe State.")

            # 1. Safely stop the MFCs independently
            if mfc:
                try:
                    mfc.stop_all()
                except Exception as e:
                    print(
                        f"[CLEANUP WARNING] Could not zero MFCs "
                        f"(Device likely offline): {e}"
                    )

            # 2. Safely close the physical shutter door
            if shutter:
                try:
                    shutter.set_open(False)
                except Exception as e:
                    print(f"[CLEANUP WARNING] Could not command shutter to close: {e}")

            # 3. Defensively disengage Keithley sourcing power
            if meter:
                try:
                    meter.close_connection()
                except Exception as e:
                    print(f"[CLEANUP WARNING] Could not disable Keithley output: {e}")

            # 4. Defensively disengage the Keithley 2700 MUX if it was used
            if meter_mux:
                try:
                    meter_mux.close_connection()
                except Exception as e:
                    print(
                        f"[CLEANUP WARNING] Could not safely close Keithley 2700 MUX: {e}"
                    )

            # 5. Close low-level communication handles cleanly
            if mfc:
                try:
                    mfc.close()
                except Exception:
                    pass

            if shutter:
                try:
                    shutter.close()
                except Exception:
                    pass

            self.finished_sig.emit(self.log_path)
