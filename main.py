import time, ntptime, esp32, machine
from machine import Pin, I2C, ADC, deepsleep, reset_cause, wake_reason, DEEPSLEEP_RESET, EXT0_WAKE, WDT
import sh1106, wifi, sht41, soil_moisture_sensor, ota
import svensk_tid as sv
import API_ThingSpeak as API

# ---- KONFIG ----
NORMAL_INTERVAL_MIN = 10       # 10 minuter
FAN_RUN_SECONDS = 300          # 5 minuter
FAN_SAMPLE_INTERVAL = 15       # sekunder under fläktkörning
SAFETY_MARGIN = 10             # sekunder

SOIL_WATER_THRESHOLD = 75.0   # %
PUMP_RUN_SECONDS = 10          # sekunder

wake_times = [(9, 5), (13, 5), (18, 5)] # (timme, minut) för fläktkörningar

# ---- Fläkt (MOSFET Gate) ----
fan_pin = Pin(19, Pin.OUT)
fan_pin.value(0)

# ---- Pump (MOSFET Gate)----
pump_pin = 23


def run_pump(seconds):
    print("Startar pump i", seconds, "sekunder")
    pump_pin.value(1)
    time.sleep(seconds)
    pump_pin.value(0)
    print("Pump stoppad")

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


# ---- WATCHDOG ---- 
wdt = WDT(timeout=20000) #Initiera watchdog (20 sekunder är ganska säkert för dina mätningar)

