#!/usr/bin/env python3
"""
LDT1 File Transfer - Command Line Browser
Simple interactive browser for LDT1 servers
"""

import socket
import struct
import threading
import os
import time
import sys
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime
import signal

# --- CONFIGURATION ---
MAGIC = b"LDT1"
PORT = 3445

# Protocol Constants
TYPE_LIST = 0x01
TYPE_LIST_RESP = 0x02
TYPE_GET = 0x03
TYPE_META = 0x04
TYPE_CHUNK = 0x05
TYPE_ERR = 0x07

# ANSI color codes
class Colors:
    if sys.platform == "win32":
        RESET = "\033[0m"
        RED = "\033[91m"
        GREEN = "\033[92m"
        YELLOW = "\033[93m"
        BLUE = "\033[94m"
        CYAN = "\033[96m"
        BOLD = "\033[1m"
        GRAY = "\033[90m"
    else:
        RESET = "\033[0m"
        RED = "\033[91m"
        GREEN = "\033[92m"
        YELLOW = "\033[93m"
        BLUE = "\033[94m"
        CYAN = "\033[96m"
        BOLD = "\033[1m"
        GRAY = "\033[90m"

def clear_screen():
    """Clear the terminal screen."""
    if sys.platform == "win32":
        os.system('cls')
    else:
        os.system('clear')

def print_banner():
    """Print the application banner."""
    print(f"{Colors.CYAN}{'='*60}")
    print(f"{Colors.BOLD}      LDT1 File Transfer Browser")
    print(f"{'='*60}{Colors.RESET}\n")

def pack(t: int, p: bytes = b"") -> bytes:
    """Pack a protocol packet."""
    return MAGIC + struct.pack(">BI", t, len(p)) + p

def recv_exact(s: socket.socket, n: int, timeout: int = 10) -> bytes:
    """Receive exactly n bytes from socket."""
    s.settimeout(timeout)
    b = b""
    while len(b) < n:
        try:
            d = s.recv(n - len(b))
            if not d:
                raise ConnectionError("Connection closed")
            b += d
        except socket.timeout:
            raise ConnectionError("Timeout")
        except Exception as e:
            raise ConnectionError(f"Receive error: {e}")
    return b

def recv_packet(s: socket.socket, timeout: int = 10) -> tuple:
    """Receive a complete protocol packet."""
    try:
        if recv_exact(s, 4, timeout) != MAGIC:
            raise ValueError("Bad magic")
        t, ln = struct.unpack(">BI", recv_exact(s, 5, timeout))
        payload = recv_exact(s, ln, timeout)
        if t == TYPE_ERR:
            raise Exception(payload.decode('utf-8', errors='ignore'))
        return t, payload
    except Exception as e:
        raise ConnectionError(f"Packet error: {e}")

