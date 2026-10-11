from time import sleep
from datafeel.device import VibrationMode, discover_devices, LedMode, ThermalMode, VibrationWaveforms
devices = discover_devices(4)

device = devices[0]

print("found", len(devices), "devices")
print(device)
print("Reading all data...")
print("skin temperature:", device.registers.get_skin_temperature())
print("sink temperature:", device.registers.get_sink_temperature())
print("mcu temperature:", device.registers.get_mcu_temperature())
print("gate driver temperature:", device.registers.get_gate_driver_temperature())
print("thermal power:", device.registers.get_thermal_power())
print("thermal mode:", device.registers.get_thermal_mode())
print("thermal intensity:", device.registers.get_thermal_intensity())
print("vibration mode:", device.registers.get_vibration_mode())
print("vibration frequency:", device.registers.get_vibration_frequency())
print("vibration intensity:", device.registers.get_vibration_intensity())
print("vibration go:", device.registers.get_vibration_go())
print("vibration sequence 0123:", device.registers.get_vibration_sequence_0123())
print("vibration sequence 3456:", device.registers.get_vibration_sequence_3456())

# Set the global LED color
device.set_led(255, 0, 0)
sleep(1)

# Set the color of a specific LED
device.set_led(255, 0, 0, 5)
sleep(1)

# Set the LED to breathe mode
device.set_led_breathe()
sleep(1)

device.set_led_off()

# play an A major scale
frequencies = [110, 123, 139, 147, 165, 185, 208, 220]

device.registers.set_vibration_mode(VibrationMode.MANUAL)   
for frequency in frequencies:
    device.play_frequency(frequency, 1.0)
    sleep(0.5)

vibration_sequence = [
    VibrationWaveforms.STRONG_BUZZ_P100,
    VibrationWaveforms.Rest(0.5),
    VibrationWaveforms.TRANSITION_HUM1_P100,
    VibrationWaveforms.Rest(0.5),
    VibrationWaveforms.TRANSITION_RAMP_DOWN_MEDIUM_SHARP2_P50_TO_P0,
    VibrationWaveforms.TRANSITION_RAMP_UP_MEDIUM_SHARP2_P0_TO_P50,
    VibrationWaveforms.DOUBLE_CLICK_P100,
    VibrationWaveforms.TRANSITION_RAMP_UP_SHORT_SMOOTH2_P0_TO_P100,
]

device.play_vibration_sequence(vibration_sequence)
sleep(3)
device.set_vibration_sequence(vibration_sequence)
sleep(3)

device.play_frequency(250, 1.0)

device.stop_vibration()

sleep(1)

device.set_led(0, 255, 0)
print("global led:", device.registers.get_global_led())
sleep(1)

device.registers.set_led_mode(LedMode.BREATHE)
sleep(1)

device.activate_thermal_intensity_control(1.0) # Heating
sleep(5)
device.activate_thermal_intensity_control(-1.0) # Cooling
sleep(5)
device.disable_all_thermal()

# If you don't like the high-level API, you can use the low-level API for any of the features

# Thermal Low-Level API
device.registers.set_thermal_mode(ThermalMode.MANUAL)
device.registers.set_thermal_intensity(1.0)
print("\r\n\r\nHeating for 10 seconds...\r\n\r\n")

for i in range(20):
    print("skin temperature:", device.registers.get_skin_temperature())
    print("thermal power:", device.registers.get_thermal_power())
    sleep(0.5)

device.registers.set_thermal_intensity(-1.0)
print("\r\n\r\nCooling for 10 seconds...\r\n\r\n")
for i in range(20):
    print("skin temperature:", device.registers.get_skin_temperature())
    print("thermal power:", device.registers.get_thermal_power())
    sleep(0.5)
device.registers.set_thermal_mode(ThermalMode.OFF)



# Vibration Low-Level API
device.registers.set_vibration_mode(VibrationMode.MANUAL)
device.registers.set_vibration_frequency(200)
device.registers.set_vibration_intensity(1.0)
sleep(3)

# LED Low-Level API
device.registers.set_led_mode(LedMode.INDIVIDUAL_MANUAL)
device.registers.set_individual_led(0, 255, 0, 0)
sleep(.5)
device.registers.set_individual_led(1, 0, 255, 0)
sleep(.5)
device.registers.set_individual_led(2, 0, 0, 255)
sleep(.5)
device.registers.set_individual_led(3, 255, 255, 0)
sleep(.5)
device.registers.set_individual_led(4, 255, 0, 255)
sleep(.5)
device.registers.set_individual_led(5, 0, 255, 255)
sleep(.5)
device.registers.set_individual_led(6, 255, 255, 255)
sleep(.5)
device.registers.set_individual_led(7, 255, 255, 255)