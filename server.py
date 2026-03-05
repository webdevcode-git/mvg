from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import asyncio
import main 
from datetime import datetime

app = FastAPI()

FIXED_STATIONS = ["Klinikum Großhadern", "Max-Lebschke Platz"]
UPDATE_INTERVAL = 10
cached_departures = []

async def update_departures():
    global cached_departures
    while True:
        try:
            cached_departures = main.mvg_api(FIXED_STATIONS, api_type="departures", combine_departures=True)
        except Exception as e:
            print(e)
        await asyncio.sleep(UPDATE_INTERVAL)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(update_departures())

@app.get("/departures")
async def get_departures():
    return JSONResponse(content=cached_departures)

@app.get("/", response_class=HTMLResponse)
async def index():
    html_content = """
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<style>
    body { font-family: Arial, sans-serif; background: #fff; padding: 20px; }
    table { border-collapse: collapse; width: 100%; max-width: 800px; }
    td { padding: 12px 8px; border-bottom: 1px solid #eee; vertical-align: middle; }
    .line-row { display: flex; align-items: center; gap: 10px; margin-bottom: 2px; }
    .icon-u { width: 28px; height: 28px; background: #0055a4; color: #fff; display: flex; align-items: center; justify-content: center; font-weight: bold; border-radius: 4px; }
    .icon-bus { width: 24px; height: 24px; background: #fff; color: #00656e; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 9px; border-radius: 50%; border: 2px solid #00656e; }
    .icon-tram { width: 24px; height: 24px; background: #fff; color: #e30613; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 8px; border-radius: 50%; border: 2px solid #e30613; }
    .badge { color: #fff; padding: 3px 8px; border-radius: 4px; font-weight: bold; min-width: 35px; text-align: center; }
    .bg-u { background: #0055a4; }
    .bg-bus { background: #00656e; }
    .bg-tram { background: #e30613; }
    .status { font-size: 0.8em; margin-left: 2px; }
    .ontime { color: #008542; }
    .delayed { color: #d00; }
    .scheduled { color: #666; font-style: italic; }
    .dest { font-size: 1.1em; font-weight: 500; }
    .min { font-weight: bold; font-size: 1.2em; text-align: right; }
    
    /* Animation for "Jetzt" */
    .jetzt { color: #008542; animation: blink 2s infinite; }
    @keyframes blink { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }
</style>
</head>
<body>
    <table><tbody id="board"></tbody></table>
<script>
async function update() {
    try {
        const res = await fetch('/departures');
        const data = await res.json();
        const now = Date.now() / 1000;
        let html = "";
        data.forEach(dep => {
            const diffSeconds = dep.time - now;
            const minutes = Math.max(0, Math.round(diffSeconds / 60));
            
            const type = dep.type.toLowerCase();
            let icon = "", bCls = "bg-bus";
            if(type.includes("u-bahn")){ icon='<div class="icon-u">U</div>'; bCls="bg-u"; }
            else if(type.includes("bus")){ icon='<div class="icon-bus">BUS</div>'; bCls="bg-bus"; }
            else if(type.includes("tram")){ icon='<div class="icon-tram">TRAM</div>'; bCls="bg-tram"; }
            
            let status = '<span class="status scheduled">Planmäßig</span>';
            if (dep.realtime) {
                if (dep.delay > 0) status = `<span class="status delayed">+${dep.delay} min</span>`;
                else if (dep.delay < 0) status = `<span class="status ontime">${dep.delay} min</span>`;
                else status = '<span class="status ontime">pünktlich</span>';
            }

            // Display "Jetzt" if 0 minutes
            const timeDisplay = minutes === 0 ? '<span class="jetzt">Jetzt</span>' : minutes + ' min';

            html += `<tr>
                <td style="width:130px"><div class="line-row">${icon}<div class="badge ${bCls}">${dep.line}</div></div>${status}</td>
                <td class="dest">${dep.destination}</td>
                <td style="text-align:right"><div class="min">${timeDisplay}</div></td>
            </tr>`;
        });
        document.getElementById('board').innerHTML = html;
    } catch(e) {}
}
update(); setInterval(update, 10000);
</script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)