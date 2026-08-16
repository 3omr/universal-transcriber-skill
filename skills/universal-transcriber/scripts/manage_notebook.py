#!/usr/bin/env python3
"""
NotebookLM Management Utility
-------------------------------
Provides command-line utilities to create notebooks, list notebooks/sources,
and add local files (PDFs, PPTX, TXT, DOCX), URLs, or text notes to a NotebookLM notebook via MCP.
"""

import os
import sys
import json
import argparse
import subprocess
import time

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def get_mcp_wrapper_path():
    config = load_config()
    mcp_path = config.get("mcp_wrapper_path")
    if mcp_path and os.path.exists(mcp_path):
        return mcp_path
    
    env_path = os.environ.get("NOTEBOOKLM_MCP_PATH")
    if env_path and os.path.exists(env_path):
        return env_path
        
    default_paths = [
        "/home/omar/notebooklm-mcp-server/run_mcp.py",
        os.path.expanduser("~/notebooklm-mcp-server/run_mcp.py"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "notebooklm-mcp-server", "run_mcp.py")
    ]
    for p in default_paths:
        if os.path.exists(p):
            return p
    return default_paths[0]

def call_mcp_tool(tool_name, arguments):
    """
    Executes an MCP tool via JSON-RPC protocol talking to run_mcp.py wrapper.
    """
    mcp_path = get_mcp_wrapper_path()
    if not os.path.exists(mcp_path):
        print(f"[Error] MCP wrapper not found at {mcp_path}.")
        sys.exit(1)
        
    proc = subprocess.Popen(
        [mcp_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    
    init_req = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "universal-notebook-manager", "version": "1.0"}
        },
        "id": 1
    }
    proc.stdin.write(json.dumps(init_req) + "\n")
    proc.stdin.flush()
    proc.stdout.readline()  # Read init response
    
    initialized_notification = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized"
    }
    proc.stdin.write(json.dumps(initialized_notification) + "\n")
    proc.stdin.flush()
    
    tool_call = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        },
        "id": 2
    }
    proc.stdin.write(json.dumps(tool_call) + "\n")
    proc.stdin.flush()
    
    resp_line = proc.stdout.readline()
    try:
        resp_data = json.loads(resp_line)
        content = resp_data.get("result", {}).get("content", [])
        if content:
            raw_text = content[0].get("text", "")
            try:
                inner_data = json.loads(raw_text, strict=False)
                return inner_data
            except Exception:
                return raw_text
        return "No content in response."
    except Exception as e:
        return f"Error parsing response: {e}"
    finally:
        proc.terminate()
        proc.wait()

def create_notebook(title):
    print(f"[*] Creating new notebook: '{title}'...")
    res = call_mcp_tool("notebook_create", {"title": title})
    print("[+] Result:", json.dumps(res, indent=2, ensure_ascii=False))
    return res

def list_notebooks():
    print("[*] Listing available notebooks...")
    res = call_mcp_tool("notebook_list", {})
    print("[+] Notebooks:", json.dumps(res, indent=2, ensure_ascii=False))
    return res

def add_local_file(notebook_id, file_path):
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        print(f"[Error] File not found: {abs_path}")
        return None
    print(f"[*] Uploading local file '{os.path.basename(abs_path)}' to notebook '{notebook_id}'...")
    notebook_url = f"https://notebooklm.google.com/notebook/{notebook_id}" if not notebook_id.startswith("http") else notebook_id
    res = call_mcp_tool("source_add", {
        "source_type": "file",
        "file_path": abs_path,
        "notebook_id": notebook_id,
        "notebook_url": notebook_url
    })
    print("[+] Result:", json.dumps(res, indent=2, ensure_ascii=False))
    return res

