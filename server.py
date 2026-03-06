from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Form, Response, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from passlib.context import CryptContext
import asyncio
import main 
import json
import os
import secrets
import time

# --- INITIALIZATION ---
app = FastAPI()
# pwd_context handles the hashing for both permanent passwords and the 2FA codes
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Temporary credentials generated at runtime
TEMP_ADMIN_USER = f"admin_{secrets.token_hex(2)}"
TEMP_ADMIN_PASS = secrets.token_urlsafe(12)

# Session Storage
# Format: { session_id: { "user": username, "approved": bool, "hashed_code": "..." } }
ACTIVE_SESSIONS = {}

SETTINGS = {}
cached_departures = []

def load_settings():
    global SETTINGS
    defaults = {
        "stations": ["Klinikum Großhadern", "Max-Lebschke Platz"],
        "update_interval": 30,
        "admin_user": "admin",
        "admin_hash": "" 
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

# --- HELPER: GET CURRENT SESSION ---
async def get_session(request: Request):
    session_id = request.cookies.get("admin_session")
    if not session_id or session_id not in ACTIVE_SESSIONS:
        return None
    return ACTIVE_SESSIONS[session_id]

# --- BACKGROUND TASK ---
async def update_departures():
    global cached_departures
    while True:
        try:
            data = main.mvg_api(SETTINGS["stations"], api_type="departures", combine_departures=True)
            cached_departures = data
            await manager.broadcast(cached_departures)
        except Exception as e:
            print(f"MVG API Error: {e}")
        await asyncio.sleep(SETTINGS["update_interval"])

@app.on_event("startup")
async def startup_event():
    print("\n" + "="*50)
    print("INITIAL ADMIN ACCESS GENERATED")
    print(f"Username: {TEMP_ADMIN_USER}")
    print(f"Password: {TEMP_ADMIN_PASS}")
    print("="*50 + "\n")
    asyncio.create_task(update_departures())

# --- LOGIN & 2FA ROUTES ---

@app.get("/admin/login", response_class=HTMLResponse)
async def login_page():
    return """
    <html>
        <head><title>Admin Login</title>
        <style>
            body { font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #f0f2f5; margin: 0; }
            .box { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); width: 300px; }
            input { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; box-sizing: border-box; }
            button { width: 100%; padding: 10px; background: #0055a4; color: white; border: none; border-radius: 5px; cursor: pointer; }
        </style>
        </head>
        <body>
            <div class="box">
                <h2>Admin Login</h2>
                <form action="/admin/login" method="post">
                    <input type="text" name="username" placeholder="Username" required>
                    <input type="password" name="password" placeholder="Password" required>
                    <button type="submit">Login</button>
                </form>
            </div>
        </body>
    </html>
    """

@app.post("/admin/login")
async def process_login(response: Response, username: str = Form(...), password: str = Form(...)):
    is_temp = (username == TEMP_ADMIN_USER and password == TEMP_ADMIN_PASS)
    is_perm = (username == SETTINGS.get("admin_user") and SETTINGS.get("admin_hash") and pwd_context.verify(password, SETTINGS["admin_hash"]))

    if not (is_temp or is_perm):
        return HTMLResponse("Invalid Credentials", status_code=401)

    session_id = secrets.token_hex(16)
    # Generate the plain code for display
    plain_code = str(secrets.randbelow(9000) + 1000) 
    
    ACTIVE_SESSIONS[session_id] = {
        "user": username,
        "approved": is_perm, 
        "hashed_code": pwd_context.hash(plain_code), # Only store the hash
        "display_code": plain_code # We only need this for the verify page once
    }
    
    response = RedirectResponse(url="/admin/verify", status_code=303)
    response.set_cookie(key="admin_session", value=session_id, httponly=True)
    return response

@app.get("/admin/verify", response_class=HTMLResponse)
async def verify_page(session: dict = Depends(get_session)):
    if not session: return RedirectResponse("/admin/login")
    if session["approved"]: return RedirectResponse("/admin")

    # If code isn't approved, grab the display code once
    current_display_code = session.get("display_code")

    async def wait_for_code():
        print(f"\n[2FA-SECURE] Verification required for: {session['user']}")
        loop = asyncio.get_event_loop()
        user_input = await loop.run_in_executor(None, input, "Enter 4-Digit Code from Browser: ")
        
        # Verify the user input against the stored HASH
        if pwd_context.verify(user_input.strip(), session["hashed_code"]):
            session["approved"] = True
            # Security: Clear the plain text code from memory once approved
            session.pop("display_code", None)
            print("[2FA-SECURE] Access Granted.")
        else:
            print("[2FA-SECURE] Access Denied: Incorrect Code.")

    asyncio.create_task(wait_for_code())

    return f"""
    <html>
        <head>
            <meta http-equiv="refresh" content="3">
            <style>
                body {{ font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #fffbe6; }}
                .card {{ text-align: center; border: 2px solid #ffe58f; padding: 40px; border-radius: 12px; background: white; }}
                .code {{ font-size: 48px; font-weight: bold; letter-spacing: 10px; color: #856404; background: #fff2e8; padding: 10px; border-radius: 8px; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h2>Secure 2FA Required</h2>
                <p>Type this code into the server console to verify your identity:</p>
                <div class="code">{current_display_code}</div>
                <p><i>The server stores only a hash of this code.</i></p>
            </div>
        </body>
    </html>
    """

# --- ADMIN SETTINGS ROUTES ---

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(session: dict = Depends(get_session)):
    if not session or not session["approved"]:
        return RedirectResponse("/admin/login")
    
    stations_str = ", ".join(SETTINGS['stations'])
    return f"""
    <html>
        <head><title>Admin Panel</title>
        <style>
            body {{ font-family: sans-serif; background: #f0f2f5; padding: 20px; }}
            .container {{ max-width: 600px; margin: auto; background: white; padding: 30px; border-radius: 15px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }}
            h2 {{ color: #0055a4; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
            .form-group {{ margin-bottom: 20px; }}
            label {{ display: block; font-weight: bold; margin-bottom: 5px; }}
            input {{ width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 5px; box-sizing: border-box; }}
            button {{ background: #0055a4; color: white; border: none; padding: 12px; border-radius: 5px; cursor: pointer; width: 100%; font-size: 16px; }}
        </style>
        </head>
        <body>
            <div class="container">
                <h2>Dashboard Settings</h2>
                <form action="/admin/save" method="post">
                    <div class="form-group"><label>Stations (Comma Separated)</label><input type="text" name="stations" value="{stations_str}"></div>
                    <div class="form-group"><label>Refresh Interval (Seconds)</label><input type="number" name="interval" value="{SETTINGS['update_interval']}"></div>
                    <hr>
                    <div class="form-group"><label>Set New Permanent Password</label><input type="password" name="new_password" placeholder="Keep empty to leave unchanged"></div>
                    <button type="submit">Update & Save to JSON</button>
                </form>
                <br><a href="/" style="color: #666; text-decoration: none;">← Return to Live Board</a>
            </div>
        </body>
    </html>
    """

@app.post("/admin/save")
async def save_admin_settings(stations: str = Form(...), interval: int = Form(...), new_password: str = Form(None), session: dict = Depends(get_session)):
    if not session or not session["approved"]: return RedirectResponse("/admin/login")
    
    SETTINGS["stations"] = [s.strip() for s in stations.split(",")]
    SETTINGS["update_interval"] = max(5, interval)
    
    if new_password and len(new_password.strip()) > 0:
        SETTINGS["admin_hash"] = pwd_context.hash(new_password)
    
    save_settings()
    return RedirectResponse(url="/admin", status_code=303)

# --- PUBLIC BOARD & WEBSOCKETS ---

class ConnectionManager:
    def __init__(self): self.active_connections = []
    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active_connections.append(ws)
        if cached_departures: await ws.send_json(cached_departures)
    def disconnect(self, ws: WebSocket):
        if ws in self.active_connections: self.active_connections.remove(ws)
    async def broadcast(self, msg: list):
        for conn in self.active_connections:
            try: await conn.send_json(msg)
            except: pass

manager = ConnectionManager()

@app.get("/departures")
async def get_departures(): return JSONResponse(content=cached_departures)

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content="""
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; background: #fff; margin: 0; padding: 0; height: 100vh; width: 100vw; display: flex; flex-direction: column; overflow: hidden;}
        table { border-collapse: collapse; width: 100%; height: 100%; table-layout: fixed; }
        td { padding: 1vh 2vw; border-bottom: 2px solid #eee; vertical-align: middle; }
        .line-row { display: flex; align-items: center; gap: 15px; margin-bottom: 5px; }
        .icon-u { width: 60px; height: 60px; background: #0055a4; color: #fff; display: flex; align-items: center; justify-content: center; font-weight: bold; border-radius: 8px; font-size: 35px; }
        .icon-bus { width: 55px; height: 55px; background: #fff; color: #00656e; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 16px; border-radius: 50%; border: 4px solid #00656e; }
        .icon-tram { width: 55px; height: 55px; background: #fff; color: #e30613; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 14px; border-radius: 50%; border: 4px solid #e30613; }
        .badge { color: #fff; padding: 5px 15px; border-radius: 8px; font-weight: bold; min-width: 70px; text-align: center; font-size: 28px; }
        .bg-u { background: #0055a4; } .bg-bus { background: #00656e; } .bg-tram { background: #e30613; }
        .status { font-size: 1.2em; margin-left: 5px; font-weight: bold; }
        .ontime { color: #008542; } .delayed { color: #d00; } .scheduled { color: #666; font-style: italic; }
        .dest { font-size: 2.5em; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .min { font-weight: bold; font-size: 3em; text-align: right; white-space: nowrap; }
        .jetzt { color: #008542; animation: blink 2s infinite; }
        @keyframes blink { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }
        ::-webkit-scrollbar { display: none; }
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
            const activeDepartures = lastData.filter(dep => (dep.time - now) > -60);
            activeDepartures.forEach(dep => {
                const diffSeconds = dep.time - now;
                const minutes = Math.max(0, Math.round(diffSeconds / 60));
                const type = dep.type.toLowerCase();
                let icon = "", bCls = "bg-bus";
                if(type.includes("u-bahn")){ icon='<div class="icon-u">U</div>'; bCls="bg-u"; }
                else if(type.includes("bus")){ icon='<div class="icon-bus">BUS</div>'; bCls="bg-bus"; }
                else if(type.includes("tram")){ icon='<div class="icon-tram">TRAM</div>'; bCls="bg-tram"; }
                let status = dep.realtime ? (dep.delay > 0 ? `<span class="status delayed">+${dep.delay} min</span>` : (dep.delay < 0 ? `<span class="status ontime">${dep.delay} min</span>` : '<span class="status ontime">pünktlich</span>')) : '<span class="status scheduled">Planmäßig*</span>';
                const timeDisplay = minutes === 0 ? '<span class="jetzt">Jetzt</span>' : minutes + ' min';
                html += `<tr>
                    <td style="width:25%"><div class="line-row">${icon}<div class="badge ${bCls}">${dep.line}</div></div>${status}</td>
                    <td class="dest">${dep.destination}</td>
                    <td style="text-align:right; width:20%;"><div class="min">${timeDisplay}</div></td>
                </tr>`;
            });
            document.getElementById('board').innerHTML = html;
        }
        function connectWS() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
            ws.onmessage = (event) => { lastData = JSON.parse(event.data); updateUI(); };
            ws.onclose = () => setTimeout(connectWS, 2000);
        }
        setInterval(updateUI, 1000);
        connectWS();
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