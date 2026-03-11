# ==========================================================
# DISABLE BROWNOUT DETECTOR - MUST BE FIRST!
# ==========================================================
def disable_brownout():
    from machine import mem32
    RTC_CNTL_BROWN_OUT_REG = 0x3FF480D4
    mem32[RTC_CNTL_BROWN_OUT_REG] = 0

try:
    disable_brownout()
except:
    pass

import machine
import time
machine.freq(160000000)
time.sleep(1)
print("Starting Smart Industry System...")

# ==========================================================
# IMPORTS
# ==========================================================
from machine import Pin, I2C, PWM, RTC
import network
import socket
import urandom
import gc
gc.collect()

# ==========================================================
# RTC (REAL TIME CLOCK) SETUP
# ==========================================================
rtc = RTC()

TIMEZONE_OFFSET = 19800

NTP_AVAILABLE = False
try:
    import ntptime
    NTP_AVAILABLE = True
except:
    print("ntptime not available")

def sync_time():
    global NTP_AVAILABLE
    if not NTP_AVAILABLE:
        return False
    try:
        print("Syncing time with NTP...")
        ntptime.host = "pool.ntp.org"
        ntptime.settime()
        print("Time synced!")
        return True
    except:
        print("NTP sync failed")
        return False

def get_local_time():
    try:
        return time.localtime(time.time() + TIMEZONE_OFFSET)
    except:
        return time.localtime()

def get_day_name(wd):
    d = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return d[wd] if 0 <= wd <= 6 else "---"

def get_date_str():
    t = get_local_time()
    return "{} {:02d}/{:02d}/{:04d}".format(get_day_name(t[6]), t[2], t[1], t[0])

def get_time_str():
    t = get_local_time()
    return "{:02d}:{:02d}".format(t[3], t[4], t[5])

# ==========================================================
# HELPER FUNCTION - REPLACE ZFILL
# ==========================================================
def zpad(s, n):
    s = str(s)
    while len(s) < n:
        s = "0" + s
    return s

# ==========================================================
# WIFI SETUP
# ==========================================================
ssid = "test"
password_wifi = ""

sta = network.WLAN(network.STA_IF)
time.sleep_ms(300)
sta.active(True)
time.sleep_ms(300)

print("Connecting to WiFi...")
sta.connect(ssid, password_wifi)

timeout = 0
while not sta.isconnected():
    time.sleep(1)
    timeout += 1
    print(".", end="")
    if timeout > 30:
        print("\nWiFi Failed, restarting...")
        machine.reset()

ip = sta.ifconfig()[0]
print("\nConnected:", ip)
gc.collect()

time.sleep(1)
sync_time()
gc.collect()

# ==========================================================
# WORKER LIST
# ==========================================================
worker_ids = ["1001","1002","1003","1004","1005","2001","2002","2003","3001","3002"]

# ==========================================================
# VISITOR ID MANAGEMENT
# ==========================================================
visitor_ids_in_use = []
visitor_id_cooldown = {}
VISITOR_COOLDOWN_SECONDS = 180

def clean_expired_cooldowns():
    ct = time.time()
    exp = [v for v in visitor_id_cooldown if ct - visitor_id_cooldown[v] >= VISITOR_COOLDOWN_SECONDS]
    for v in exp:
        del visitor_id_cooldown[v]

def get_next_visitor_id():
    ct = time.time()
    clean_expired_cooldowns()
    for i in range(1, 101):
        vid = "V" + zpad(i, 3)
        if vid in visitor_ids_in_use:
            continue
        if vid in visitor_id_cooldown:
            if ct - visitor_id_cooldown[vid] < VISITOR_COOLDOWN_SECONDS:
                continue
        return vid
    return None

def assign_visitor_id():
    vid = get_next_visitor_id()
    if vid:
        visitor_ids_in_use.append(vid)
    return vid

def release_visitor_id(vid):
    if vid in visitor_ids_in_use:
        visitor_ids_in_use.remove(vid)
        visitor_id_cooldown[vid] = time.time()
        return True
    return False

def is_valid_visitor_id(vid):
    return vid in visitor_ids_in_use

def get_cooldown_remaining(vid):
    if vid in visitor_id_cooldown:
        rem = VISITOR_COOLDOWN_SECONDS - (time.time() - visitor_id_cooldown[vid])
        if rem > 0:
            return int(rem)
    return 0

# ==========================================================
# GLOBAL VARIABLES
# ==========================================================
page = "home"
entry_mode = None
user_mode = None
entered = ""
current_worker_id = ""
current_visitor_id = ""
assigned_visitor_id = ""
otp = None
otp_start = 0
otp_valid = False
worker_in = 0
visitor_in = 0
worker_out = 0
visitor_out = 0
total_workers = 0
total_visitors = 0
logs = []
max_logs = 30
lcd_phase = 0
lcd_last_switch = time.time()
error_msg = ""