def sync_folder(notebook_id, folder_path):
    abs_dir = os.path.abspath(folder_path)
    if not os.path.exists(abs_dir):
        print(f"[Error] Folder not found: {abs_dir}")
        return
        
    valid_exts = {".pdf", ".pptx", ".ppsx", ".docx", ".txt", ".mp3", ".m4a", ".wav"}
    print(f"[*] Auto-Syncing folder '{abs_dir}' with notebook '{notebook_id}'...")
    
    synced_log = os.path.join(abs_dir, ".synced_sources.json")
    synced_files = set()
    if os.path.exists(synced_log):
        try:
            with open(synced_log, "r", encoding="utf-8") as f:
                synced_files = set(json.load(f))
        except Exception:
            pass
            
    files_to_upload = []
    for fname in sorted(os.listdir(abs_dir)):
        ext = os.path.splitext(fname)[1].lower()
        if ext in valid_exts and fname not in synced_files:
            files_to_upload.append(fname)
            
    if not files_to_upload:
        print("[✔] All local files are already synced with NotebookLM.")
        return
        
    print(f"[*] Found {len(files_to_upload)} new file(s) to upload: {files_to_upload}")
    for fname in files_to_upload:
        fpath = os.path.join(abs_dir, fname)
        upload_succeeded = False
        for attempt in range(1, 4):
            res = add_local_file(notebook_id, fpath)
            upload_succeeded = (
                isinstance(res, dict)
                and res.get("success") is True
            )
            if upload_succeeded:
                synced_files.add(fname)
                with open(synced_log, "w", encoding="utf-8") as f:
                    json.dump(sorted(synced_files), f, indent=2, ensure_ascii=False)
                break
            if attempt < 3:
                wait_time = attempt * 5
                print(f"[!] Upload attempt {attempt}/3 failed; retrying in {wait_time}s...")
                time.sleep(wait_time)
        if not upload_succeeded:
            print(f"[!] Upload failed; leaving '{fname}' unsynced for the next retry.")
            
    with open(synced_log, "w", encoding="utf-8") as f:
        json.dump(sorted(synced_files), f, indent=2, ensure_ascii=False)
    print(f"[✔] Folder sync complete! Log updated at {synced_log}")

def add_url(notebook_id, url):
    print(f"[*] Adding URL '{url}' to notebook '{notebook_id}'...")
    res = call_mcp_tool("notebook_add_url", {
        "notebook_id": notebook_id,
        "url": url
    })
    print("[+] Result:", json.dumps(res, indent=2, ensure_ascii=False))
    return res

def add_text(notebook_id, title, text):
    print(f"[*] Adding text note '{title}' to notebook '{notebook_id}'...")
    res = call_mcp_tool("notebook_add_text", {
        "notebook_id": notebook_id,
        "title": title,
        "text": text
    })
    print("[+] Result:", json.dumps(res, indent=2, ensure_ascii=False))
    return res

def main():
    parser = argparse.ArgumentParser(description="NotebookLM Management Utility")
    subparsers = parser.add_subparsers(dest="command", help="Sub-command help")
    
    # Create notebook
    create_parser = subparsers.add_parser("create", help="Create a new notebook")
    create_parser.add_argument("--title", required=True, help="Title of the new notebook")
    
    # List notebooks
    subparsers.add_parser("list", help="List existing notebooks")
    
    # Add file
    file_parser = subparsers.add_parser("add-file", help="Add local file to notebook")
    file_parser.add_argument("--notebook-id", required=True, help="Target notebook ID")
    file_parser.add_argument("--file", required=True, help="Path to local file (PDF, PPTX, TXT, etc.)")
    
    # Add URL
    url_parser = subparsers.add_parser("add-url", help="Add URL source to notebook")
    url_parser.add_argument("--notebook-id", required=True, help="Target notebook ID")
    url_parser.add_argument("--url", required=True, help="Web URL source")
    
    # Add Text
    text_parser = subparsers.add_parser("add-text", help="Add text note to notebook")
    text_parser.add_argument("--notebook-id", required=True, help="Target notebook ID")
    text_parser.add_argument("--title", required=True, help="Note title")
    text_parser.add_argument("--text", required=True, help="Note content text")
    
    args = parser.parse_args()
    
    if args.command == "create":
        create_notebook(args.title)
    elif args.command == "list":
        list_notebooks()
    elif args.command == "add-file":
        add_local_file(args.notebook_id, args.file)
    elif args.command == "add-url":
        add_url(args.notebook_id, args.url)
    elif args.command == "add-text":
        add_text(args.notebook_id, args.title, args.text)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
