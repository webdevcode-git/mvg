from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Form, Response, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from passlib.context import CryptContext
import asyncio
import main  # Assumes your MVG API logic is in main.py
import json
import os
import secrets
import pyotp
import logging
from datetime import datetime, timedelta

# Filter frequent polling logs to keep the console clean for 2FA prompts
logging.getLogger("uvicorn.access").addFilter(
    lambda record: "/admin/check-auth" not in record.getMessage() and "/ws" not in record.getMessage()
)

app = FastAPI()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SETTINGS = {}
ACTIVE_SESSIONS = {}
TEMP_ACCESS = None 
cached_departures = []

def load_settings():
    global SETTINGS
    defaults = {
        "stations": ["Klinikum Großhadern", "Max-Lebschke Platz"],
        "update_interval": 30,
        "admin_user": "admin",
        "admin_hash": "",
        "totp_devices": []
    }
    if os.path.exists("settings.json"):
        try:
            with open("settings.json", "r", encoding="utf-8") as f:
                SETTINGS = {**defaults, **json.load(f)}
                return
        except: pass
    SETTINGS = defaults

def save_settings():
    with open("settings.json", "w", encoding="utf-8") as f:
        json.dump(SETTINGS, f, indent=4)

load_settings()

async def get_session(request: Request):
    session_id = request.cookies.get("admin_session")
    if not session_id or session_id not in ACTIVE_SESSIONS:
        return None
    return ACTIVE_SESSIONS[session_id]

async def update_departures():
    global cached_departures
    while True:
        try:
            # Fetches data from your existing mvg_api function
            data = main.mvg_api(SETTINGS["stations"], api_type="departures", combine_departures=True)
            cached_departures = data
            await manager.broadcast(cached_departures)
        except Exception as e:
            print(f"MVG API Error: {e}")
        await asyncio.sleep(SETTINGS["update_interval"])

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(update_departures())

# --- LOGIN & AUTHENTICATION ---

@app.get("/admin/login", response_class=HTMLResponse)
async def login_page():
    return """
    <html>
        <head><title>Admin Login</title>
        <style>
            body { font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #f0f2f5; margin: 0; }
            .box { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); width: 320px; text-align: center; }
            input { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; box-sizing: border-box; }
            .btn-blue { width: 100%; padding: 10px; background: #0055a4; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; }
            .btn-temp { width: 100%; padding: 8px; background: #fff; color: #d97706; border: 1px solid #d97706; border-radius: 5px; cursor: pointer; margin-top: 20px; font-size: 11px; }
            .hint { font-size: 11px; color: #777; margin-top: 10px; }
        </style>
        </head>
        <body>
            <div class="box">
                <h2>Admin Login</h2>
                <form action="/admin/login" method="post">
                    <input type="text" name="username" placeholder="Username or TOTP Code" required>
                    <input type="password" name="password" placeholder="Password">
                    <button type="submit" class="btn-blue">Login</button>
                    <div class="hint">Tip: Enter your 6-digit Authenticator code as 'Username' for Quick Login.</div>
                </form>
                <form action="/admin/request-temp" method="post">
                    <button type="submit" class="btn-temp">Request Console Access (10m)</button>
                </form>
            </div>
        </body>
    </html>
    """

@app.post("/admin/login")
async def process_login(response: Response, username: str = Form(...), password: str = Form("")):
    # 1. QuickAuth: Check if the username field contains a valid TOTP
    if username.isdigit() and len(username) == 6:
        for device in SETTINGS["totp_devices"]:
            if pyotp.TOTP(device["secret"]).verify(username):
                session_id = secrets.token_hex(16)
                ACTIVE_SESSIONS[session_id] = {"user": f"QuickAuth ({device['name']})", "approved": True}
                resp = RedirectResponse(url="/admin", status_code=303)
                resp.set_cookie(key="admin_session", value=session_id, httponly=True)
                return resp

    # 2. Standard Credentials check
    is_temp = TEMP_ACCESS and username == TEMP_ACCESS["user"] and password == TEMP_ACCESS["pass"] and datetime.now() < TEMP_ACCESS["expires"]
    is_perm = (username == SETTINGS.get("admin_user") and SETTINGS.get("admin_hash") and pwd_context.verify(password, SETTINGS["admin_hash"]))
    
    if not (is_temp or is_perm):
        raise HTTPException(401, "Invalid Credentials")

    # 3. Create unapproved session for 2FA step
    session_id = secrets.token_hex(16)
    plain_code = str(secrets.randbelow(9000) + 1000)
    ACTIVE_SESSIONS[session_id] = {
        "user": username, 
        "approved": False, 
        "hashed_code": pwd_context.hash(plain_code), 
        "display_code": plain_code,
        "processing": False
    }
    resp = RedirectResponse(url="/admin/verify", status_code=303)
    resp.set_cookie(key="admin_session", value=session_id, httponly=True)
    return resp

