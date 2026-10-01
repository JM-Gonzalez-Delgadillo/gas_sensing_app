import pyvisa
import logging
import time
from typing import List, Tuple

class Keithley2700:
    def __init__(self, resource_address: str):
        """
        Initializes the connection to the Keithley 2700 Data Acquisition System.
        """
        self.logger = logging.getLogger(__name__)
        self.rm = pyvisa.ResourceManager()
        try:
            self.instrument = self.rm.open_resource(resource_address)
            self.instrument.timeout = 15000  # 15 seconds, sufficient for a 20-channel scan
            self.instrument.write("*RST")
            time.sleep(0.5)
            # Disable continuous initiation to prevent internal measurement conflicts
            self.instrument.write(":INIT:CONT OFF")
            self.logger.info(f"Keithley 2700 connected successfully at {resource_address}")
        except pyvisa.errors.VisaIOError as e:
            self.logger.error(f"Failed to connect to Keithley 2700: {e}")
            raise

    # =========================================================================
    # MODE 1 & 3: AUTOMATED SCANNING (Resistance or Shunt Voltage)
    # =========================================================================
    
    def setup_internal_resistance_scan(self, start_ch: int = 101, end_ch: int = 120) -> None:
        """
        [Mode 1] Configures the internal DMM to scan and measure resistance directly.
        Does not require the external SMU 2400.
        """
        scan_list = f"(@{start_ch}:{end_ch})"
        self.instrument.write("*RST")
        self.instrument.write(f":FUNC 'RES', {scan_list}")
        self.instrument.write(f":ROUT:SCAN {scan_list}")
        self.instrument.write(":ROUT:SCAN:TSO IMM")  # Trigger immediately on READ
        self.instrument.write(":FORM:ELEM READ, CHAN")
        self.logger.info(f"Configured Mode 1: Internal Resistance Scan on {scan_list}")

    def setup_shunt_voltage_scan(self, start_ch: int = 101, end_ch: int = 120) -> None:
        """
        [Mode 3] Configures the DMM to read DC Voltage passively across shunt resistors.
        Allows the SMU 2400 to maintain a constant global bias field.
        """
        scan_list = f"(@{start_ch}:{end_ch})"
        self.instrument.write("*RST")
        self.instrument.write(f":FUNC 'VOLT:DC', {scan_list}")
        self.instrument.write(f":VOLT:DC:NPLC 1.0, {scan_list}") # Balance speed and noise
        self.instrument.write(f":ROUT:SCAN {scan_list}")
        self.instrument.write(":ROUT:SCAN:TSO IMM")
        self.instrument.write(":FORM:ELEM READ, CHAN")
        self.logger.info(f"Configured Mode 3: Shunt Voltage Scan on {scan_list}")

    def execute_scan(self) -> List[Tuple[int, float]]:
        """
        Executes the configured scan (Mode 1 or 3) and parses the data block.
        
        Returns:
            List of tuples containing (channel_number, measured_value)
            Example: [(101, 10500.5), (102, 10800.2), ...]
        """
        # The READ? command triggers the scan and waits for it to complete
        raw_data = self.instrument.query(":READ?")
        
        # Keithley returns comma-separated pairs based on :FORM:ELEM READ, CHAN
        # e.g., "1.50E+03,101, 1.45E+03,102"
        data_points = raw_data.split(',')
        results = []
        
        # Iterate in pairs (value, channel)
        try:
            for i in range(0, len(data_points), 2):
                val = float(data_points[i].strip())
                # Ensure the channel is parsed as an integer and stripped of the VDC/OHM string
                chan = int(float(data_points[i+1].strip().replace('VDC', '').replace('OHM', '')))
                results.append((chan, val))
        except (ValueError, IndexError) as e:
            self.logger.error(f"Error parsing scan data string: {raw_data} | Exception: {e}")
            
        return results

    # =========================================================================
    # MODE 2: EXTERNAL SMU MULTIPLEXING (Switch Matrix)
    # =========================================================================

    def close_single_relay(self, channel: int) -> None:
        """
        [Mode 2] Opens all relays, then closes only the target channel.
        Allows the external SMU 2400 to measure the target sensor.
        """
        self.instrument.write(":ROUT:OPEN:ALL")
        time.sleep(0.01) # Break-before-make delay
        
        self.instrument.write(f":ROUT:CLOS (@{channel})")
        time.sleep(0.05) # Mechanical relay settling and contact debouncing time
        self.logger.debug(f"Relay {channel} closed for SMU measurement.")

    # =========================================================================
    # SAFETY AND CLEANUP
    # =========================================================================

    def open_all_channels(self) -> None:
        """Disengages all mechanical relays."""
        self.instrument.write(":ROUT:OPEN:ALL")
        self.logger.info("All Keithley 2700 relays opened.")

    def close_connection(self) -> None:
        """Safely isolates the physical array and releases the VISA resource."""
        self.open_all_channels()
        self.instrument.close()
        self.logger.info("Connection to Keithley 2700 safely closed.")