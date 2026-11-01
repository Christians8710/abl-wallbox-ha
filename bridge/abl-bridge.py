#!/usr/bin/python3
"""USB serial discovery and supervision for Debian stable ser2net.

Legacy mode forwards bytes to a HA client. Optional standalone mode owns the
bus locally and exposes the authenticated controller API. Stable USB port mapping.
"""
import html
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

CONFIG = Path("/etc/abl-bridge.json")
STATE = Path("/var/lib/abl-bridge")
RUN = Path("/run/abl-bridge")
UI = Path(__file__).resolve().parent / "ui"
if not UI.is_dir():
    UI = Path("/usr/local/share/abl-bridge/ui")
CONTROLLER = UI.parent / "controller"
if not CONTROLLER.is_dir():
    CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
try:
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
except OSError:
    boot_id = 'unknown'
status = {"adapters": [], "service": "starting", "process_id":os.getpid(), "boot_id":boot_id, "reboot_pending":False}
stop = threading.Event()
reboot_token = secrets.token_urlsafe(32)
reboot_lock = threading.Lock()

def schedule_board_reboot():
    """Use a separate systemd timer so the HTTP response arrives first."""
    subprocess.run(['/usr/bin/systemd-run','--quiet','--collect',
                    '--unit=abl-bridge-board-reboot','--on-active=5s',
                    '/usr/bin/systemctl','reboot'],
                   check=True,capture_output=True,timeout=5)

def discover():
    devices = {}
    for entry in sorted(Path("/dev/serial/by-id").glob("*")):
        target = entry.resolve()
        if entry.exists() and target.name.startswith(("ttyUSB", "ttyACM")):
            devices.setdefault(str(target), (entry.name, str(target)))
    for pattern in ("ttyUSB*", "ttyACM*"):
        for entry in sorted(Path("/dev").glob(pattern)):
            if str(entry) in devices:
                continue
            # devtmpfs creates ttyUSB before udev creates its stable by-id link.
            # Do not allocate a temporary port while that USB event is pending.
            node = Path('/sys/class/tty') / entry.name / 'device'
            if not node.exists():
                continue
            has_usb_serial = None
            for parent in (node.resolve(), *node.resolve().parents):
                if (parent/'idVendor').exists():
                    serial = parent/'serial'
                    try:
                        has_usb_serial = serial.exists() and bool(serial.read_text().strip())
                    except OSError:
                        has_usb_serial = True
                    break
            if has_usb_serial is not False:
                continue
            devices.setdefault(str(entry), (entry.name, str(entry)))
    return sorted(devices.values())

def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2)+"\n", encoding="utf-8")
    temporary.replace(path)