# ==========================================================
# LED SETUP
# ==========================================================
green = Pin(2, Pin.OUT)
yellow = Pin(4, Pin.OUT)
red = Pin(5, Pin.OUT)
green.off()
yellow.off()
red.off()

# ==========================================================
# BUZZER SETUP
# ==========================================================
buzzer = Pin(15, Pin.OUT)
buzzer.off()

def buzzer_beep(times, dur):
    for i in range(times):
        buzzer.on()
        time.sleep_ms(dur)
        buzzer.off()
        if i < times - 1:
            time.sleep_ms(dur)

def buzzer_success():
    buzzer_beep(2, 100)

def buzzer_error():
    buzzer_beep(1, 500)

def buzzer_door_open():
    buzzer_beep(3, 80)

def buzzer_door_close():
    buzzer_beep(1, 200)

# ==========================================================
# SERVO MOTOR (DOOR) SETUP
# ==========================================================
servo_pin = Pin(13)
servo = PWM(servo_pin, freq=50)
DOOR_CLOSED = 26
DOOR_OPEN = 77
door_is_open = False

def set_servo_angle(duty):
    servo.duty(duty)

def door_open():
    global door_is_open
    print_lcd("Door Opening...", 1)
    print_lcd("", 2)
    buzzer_door_open()
    set_servo_angle(DOOR_OPEN)
    door_is_open = True
    time.sleep(1)

def door_close():
    global door_is_open
    print_lcd("Door Closing...", 1)
    print_lcd("", 2)
    time.sleep_ms(500)
    set_servo_angle(DOOR_CLOSED)
    buzzer_door_close()
    door_is_open = False
    time.sleep(1)

def door_sequence():
    door_open()
    time.sleep(3)
    door_close()

set_servo_angle(DOOR_CLOSED)
time.sleep_ms(500)

# ==========================================================
# LCD SETUP
# ==========================================================
i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=400000)
time.sleep_ms(100)

try:
    addr = i2c.scan()[0]
    print("LCD found at:", addr)
except:
    addr = 0x27

BL = 0x08
EN = 0x04
RS = 0x01

def lcd_write(val):
    try:
        i2c.writeto(addr, bytes([val | BL]))
    except:
        pass

def lcd_pulse(val):
    lcd_write(val | EN)
    time.sleep_us(1)
    lcd_write(val & ~EN)
    time.sleep_us(50)

def lcd_send(data, mode=0):
    lcd_pulse((data & 0xF0) | mode)
    lcd_pulse(((data << 4) & 0xF0) | mode)

def lcd_cmd(cmd):
    lcd_send(cmd, 0)

def lcd_data(data):
    lcd_send(data, RS)

def lcd_init():
    time.sleep_ms(50)
    lcd_cmd(0x33)
    lcd_cmd(0x32)
    lcd_cmd(0x28)
    lcd_cmd(0x0C)
    lcd_cmd(0x06)
    lcd_cmd(0x01)
    time.sleep_ms(5)

def print_lcd(text, line):
    lcd_cmd(0x80 if line == 1 else 0xC0)
    text = text[:16]
    text += " " * (16 - len(text))
    for c in text:
        lcd_data(ord(c))

try:
    lcd_init()
    print_lcd("System Starting", 1)
    print_lcd("Please Wait...", 2)
except:
    print("LCD init failed")

time.sleep(1)

# ==========================================================
# OTP FUNCTIONS
# ==========================================================
def generate_otp(length):
    global otp, otp_start, otp_valid
    otp = ""
    for _ in range(length):
        otp += str(urandom.getrandbits(4) % 10)
    otp_start = time.time()
    otp_valid = True
    buzzer_beep(1, 50)

def expire_otp():
    global otp, otp_valid
    otp = None
    otp_valid = False

def reset_all():
    global page, entry_mode, user_mode, entered, current_worker_id, current_visitor_id, assigned_visitor_id, error_msg
    expire_otp()
    page = "home"
    entry_mode = None
    user_mode = None
    entered = ""
    current_worker_id = ""
    current_visitor_id = ""
    assigned_visitor_id = ""
    error_msg = ""

# ==========================================================
# LOG FUNCTIONS
# ==========================================================
def add_log(user_type, action, user_id):
    global logs
    t = get_local_time()
    logs.insert(0, {
        "date": "{:02d}/{:02d}/{:04d}".format(t[2], t[1], t[0]),
        "time": "{:02d}:{:02d}".format(t[3], t[4]),
        "user": user_type,
        "action": action,
        "id": user_id
    })
    if len(logs) > max_logs:
        logs.pop()

