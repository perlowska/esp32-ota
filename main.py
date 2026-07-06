# v.12
import time, ntptime, esp32, machine
from machine import Pin, I2C, ADC, deepsleep, reset_cause, wake_reason, DEEPSLEEP_RESET, EXT0_WAKE, WDT
import sh1106, wifi, sht41, soil_moisture_sensor, ota
import svensk_tid as sv
import API_ThingSpeak as API


# ---- KONFIG ----
SCREEN_TIME = 10               # secunder som skärmen ska vara igång
NORMAL_INTERVAL_MIN = 10       # 10 minuter
FAN_RUN_SECONDS = 300          # 5 minuter
FAN_SAMPLE_INTERVAL = 15       # sekunder under fläktkörning
SAFETY_MARGIN = 10             # sekunder

USE_WDT = True
WATERING = True

SOIL_WATER_THRESHOLD = 75.0   # %
UPPER_JF_THRESHOLD = 90.0     # %
PUMP_RUN_SECONDS = 10         # sekunder
WAIT_FOR_MOISTURE_SECS = 10   # sekunder
WATERING_ITERATIONS = 4

wake_times = [(9, 5), (13, 5), (18, 5)] # (timme, minut) för fläktkörningar



# ---- Fläkt (MOSFET Gate) ----
fan_pin = Pin(19, Pin.OUT)
fan_pin.value(0)

# ---- Pump (MOSFET Gate)----
pump_gpio = 23

# ---- KNAPP för wake-up ----
btn = Pin(4, Pin.IN, Pin.PULL_UP)      # välj GPIO som stöds för EXT0
esp32.wake_on_ext0(pin=btn, level=0)    # väck på låg nivå

# ---- CSMS (jordfuktighet) ----
soil = ADC(Pin(34))
csms = soil_moisture_sensor.CSMS(soil, min_value=3269, max_value=1807)

# ---- I2C (SHT41, oled) ----
i2c0 = I2C(0, scl=Pin(32), sda=Pin(33)) # SHT41 inne och OLED
i2c1 = I2C(1, scl=Pin(25), sda=Pin(26)) # SHT41 ute
sht_in = sht41.SHT41(i2c0)
sht_ut = sht41.SHT41(i2c1)
oled = sh1106.SH1106_I2C(128, 64, i2c0)



# ---- WATCHDOG ---- 
if USE_WDT:
    from machine import WDT
    wdt = WDT(timeout=20000)
else: # --- Dummy WDT ----
    class DummyWDT:
        def feed(self, t=None):
            pass
    wdt = DummyWDT()
    
watering_wdt_time = (PUMP_RUN_SECONDS + WAIT_FOR_MOISTURE_SECS + 1) * 1000

# ---- säker NTP-hämtning ----
def get_ntptime_safe():
    try:
        ntptime.settime()
        print("Tid uppdaterad från NTP")
    except Exception as e:
        print("NTP error:", e)

# ---- säker sensor-läsning ---
""" Returnerar värde eller None """
def read_sht41_in(sht_in):
    try:
        temp_in, rh_in = sht_in.read()
        return temp_in, rh_in
    except Exception as e:
        print("SHT41_inne error:", e)
        return None, None

def read_sht41_ut(sht_ut):
    try:
        temp_ut, rh_ut = sht_ut.read()
        return temp_ut, rh_ut
    except Exception as e:
        print("SHT41_ute error:", e)
        return None, None
    
def read_csms(iterations=25):
    try:
        return csms.read(iterations)
    except Exception as e:
        print("CSMS error:", e)
        return None

def start_sensor_oled(mode): 
    jf_iter = 15 if mode == "Knappväckning" else 25
    knapp = True if mode == "Knappväckning" else False
    temp_in, rh_in = read_sht41_in(sht_in)
    temp_ut, rh_ut = read_sht41_ut(sht_ut)
    jf = None
    if knapp: oled.show_orkide_view(temp_in, rh_in, jf)
    jf = read_csms(jf_iter)
    if knapp: oled.show_orkide_view(temp_in, rh_in, jf)
    wdt.feed()
    print(mode, ": T_in:{:.2f}C | Rh_in:{:.2f}% | Jf:{:.1f}% | T_ut:{:.2f}C | Rh_ut:{:.2f}%".format(temp_in, rh_in, jf, temp_ut, rh_ut))
    return temp_in, rh_in, temp_ut, rh_ut, jf