# ---- MAIN ----
def main():
    time.sleep(0.5)
    print("Main startar...")
    wdt.feed()
    
    # --- re-initiera GPIO efter deepsleep ---
    global pump_pin
    global PUMP_RUN_SECONDS
    pump_pin = Pin(23, Pin.OUT)
    pump_pin.value(0)   # säker OFF
    time.sleep_ms(50)   # ge MOSFET-gaten tid att stabiliseras
    
    # ---- Knappväckning ----
    if reset_cause() == DEEPSLEEP_RESET and wake_reason() == EXT0_WAKE:
        temp_in, rh_in = read_sht41_in(sht_in)
        temp_ut, rh_ut = read_sht41_ut(sht_ut)
        jf = None
        oled.show_orkide_view(temp_in, rh_in, jf)
        jf = read_csms(15)
        oled.show_orkide_view(temp_in, rh_in, jf)
        wdt.feed()
        print("Knapp-väckning: T_in:{:.2f}C | Rh_in:{:.2f}% | Jf:{:.1f}% | T_ut:{:.2f}C | Rh_ut:{:.2f}%".format(temp_in, rh_in, jf, temp_ut, rh_ut))
        delta_temp = abs(temp_in - temp_ut)
        delta_rh = abs(rh_in - rh_ut)
        visningstid = 10 # hur länge ska skärmen vara igång
        t_slut = time.time() + visningstid
        runda = 0
        while time.time() < t_slut:
            if btn.value() == 0 and runda == 0:
                oled.show_hus_view(temp_ut, delta_temp, rh_ut, delta_rh)
                t_slut = time.time() + visningstid
                runda = 1
                time.sleep(0.5)
            if btn.value() == 0 and runda == 1:
                oled.show_orkide_view(temp_in, rh_in, jf)
                t_slut = time.time() + visningstid
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
        time.sleep(1) # behövs verkligen denna?
        ota.check_and_update()
        
        vattnat = 0
        if jf is not None and jf < SOIL_WATER_THRESHOLD:
            print("Knappväckning: jorden torr → vattnar")
            run_pump(PUMP_RUN_SECONDS)
            vattnat = PUMP_RUN_SECONDS
        
        
        API.send_data_base(temp_in, rh_in, jf, temp_ut, rh_ut, vattnat)
        wdt.feed()
        swe_now = sv.get_swedish_time()
        year, month, day, hour, minute, second, _, _ = swe_now
        tid, datum = sv.format_datetime(swe_now)
        info = """
                    Klockan är: {},
                    Dagens datum: {}
            """.format(tid, datum)
        print(info)
        print("Beräknar nästa wake-up efter knapptryck...")

        now_secs = hour * 3600 + minute * 60 + second
        today_secs = [h * 3600 + m * 60 for h, m in wake_times]
        future_secs = [t for t in today_secs if t > now_secs]

        if future_secs:
            next_secs = future_secs[0]
        else:
            next_secs = today_secs[0] + 24 * 3600  # nästa dags första tid

        sleep_time = next_secs - now_secs
        sleep_time = max(0, sleep_time - SAFETY_MARGIN)  # marginal
        # Se till att inte sova längre än normalmätningen om ingen fläkt snart
        sleep_time = min(sleep_time, NORMAL_INTERVAL_MIN * 60)

        print("Sover i", sleep_time, "sekunder (efter knappväckning).")
        deepsleep(sleep_time * 1000)


    if not wifi.connect_wifi():
        print("WiFi misslyckades")
        return
    wdt.feed()
    get_ntptime_safe()
    wdt.feed()
    time.sleep(1)
    ota.check_and_update()
    
    swe_now = sv.get_swedish_time()
    year, month, day, hour, minute, second, _, _ = swe_now
    tid, datum = sv.format_datetime(swe_now)
    info = """
                    Klockan är: {},
                    Dagens datum: {}
        """.format(tid, datum)
    print(info)

    # ---- Fläktfönster? ----
    in_fan_window = False
    for (wake_hour, wake_minute) in wake_times:
        if wake_hour == hour and abs(minute - wake_minute) <= 1:
            in_fan_window = True
            break

    if in_fan_window:
        print("Startar fläkt")
        fan_pin.value(1)
        t_end = time.time() + FAN_RUN_SECONDS
        while time.time() < t_end:
            temp_in, rh_in = read_sht41_in(sht_in)
            temp_ut, rh_ut = read_sht41_ut(sht_ut)
            delta_temp = abs(temp_in - temp_ut)
            delta_rh = abs(rh_in - rh_ut)
            oled.show_fan_view(temp_in, delta_temp, rh_in, delta_rh)
            print("Normal-mätning: T_in:{:.2f}C | Rh_in:{:.2f}% | T_ut:{:.2f}C | Rh_ut:{:.2f}%".format(temp_in, rh_in, temp_ut, rh_ut))
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
        temp_in, rh_in = read_sht41_in(sht_in)
        temp_ut, rh_ut = read_sht41_ut(sht_ut)
        jf = read_csms(25)
        wdt.feed()
        print("Normal-mätning: T_in:{:.2f}C | Rh_in:{:.2f}% | Jf:{:.1f}% | T_ut:{:.2f}C | Rh_ut:{:.2f}%".format(temp_in, rh_in, jf, temp_ut, rh_ut))
        
        vattnat = 0
        if jf is not None and jf < SOIL_WATER_THRESHOLD:
            print("Normalmätning: jorden torr → vattnar")
            run_pump(PUMP_RUN_SECONDS)
            vattnat = PUMP_RUN_SECONDS
        print("vattnat=", vattnat)
        API.send_data_base(temp_in, rh_in, jf, temp_ut, rh_ut, vattnat)
        wdt.feed()

    # --- Räkna ut nästa wake-up ---
    swe_now = sv.get_swedish_time()
    year, month, day, hour, minute, second, _, _ = swe_now
    tid, datum = sv.format_datetime(swe_now)
    info = """
                    Klockan är: {},
                    Dagens datum: {}
        """.format(tid, datum)
    print(info)
    
    now_secs = hour * 3600 + minute * 60 + second
    today_secs = [h * 3600 + m * 60 for h, m in wake_times]
    future_secs = [t for t in today_secs if t > now_secs]

    if future_secs:
        next_secs = future_secs[0]
    else:
        next_secs = today_secs[0] + 24 * 3600  # nästa dags första tid

    sleep_time = next_secs - now_secs
    sleep_time = max(0, sleep_time - SAFETY_MARGIN)  # marginal
    sleep_time = min(sleep_time, NORMAL_INTERVAL_MIN * 60)

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