def get_logs_html():
    gc.collect()
    if not logs:
        return "<p style='color:#c9a227;'>No logs yet</p>"
    h = "<table style='width:100%;border-collapse:collapse;'>"
    h += "<tr style='background:#1a1a2e;color:#c9a227;'>"
    h += "<th style='padding:8px;border:1px solid #333;'>Date</th>"
    h += "<th style='padding:8px;border:1px solid #333;'>Time</th>"
    h += "<th style='padding:8px;border:1px solid #333;'>User</th>"
    h += "<th style='padding:8px;border:1px solid #333;'>Action</th>"
    h += "<th style='padding:8px;border:1px solid #333;'>ID</th></tr>"
    for i, l in enumerate(logs):
        bg = "#1e1e3f" if i % 2 == 0 else "#16163a"
        h += "<tr style='background:{};'>".format(bg)
        h += "<td style='padding:6px;border:1px solid #333;color:#eee;'>{}</td>".format(l["date"])
        h += "<td style='padding:6px;border:1px solid #333;color:#eee;'>{}</td>".format(l["time"])
        h += "<td style='padding:6px;border:1px solid #333;color:#eee;'>{}</td>".format(l["user"])
        h += "<td style='padding:6px;border:1px solid #333;color:#eee;'>{}</td>".format(l["action"])
        h += "<td style='padding:6px;border:1px solid #333;color:#c9a227;'>{}</td>".format(l["id"])
        h += "</tr>"
    h += "</table>"
    return h

# ==========================================================
# LCD SEQUENCE - NO DATE/TIME
# ==========================================================
def run_lcd_sequence():
    global lcd_phase, lcd_last_switch
    now = time.time()
    if now - lcd_last_switch >= 5:
        lcd_phase = (lcd_phase + 1) % 5
        lcd_last_switch = now
    
    if lcd_phase == 0:
        print_lcd("Welcome to Smart", 1)
        print_lcd("Industry", 2)
    elif lcd_phase == 1:
        print_lcd("Type in browser:", 1)
        print_lcd(ip, 2)
    elif lcd_phase == 2:
        print_lcd("Workers In:" + str(worker_in), 1)
        print_lcd("Visitors In:" + str(visitor_in), 2)
    elif lcd_phase == 3:
        print_lcd("Workers Out:" + str(worker_out), 1)
        print_lcd("Visitors Out:" + str(visitor_out), 2)
    elif lcd_phase == 4:
        print_lcd("Total W:" + str(total_workers), 1)
        print_lcd("Total V:" + str(total_visitors), 2)

# ==========================================================
# COMPACT CSS STYLES
# ==========================================================
CSS_MAIN = """<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Georgia,serif;background:linear-gradient(135deg,#0a0a1a,#1a1a3e,#0f1f4a);color:#f0f0f0;min-height:100vh;padding:15px}
.c{max-width:900px;margin:0 auto}
.hdr{text-align:center;padding:20px;background:linear-gradient(180deg,rgba(201,162,39,0.15),rgba(26,26,46,0.9));border-radius:15px;border:1px solid rgba(201,162,39,0.4);margin-bottom:20px}
h1{font-size:clamp(22px,5vw,38px);background:linear-gradient(135deg,#c9a227,#f5d742,#c9a227);-webkit-background-clip:text;-webkit-text-fill-color:transparent;letter-spacing:2px;margin-bottom:10px}
.sub{color:#c9a227;font-size:12px;letter-spacing:3px;margin-bottom:15px}
.dt-box{display:inline-block;background:rgba(201,162,39,0.1);padding:10px 20px;border-radius:10px;border:1px solid rgba(201,162,39,0.3);margin:5px}
.dt-lbl{font-size:10px;color:#c9a227;letter-spacing:1px}
.dt-val{font-size:18px;color:#f0f0f0}
.door{display:inline-block;padding:10px 30px;border-radius:25px;font-size:14px;color:#fff;margin-top:10px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin:15px 0}
.stat{background:linear-gradient(180deg,rgba(201,162,39,0.1),rgba(26,26,46,0.9));padding:15px;border-radius:10px;border:1px solid rgba(201,162,39,0.25);text-align:center}
.stat:hover{border-color:rgba(201,162,39,0.5)}
.s-lbl{font-size:10px;color:#c9a227;letter-spacing:1px;margin:5px 0}
.s-val{font-size:28px;color:#f0f0f0}
.info{background:rgba(201,162,39,0.1);padding:12px;border-radius:10px;border:1px solid rgba(201,162,39,0.2);text-align:center;margin:10px 0}
.btns{display:flex;flex-direction:column;align-items:center;gap:12px;margin:20px 0}
.btn-main{width:85%;max-width:350px;padding:18px;font-size:20px;font-weight:bold;border-radius:12px;border:2px solid #c9a227;background:linear-gradient(135deg,#c9a227,#a68523);color:#1a1a2e;cursor:pointer;letter-spacing:2px}
.btn-main:hover{background:linear-gradient(135deg,#f5d742,#c9a227)}
.btn-log{width:65%;max-width:260px;padding:14px;font-size:16px;border-radius:10px;border:1px solid #3a6ea5;background:linear-gradient(135deg,#1e3a5f,#2d5a87);color:#f0f0f0;cursor:pointer}
.btn-log:hover{background:linear-gradient(135deg,#2d5a87,#3d7ab7)}
.ftr{text-align:center;padding:15px;color:rgba(201,162,39,0.5);font-size:11px}
</style>"""