# --- Pump ---
def run_pump(pump_pin, jf, mode):
    vattnat = 0
    try:
        if WATERING:
            if jf is not None and jf < SOIL_WATER_THRESHOLD:
                print(mode, ": jorden torr → vattnar")
                curr_water_iter = 0
                while curr_water_iter < WATERING_ITERATIONS:
                    wdt.feed(watering_wdt_time)
                    curr_water_iter += 1
                    print("Startar pump i", PUMP_RUN_SECONDS, "sekunder")
                    pump_pin.value(1)
                    time.sleep(PUMP_RUN_SECONDS)
                    pump_pin.value(0)
                    time.sleep(WAIT_FOR_MOISTURE_SECS) # vänta på att vattnet tagit sig till jordsensorn
                    vattnat += PUMP_RUN_SECONDS
                    jf = read_csms()
                    print("Jordfuktighet: ", jf, "%")
                    if jf >= UPPER_JF_THRESHOLD:
                        break  
                print("Pump klar, stoppad")
    except Exception as e:
        print("Pump error:", e)
    return vattnat



# ---- MAIN ----
def main():
    time.sleep(0.5)
    print("Main startar...")
    wdt.feed()
    
    # --- re-initiera GPIO efter deepsleep ---
    pump_pin = Pin(pump_gpio, Pin.OUT)
    pump_pin.value(0)   # säker OFF
    time.sleep_ms(50)   # ge MOSFET-gaten tid att stabiliseras
    mode = "Normalmätning"
    # ---- Knappväckning ----
    if reset_cause() == DEEPSLEEP_RESET and wake_reason() == EXT0_WAKE:
        mode = "Knappväckning"
        temp_in, rh_in, temp_ut, rh_ut, jf = start_sensor_oled(mode)
        t_slut = time.time() + SCREEN_TIME
        runda = 0
        while time.time() < t_slut:
            if btn.value() == 0 and runda == 0:
                oled.show_delta_view(mode, temp_in, temp_ut, rh_in, rh_ut)
                t_slut = time.time() + SCREEN_TIME
                runda = 1
                time.sleep(0.5)
            if btn.value() == 0 and runda == 1:
                oled.show_orkide_view(temp_in, rh_in, jf)
                t_slut = time.time() + SCREEN_TIME
                runda = 0
                time.sleep(0.5)
            time.sleep(0.1)
            wdt.feed()
            
        oled.poweroff()


    if not wifi.connect_wifi():
        print("WiFi misslyckades")
        return
    wdt.feed()

    get_ntptime_safe()
    wdt.feed()
    time.sleep(1)
    ota.check_and_update()
    sv.format_datetime_print()

    # ---- Fläktfönster? ----
    in_fan_window = sv.in_time_window(wake_times)

    if in_fan_window:
        print("Startar fläkt")
        fan_pin.value(1)
        t_end = time.time() + FAN_RUN_SECONDS
        while time.time() < t_end:
            temp_in, rh_in = read_sht41_in(sht_in)
            temp_ut, rh_ut = read_sht41_ut(sht_ut)
            oled.show_delta_view(mode, temp_in, temp_ut, rh_in, rh_ut)
            print(mode, ": T_in:{:.2f}C | Rh_in:{:.2f}% | T_ut:{:.2f}C | Rh_ut:{:.2f}%".format(temp_in, rh_in, temp_ut, rh_ut))
            
            API.send_data_fan(temp_in, rh_in, temp_ut, rh_ut)
            wdt.feed()
            time.sleep(FAN_SAMPLE_INTERVAL)
            wdt.feed()
        fan_pin.value(0)
        wdt.feed()
        oled.poweroff()
        print("Stänger fläkt")
    else:
        # Normalmätning
        if mode == "Normalmätning": # dvs om mätningar ej gjorts vid knapptryck
            temp_in, rh_in, temp_ut, rh_ut, jf = start_sensor_oled(mode)

        vattnat = run_pump(pump_pin, jf, mode)

        wdt.feed()
        API.send_data_base(temp_in, rh_in, jf, temp_ut, rh_ut, vattnat)
        wdt.feed()

    # --- Räkna ut nästa wake-up ---
    sv.format_datetime_print()

    sleep_time = sv.sleep_time(wake_times, SAFETY_MARGIN, NORMAL_INTERVAL_MIN)
    print("Sover i", sleep_time, "sekunder (med marginal).")
    deepsleep(sleep_time * 1000)


def safe_main():
    """Kör main() men fånga oväntade fel och resetta ESP32."""
    try:
        main()
    except Exception as e:
        print("Fatal error:", e)
        time.sleep(5)
        machine.reset()


# Kör safe_main när programmet startar
while True:
    safe_main()
    wdt.feed()





