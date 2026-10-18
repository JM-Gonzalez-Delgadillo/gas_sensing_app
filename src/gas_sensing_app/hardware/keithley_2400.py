import logging
import time

import pyvisa


class Keithley2400:
    def __init__(self, resource_address: str):
        """Initialize the Keithley 2400 device."""
        self.logger = logging.getLogger(__name__)
        self.rm = pyvisa.ResourceManager()

        try:
            self.instrument = self.rm.open_resource(resource_address)
            self.instrument.timeout = 5000  # Set timeout to 5 seconds
            self.logger.info(
                f"Successfully connected to Keithley 2400 at {resource_address}."
            )
        except pyvisa.VisaIOError as e:
            self.logger.error(
                f"Failed to connect to Keithley 2400 at {resource_address}: {e}"
            )
            raise

        # self.rm = pyvisa.ResourceManager('@py')
        # self.inst = self.rm.open_resource(resource_string)
        # self.inst.baud_rate = 9600
        # self.inst.read_termination = '\r'
        # self.inst.write_termination = '\r'
        # self.inst.timeout = 5000

        # Initial Reset and Clear
        self.inst.write("*RST")
        self.inst.write("*CLS")
        print(f"Keithley 2400 connected at {resource_string}.")
        time.sleep(0.5)

    def setup_resistance_mode(self, wire_mode: int = 2) -> None:
        """Configure the Keithley 2400 for resistance measurement."""

        self.instrument.write("*RST")
        self.instrument.write(":SENS:FUNC 'RES'")
        self.instrument.write(":SENS:RES:MODE AUTO")

        # Configure 2-wire or 4-wire (Kelvin) measurement
        if wire_mode == 4:
            self.instrument.write(":SYST:RSEN ON")  # Remote sensing ON
            self.logger.info(
                "SMU configured for 4-wire (Remote Sense) resistance measurement."
            )
        else:
            self.instrument.write(":SYST:RSEN OFF")  # Remote sensing OFF
            self.logger.info(
                "SMU configured for 2-wire (Local Sense) resistance measurement."
            )

        self.instrument.write(":FORM:ELEM RES")

    # def setup_resistance(self, auto_range=True, manual_range=None, four_wire=False):
    #     """Configure the device for resistance measurement."""
    #     sense_str = "4-Wire (Remote)" if four_wire else "2-Wire (Local)"
    #     print(f"Configuring Keithley for Resistance ({sense_str}, Auto-Range: {auto_range})...")

    #     Clear any existing data in the buffer
    #     self.inst.write(":TRAC:CLEAR")

    #     commands = [
    #         ":SENS:FUNC 'RES'",        # Select resistance function
    #         ":SENS:RES:MODE MAN"       # Manual Ohms mode
    #     ]

    #     Configure 2-wire vs 4-wire remote sensing
    #     if four_wire:
    #         commands.append(":SYST:RSEN ON")   # Remote Sense On (4-wire)
    #     else:
    #         commands.append(":SYST:RSEN OFF")  # Remote Sense Off (2-wire)

    #     Configure Ranging
    #     if auto_range:
    #         commands.append(":SENS:RES:RANG:AUTO ON")
    #     else:
    #         commands.append(":SENS:RES:RANG:AUTO OFF")
    #         if manual_range is not None:
    #             commands.append(f":SENS:RES:RANG {manual_range}")
    #         else:
    #             print("Warning: Manual range selected but no range value provided.")

    #     commands.append(":OUTP ON")     # Turn output on

    #     for cmd in commands:
    #         self.inst.write(cmd)
    #         time.sleep(0.1)             # Give the old processor time to breathe

    def setup_constant_voltage_mode(
        self, bias_voltage: float, compliance_current: float
    ) -> None:
        self.instrument.write("*RST")  # Reset the instrument

        # Source configuration (Voltage)
        self.instrument.write(":SOUR:FUNC VOLT")  # Set source function to voltage
        self.instrument.write(":SOUR:VOLT:MODE FIXED")  # Set voltage mode to fixed
        self.instrument.write(
            f":SOUR:VOLT:LEVEL {bias_voltage}"
        )  # Set the bias voltage

        # Sense configuration (Current)
        self.instrument.write(":SENS:FUNC 'CURR'")  # Set sense function to current
        self.instrument.write(
            f":SENS:CURR:PROT {compliance_current}"
        )  # Set compliance current

        # Data formating (Return only the measured value)
        self.instrument.write(":FORM:ELEM CURR")  # Return only the current measurement

        self.logger.info(
            f"Keithley 2400 configured for constant voltage mode: {bias_voltage} V, compliance: {compliance_current} A."
        )

    def read_resistance_direct(self) -> float:
        """Read the resistance directly from the Keithley 2400."""
        response = self.instrument.query(":READ?")
        return float(response.strip())  # Convert the response to float and return

    def read_current(self) -> float:
        """Read the current from the Keithley 2400 in constant voltage mode."""
        response = self.instrument.query(":READ?")
        return float(response.strip())  # Convert the response to float and return

    def turn_output_on(self) -> None:
        """Enables the output terminals of the SMU."""
        self.instrument.write(":OUTP ON")
        self.logger.info("SMU output turned ON.")

    def turn_output_off(self) -> None:
        """Disables the output terminals of the SMU for sample protection."""
        self.instrument.write(":OUTP OFF")
        self.logger.info("SMU output turned OFF.")

    def close_connection(self) -> None:
        """Safely turns off the output and closes the VISA connection."""
        self.turn_output_off()
        self.instrument.close()
        self.logger.info("Connection to Keithley 2400 closed.")

    # def get_reading(self):
    #     """Call this inside your 1s loop. No setup, just data."""
    #     try:
    #         $We use :FETCH? or :READ?
    #         $:READ? triggers a new acquisition
    #         raw_data = self.inst.query(":READ?")
    #         parts = raw_data.split(',')
    #         return float(parts[2]) if len(parts) >= 3 else None
    #     except Exception as e:
    #         return f"Error: {e}"

    # def close(self):
    #     self.inst.write(":OUTP OFF")
    #     self.inst.close()


# --- The Master Loop (Your 1s Data Acquisition) ---
if __name__ == "__main__":
    PORT = "ASRL/dev/cu.usbserial-120::INSTR"
    sensor = Keithley2400(PORT)

    # SETUP ONCE
    sensor.setup_resistance()

    print("\nStarting 1s logging loop. Press Ctrl+C to stop.\n")
    try:
        while True:
            start_time = time.time()

            value = sensor.get_reading()
            print(f"[{time.strftime('%H:%M:%S')}] Resistance: {value:.4e} Ohms")

            # Precise 1s timing (compensating for command execution time)
            elapsed = time.time() - start_time
            time.sleep(max(0, 1.0 - elapsed))

    except KeyboardInterrupt:
        print("\nStopping experiment...")

    finally:
        sensor.inst.write(":OUTP OFF")
        sensor.inst.close()