CSS_KEYPAD = """<style>
body{text-align:center;background:linear-gradient(135deg,#0a0a1a,#1a1a3e);color:#f0f0f0;font-family:Georgia,serif;padding:15px;min-height:100vh}
h2{font-size:22px;color:#c9a227;margin-bottom:15px;letter-spacing:1px}
.disp{font-size:32px;margin:15px;letter-spacing:6px;color:#c9a227;padding:12px;background:rgba(201,162,39,0.1);border-radius:10px;border:1px solid rgba(201,162,39,0.3);min-height:50px}
.info{font-size:14px;color:#c9a227;margin:10px}
.kp{display:grid;grid-template-columns:repeat(3,80px);grid-gap:12px;justify-content:center;margin:20px auto}
.kp button{height:70px;font-size:26px;border-radius:12px;background:linear-gradient(135deg,rgba(201,162,39,0.2),rgba(26,26,46,0.9));color:#f0f0f0;border:1px solid rgba(201,162,39,0.4);cursor:pointer}
.kp button:hover{background:linear-gradient(135deg,rgba(201,162,39,0.4),rgba(26,26,46,0.9))}
.zero{grid-column:2}
.act{width:180px;height:50px;font-size:16px;margin:8px;border-radius:10px;cursor:pointer;border:none;letter-spacing:1px}
.enter{background:linear-gradient(135deg,#2e7d32,#4caf50);color:#fff}
.gen{background:linear-gradient(135deg,#1565c0,#42a5f5);color:#fff}
.clr{background:linear-gradient(135deg,#c9a227,#f5d742);color:#1a1a2e}
.back{background:linear-gradient(135deg,#c62828,#ef5350);color:#fff}
.timer{font-size:18px;color:#c9a227;margin:10px}
.id-info{font-size:18px;color:#c9a227;margin:10px}
.err{color:#ef5350;font-size:16px;margin:10px;padding:8px;background:rgba(239,83,80,0.1);border-radius:8px}
</style>"""

# ==========================================================
# KEYPAD HTML
# ==========================================================
def number_buttons():
    return '''<div class="kp">
<button name="key" value="1">1</button>
<button name="key" value="2">2</button>
<button name="key" value="3">3</button>
<button name="key" value="4">4</button>
<button name="key" value="5">5</button>
<button name="key" value="6">6</button>
<button name="key" value="7">7</button>
<button name="key" value="8">8</button>
<button name="key" value="9">9</button>
<button name="key" value="0" class="zero">0</button>
</div>'''

