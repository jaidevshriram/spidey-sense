from math import ceil
from typing import List
import serial
import serial.tools.list_ports
import minimalmodbus as modbus
from enum import IntEnum

def _fix_string_endianness(string):
    return ''.join(string[i:i+2][::-1] for i in range(0, len(string), 2))

class VibrationWaveforms(IntEnum):
    END_SEQUENCE = 0
    STRONG_CLICK_P100 = 1
    STRONG_CLICK_P60 = 2
    STRONG_CLICK_P30 = 3
    SHARP_CLICK_P100 = 4
    SHARP_CLICK_P60 = 5
    SHARP_CLICK_P30 = 6
    SOFT_BUMP_P100 = 7
    SOFT_BUMP_P60 = 8
    SOFT_BUMP_P30 = 9
    DOUBLE_CLICK_P100 = 10
    DOUBLE_CLICK_P60 = 11
    TRIPLE_CLICK_P100 = 12
    SOFT_FUZZ_P60 = 13
    STRONG_BUZZ_P100 = 14
    ALERT_750MS_P100 = 15
    ALERT_1000MS_P100 = 16
    STRONG_CLICK1_P100 = 17
    STRONG_CLICK2_P80 = 18
    STRONG_CLICK3_P60 = 19
    STRONG_CLICK4_P30 = 20
    MEDIUM_CLICK1_P100 = 21
    MEDIUM_CLICK2_P80 = 22
    MEDIUM_CLICK3_P60 = 23
    SHARP_TICK1_P100 = 24
    SHARP_TICK2_P80 = 25
    SHARP_TICK3_P60 = 26
    SHORT_DOUBLE_CLICK_STRONG1_P100 = 27
    SHORT_DOUBLE_CLICK_STRONG2_P80 = 28
    SHORT_DOUBLE_CLICK_STRONG3_P60 = 29
    SHORT_DOUBLE_CLICK_STRONG4_P30 = 30
    SHORT_DOUBLE_CLICK_MEDIUM1_P100 = 31
    SHORT_DOUBLE_CLICK_MEDIUM2_P80 = 32
    SHORT_DOUBLE_CLICK_MEDIUM3_P60 = 33
    SHORT_DOUBLE_SHARP_TICK1_P100 = 34
    SHORT_DOUBLE_SHARP_TICK2_P80 = 35
    SHORT_DOUBLE_SHARP_TICK3_P60 = 36
    LONG_DOUBLE_SHARP_CLICK_STRONG1_P100 = 37
    LONG_DOUBLE_SHARP_CLICK_STRONG2_P80 = 38
    LONG_DOUBLE_SHARP_CLICK_STRONG3_P60 = 39
    LONG_DOUBLE_SHARP_CLICK_STRONG4_P30 = 40
    LONG_DOUBLE_SHARP_CLICK_MEDIUM1_P100 = 41
    LONG_DOUBLE_SHARP_CLICK_MEDIUM2_P80 = 42
    LONG_DOUBLE_SHARP_CLICK_MEDIUM3_P60 = 43
    LONG_DOUBLE_SHARP_TICK1_P100 = 44
    LONG_DOUBLE_SHARP_TICK2_P80 = 45
    LONG_DOUBLE_SHARP_TICK3_P60 = 46
    BUZZ1_P100 = 47
    BUZZ2_P80 = 48
    BUZZ3_P60 = 49
    BUZZ4_P40 = 50
    BUZZ5_P20 = 51
    PULSING_STRONG1_P100 = 52
    PULSING_STRONG2_P60 = 53
    PULSING_MEDIUM1_P100 = 54
    PULSING_MEDIUM2_P60 = 55
    PULSING_SHARP1_P100 = 56
    PULSING_SHARP2_P60 = 57
    TRANSITION_CLICK1_P100 = 58
    TRANSITION_CLICK2_P80 = 59
    TRANSITION_CLICK3_P60 = 60
    TRANSITION_CLICK4_P40 = 61
    TRANSITION_CLICK5_P20 = 62
    TRANSITION_CLICK6_P10 = 63
    TRANSITION_HUM1_P100 = 64
    TRANSITION_HUM2_P80 = 65
    TRANSITION_HUM3_P60 = 66
    TRANSITION_HUM4_P40 = 67
    TRANSITION_HUM5_P20 = 68
    TRANSITION_HUM6_P10 = 69
    TRANSITION_RAMP_DOWN_LONG_SMOOTH1_P100_TO_P0 = 70
    TRANSITION_RAMP_DOWN_LONG_SMOOTH2_P100_TO_P0 = 71
    TRANSITION_RAMP_DOWN_MEDIUM_SMOOTH1_P100_TO_P0 = 72
    TRANSITION_RAMP_DOWN_MEDIUM_SMOOTH2_P100_TO_P0 = 73
    TRANSITION_RAMP_DOWN_SHORT_SMOOTH1_P100_TO_P0 = 74
    TRANSITION_RAMP_DOWN_SHORT_SMOOTH2_P100_TO_P0 = 75
    TRANSITION_RAMP_DOWN_LONG_SHARP1_P100_TO_P0 = 76
    TRANSITION_RAMP_DOWN_LONG_SHARP2_P100_TO_P0 = 77
    TRANSITION_RAMP_DOWN_MEDIUM_SHARP1_P100_TO_P0 = 78
    TRANSITION_RAMP_DOWN_MEDIUM_SHARP2_P100_TO_P0 = 79
    TRANSITION_RAMP_DOWN_SHORT_SHARP1_P100_TO_P0 = 80
    TRANSITION_RAMP_DOWN_SHORT_SHARP2_P100_TO_P0 = 81
    TRANSITION_RAMP_UP_LONG_SMOOTH1_P0_TO_P100 = 82
    TRANSITION_RAMP_UP_LONG_SMOOTH2_P0_TO_P100 = 83
    TRANSITION_RAMP_UP_MEDIUM_SMOOTH1_P0_TO_P100 = 84
    TRANSITION_RAMP_UP_MEDIUM_SMOOTH2_P0_TO_P100 = 85
    TRANSITION_RAMP_UP_SHORT_SMOOTH1_P0_TO_P100 = 86
    TRANSITION_RAMP_UP_SHORT_SMOOTH2_P0_TO_P100 = 87
    TRANSITION_RAMP_UP_LONG_SHARP1_P0_TO_P100 = 88
    TRANSITION_RAMP_UP_LONG_SHARP2_P0_TO_P100 = 89
    TRANSITION_RAMP_UP_MEDIUM_SHARP1_P0_TO_P100 = 90
    TRANSITION_RAMP_UP_MEDIUM_SHARP2_P0_TO_P100 = 91
    TRANSITION_RAMP_UP_SHORT_SHARP1_P0_TO_P100 = 92
    TRANSITION_RAMP_UP_SHORT_SHARP2_P0_TO_P100 = 93
    TRANSITION_RAMP_DOWN_LONG_SMOOTH1_P50_TO_P0 = 94
    TRANSITION_RAMP_DOWN_LONG_SMOOTH2_P50_TO_P0 = 95
    TRANSITION_RAMP_DOWN_MEDIUM_SMOOTH1_P50_TO_P0 = 96
    TRANSITION_RAMP_DOWN_MEDIUM_SMOOTH2_P50_TO_P0 = 97
    TRANSITION_RAMP_DOWN_SHORT_SMOOTH1_P50_TO_P0 = 98
    TRANSITION_RAMP_DOWN_SHORT_SMOOTH2_P50_TO_P0 = 99
    TRANSITION_RAMP_DOWN_LONG_SHARP1_P50_TO_P0 = 100
    TRANSITION_RAMP_DOWN_LONG_SHARP2_P50_TO_P0 = 101
    TRANSITION_RAMP_DOWN_MEDIUM_SHARP1_P50_TO_P0 = 102
    TRANSITION_RAMP_DOWN_MEDIUM_SHARP2_P50_TO_P0 = 103
    TRANSITION_RAMP_DOWN_SHORT_SHARP1_P50_TO_P0 = 104
    TRANSITION_RAMP_DOWN_SHORT_SHARP2_P50_TO_P0 = 105
    TRANSITION_RAMP_UP_LONG_SMOOTH1_P0_TO_P50 = 106
    TRANSITION_RAMP_UP_LONG_SMOOTH2_P0_TO_P50 = 107
    TRANSITION_RAMP_UP_MEDIUM_SMOOTH1_P0_TO_P50 = 108
    TRANSITION_RAMP_UP_MEDIUM_SMOOTH2_P0_TO_P50 = 109
    TRANSITION_RAMP_UP_SHORT_SMOOTH1_P0_TO_P50 = 110
    TRANSITION_RAMP_UP_SHORT_SMOOTH2_P0_TO_P50 = 111
    TRANSITION_RAMP_UP_LONG_SHARP1_P0_TO_P50 = 112
    TRANSITION_RAMP_UP_LONG_SHARP2_P0_TO_P50 = 113
    TRANSITION_RAMP_UP_MEDIUM_SHARP1_P0_TO_P50 = 114
    TRANSITION_RAMP_UP_MEDIUM_SHARP2_P0_TO_P50 = 115
    TRANSITION_RAMP_UP_SHORT_SHARP1_P0_TO_P50 = 116
    TRANSITION_RAMP_UP_SHORT_SHARP2_P0_TO_P50 = 117
    LONG_BUZZ_FOR_PROGRAMMATIC_STOPPING_P100 = 118
    SMOOTH_HUM_P50 = 119
    SMOOTH_HUM_P40 = 120
    SMOOTH_HUM_P30 = 121
    SMOOTH_HUM_P20 = 122
    SMOOTH_HUM_P10 = 123

    def Rest(seconds):
        if seconds > 1.270 or seconds < 0.0:
            raise ValueError("Seconds must be between 0.0 and 1.270")

        return 0x80 + int(round(seconds * 100.0))

