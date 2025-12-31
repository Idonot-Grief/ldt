import socket
import struct
import threading
import os
import time
import webview

# --- CONFIGURATION ---
MAGIC = b"LDT1"
PORT = 3445

# Protocol Constants
TYPE_LIST      = 0x01
TYPE_LIST_RESP = 0x02
TYPE_GET       = 0x03
TYPE_META      = 0x04
TYPE_CHUNK     = 0x05
TYPE_ERR       = 0x07

def pack(t, p=b""):
    return MAGIC + struct.pack(">BI", t, len(p)) + p

def recv_exact(s, n, timeout=10):
    s.settimeout(timeout)
    b = b""
    while len(b) < n:
        try:
            d = s.recv(n - len(b))
            if not d: raise ConnectionError("Closed")
            b += d
        except: raise ConnectionError("Timeout/Error")
    return b

def recv_packet(s, timeout=10):
    try:
        if recv_exact(s, 4, timeout) != MAGIC: raise ValueError("Bad Magic")
        t, ln = struct.unpack(">BI", recv_exact(s, 5, timeout))
        payload = recv_exact(s, ln, timeout)
        if t == TYPE_ERR: raise Exception(payload.decode())
        return t, payload
    except Exception as e:
        raise ConnectionError(str(e))

class API:
    def __init__(self):
        self.server = "127.0.0.1"
        self.downloads = {}
        self.lock = threading.Lock()

    def connect_to_server(self, addr):
        """Validates the server address and attempts a test connection."""
        addr = addr.strip()
        if not addr:
            return {"success": False, "error": "IP Address cannot be blank"}
        
        self.server = addr
        try:
            # Test connection
            s = socket.socket()
            s.settimeout(3)
            s.connect((self.server, PORT))
            s.close()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list(self, path):
        try:
            s = socket.socket()
            s.settimeout(5)
            s.connect((self.server, PORT))
            s.sendall(pack(TYPE_LIST, path.replace("\\", "/").encode('utf-8')))
            t, data = recv_packet(s)
            s.close()

            items = []
            if t == TYPE_LIST_RESP:
                for line in data.decode(errors="ignore").splitlines():
                    if not line.strip(): continue
                    parts = line.split("|")
                    if len(parts) == 4:
                        n, d, sz, mt = parts
                        items.append({"name": n, "dir": bool(int(d)), "size": int(sz), "mtime": int(mt)})
            return {"success": True, "items": items}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def enqueue(self, remote_path):
        window = webview.windows[0]
        filename = os.path.basename(remote_path)
        res = window.create_file_dialog(webview.SAVE_DIALOG, save_filename=filename)
        if not res: return False
        
        save_path = res[0]
        with self.lock:
            self.downloads[filename] = {"remote_path": remote_path, "save_path": save_path, "size": 0, "downloaded": 0, "start_time": None, "status": "preparing"}
        
        threading.Thread(target=self._download, args=(remote_path, save_path, filename), daemon=True).start()
        return True

    def get_progress(self):
        with self.lock:
            now = time.time()
            progress = {}
            for name, info in list(self.downloads.items()):
                dl, sz, st = info["downloaded"], info["size"], info["start_time"]
                percent = (dl / sz * 100) if sz > 0 else 0
                speed = (dl / (now - st) / (1024*1024)) if st and (now-st) > 0.1 else 0
                progress[name] = {"percent": round(percent, 1), "speed": round(speed, 2), "status": info["status"]}
            return progress

    def _get_size(self, path):
        s = socket.socket()
        s.settimeout(10)
        s.connect((self.server, PORT))
        s.sendall(pack(TYPE_GET, f"{path}|0|1".encode()))
        t, data = recv_packet(s)
        s.close()
        return int(data.decode()) if t == TYPE_META else 0

    def _download(self, remote_path, save_path, filename):
        try:
            size = self._get_size(remote_path)
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "wb") as f:
                if size > 0: f.seek(size-1); f.write(b"\0")
            
            with self.lock: self.downloads[filename].update({"size": size, "start_time": time.time(), "status": "downloading"})
            
            # Simple single-threaded download for reliability in this fix
            s = socket.socket(); s.connect((self.server, PORT))
            s.sendall(pack(TYPE_GET, f"{remote_path}|0|{size}".encode()))
            recv_packet(s) # Meta
            
            with open(save_path, "r+b") as f:
                pos = 0
                while pos < size:
                    t, data = recv_packet(s)
                    if t != TYPE_CHUNK: break
                    f.seek(pos); f.write(data)
                    pos += len(data)
                    with self.lock: self.downloads[filename]["downloaded"] = pos
            
            with self.lock: self.downloads[filename]["status"] = "completed"
        except Exception as e:
            with self.lock: self.downloads[filename]["status"] = f"Error: {e}"