# ==========================================================
# PAGES - NO EMOJIS
# ==========================================================
def home_page():
    gc.collect()
    clean_expired_cooldowns()
    cc = len(visitor_id_cooldown)
    ds = "OPEN" if door_is_open else "CLOSED"
    dc = "#2e7d32" if door_is_open else "#c62828"
    
    return '''<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta charset="UTF-8"><title>Smart Industry</title>
''' + CSS_MAIN + '''</head><body>
<div class="c">
<div class="hdr">
<h1>SMART INDUSTRY</h1>
<div class="sub">ACCESS CONTROL SYSTEM</div>
<div class="dt-box"><div class="dt-lbl">DATE</div><div class="dt-val">''' + get_date_str() + '''</div></div>
<div class="dt-box"><div class="dt-lbl">TIME</div><div class="dt-val">''' + get_time_str() + '''</div></div>
<div class="door" style="background:''' + dc + '''">DOOR: ''' + ds + '''</div>
</div>
<div class="stats">
<div class="stat"><div class="s-lbl">WORKERS IN</div><div class="s-val">''' + str(worker_in) + '''</div></div>
<div class="stat"><div class="s-lbl">VISITORS IN</div><div class="s-val">''' + str(visitor_in) + '''</div></div>
<div class="stat"><div class="s-lbl">WORKERS OUT</div><div class="s-val">''' + str(worker_out) + '''</div></div>
<div class="stat"><div class="s-lbl">VISITORS OUT</div><div class="s-val">''' + str(visitor_out) + '''</div></div>
<div class="stat"><div class="s-lbl">TOTAL WORKERS</div><div class="s-val">''' + str(total_workers) + '''</div></div>
<div class="stat"><div class="s-lbl">TOTAL VISITORS</div><div class="s-val">''' + str(total_visitors) + '''</div></div>
</div>
<div class="info">Active: ''' + str(len(visitor_ids_in_use)) + ''' | Cooldown: ''' + str(cc) + '''</div>
<form method="GET" action="/press">
<div class="btns">
<button name="key" value="entry" class="btn-main">ENTRY</button>
<button name="key" value="exit" class="btn-main">EXIT</button>
<button name="key" value="viewlogs" class="btn-log">VIEW LOGS</button>
</div></form>
<div class="ftr">Smart Industry Access Control | ESP32</div>
</div></body></html>'''

def select_user_page():
    gc.collect()
    act = "ENTRY" if entry_mode == "entry" else "EXIT"
    return '''<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
''' + CSS_MAIN + '''</head><body>
<div class="c">
<div class="hdr">
<h1>''' + act + '''</h1>
<div class="sub">SELECT USER TYPE</div>
</div>
<form method="GET" action="/press">
<div class="btns" style="margin-top:30px">
<button name="key" value="worker" class="btn-main">WORKER</button>
<button name="key" value="visitor" class="btn-main">VISITOR</button>
<button name="key" value="back" class="btn-log" style="background:linear-gradient(135deg,#c62828,#ef5350);border-color:#c62828">BACK</button>
</div></form>
</div></body></html>'''

def worker_id_page():
    gc.collect()
    act = "Entry" if entry_mode == "entry" else "Exit"
    err = '<div class="err">' + error_msg + '</div>' if error_msg else ''
    return '''<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
''' + CSS_KEYPAD + '''</head><body>
<h2>Worker ''' + act + ''' - Enter ID</h2>
''' + err + '''<div class="disp">''' + entered + '''</div>
<form method="GET" action="/press">
''' + number_buttons() + '''<br>
<button name="key" value="Enter" class="act enter">ENTER</button>
<button name="key" value="Clear" class="act clr">CLEAR</button>
<button name="key" value="back" class="act back">BACK</button>
</form></body></html>'''

def visitor_id_page():
    gc.collect()
    err = '<div class="err">' + error_msg + '</div>' if error_msg else ''
    return '''<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
''' + CSS_KEYPAD + '''</head><body>
<h2>Visitor Exit - Enter ID</h2>
<div class="info">Enter number only (1, 2, 3...)</div>
''' + err + '''<div class="disp">''' + entered + '''</div>
<form method="GET" action="/press">
''' + number_buttons() + '''<br>
<button name="key" value="Enter" class="act enter">ENTER</button>
<button name="key" value="Clear" class="act clr">CLEAR</button>
<button name="key" value="back" class="act back">BACK</button>
</form></body></html>'''

def otp_page():
    gc.collect()
    rem = max(0, 30 - int(time.time() - otp_start)) if otp_valid else 0
    act = "Entry" if entry_mode == "entry" else "Exit"
    usr = "Worker" if user_mode == "worker" else "Visitor"
    
    id_info = '<div class="id-info">Visitor ID: ' + current_visitor_id + '</div>' if user_mode == "visitor" and entry_mode == "exit" else ''
    err = '<div class="err">' + error_msg + '</div>' if error_msg else ''
    
    return '''<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<script>
let t=''' + str(rem) + ''';
function tick(){
if(t>0){document.getElementById("tm").innerHTML="Time: "+t+"s";t--;setTimeout(tick,1000);}
else{document.getElementById("tm").innerHTML="EXPIRED";}
}
window.onload=tick;
</script>
''' + CSS_KEYPAD + '''</head><body>
<h2>''' + usr + ''' ''' + act + ''' - Enter OTP</h2>
<div id="tm" class="timer">Time: ''' + str(rem) + '''s</div>
''' + id_info + err + '''<div class="disp">''' + entered + '''</div>
<form method="GET" action="/press">
''' + number_buttons() + '''<br>
<button name="key" value="Generate" class="act gen">GENERATE</button>
<button name="key" value="Enter" class="act enter">ENTER</button>
<button name="key" value="Clear" class="act clr">CLEAR</button>
<button name="key" value="back" class="act back">BACK</button>
</form></body></html>'''