class StatusPage(BaseHTTPRequestHandler):
    def reply(self, code, body, kind='application/json'):
        self.send_response(code)
        self.send_header('Content-Type',kind+'; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy',"frame-ancestors 'none'")
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def json_reply(self, code, value):
        self.reply(code,json.dumps(value,ensure_ascii=False).encode('utf-8'))

    def do_POST(self):
        api = getattr(self.server, 'controller_api', None)
        if self.path.startswith('/api/v1/') and api:
            api.post(self)
            return
        if self.path != '/reboot':
            self.send_error(404)
            return
        if api and not api.auth.context(self):
            self.json_reply(401, {'error': 'Bitte zuerst an der Wallbox-Oberfläche anmelden.'})
            return
        # Protect the LAN control action against cross-site requests and DNS rebinding.
        try:
            target = urlsplit('http://'+self.headers.get('Host',''))
            origin = urlsplit(self.headers.get('Origin',''))
            allowed = {self.connection.getsockname()[0],socket.gethostname().lower(),
                       socket.gethostname().lower()+'.local','abl-bridge','abl-bridge.local','localhost'}
            same_origin = (origin.scheme=='http' and not origin.username and not origin.password and not origin.path and not origin.query and
                           not origin.fragment and origin.hostname==target.hostname and
                           (origin.port or 80)==(target.port or 80))
            valid_host = (target.hostname in allowed and not target.username and not target.password and
                          not target.path and not target.query and not target.fragment and
                          (target.port or 80)==self.server.server_address[1])
        except ValueError:
            same_origin = valid_host = False
        supplied = self.headers.get('X-ABL-CSRF','').encode('utf-8')
        if not same_origin or not valid_host or not secrets.compare_digest(supplied,reboot_token.encode('ascii')):
            self.json_reply(403,{'error':'Neustart nicht bestaetigt. Bitte die Seite neu laden.'})
            return
        if self.headers.get('Content-Type','').split(';')[0].strip().lower() != 'application/json':
            self.json_reply(415,{'error':'Ungueltige Anfrage.'})
            return
        try:
            length = int(self.headers.get('Content-Length','-1'))
            if not 0 < length <= 1024:
                self.json_reply(413 if length>1024 else 400,{'error':'Ungueltige Anfragegroesse.'})
                return
            self.connection.settimeout(5)
            value = json.loads(self.rfile.read(length).decode('utf-8'))
            if not isinstance(value,dict) or value.get('confirm') is not True:
                self.json_reply(400,{'error':'Bestaetigung fehlt.'})
                return
        except (ValueError,UnicodeDecodeError,socket.timeout):
            self.json_reply(400,{'error':'Ungueltige Anfrage.'})
            return
        with reboot_lock:
            if not status['reboot_pending']:
                try:
                    schedule_board_reboot()
                except (OSError,subprocess.SubprocessError) as error:
                    print('Could not schedule board reboot: '+str(error),flush=True)
                    self.json_reply(503,{'error':'Neustart konnte nicht vorbereitet werden. Bitte erneut versuchen.'})
                    return
                status['reboot_pending'] = True
        self.json_reply(202,{'ok':True,'message':'Tinker Board startet neu.','boot_id':boot_id})

    def do_GET(self):
        api = getattr(self.server, 'controller_api', None)
        if self.path.startswith('/api/v1/') and api:
            api.get(self)
            return
        if api and self.path == '/':
            self.reply(200, (CONTROLLER / 'web/index.html').read_bytes(), 'text/html')
            return
        if api and self.path == '/controller-assets/app.js':
            self.reply(200, (CONTROLLER / 'web/app.js').read_bytes(), 'text/javascript')
            return
        assets = {"/assets/abl-design.css": ("abl-design.css", "text/css"),
                  "/assets/icon.png": ("icon.png", "image/png"),
                  "/assets/LICENSE": ("LICENSE", "text/plain")}
        if self.path in assets:
            name, kind = assets[self.path]
            self.reply(200, (UI / name).read_bytes(), kind)
            return
        if self.path in ("/status.json", "/health"):
            body = json.dumps(status, ensure_ascii=False).encode("utf-8")
            kind = "application/json"
        elif self.path in ("/", "/bridge/"):
            rows = "".join("<tr><td>"+html.escape(row["adapter"])+"</td><td>"+str(row["tcp_port"])+"</td><td>"+html.escape(row["serial"])+"</td></tr>" for row in status["adapters"])
            message = "CONFCAB erkannt" if rows else "Wartet auf USB-Adapter"
            body = ((UI / "status.html").read_text(encoding="utf-8")
                    .replace("{{STATUS}}", message)
                    .replace("{{ROWS}}", rows or '<tr><td colspan="3">Noch kein USB-Adapter angeschlossen.</td></tr>')
                    .replace("{{COUNT}}", str(len(status["adapters"]))) + '''<script>
const csrfToken='''+json.dumps(reboot_token)+''';
const currentBoot='''+json.dumps(boot_id)+''';
const alreadyPending='''+json.dumps(status['reboot_pending'])+''';
const button=document.getElementById('reboot-button');
const message=document.getElementById('reboot-message');
let refresh=setTimeout(updateStatus,10000);
let statusBusy=false;
async function updateStatus(){
  if(statusBusy || button.disabled)return;
  statusBusy=true;clearTimeout(refresh);
  const control=document.getElementById('status-button');control.disabled=true;
  const feedback=document.getElementById('bridge-feedback');
  try{
    const response=await fetch('/status.json',{cache:'no-store',signal:AbortSignal.timeout(4000)});
    if(!response.ok)throw new Error();
    const state=await response.json();
    document.getElementById('usb-status').textContent=state.adapters.length?'CONFCAB erkannt':'Wartet auf USB-Adapter';
    document.getElementById('usb-count').textContent='Erkannte Schnittstellen: '+state.adapters.length;
    const rows=document.getElementById('usb-rows');rows.replaceChildren();
    for(const adapter of state.adapters){
      const row=document.createElement('tr');
      for(const value of [adapter.adapter,adapter.tcp_port,adapter.serial]){
        const cell=document.createElement('td');cell.textContent=String(value);row.append(cell);
      }rows.append(row);
    }
    if(!state.adapters.length){const row=document.createElement('tr');const cell=document.createElement('td');cell.colSpan=3;cell.textContent='Noch kein USB-Adapter angeschlossen.';row.append(cell);rows.append(row);}
    feedback.textContent='';
    if(state.reboot_pending){waitForBoard(currentBoot);}
  }catch(error){feedback.textContent='Status nicht erreichbar. Angezeigte Werte sind möglicherweise veraltet.';}
  finally{statusBusy=false;control.disabled=false;if(!button.disabled)refresh=setTimeout(updateStatus,10000);}
}
document.getElementById('status-button').addEventListener('click',updateStatus);
async function waitForBoard(previousBoot){
  button.disabled=true;
  message.textContent='Neustart angefordert. Warte, bis das Board wieder erreichbar ist …';
  let attempts=0;
  async function poll(){
    try{
      const response=await fetch('/status.json',{cache:'no-store',signal:AbortSignal.timeout(4000)});
      if(response.ok){
        const state=await response.json();
        if(state.boot_id && state.boot_id!==previousBoot && !state.reboot_pending){location.reload();return;}
      }
    }catch(error){}
    if(++attempts<60){setTimeout(poll,3000);}
    else{message.textContent='Das Board ist noch nicht zurück. Bitte diese Seite später neu laden.';}
  }
  setTimeout(poll,4000);
}
button.addEventListener('click',async()=>{
  if(!confirm('Tinker Board wirklich neu starten? Die Verbindung zu Home Assistant wird kurz unterbrochen.'))return;
  clearTimeout(refresh);
  button.disabled=true;
  message.textContent='Neustart wird angefordert …';
  try{
    const response=await fetch('/reboot',{method:'POST',headers:{'Content-Type':'application/json','X-ABL-CSRF':csrfToken},body:JSON.stringify({confirm:true})});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error||'Neustart konnte nicht angefordert werden.');
    waitForBoard(result.boot_id||currentBoot);
  }catch(error){message.textContent=error.message+' Bitte gegebenenfalls die Seite neu laden.';button.disabled=false;}
});
if(alreadyPending){clearTimeout(refresh);waitForBoard(currentBoot);}
</script></body></html>''').encode("utf-8")
            kind = "text/html"
        else:
            self.send_error(404)
            return
        self.reply(200,body,kind)
    def log_message(self, *_):
        pass