html = r"""
<!DOCTYPE html>
<html>
<head>
<style>
    body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica; background:#111; color:#eee; }
    #top { padding:12px; background:#1a1a1a; display:flex; gap:10px; align-items:center; border-bottom:1px solid #333; }
    input { background:#222; color:#eee; border:1px solid #444; padding:8px; border-radius:4px; outline:none; }
    button { background:#0066cc; color:white; border:none; padding:8px 16px; border-radius:4px; cursor:pointer; font-weight:bold; }
    button:hover { background:#0052a3; }
    #status { font-size: 12px; padding: 4px 12px; background: #222; color: #888; border-bottom: 1px solid #333; }
    #main { display:grid; grid-template-columns: 1fr 1fr; height:calc(100vh - 90px); }
    #browser, #queue { padding:15px; overflow-y:auto; }
    .item { padding:10px; cursor:pointer; border-radius:4px; border-bottom: 1px solid #222; }
    .item:hover { background:#222; }
    .progress-bg { height:6px; background:#333; border-radius:3px; margin:8px 0; }
    .progress-fill { height:100%; background:#0066cc; width:0%; transition:0.3s; }
    .error { color: #ff4444; }
    .success { color: #44ff44; }
</style>
</head>
<body>
<div id="top">
    <input id="server" value="127.0.0.1" placeholder="Server IP">
    <input id="path" value="/" style="flex:1" placeholder="Remote Path">
    <button onclick="doConnect()">CONNECT</button>
</div>
<div id="status">Ready</div>
<div id="main">
    <div id="browser"><h3>Files</h3><div id="fileList"></div></div>
    <div id="queue"><h3>Downloads</h3><div id="queueList"></div></div>
</div>

<script>
    let currentPath = "/";
    const queueItems = new Map();

    function setStatus(msg, isError=false) {
        const s = document.getElementById("status");
        s.innerText = msg;
        s.className = isError ? "error" : "success";
    }

    function doConnect() {
        const ip = document.getElementById("server").value;
        setStatus("Connecting to " + ip + "...");
        pywebview.api.connect_to_server(ip).then(res => {
            if (res.success) {
                setStatus("Connected to " + ip);
                load();
            } else {
                setStatus("Connection Failed: " + res.error, true);
            }
        });
    }

    function load() {
        pywebview.api.list(currentPath).then(res => {
            if (!res.success) {
                setStatus("List Error: " + res.error, true);
                return;
            }
            const list = document.getElementById("fileList");
            list.innerHTML = `<div class="item" onclick="goUp()"><b>[ .. ] Parent Directory</b></div>`;
            res.items.forEach(item => {
                const div = document.createElement("div");
                div.className = "item";
                div.innerHTML = `<span>${item.dir ? "📁" : "📄"} ${item.name}</span>`;
                div.onclick = () => {
                    if (item.dir) {
                        currentPath = (currentPath.endsWith("/") ? currentPath : currentPath + "/") + item.name;
                        load();
                    } else {
                        pywebview.api.enqueue((currentPath.endsWith("/") ? currentPath : currentPath + "/") + item.name);
                    }
                };
                list.appendChild(div);
            });
        });
    }

    function goUp() {
        let parts = currentPath.split("/").filter(x => x);
        parts.pop();
        currentPath = "/" + parts.join("/");
        load();
    }

    function updateProgress() {
        pywebview.api.get_progress().then(progress => {
            const list = document.getElementById("queueList");
            for (const [name, info] of Object.entries(progress)) {
                let el = queueItems.get(name);
                if (!el) {
                    el = document.createElement("div");
                    el.style.marginBottom = "15px";
                    el.innerHTML = `<b>${name}</b><div class="progress-bg"><div class="progress-fill"></div></div><div class="info" style="font-size:12px;color:#888"></div>`;
                    list.appendChild(el);
                    queueItems.set(name, el);
                }
                el.querySelector(".progress-fill").style.width = info.percent + "%";
                el.querySelector(".info").innerText = `${info.status} - ${info.percent}% (${info.speed} MB/s)`;
            }
        });
    }

    setInterval(updateProgress, 1000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    api = API()
    webview.create_window("LDT1 File Transfer", html=html, js_api=api, width=1000, height=700)
    webview.start()