@app.get("/admin/verify", response_class=HTMLResponse)
async def verify_page(session: dict = Depends(get_session)):
    if not session: return RedirectResponse("/admin/login")
    if session["approved"]: return RedirectResponse("/admin")
    
    return f"""
    <html>
        <head>
            <script>setInterval(() => {{ fetch('/admin/check-auth').then(r => r.json()).then(d => {{ if(d.ok) location.href='/admin'; }}) }}, 2000);</script>
            <style>
                body{{font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;background:#fffbe6;margin:0;}} 
                .card{{text-align:center;padding:40px;background:#fff;border-radius:12px;box-shadow:0 4px 10px rgba(0,0,0,0.05); width: 350px;}} 
                input{{padding:10px;width:100%;margin:10px 0;border-radius:5px;border:1px solid #ddd;box-sizing:border-box;}} 
                button{{padding:10px;width:100%;background:#0055a4;color:#fff;border:none;border-radius:5px;cursor:pointer;}}
                .btn-console {{ background: #333; margin-top: 20px; font-size: 12px; }}
                .code-box {{ font-size: 24px; font-weight: bold; color: #d97706; margin: 10px 0; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h2>Two-Factor Authentication</h2>
                <form action="/admin/verify-totp" method="post">
                    <p>Enter 6-digit App Code:</p>
                    <input type="text" name="totp_code" placeholder="000000" autofocus autocomplete="off">
                    <button type="submit">Verify App</button>
                </form>
                <hr style="margin:25px 0; border:0; border-top:1px solid #eee;">
                <p>Option 2: Use Server Console</p>
                <div class="code-box">{session['display_code']}</div>
                <form action="/admin/trigger-console" method="post">
                    <button type="submit" class="btn-console">Authorize via Console</button>
                </form>
            </div>
        </body>
    </html>
    """

@app.post("/admin/trigger-console")
async def trigger_console(session: dict = Depends(get_session)):
    if not session or session.get("processing"): 
        return RedirectResponse("/admin/verify", status_code=303)

    async def console_input_loop():
        session["processing"] = True
        print(f"\n[LOGIN ATTEMPT] User: {session['user']}")
        print(f"To approve, enter the code shown on the screen: {session['display_code']}")
        
        loop = asyncio.get_event_loop()
        try:
            # We use an executor because input() is blocking
            u_input = await loop.run_in_executor(None, lambda: input("Enter Code: "))
            if pwd_context.verify(u_input.strip(), session["hashed_code"]):
                session["approved"] = True
                print(">>> Access GRANTED.")
            else:
                print(">>> Access DENIED: Invalid code.")
        except Exception as e:
            print(f"Console error: {e}")
        finally:
            session["processing"] = False

    asyncio.create_task(console_input_loop())
    return RedirectResponse("/admin/verify", status_code=303)

@app.post("/admin/verify-totp")
async def verify_totp(totp_code: str = Form(...), session: dict = Depends(get_session)):
    if not session: return RedirectResponse("/admin/login")
    for device in SETTINGS["totp_devices"]:
        if pyotp.TOTP(device["secret"]).verify(totp_code):
            session["approved"] = True
            return RedirectResponse("/admin", status_code=303)
    return HTMLResponse("Invalid Code. <a href='/admin/verify'>Try again</a>")

@app.get("/admin/check-auth")
async def check_auth(session: dict = Depends(get_session)):
    return {"ok": session["approved"] if session else False}

@app.post("/admin/request-temp")
async def request_temp():
    global TEMP_ACCESS
    user, pw = f"temp_{secrets.token_hex(2)}", secrets.token_urlsafe(8)
    TEMP_ACCESS = {"user": user, "pass": pw, "expires": datetime.now() + timedelta(minutes=10)}
    print(f"\n\n{'='*20}\nTEMP LOGIN DETAILS:\nUser: {user}\nPass: {pw}\n{'='*20}\n")
    return HTMLResponse(f"Credentials printed to console. <a href='/admin/login'>Back to Login</a>")