def success_page():
    gc.collect()
    act = "Entry" if entry_mode == "entry" else "Exit"
    usr = "Worker" if user_mode == "worker" else "Visitor"
    
    if user_mode == "worker":
        id_d = current_worker_id
    else:
        id_d = assigned_visitor_id if entry_mode == "entry" else current_visitor_id
    
    note = ""
    if user_mode == "visitor" and entry_mode == "entry":
        note = '<div style="background:rgba(201,162,39,0.2);border:2px solid #c9a227;padding:15px;border-radius:10px;margin:15px auto;max-width:350px"><h3 style="color:#c9a227">NOTE THIS ID!</h3><p>Required for Exit</p></div>'
    elif user_mode == "visitor" and entry_mode == "exit":
        note = '<p style="color:#c9a227">ID available after 3 min cooldown</p>'
    
    return '''<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="5;url=/">
<style>
body{text-align:center;background:linear-gradient(135deg,#0d3320,#1a5c3a,#2e7d32);color:#f0f0f0;font-family:Georgia,serif;padding:30px;min-height:100vh}
h1{font-size:36px;color:#c9a227;margin:20px}
h2{font-size:24px;margin:15px}
.suc{font-size:50px;font-weight:bold;color:#4caf50;margin:20px}
.id-box{font-size:50px;font-weight:bold;background:linear-gradient(135deg,#c9a227,#f5d742);color:#1a1a2e;padding:20px 40px;border-radius:15px;display:inline-block;margin:20px}
.door-msg{font-size:18px;color:#a5d6a7;margin:15px;padding:12px;background:rgba(255,255,255,0.1);border-radius:10px;display:inline-block}
</style></head><body>
<div class="suc">SUCCESS</div>
<h1>''' + act + ''' Successful!</h1>
<h2>''' + usr + '''</h2>
<div class="id-box">''' + id_d + '''</div>
<div class="door-msg">Door opened for 3 seconds</div>
''' + note + '''
<p style="margin-top:25px;color:rgba(255,255,255,0.6)">Redirecting in 5 seconds...</p>
</body></html>'''

def logs_auth_page():
    gc.collect()
    err = '<div class="err">' + error_msg + '</div>' if error_msg else ''
    return '''<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
''' + CSS_KEYPAD + '''</head><body>
<h2>View Activity Logs</h2>
<div class="info">Enter Worker ID to access</div>
''' + err + '''<div class="disp">''' + entered + '''</div>
<form method="GET" action="/press">
''' + number_buttons() + '''<br>
<button name="key" value="Enter" class="act enter">ENTER</button>
<button name="key" value="Clear" class="act clr">CLEAR</button>
<button name="key" value="back" class="act back">BACK</button>
</form></body></html>'''

def logs_view_page():
    gc.collect()
    return '''<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
''' + CSS_MAIN + '''
<style>.log-c{max-height:400px;overflow-y:auto;padding:10px}</style>
</head><body>
<div class="c">
<div class="hdr" style="padding:15px">
<h1 style="font-size:28px">ACTIVITY LOGS</h1>
<div class="dt-box"><div class="dt-lbl">DATE</div><div class="dt-val" style="font-size:14px">''' + get_date_str() + '''</div></div>
<div class="dt-box"><div class="dt-lbl">TIME</div><div class="dt-val" style="font-size:14px">''' + get_time_str() + '''</div></div>
</div>
<div class="info">Worker: ''' + current_worker_id + '''</div>
<div class="log-c">''' + get_logs_html() + '''</div>
<form method="GET" action="/press">
<button name="key" value="back" class="btn-log" style="background:linear-gradient(135deg,#c62828,#ef5350);border-color:#c62828;margin-top:15px">BACK</button>
</form>
</div></body></html>'''

# ==========================================================
# WEB PAGE ROUTER
# ==========================================================
def web_page():
    gc.collect()
    if page == "home":
        return home_page()
    elif page == "select_user":
        return select_user_page()
    elif page == "worker_id":
        return worker_id_page()
    elif page == "visitor_id":
        return visitor_id_page()
    elif page == "success":
        return success_page()
    elif page == "logs_auth":
        return logs_auth_page()
    elif page == "logs_view":
        return logs_view_page()
    else:
        return otp_page()

# ==========================================================
# SEND RESPONSE FUNCTION
# ==========================================================
def send_response(conn, html):
    try:
        conn.send("HTTP/1.1 200 OK\r\n")
        conn.send("Content-Type:text/html\r\n")
        conn.send("Connection:close\r\n\r\n")
        
        for i in range(0, len(html), 512):
            conn.send(html[i:i+512])
            time.sleep_ms(10)
        conn.close()
    except Exception as e:
        print("Send err:", e)
        try:
            conn.close()
        except:
            pass

