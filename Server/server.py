import os
import socket
import struct
import threading

# --- CONFIGURATION ---
ROOT = os.path.abspath("D:/") # Ensure absolute path
PORT = 3445
MAGIC = b"LDT1"

# Protocol Constants
TYPE_LIST      = 0x01
TYPE_LIST_RESP = 0x02
TYPE_GET       = 0x03
TYPE_META      = 0x04  # ACK + file metadata (size)
TYPE_CHUNK     = 0x05
TYPE_ERR       = 0x07

CHUNK_SIZE = 1024 * 1024  # 1MB chunks

def pack(msg_type, payload=b""):
    """Packs a message: Magic(4) + Type(1) + Length(4) + Payload(N)"""
    return MAGIC + struct.pack(">BI", msg_type, len(payload)) + payload

def recv_exact(sock, n):
    """Ensures exactly n bytes are read from the socket."""
    buf = b""
    while len(buf) < n:
        try:
            d = sock.recv(n - len(buf))
            if not d:
                raise ConnectionError("Connection closed by peer")
            buf += d
        except socket.error as e:
            raise ConnectionError(f"Socket error: {e}")
    return buf

def recv_packet(sock):
    """Parses the next protocol packet."""
    magic = recv_exact(sock, 4)
    if magic != MAGIC:
        raise ValueError(f"Invalid protocol magic: {magic}")
    
    header = recv_exact(sock, 5) # 1 byte type + 4 bytes length
    t, ln = struct.unpack(">BI", header)
    
    payload = recv_exact(sock, ln)
    return t, payload

def safe_path(rel_path):
    """Prevents directory traversal attacks."""
    # Normalize path and remove leading slashes/dots
    rel_path = rel_path.lstrip("\\/ ")
    full = os.path.abspath(os.path.join(ROOT, rel_path))
    
    # Security Check: Ensure 'full' is inside 'ROOT'
    # We use join(ROOT, '') to ensure a trailing separator for the prefix check
    root_prefix = os.path.join(ROOT, "")
    if not full.startswith(root_prefix):
        raise ValueError("Security: Path traversal attempt blocked")
    return full

def list_dir(rel_path):
    """Safely lists directory contents."""
    try:
        full = safe_path(rel_path)
        if not os.path.isdir(full):
            return []
        
        out = []
        for name in os.listdir(full):
            p = os.path.join(full, name)
            try:
                st = os.stat(p)
                out.append({
                    "name": name,
                    "dir": os.path.isdir(p),
                    "size": st.st_size if not os.path.isdir(p) else 0,
                    "mtime": int(st.st_mtime)
                })
            except (PermissionError, OSError):
                continue # Skip files we can't access
        return out
    except Exception:
        return []

def handle_client(sock, addr):
    print(f"[+] Handling {addr}")
    try:
        while True:
            try:
                t, payload = recv_packet(sock)
            except ConnectionError:
                break # Client disconnected gracefully

            if t == TYPE_LIST:
                path = payload.decode('utf-8', errors='replace')
                items = list_dir(path)
                data = "\n".join(
                    f"{i['name']}|{int(i['dir'])}|{i['size']}|{i['mtime']}"
                    for i in items
                ).encode('utf-8')
                sock.sendall(pack(TYPE_LIST_RESP, data))

            elif t == TYPE_GET:
                # Format: "path|start_byte|end_byte"
                parts = payload.decode('utf-8', errors='replace').split("|")
                if len(parts) != 3:
                    raise ValueError("Invalid GET request format")
                
                path, start_str, end_str = parts
                full_path = safe_path(path)
                
                if not os.path.isfile(full_path):
                    raise FileNotFoundError(f"File not found: {path}")

                file_size = os.path.getsize(full_path)
                start = max(0, int(start_str))
                # If end is -1 or beyond file size, cap it at file_size
                end = int(end_str)
                if end < 0 or end > file_size:
                    end = file_size

                # Send Meta (Total File Size)
                sock.sendall(pack(TYPE_META, str(file_size).encode('utf-8')))

                # Data streaming
                if end > start:
                    with open(full_path, "rb") as f:
                        f.seek(start)
                        remaining = end - start
                        while remaining > 0:
                            to_read = min(CHUNK_SIZE, remaining)
                            chunk = f.read(to_read)
                            if not chunk:
                                break
                            sock.sendall(pack(TYPE_CHUNK, chunk))
                            remaining -= len(chunk)

    except Exception as e:
        print(f"[!] Error with {addr}: {e}")
        try:
            sock.sendall(pack(TYPE_ERR, str(e).encode('utf-8')))
        except:
            pass
    finally:
        sock.close()
        print(f"[-] Disconnected: {addr}")

def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", PORT))
    except PermissionError:
        print(f"Error: Could not bind to port {PORT}. Try a higher port or run as admin.")
        return
        
    s.listen(100)
    print(f"Server listening on port {PORT} — serving {ROOT}")
    
    while True:
        try:
            client_sock, addr = s.accept()
            t = threading.Thread(target=handle_client, args=(client_sock, addr), daemon=True)
            t.start()
        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    main()