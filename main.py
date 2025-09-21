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

wake_times = [(9, 5), (13, 5), (18, 5)] # (timme, minut) för fläktkörningar

# ---- Fläkt (MOSFET Gate) ----
fan_pin = Pin(19, Pin.OUT)

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
    
    # ---- Knappväckning ----
    if reset_cause() == DEEPSLEEP_RESET and wake_reason() == EXT0_WAKE:
        temp_in, rh_in = read_sht41_in(sht_in)
        temp_ut, rh_ut = read_sht41_ut(sht_ut)
        oled.fill(0)
        oled.draw_bitmap(sh1106.orkide_data, sh1106.orkide_w, sh1106.orkide_h, 0, 5)
        oled.draw_bitmap(sh1106.termo_data, sh1106.termo_w, sh1106.termo_h, 60, 3)
        oled.draw_bitmap(sh1106.droppe_data, sh1106.droppe_w, sh1106.droppe_h, 60, 27)
        oled.draw_bitmap(sh1106.jord_data, sh1106.jord_w, sh1106.jord_h, 60, 48)
        #oled.show()
        #time.sleep(1.5)
        oled.text_with_degree("", temp_in, 71, 10)
        oled.text_with_percent("", rh_in, 71, 30)
        oled.show()
        jf = read_csms(15)
        oled.text_with_percent("", jf, 71, 50)
        oled.show()
        wdt.feed()
        #print("Knapp-väckning:", temp_in, rh_in, jf, temp_ut, rh_ut)
        print("Knapp-väckning: T_in:{:.2f}C | Rh_in:{:.2f}% | Jf:{:.1f}% | T_ut:{:.2f}C | Rh_ut:{:.2f}%".format(temp_in, rh_in, jf, temp_ut, rh_ut))
        delta_temp = abs(temp_in - temp_ut)
        delta_rh = abs(rh_in - rh_ut)
        visningstid = 10 # hur länge ska skärmen vara igång
        t_slut = time.time() + visningstid
        runda = 0
        while time.time() < t_slut:
            if btn.value() == 0 and runda == 0:
                oled.fill(0)
                oled.draw_bitmap(sh1106.hus_data, sh1106.hus_w, sh1106.hus_h, 0, 13)
                oled.draw_bitmap(sh1106.termo_data, sh1106.termo_w, sh1106.termo_h, 42, 2)
                oled.text_with_degree("", temp_ut, 55, 5)
                oled.draw_bitmap(sh1106.delta_lm_data, sh1106.delta_lm_w, sh1106.delta_lm_h, 56, 17)
                oled.text_with_degree("", delta_temp, 79, 20)
                oled.draw_bitmap(sh1106.droppe_data, sh1106.droppe_w, sh1106.droppe_h, 42, 35)
                oled.text_with_percent("", rh_ut, 55, 38)
                oled.draw_bitmap(sh1106.delta_lm_data, sh1106.delta_lm_w, sh1106.delta_lm_h, 56, 51)
                oled.text_with_percent("", delta_rh, 79, 54)
                oled.show()
                t_slut = time.time() + visningstid
                runda = 1
                time.sleep(0.5)
            if btn.value() == 0 and runda == 1:
                oled.fill(0)
                oled.draw_bitmap(sh1106.orkide_data, sh1106.orkide_w, sh1106.orkide_h, 0, 5)
                oled.draw_bitmap(sh1106.termo_data, sh1106.termo_w, sh1106.termo_h, 60, 3)
                oled.draw_bitmap(sh1106.droppe_data, sh1106.droppe_w, sh1106.droppe_h, 60, 27)
                oled.draw_bitmap(sh1106.jord_data, sh1106.jord_w, sh1106.jord_h, 60, 48)
                oled.text_with_degree("", temp_in, 71, 10)
                oled.text_with_percent("", rh_in, 71, 30)    
                oled.text_with_percent("", jf, 71, 50)
                oled.show()
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
        
        API.send_data_base(temp_in, rh_in, jf, temp_ut, rh_ut)
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
            oled.fill(0)
            oled.draw_bitmap(sh1106.fläkt_data, sh1106.fläkt_w, sh1106.fläkt_h, 0, 10)
            oled.draw_bitmap(sh1106.termo_data, sh1106.termo_w, sh1106.termo_h, 49, 2)
            oled.text_with_degree("", temp_in, 62, 5)
            oled.draw_bitmap(sh1106.delta_lm_data, sh1106.delta_lm_w, sh1106.delta_lm_h, 59, 17)
            oled.text_with_degree("", delta_temp, 82, 20)
            oled.draw_bitmap(sh1106.droppe_data, sh1106.droppe_w, sh1106.droppe_h, 49, 35)
            oled.text_with_percent("", rh_in, 62, 38)
            oled.draw_bitmap(sh1106.delta_lm_data, sh1106.delta_lm_w, sh1106.delta_lm_h, 59, 51)
            oled.text_with_percent("", delta_rh, 82, 54)
            oled.show()
            #print("Fläkt-mätning:", temp_in, rh_in, temp_ut, rh_ut)
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
        API.send_data_base(temp_in, rh_in, jf, temp_ut, rh_ut)
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