# --- ADMIN PANEL & SETTINGS ---

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(session: dict = Depends(get_session)):
    if not session or not session["approved"]: return RedirectResponse("/admin/login")
    new_secret = pyotp.random_base32()
    otp_uri = pyotp.totp.TOTP(new_secret).provisioning_uri(name=f"MVG_{secrets.token_hex(2)}", issuer_name="LocalServer")
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=150x150&data={otp_uri}"
    device_list_html = "".join([f"<li><b>{d['name']}</b> <a href='/admin/delete-device/{i}' style='color:red; float:right;'>Delete</a></li>" for i, d in enumerate(SETTINGS["totp_devices"])])
    
    return f"""
    <html>
        <head><title>Admin Settings</title><style>
            body{{font-family:sans-serif;max-width:500px;margin:40px auto; background:#f4f4f9;}}
            .sect{{border:1px solid #ddd;padding:20px;border-radius:10px; background:white; margin-bottom:20px;}}
            input{{width:100%;padding:8px;box-sizing:border-box;margin-top:5px; border:1px solid #ccc; border-radius:4px;}}
            .btn{{width:100%;padding:10px;margin-top:15px;background:#0055a4;color:white;border:none;border-radius:5px;cursor:pointer; font-weight:bold;}}
            ul{{padding:0; list-style:none;}} li{{background:#eee; padding:10px; margin-bottom:5px; border-radius:5px; overflow:hidden;}}
        </style></head>
        <body>
            <h2>General Settings</h2>
            <form action="/admin/save" method="post" class="sect">
                <label>Stations (comma separated):</label><input type="text" name="stations" value="{", ".join(SETTINGS['stations'])}">
                <br><br><label>Refresh Interval (seconds):</label><input type="number" name="interval" value="{SETTINGS['update_interval']}">
                <br><br><label>Change Admin Password:</label><input type="password" name="new_password" placeholder="Leave blank to keep current">
                <button type="submit" class="btn">Save Settings</button>
            </form>
            <h2>Authenticator Devices</h2>
            <div class="sect">
                <ul>{device_list_html or "<li>No devices linked</li>"}</ul>
                <hr>
                <form action="/admin/add-device" method="post" style="text-align:center;">
                    <p><b>Link New Device</b></p>
                    <img src="{qr_url}"><br>
                    <input type="text" name="device_name" placeholder="Device Name (e.g. My Phone)" required>
                    <input type="hidden" name="new_secret" value="{new_secret}">
                    <button type="submit" class="btn" style="background:#28a745;">Add This Device</button>
                </form>
            </div>
            <br><a href="/" style="text-decoration:none; color:#666;">← Back to Board</a>
        </body>
    </html>
    """

@app.post("/admin/save")
async def save_admin(stations: str = Form(...), interval: int = Form(...), new_password: str = Form(None), session: dict = Depends(get_session)):
    if not session or not session["approved"]: return RedirectResponse("/admin/login")
    SETTINGS["stations"] = [s.strip() for s in stations.split(",")]
    SETTINGS["update_interval"] = max(5, interval)
    if new_password: SETTINGS["admin_hash"] = pwd_context.hash(new_password)
    save_settings()
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/add-device")
async def add_device(device_name: str = Form(...), new_secret: str = Form(...), session: dict = Depends(get_session)):
    if not session or not session["approved"]: return RedirectResponse("/admin/login")
    SETTINGS["totp_devices"].append({"name": device_name, "secret": new_secret})
    save_settings()
    return RedirectResponse("/admin", status_code=303)

@app.get("/admin/delete-device/{index}")
async def delete_device(index: int, session: dict = Depends(get_session)):
    if not session or not session["approved"]: return RedirectResponse("/admin/login")
    if 0 <= index < len(SETTINGS["totp_devices"]):
        SETTINGS["totp_devices"].pop(index)
        save_settings()
    return RedirectResponse("/admin", status_code=303)

# --- WEBSOCKET & CORE ---