# ==========================================================
# SERVER SETUP
# ==========================================================
gc.collect()
time.sleep(1)

s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('', 80))
s.listen(3)
s.setblocking(False)

print("Server running on:", ip)
print_lcd("Server Ready", 1)
print_lcd(ip, 2)
buzzer_beep(2, 100)
time.sleep(2)

# ==========================================================
# MAIN LOOP
# ==========================================================
loop_count = 0

while True:
    loop_count += 1
    if loop_count > 50:
        gc.collect()
        clean_expired_cooldowns()
        loop_count = 0

    # LCD LOGIC
    if otp_valid:
        rem = 30 - int(time.time() - otp_start)
        if rem <= 0:
            print_lcd("OTP Expired", 1)
            print_lcd("Generate New", 2)
            expire_otp()
            buzzer_error()
            entered = ""
            error_msg = "OTP Expired"
            time.sleep(1)
        else:
            print_lcd("OTP:" + otp, 1)
            print_lcd("Time:" + str(rem) + "s", 2)
    else:
        run_lcd_sequence()

    # WEB REQUEST HANDLING
    conn = None
    try:
        conn, addr_client = s.accept()
        conn.settimeout(3)
        
        req = conn.recv(1024).decode()
        line = req.split("\r\n")[0]
        
        print("Req:", line[:40])

        if "GET /press?key=" in line:
            key = line.split("GET /press?key=")[1].split(" ")[0]
            error_msg = ""
            
            print("Key:", key)

            if key == "entry":
                page = "select_user"
                entry_mode = "entry"
                entered = ""
                assigned_visitor_id = ""
                buzzer_beep(1, 50)

            elif key == "exit":
                page = "select_user"
                entry_mode = "exit"
                entered = ""
                buzzer_beep(1, 50)

            elif key == "viewlogs":
                page = "logs_auth"
                entered = ""
                current_worker_id = ""
                buzzer_beep(1, 50)

            elif key == "worker":
                page = "worker_id"
                user_mode = "worker"
                entered = ""
                buzzer_beep(1, 50)

            elif key == "visitor":
                if entry_mode == "entry":
                    page = "otp"
                    user_mode = "visitor"
                    entered = ""
                else:
                    page = "visitor_id"
                    user_mode = "visitor"
                    entered = ""
                buzzer_beep(1, 50)

            elif key == "back":
                expire_otp()
                yellow.off()
                
                if page == "otp":
                    if user_mode == "worker":
                        page = "worker_id"
                    else:
                        page = "visitor_id" if entry_mode == "exit" else "select_user"
                elif page == "worker_id":
                    page = "select_user"
                elif page == "visitor_id":
                    page = "select_user"
                elif page == "select_user":
                    page = "home"
                    entry_mode = None
                    user_mode = None
                elif page == "logs_auth":
                    page = "home"
                elif page == "logs_view":
                    page = "home"
                    current_worker_id = ""
                
                entered = ""
                buzzer_beep(1, 50)

            elif key.isdigit():
                yellow.on()
                if len(entered) < 10:
                    entered += key
                    buzzer_beep(1, 30)

            elif key == "Clear":
                if entered == "":
                    print_lcd("Enter First", 1)
                    print_lcd("", 2)
                    error_msg = "Nothing to clear"
                    buzzer_error()
                    time.sleep(1)
                else:
                    entered = ""
                    buzzer_beep(1, 50)
                yellow.off()

            elif key == "Generate":
                generate_otp(4 if user_mode == "worker" else 6)
                entered = ""
                error_msg = ""

            elif key == "Enter":
                yellow.off()

                if page == "worker_id":
                    if entered == "":
                        print_lcd("Enter ID First", 1)
                        print_lcd("", 2)
                        error_msg = "Please enter ID"
                        buzzer_error()
                        time.sleep(1)
                    elif entered in worker_ids:
                        current_worker_id = entered
                        page = "otp"
                        entered = ""
                        error_msg = ""
                        buzzer_success()
                    else:
                        print_lcd("Invalid ID", 1)
                        print_lcd("", 2)
                        red.on()
                        buzzer_error()
                        time.sleep(1)
                        red.off()
                        error_msg = "Invalid Worker ID"
                        entered = ""

                elif page == "logs_auth":
                    if entered == "":
                        print_lcd("Enter ID First", 1)
                        print_lcd("", 2)
                        error_msg = "Please enter Worker ID"
                        buzzer_error()
                        time.sleep(1)
                    elif entered in worker_ids:
                        current_worker_id = entered
                        page = "logs_view"
                        entered = ""
                        error_msg = ""
                        print_lcd("Access Granted", 1)
                        print_lcd("W:" + current_worker_id, 2)
                        green.on()
                        buzzer_success()
                        time.sleep(1)
                        green.off()
                    else:
                        print_lcd("Invalid ID", 1)
                        print_lcd("Access Denied", 2)
                        red.on()
                        buzzer_error()
                        time.sleep(1)
                        red.off()
                        error_msg = "Invalid Worker ID"
                        entered = ""

                elif page == "visitor_id":
                    if entered == "":
                        print_lcd("Enter ID First", 1)
                        print_lcd("", 2)
                        error_msg = "Please enter ID"
                        buzzer_error()
                        time.sleep(1)
                    else:
                        check_id = "V" + zpad(entered, 3)
                        if is_valid_visitor_id(check_id):
                            current_visitor_id = check_id
                            page = "otp"
                            entered = ""
                            error_msg = ""
                            buzzer_success()
                        else:
                            cl = get_cooldown_remaining(check_id)
                            if cl > 0:
                                print_lcd("ID in Cooldown", 1)
                                print_lcd(str(cl) + "s left", 2)
                                error_msg = check_id + " cooldown (" + str(cl) + "s)"
                            else:
                                print_lcd("Invalid Visitor", 1)
                                print_lcd(check_id, 2)
                                error_msg = "Invalid ID: " + check_id
                            red.on()
                            buzzer_error()
                            time.sleep(1)
                            red.off()
                            entered = ""

                elif page == "otp":
                    if not otp_valid:
                        print_lcd("Generate OTP", 1)
                        print_lcd("First", 2)
                        error_msg = "Generate OTP first"
                        buzzer_error()
                        time.sleep(1)
                        entered = ""
                    elif entered == "":
                        print_lcd("Enter OTP First", 1)
                        print_lcd("", 2)
                        error_msg = "Please enter OTP"
                        buzzer_error()
                        time.sleep(1)
                    elif entered == otp:
                        print("OTP Correct!")
                        
                        if entry_mode == "entry":
                            if user_mode == "worker":
                                worker_in += 1
                                total_workers += 1
                                add_log("Worker", "Entry", current_worker_id)
                                print_lcd("Access Allowed", 1)
                                print_lcd("W:" + current_worker_id, 2)
                                green.on()
                                door_sequence()
                                green.off()
                                page = "success"
                                expire_otp()
                            else:
                                new_vid = assign_visitor_id()
                                if new_vid:
                                    assigned_visitor_id = new_vid
                                    visitor_in += 1
                                    total_visitors += 1
                                    add_log("Visitor", "Entry", assigned_visitor_id)
                                    print_lcd("Access Allowed", 1)
                                    print_lcd("V:" + assigned_visitor_id, 2)
                                    green.on()
                                    door_sequence()
                                    green.off()
                                    page = "success"
                                    expire_otp()
                                else:
                                    print_lcd("All IDs Busy", 1)
                                    print_lcd("Wait 3 min", 2)
                                    red.on()
                                    buzzer_error()
                                    time.sleep(2)
                                    red.off()
                                    error_msg = "All IDs busy"
                                    entered = ""
                        else:
                            if user_mode == "worker":
                                if worker_in > 0:
                                    worker_in -= 1
                                worker_out += 1
                                add_log("Worker", "Exit", current_worker_id)
                                print_lcd("Exit Allowed", 1)
                                print_lcd("W:" + current_worker_id, 2)
                            else:
                                if visitor_in > 0:
                                    visitor_in -= 1
                                visitor_out += 1
                                release_visitor_id(current_visitor_id)
                                add_log("Visitor", "Exit", current_visitor_id)
                                print_lcd("Exit Allowed", 1)
                                print_lcd("V:" + current_visitor_id, 2)
                            
                            green.on()
                            door_sequence()
                            green.off()
                            page = "success"
                            expire_otp()
                    else:
                        print_lcd("Wrong OTP", 1)
                        print_lcd("Try Again", 2)
                        red.on()
                        buzzer_error()
                        time.sleep(1)
                        red.off()
                        error_msg = "Wrong OTP"
                        entered = ""

        elif "GET / " in line:
            if page == "success":
                reset_all()

        gc.collect()
        resp = web_page()
        send_response(conn, resp)
        print("Page:", page)

    except OSError as e:
        if e.args[0] != 11:
            print("OSErr:", e)
    except Exception as e:
        print("Err:", e)
        if conn:
            try:
                conn.close()
            except:
                pass
    
    time.sleep_ms(50)