class FileBrowser:
    def __init__(self):
        self.server_ip = "127.0.0.1"
        self.current_path = "/"
        self.downloads = {}
        self.download_lock = threading.Lock()
        self.running = True
    
    def test_connection(self, ip: str) -> bool:
        """Test if we can connect to the server."""
        try:
            print(f"{Colors.YELLOW}Testing connection to {ip}...{Colors.RESET}")
            s = socket.socket()
            s.settimeout(3)
            s.connect((ip, PORT))
            s.close()
            self.server_ip = ip
            print(f"{Colors.GREEN}✓ Connected{Colors.RESET}")
            return True
        except Exception as e:
            print(f"{Colors.RED}✗ Failed: {e}{Colors.RESET}")
            return False
    
    def list_directory(self, path: str = None) -> List[Dict]:
        """List contents of a directory on the server."""
        path = path or self.current_path
        try:
            s = socket.socket()
            s.settimeout(5)
            s.connect((self.server_ip, PORT))
            s.sendall(pack(TYPE_LIST, path.replace("\\", "/").encode('utf-8')))
            t, data = recv_packet(s)
            s.close()
            
            items = []
            if t == TYPE_LIST_RESP:
                for line in data.decode('utf-8', errors='ignore').splitlines():
                    if not line.strip():
                        continue
                    parts = line.split("|")
                    if len(parts) == 4:
                        n, d, sz, mt = parts
                        items.append({
                            "name": n,
                            "is_dir": bool(int(d)),
                            "size": int(sz),
                            "mtime": int(mt)
                        })
            return items
        except Exception as e:
            print(f"{Colors.RED}Error: {e}{Colors.RESET}")
            return []
    
    def get_file_size(self, remote_path: str) -> int:
        """Get size of a file on the server."""
        try:
            s = socket.socket()
            s.settimeout(10)
            s.connect((self.server_ip, PORT))
            s.sendall(pack(TYPE_GET, f"{remote_path}|0|1".encode()))
            t, data = recv_packet(s)
            s.close()
            return int(data.decode()) if t == TYPE_META else 0
        except:
            return 0
    
    def download_file(self, remote_path: str):
        """Start downloading a file."""
        filename = os.path.basename(remote_path)
        default_path = os.path.join(os.getcwd(), "downloads", filename)
        os.makedirs(os.path.dirname(default_path), exist_ok=True)
        
        # Ask for save location
        print(f"\n{Colors.YELLOW}Where to save '{filename}'?")
        print(f"Press Enter for: {default_path}{Colors.RESET}")
        
        save_path = input(f"{Colors.CYAN}Save to: {Colors.RESET}").strip()
        if not save_path:
            save_path = default_path
        
        # Create download entry
        with self.download_lock:
            self.downloads[filename] = {
                "remote_path": remote_path,
                "local_path": save_path,
                "size": 0,
                "downloaded": 0,
                "start_time": time.time(),
                "status": "starting",
                "percent": 0
            }
        
        # Start download thread
        thread = threading.Thread(
            target=self._download_thread,
            args=(remote_path, save_path, filename),
            daemon=True
        )
        thread.start()
        
        print(f"{Colors.GREEN}✓ Download started in background{Colors.RESET}")
        print(f"  Type 'progress' to check status")
    
    def _download_thread(self, remote_path: str, save_path: str, filename: str):
        """Background thread for downloading."""
        try:
            # Get file size
            size = self.get_file_size(remote_path)
            if size == 0:
                raise ValueError("File not found or empty")
            
            with self.download_lock:
                self.downloads[filename].update({
                    "size": size,
                    "status": "downloading"
                })
            
            # Create directory if needed
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            
            # Download file
            s = socket.socket()
            s.connect((self.server_ip, PORT))
            s.sendall(pack(TYPE_GET, f"{remote_path}|0|{size}".encode()))
            
            # Get metadata
            recv_packet(s)
            
            # Download chunks
            with open(save_path, "wb") as f:
                pos = 0
                while pos < size:
                    t, data = recv_packet(s)
                    if t != TYPE_CHUNK:
                        break
                    
                    f.write(data)
                    pos += len(data)
                    
                    # Update progress
                    percent = (pos / size * 100) if size > 0 else 0
                    with self.download_lock:
                        self.downloads[filename].update({
                            "downloaded": pos,
                            "percent": percent
                        })
            
            s.close()
            
            # Mark as complete
            elapsed = time.time() - self.downloads[filename]["start_time"]
            with self.download_lock:
                self.downloads[filename].update({
                    "status": "completed",
                    "percent": 100
                })
            
        except Exception as e:
            with self.download_lock:
                self.downloads[filename]["status"] = f"error: {str(e)}"
    
    def show_progress(self):
        """Show download progress."""
        with self.download_lock:
            if not self.downloads:
                print(f"{Colors.GRAY}No active downloads{Colors.RESET}")
                return
            
            print(f"\n{Colors.BOLD}Active Downloads:{Colors.RESET}")
            print(f"{Colors.CYAN}{'─'*50}{Colors.RESET}")
            
            for filename, info in self.downloads.items():
                status = info["status"]
                if "completed" in status:
                    color = Colors.GREEN
                elif "error" in status:
                    color = Colors.RED
                else:
                    color = Colors.YELLOW
                
                # Simple progress bar
                bar_width = 30
                filled = int(bar_width * info["percent"] / 100)
                bar = "█" * filled + "░" * (bar_width - filled)
                
                print(f"{Colors.BOLD}{filename}{Colors.RESET}")
                print(f"  [{bar}] {info['percent']:.1f}%")
                print(f"  Status: {color}{status}{Colors.RESET}")
                
                if info["size"] > 0:
                    size_str = self._format_size(info["downloaded"])
                    total_str = self._format_size(info["size"])
                    print(f"  {size_str} / {total_str}")
                
                if "error" not in status and info["percent"] < 100:
                    elapsed = time.time() - info["start_time"]
                    if elapsed > 0:
                        speed = info["downloaded"] / elapsed / 1024  # KB/s
                        print(f"  Speed: {speed:.1f} KB/s")
                
                print()
    
    def _format_size(self, size: int) -> str:
        """Format bytes to human readable."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
    
    def _format_time(self, timestamp: int) -> str:
        """Format timestamp."""
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    
    def display_files(self, items: List[Dict]):
        """Display files in a nice format."""
        if not items:
            print(f"{Colors.GRAY}(empty){Colors.RESET}")
            return
        
        # Separate dirs and files
        dirs = [item for item in items if item["is_dir"]]
        files = [item for item in items if not item["is_dir"]]
        
        # Sort alphabetically
        dirs.sort(key=lambda x: x["name"].lower())
        files.sort(key=lambda x: x["name"].lower())
        
        # Display directories
        print(f"{Colors.BOLD}Directories:{Colors.RESET}")
        if dirs:
            for item in dirs:
                print(f"  {Colors.BLUE}📁 {item['name']}/{Colors.RESET}")
        else:
            print(f"  {Colors.GRAY}(none){Colors.RESET}")
        
        print(f"\n{Colors.BOLD}Files:{Colors.RESET}")
        if files:
            for item in files:
                size_str = self._format_size(item["size"])
                time_str = self._format_time(item["mtime"])
                print(f"  {Colors.GREEN}📄 {item['name']}{Colors.RESET}")
                print(f"        Size: {size_str:>8}  Modified: {time_str}")
        else:
            print(f"  {Colors.GRAY}(none){Colors.RESET}")
    
    def show_help(self):
        """Show help menu."""
        print(f"\n{Colors.BOLD}Available Commands:{Colors.RESET}")
        print(f"{Colors.CYAN}{'─'*50}{Colors.RESET}")
        print(f"  {Colors.GREEN}ls{Colors.RESET}           - List current directory")
        print(f"  {Colors.GREEN}cd <dir>{Colors.RESET}     - Change directory")
        print(f"  {Colors.GREEN}cd ..{Colors.RESET}        - Go to parent directory")
        print(f"  {Colors.GREEN}get <file>{Colors.RESET}   - Download a file")
        print(f"  {Colors.GREEN}pwd{Colors.RESET}          - Show current path")
        print(f"  {Colors.GREEN}progress{Colors.RESET}     - Show download progress")
        print(f"  {Colors.GREEN}clear{Colors.RESET}        - Clear screen")
        print(f"  {Colors.GREEN}help{Colors.RESET}         - Show this help")
        print(f"  {Colors.GREEN}quit{Colors.RESET}         - Exit")
        print(f"{Colors.CYAN}{'─'*50}{Colors.RESET}")

def main():
    """Main interactive browser."""
    # Handle Ctrl+C
    def signal_handler(sig, frame):
        print(f"\n{Colors.YELLOW}Exiting...{Colors.RESET}")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Clear and show banner
    clear_screen()
    print_banner()
    
    # Create browser
    browser = FileBrowser()
    
    # Get server IP
    print(f"{Colors.CYAN}Welcome to LDT1 File Browser!{Colors.RESET}\n")
    
    while True:
        ip = input(f"{Colors.YELLOW}Enter server IP [{browser.server_ip}]: {Colors.RESET}").strip()
        if not ip:
            ip = browser.server_ip
        
        if browser.test_connection(ip):
            break
        else:
            print(f"{Colors.RED}Could not connect. Please try again.{Colors.RESET}")
    
    # Main browser loop
    clear_screen()
    print_banner()
    print(f"{Colors.GREEN}Connected to: {browser.server_ip}{Colors.RESET}")
    print(f"Type 'help' for commands\n")
    
    while browser.running:
        try:
            # Show current path
            print(f"{Colors.BOLD}Path: {browser.current_path}{Colors.RESET}")
            
            # List current directory
            items = browser.list_directory(browser.current_path)
            browser.display_files(items)
            
            # Get command
            print(f"\n{Colors.CYAN}{'─'*50}{Colors.RESET}")
            cmd = input(f"{Colors.YELLOW}Command: {Colors.RESET}").strip()
            
            if not cmd:
                clear_screen()
                print_banner()
                continue
            
            parts = cmd.split(maxsplit=1)
            action = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""
            
            if action == "ls":
                clear_screen()
                print_banner()
                print(f"{Colors.GREEN}Connected to: {browser.server_ip}{Colors.RESET}\n")
                # Will be displayed at top of loop
                continue
                
            elif action == "cd":
                if arg == "..":
                    # Go up one directory
                    parts = browser.current_path.rstrip("/").split("/")
                    if len(parts) > 1:
                        browser.current_path = "/" + "/".join(parts[:-1])
                    else:
                        browser.current_path = "/"
                elif arg:
                    # Check if it's a valid directory
                    new_path = arg
                    if not new_path.startswith("/"):
                        new_path = browser.current_path.rstrip("/") + "/" + new_path
                    
                    # Test if directory exists
                    test_items = browser.list_directory(new_path)
                    if test_items is not None:
                        browser.current_path = new_path
                    else:
                        print(f"{Colors.RED}No such directory{Colors.RESET}")
                
                clear_screen()
                print_banner()
                print(f"{Colors.GREEN}Connected to: {browser.server_ip}{Colors.RESET}\n")
                
            elif action == "get":
                if not arg:
                    print(f"{Colors.RED}Usage: get <filename>{Colors.RESET}")
                    continue
                
                # Construct full path
                if arg.startswith("/"):
                    remote_path = arg
                else:
                    remote_path = browser.current_path.rstrip("/") + "/" + arg
                
                browser.download_file(remote_path)
                input(f"\n{Colors.YELLOW}Press Enter to continue...{Colors.RESET}")
                clear_screen()
                print_banner()
                print(f"{Colors.GREEN}Connected to: {browser.server_ip}{Colors.RESET}\n")
                
            elif action == "pwd":
                print(f"\n{Colors.GREEN}Current path: {browser.current_path}{Colors.RESET}")
                input(f"\n{Colors.YELLOW}Press Enter to continue...{Colors.RESET}")
                clear_screen()
                print_banner()
                print(f"{Colors.GREEN}Connected to: {browser.server_ip}{Colors.RESET}\n")
                
            elif action == "progress":
                browser.show_progress()
                input(f"\n{Colors.YELLOW}Press Enter to continue...{Colors.RESET}")
                clear_screen()
                print_banner()
                print(f"{Colors.GREEN}Connected to: {browser.server_ip}{Colors.RESET}\n")
                
            elif action == "clear":
                clear_screen()
                print_banner()
                print(f"{Colors.GREEN}Connected to: {browser.server_ip}{Colors.RESET}\n")
                
            elif action == "help":
                browser.show_help()
                input(f"\n{Colors.YELLOW}Press Enter to continue...{Colors.RESET}")
                clear_screen()
                print_banner()
                print(f"{Colors.GREEN}Connected to: {browser.server_ip}{Colors.RESET}\n")
                
            elif action in ["quit", "exit", "q"]:
                print(f"\n{Colors.CYAN}Goodbye!{Colors.RESET}")
                browser.running = False
                break
                
            else:
                print(f"{Colors.RED}Unknown command: {action}{Colors.RESET}")
                print(f"Type 'help' for available commands")
                input(f"\n{Colors.YELLOW}Press Enter to continue...{Colors.RESET}")
                clear_screen()
                print_banner()
                print(f"{Colors.GREEN}Connected to: {browser.server_ip}{Colors.RESET}\n")
                
        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Use 'quit' to exit{Colors.RESET}")
        except Exception as e:
            print(f"{Colors.RED}Error: {e}{Colors.RESET}")
            input(f"\n{Colors.YELLOW}Press Enter to continue...{Colors.RESET}")
            clear_screen()
            print_banner()
            print(f"{Colors.GREEN}Connected to: {browser.server_ip}{Colors.RESET}\n")

if __name__ == "__main__":
    main()