class LedMode(IntEnum):
    OFF = 0
    GLOBAL_MANUAL = 1
    INDIVIDUAL_MANUAL = 2
    TRACK_THERMAL = 3
    BREATHE = 4

class VibrationMode(IntEnum):
    OFF = 0
    MANUAL = 1
    LIBRARY = 2
    SWEEP_FREQUENCY = 3
    SWEEP_INTENSITY = 4

class ThermalMode(IntEnum):
    OFF = 0
    MANUAL = 1
    TEMPERATURE_TARGET = 2


class Dot:
    class V63Registers():
        # RO
        DEVICE_NAME = 0
        HARDWARE_ID = 32
        FIRMWARE_ID = 64
        SERIAL_NUMBER = 96

        # RO
        SKIN_TEMP = 1000
        SINK_TEMP = 1002
        MCU_TEMP = 1004
        GATE_DRIVER_TEMP = 1006
        THERMAL_POWER = 1008

        # RW
        LED_MODE = 1010
        GLOBAL_MANUAL = 1012
        LED_INDIVIDUAL_MANUAL_0 = 1014
        LED_INDIVIDUAL_MANUAL_1 = 1016
        LED_INDIVIDUAL_MANUAL_2 = 1018
        LED_INDIVIDUAL_MANUAL_3 = 1020
        LED_INDIVIDUAL_MANUAL_4 = 1022
        LED_INDIVIDUAL_MANUAL_5 = 1024
        LED_INDIVIDUAL_MANUAL_6 = 1026
        LED_INDIVIDUAL_MANUAL_7 = 1028

        THERMAL_MODE = 1030
        THERMAL_INTENSITY = 1032
        THERMAL_SKIN_TEMP_TARGET = 1034

        VIBRATION_MODE = 1036
        VIBRATION_FREQUENCY = 1038
        VIBRATION_INTENSITY = 1040
        VIBRATION_GO = 1042
        VIBRATION_SEQUENCE_0123 = 1044
        VIBRATION_SEQUENCE_4567 = 1046

        def __init__(self, port, id):
            self.dev = modbus.Instrument(port, id, modbus.MODE_RTU)
            self.dev.serial.baudrate = 115200
            self.dev.serial.bytesize = 8    
            self.dev.serial.parity = serial.PARITY_NONE
            self.dev.serial.stopbits = 1

        def get_skin_temperature(self):
            """
            Get the skin temperature in Celsius.
            """
            return self.dev.read_float(self.SKIN_TEMP, 3, 2, modbus.BYTEORDER_LITTLE_SWAP)

        def get_sink_temperature(self):
            """
            Get the sink temperature in Celsius.
            """
            return self.dev.read_float(self.SINK_TEMP, 3, 2, modbus.BYTEORDER_LITTLE_SWAP)   

        def get_mcu_temperature(self):
            """
            Get the MCU temperature in Celsius.
            """
            return self.dev.read_float(self.MCU_TEMP, 3, 2, modbus.BYTEORDER_LITTLE_SWAP)

        def get_gate_driver_temperature(self):
            """
            Get the gate driver temperature in Celsius.
            """
            return self.dev.read_float(self.GATE_DRIVER_TEMP, 3, 2, modbus.BYTEORDER_LITTLE_SWAP) 

        def get_thermal_power(self):
            """
            Get the thermal power.
            """
            return self.dev.read_float(self.THERMAL_POWER, 3, 2, modbus.BYTEORDER_LITTLE_SWAP)

        def set_thermal_mode(self, mode: ThermalMode):
            """
            Set the thermal mode.
            """
            self.dev.write_long(registeraddress=self.THERMAL_MODE, value=int(mode), signed=False, byteorder=modbus.BYTEORDER_LITTLE_SWAP, number_of_registers=2)   

        def get_thermal_mode(self):
            """
            Get the thermal mode.
            """
            return ThermalMode(self.dev.read_long(self.THERMAL_MODE, 3, False, modbus.BYTEORDER_LITTLE_SWAP, 2))    

        def set_thermal_intensity(self, intensity: float):
            """
            Set the thermal intensity. The intensity is a float between -1 (maximum cooling) and 1 (maximum heating).
            """
            self.dev.write_float(registeraddress=self.THERMAL_INTENSITY, value=intensity, number_of_registers=2, byteorder=modbus.BYTEORDER_LITTLE_SWAP)

        def get_thermal_intensity(self):
            """
            Get the thermal intensity. The intensity is a float between -1 (maximum cooling) and 1 (maximum heating).
            """
            return self.dev.read_float(self.THERMAL_INTENSITY, 3, 2, modbus.BYTEORDER_LITTLE_SWAP)

        def set_skin_temp_target(self, temp: float):
            """
            Set the skin temperature target in Celsius.
            """
            self.dev.write_float(registeraddress=self.THERMAL_SKIN_TEMP_TARGET, value=temp, number_of_registers=2, byteorder=modbus.BYTEORDER_LITTLE_SWAP)

        def get_thermal_skin_temp_target(self):
            """
            Get the skin temperature target in Celsius.
            """
            return self.dev.read_float(self.THERMAL_SKIN_TEMP_TARGET, 3)  


        def set_vibration_mode(self, mode: VibrationMode):
            """
            Set the vibration mode.
            """
            self.dev.write_long(registeraddress=self.VIBRATION_MODE, value=int(mode), signed=False, byteorder=modbus.BYTEORDER_LITTLE_SWAP, number_of_registers=2)

        def get_vibration_mode(self) -> VibrationMode:
            """
            Get the vibration mode.
            """ 
            return VibrationMode(self.dev.read_long(self.VIBRATION_MODE, 3, False, modbus.BYTEORDER_LITTLE_SWAP, 2))

        def get_vibration_amplitude(self):
            """
            Get the vibration amplitude.
            """ 
            return self.dev.read_float(self.VIBRATION_AMPLITUDE, 3) 

        def set_vibration_frequency(self, frequency: float):
            """
            Set the vibration frequency.
            """
            self.dev.write_float(registeraddress=self.VIBRATION_FREQUENCY, value=frequency, number_of_registers=2, byteorder=modbus.BYTEORDER_LITTLE_SWAP)

        def get_vibration_frequency(self):
            """
            Get the vibration frequency.
            """
            return self.dev.read_float(self.VIBRATION_FREQUENCY, 3, 2, modbus.BYTEORDER_LITTLE_SWAP)

        def set_vibration_intensity(self, intensity: float):
            """
            Set the vibration intensity.
            """
            self.dev.write_float(registeraddress=self.VIBRATION_INTENSITY, value=intensity, number_of_registers=2, byteorder=modbus.BYTEORDER_LITTLE_SWAP) 

        def get_vibration_intensity(self):
            """
            Get the vibration intensity.
            """
            return self.dev.read_float(self.VIBRATION_INTENSITY, 3, 2, modbus.BYTEORDER_LITTLE_SWAP)

        def set_vibration_go(self, go: bool):
            """
            Set the vibration go.
            """
            self.dev.write_long(registeraddress=self.VIBRATION_GO, value=int(go), signed=False, byteorder=modbus.BYTEORDER_LITTLE_SWAP, number_of_registers=2)

        def get_vibration_go(self):
            """
            Get the vibration go.
            """
            return self.dev.read_long(self.VIBRATION_GO, 3, False, modbus.BYTEORDER_LITTLE_SWAP, 2)

        def set_vibration_sequence_0123(self, value: int):
            self.dev.write_long(registeraddress=self.VIBRATION_SEQUENCE_0123, value=value, signed=False, byteorder=modbus.BYTEORDER_LITTLE_SWAP, number_of_registers=2)
        
        def get_vibration_sequence_0123(self):
            return self.dev.read_long(self.VIBRATION_SEQUENCE_0123, 3, False, modbus.BYTEORDER_LITTLE_SWAP, 2) 

        def set_vibration_sequence_3456(self, value: int):
            self.dev.write_long(registeraddress=self.VIBRATION_SEQUENCE_4567, value=value, signed=False, byteorder=modbus.BYTEORDER_LITTLE_SWAP, number_of_registers=2)

        def get_vibration_sequence_3456(self):
            return self.dev.read_long(self.VIBRATION_SEQUENCE_4567, 3, False, modbus.BYTEORDER_LITTLE_SWAP, 2)

        def set_led_mode(self, mode: LedMode):
            """
            Set the LED mode.
            """
            self.dev.write_long(registeraddress=self.LED_MODE, value=int(mode), signed=False, byteorder=modbus.BYTEORDER_LITTLE_SWAP, number_of_registers=2)

        def get_led_mode(self):
            """
            Get the LED mode.
            """
            return LedMode(self.dev.read_long(self.LED_MODE, 3, False, modbus.BYTEORDER_LITTLE_SWAP, 2))

        def set_global_led(self, red, green, blue):
            """
            Set the global LED color. Red, green and blue are between 0 and 255. Only valid when LED mode is GLOBAL_MANUAL.
            """
            val = (blue << 16) | (red << 8) | green
            self.dev.write_long(registeraddress=self.GLOBAL_MANUAL, value=val, signed=False, byteorder=modbus.BYTEORDER_LITTLE_SWAP, number_of_registers=2)

        def get_global_led(self):
            """
            Get the global LED color.
            """
            # val = (blue << 16) | (red << 8) | green
            val = self.dev.read_long(self.GLOBAL_MANUAL, 3, False, modbus.BYTEORDER_LITTLE_SWAP, 2)
            green = val & 0xFF
            red = (val >> 8) & 0xFF
            blue = (val >> 16) & 0xFF
            return red, green, blue
            
        def set_individual_led(self, index, red, green, blue):
            """
            Set the individual LED color. Red, green and blue are between 0 and 255. Only valid when LED mode is INDIVIDUAL_MANUAL.
            """
            val = (blue << 16) | (red << 8) | green
            self.dev.write_long(registeraddress=self.LED_INDIVIDUAL_MANUAL_0 + index * 2, value=val, signed=False, byteorder=modbus.BYTEORDER_LITTLE_SWAP, number_of_registers=2)

        def get_individual_led(self, index):
            """
            Get the individual LED color.
            """
            val = self.dev.read_long(self.LED_INDIVIDUAL_MANUAL_0 + index * 2, 3, False, modbus.BYTEORDER_LITTLE_SWAP, 2)
            green = val & 0xFF
            red = (val >> 8) & 0xFF
            blue = (val >> 16) & 0xFF
            return red, green, blue
    

    ## high level methods
    def set_vibration_sequence(self, sequence: List[VibrationWaveforms]):
        """
        Set the vibration sequence, with up to 8 waveforms.
        """
        if(len(sequence) > 8):
            raise ValueError("Sequence must be 8 waveforms or less")

        sequence_words = [0, 0]
        # walk through the sequence and convert each waveform into a byte. Then combine the bytes together into two 32-bit words
        for i in range(0, len(sequence)):
            sequence_words[i // 4] |= sequence[i] << (i % 4) * 8

        # write the sequence words to the device
        self.registers.set_vibration_sequence_0123(sequence_words[0])
        self.registers.set_vibration_sequence_3456(sequence_words[1])

    def start_vibration_sequence(self):
        self.registers.set_vibration_go(True)
    
    def stop_vibration(self):
        self.registers.set_vibration_go(False) # doesn't do anything (yet)
        self.registers.set_vibration_intensity(0.0)

    def is_vibration_sequence_playing(self) -> bool:
        return self.registers.get_vibration_go()

    def play_vibration_sequence(self, sequence: List[VibrationWaveforms]):
        self.registers.set_vibration_mode(VibrationMode.LIBRARY)
        self.set_vibration_sequence(sequence)
        self.start_vibration_sequence()

    def play_frequency(self, frequency: float, intensity: float):
        self.registers.set_vibration_mode(VibrationMode.MANUAL)
        self.registers.set_vibration_frequency(frequency)
        self.registers.set_vibration_intensity(intensity)

    def set_led(self, red: int = None, green: int = None, blue: int = None, index: int = None):
        """
        Set the LED color. If index is None, the color is set for all LEDs. If index is not None, the color is set for the specified LED.
        """
        if index is None:
            self.registers.set_led_mode(LedMode.GLOBAL_MANUAL)
            self.registers.set_global_led(red, green, blue)
        else:
            self.registers.set_led_mode(LedMode.INDIVIDUAL_MANUAL)
            self.registers.set_individual_led(index, red, green, blue)

    def set_led_breathe(self):
        """
        Set the LED to breathe.
        """
        self.registers.set_led_mode(LedMode.BREATHE)

    def set_led_off(self):
        """
        Set the LED to off.
        """
        self.registers.set_led_mode(LedMode.OFF)

    def activate_thermal_intensity_control(self, intensity: float):
        """
        Activate the thermal intensity control. Intensity is a float between -1 (maximum cooling) and 1 (maximum heating).
        """
        if intensity < -1.0 or intensity > 1.0:
            raise ValueError("Intensity must be between -1 and 1")
        self.registers.set_thermal_mode(ThermalMode.MANUAL)
        self.registers.set_thermal_intensity(intensity)

    def disable_all_thermal(self):
        """
        Deactivate the thermal.
        """
        self.registers.set_thermal_mode(ThermalMode.OFF)

    def activate_thermal_temperature_control(self, temperature: float):
        """
        Activate the thermal temperature control.
        """
        self.registers.set_thermal_mode(ThermalMode.TEMPERATURE_TARGET)
        self.registers.set_thermal_skin_temp_target(temperature)

    def get_skin_temperature(self):
        """
        Get the skin temperature in Celsius.
        """
        return self.registers.get_skin_temperature()

    def get_heatsink_temperature(self):
        """
        Get the heatsink temperature in Celsius.
        """
        return self.registers.get_sink_temperature()

    def __init__(self, port, id):
        self.port = port
        self.id = id
        self.registers = self.V63Registers(port, id)
        self.device_name = _fix_string_endianness(self.registers.dev.read_string(self.V63Registers.DEVICE_NAME,32, 3))
        self.hardware_id = _fix_string_endianness(self.registers.dev.read_string(self.V63Registers.HARDWARE_ID, 32, 3))
        self.firmware_id = _fix_string_endianness(self.registers.dev.read_string(self.V63Registers.FIRMWARE_ID, 32, 3)) 
        self.serial_number = _fix_string_endianness(self.registers.dev.read_string(self.V63Registers.SERIAL_NUMBER, 32, 3))

    def __str__(self):
        return f"Dot {self.id} (Name = {self.device_name}, Hardware ID = {self.hardware_id}, Firmware ID = {self.firmware_id}, Serial Number = {self.serial_number})"


def discover_devices(maxAddress) -> List[Dot]:
    """
    Discover all DataFeel Devices connected to the computer.
    """
    devices = []
    # first, find the serial port the Dot is connected to
    for port in serial.tools.list_ports.comports():
        if port.vid == 0x10c4 and port.pid == 0xea60:
            print("found DataFeel Device on port", port.device)
            
            for x in range(1, maxAddress + 1):
                try:
                    dot = Dot(port.device, x)
                    devices.append(dot)
                except Exception as e:
                    print(f"No device at address {x}")
            break
    return devices