def main():
    config = json.loads(CONFIG.read_text())
    baud = int(config["baudrate"])
    comset = str(config["comset"]).upper()
    if baud not in (9600,19200,38400,57600,115200) or comset not in ("8E1","8N1"):
        raise ValueError("Unsupported serial configuration")
    framing = f"{baud}{comset[1].lower()}{comset[0]}{comset[2]}"
    STATE.mkdir(parents=True,exist_ok=True)
    RUN.mkdir(parents=True,exist_ok=True)
    assignments_file = STATE/"ports.json"
    assignments = json.loads(assignments_file.read_text()) if assignments_file.exists() else {}
    if not isinstance(assignments,dict) or any(not isinstance(key,str) or type(port) is not int or not 1024 <= port <= 65535 for key,port in assignments.items()) or len(set(assignments.values())) != len(assignments):
        raise ValueError("Invalid persistent USB port assignments")
    worker = None
    api = None
    controller_config = config.get('controller', {})
    if controller_config.get('enabled'):
        sys.path.insert(0, str(CONTROLLER.parent))
        from controller.runtime import ControllerRuntime, RuntimeThread
        from controller.http_api import ControllerAPI
        from controller.shared import p
        controller_bus = p.Bus('tcp', host='127.0.0.1', port=int(controller_config.get('tcp_port', config['base_port'])))
        runtime = ControllerRuntime(p.Controller(controller_bus, int(controller_config.get('address', 1))), STATE / 'controller')
        worker = RuntimeThread(runtime)
        api = ControllerAPI(worker, STATE / 'controller/api-key')
        worker.start()
    listen_host = config.get('listen_host', '0.0.0.0')
    if listen_host not in ('0.0.0.0', '127.0.0.1'):
        raise ValueError('Unsupported bridge listening address')
    if worker and listen_host != '127.0.0.1':
        raise ValueError('The standalone controller requires a loopback-only serial bridge')
    server = ThreadingHTTPServer(("0.0.0.0",int(config["status_port"])),StatusPage)
    server.controller_api = api
    threading.Thread(target=server.serve_forever,daemon=True).start()
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig,lambda *_: stop.set())
    process = None
    previous = None
    try:
        while not stop.is_set():
            found = discover()
            rows = []
            changed = False
            for adapter,device in found:
                if adapter not in assignments:
                    fallback_key = Path(device).name
                    if fallback_key in assignments and fallback_key != adapter:
                        port = assignments.pop(fallback_key)
                    else:
                        port = int(config["base_port"])
                        while port in assignments.values(): port += 1
                    if port > 65535: raise ValueError("TCP port range exhausted")
                    assignments[adapter] = port
                    changed = True
                rows.append({"adapter":adapter,"device":device,"tcp_port":assignments[adapter],"serial":f"{baud} {comset}"})
            if changed: atomic_json(assignments_file,assignments)
            if process is not None and process.poll() is not None:
                raise RuntimeError(f"ser2net exited with {process.returncode}")
            if rows != previous:
                if rows:
                    connections = {}
                    for row in rows:
                        connections[f"confcab_{row['tcp_port']}"] = {
                            "accepter":f"tcp,{listen_host},{row['tcp_port']}",
                            "connector":f"serialdev,{row['device']},{framing},local",
                            "timeout":120,
                            "options":{"max-connections":1,"kickolduser":False}}
                    # JSON is also valid YAML, and selects ser2net's v2 schema.
                    atomic_json(RUN/"ser2net.yaml",{"connections":connections})
                    if process is None:
                        process = subprocess.Popen(["/usr/sbin/ser2net","-n","-c",str(RUN/"ser2net.yaml")])
                    else:
                        process.send_signal(signal.SIGHUP)
                elif process is not None:
                    process.terminate()
                    process.wait(timeout=10)
                    process = None
                status.update({"adapters":rows,"service":"ready" if rows else "waiting_for_usb","baudrate":baud,"comset":comset})
                atomic_json(RUN/"status.json",status)
                print(json.dumps(status),flush=True)
                previous = rows
            stop.wait(2)
    finally:
        if worker is not None:
            worker.stop()
        if process is not None and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        server.shutdown()

if __name__ == "__main__":
    main()