class ConnectionManager:
    def __init__(self): self.active_connections = []
    async def connect(self, ws: WebSocket):
        await ws.accept(); self.active_connections.append(ws)
        if cached_departures: await ws.send_json({"deps": cached_departures})
    def disconnect(self, ws: WebSocket):
        if ws in self.active_connections: self.active_connections.remove(ws)
    async def broadcast(self, msg: list):
        for conn in self.active_connections:
            try: await conn.send_json({"deps": msg})
            except: pass

manager = ConnectionManager()

@app.get("/", response_class=HTMLResponse)
async def index():
    # Frontend logic: removed max_departures dependency, it shows all incoming items
    return HTMLResponse(content="""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body { font-family: Arial, sans-serif; background: #fff; margin: 0; padding: 0; }
        table { border-collapse: collapse; width: 100%; table-layout: fixed; }
        td { padding: 1.5vh 2vw; border-bottom: 2px solid #eee; vertical-align: middle; }
        .line-row { display: flex; align-items: center; gap: 15px; }
        .icon-u { width: 60px; height: 60px; background: #0055a4; color: #fff; display: flex; align-items: center; justify-content: center; font-weight: bold; border-radius: 8px; font-size: 35px; }
        .icon-bus { width: 55px; height: 55px; background: #fff; color: #00656e; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 16px; border-radius: 50%; border: 4px solid #00656e; }
        .badge { color: #fff; padding: 5px 15px; border-radius: 8px; font-weight: bold; min-width: 70px; text-align: center; font-size: 28px; }
        .bg-u { background: #0055a4; } .bg-bus { background: #00656e; }
        .status { font-size: 1.2em; font-weight: bold; margin-top: 5px; display: block; }
        .ontime { color: #008542; } .delayed { color: #d00; } .scheduled { color: #666; font-style: italic; }
        .dest { font-size: 2.5em; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .time-box { text-align: right; }
        .clock { font-size: 1.8em; color: #444; font-weight: bold; display: block; margin-bottom: -2px; }
        .min { font-weight: bold; font-size: 3em; color: #000; }
        .jetzt { color: #008542; animation: blink 2s infinite; }
        @keyframes blink { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }
    </style>
</head>
<body>
    <table><tbody id="board"></tbody></table>
    <script>
        let lastData = [];
        function updateUI() {
            if (!lastData.length) return;
            const now = Date.now() / 1000;
            let html = "";
            // Filters out very old departures, shows all recent/upcoming ones
            lastData.filter(dep => (dep.time - now) > -60).forEach(dep => {
                const diff = dep.time - now;
                const minutes = Math.max(0, Math.round(diff / 60));
                const timeObj = new Date(dep.time * 1000);
                const clockStr = timeObj.getHours().toString().padStart(2,'0') + ":" + timeObj.getMinutes().toString().padStart(2,'0');
                let bCls = dep.type.toLowerCase().includes("u-bahn") ? "bg-u" : "bg-bus";
                let icon = dep.type.toLowerCase().includes("u-bahn") ? '<div class="icon-u">U</div>' : '<div class="icon-bus">BUS</div>';
                let status = dep.realtime ? (dep.delay > 0 ? `<span class="status delayed">+${dep.delay} min</span>` : (dep.delay < 0 ? `<span class="status ontime">${dep.delay} min</span>` : '<span class="status ontime">pünktlich</span>')) : '<span class="status scheduled">Planmäßig*</span>';
                
                html += `<tr>
                    <td style="width:28%"><div class="line-row">${icon}<div class="badge ${bCls}">${dep.line}</div></div>${status}</td>
                    <td class="dest">${dep.destination}</td>
                    <td class="time-box">
                        <span class="clock">${clockStr}</span>
                        <span class="min">${minutes === 0 ? '<span class="jetzt">Jetzt</span>' : minutes + ' min'}</span>
                    </td>
                </tr>`;
            });
            document.getElementById('board').innerHTML = html;
        }
        function connect() {
            const ws = new WebSocket((location.protocol==='https:'?'wss:':'ws:')+'//'+location.host+'/ws');
            ws.onmessage = (e) => { 
                const data = JSON.parse(e.data);
                lastData = data.deps;
                updateUI(); 
            };
            ws.onclose = () => setTimeout(connect, 2000);
        }
        setInterval(updateUI, 1000);
        connect();
    </script>
</body>
</html>
""")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True: await websocket.receive_text()
    except WebSocketDisconnect: manager.disconnect(websocket)