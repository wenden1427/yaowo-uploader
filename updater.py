"""Auto-updater for 耀我科技上传器."""

import os, json, urllib.request, urllib.error, zipfile, shutil, subprocess, sys, tkinter as tk
from tkinter import messagebox; import tempfile

REPO_API = "https://api.github.com/repos/wenden1427/yaowo-uploader/commits/main"
REPO_ZIP = "https://github.com/wenden1427/yaowo-uploader/archive/refs/heads/main.zip"
REPO_VERSION = "https://raw.githubusercontent.com/wenden1427/yaowo-uploader/main/version.txt"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(SCRIPT_DIR, "version.txt")


def _get_local_version():
    if os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, "r") as f:
            version = f.read().strip()
            if version:
                return version
    try:
        result = subprocess.run(
            ["git", "-C", SCRIPT_DIR, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        sha = result.stdout.strip()
        if result.returncode == 0 and len(sha) == 40:
            return sha
    except Exception:
        pass
    return ""


def _get_remote_version():
    try:
        req = urllib.request.Request(REPO_VERSION, headers={
            "User-Agent": "YaoWo-Uploader-Updater/1.0",
        })
        with _make_network_opener().open(req, timeout=10) as resp:
            version = resp.read().decode("utf-8", errors="replace").strip()
            return version or None
    except Exception:
        return _get_remote_commit_sha()


def _get_remote_commit_sha():
    try:
        req = urllib.request.Request(REPO_API, headers={
            "User-Agent": "YaoWo-Uploader-Updater/1.0",
            "Accept": "application/vnd.github.v3+json",
        })
        with _make_network_opener().open(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("sha", "")
    except Exception:
        return None


def _make_network_opener():
    """Build an opener that respects the configured/system proxy."""
    try:
        from config_manager import detect_proxy
        proxy = detect_proxy()
        if proxy:
            return urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            )
    except Exception:
        pass
    return urllib.request.build_opener()


def _save_version(sha):
    with open(VERSION_FILE, "w") as f:
        f.write(sha)


def check_and_update(root):
    local = _get_local_version()
    remote = _get_remote_version()
    remote_sha = _get_remote_commit_sha()
    if not remote:
        return True
    if not local:
        _save_version(remote)
        return True
    if local in {remote, remote_sha}:
        return True

    result = messagebox.askyesno(
        "发现新版本",
        f"上传器有新版本可用！\n\n当前: {local[:7]}...\n最新: {remote[:7]}...\n\n是否立即更新？\n(更新后会自动重启)",
    )
    if not result:
        return True
    return _do_update(remote)


def _do_update(remote_sha):
    try:
        tmp = os.path.join(tempfile.gettempdir(), "yaowo_uploader_update.zip")
        extract_dir = os.path.join(tempfile.gettempdir(), "yaowo_uploader_update_extract")

        req = urllib.request.Request(REPO_ZIP, headers={"User-Agent": "YaoWo-Uploader-Updater/1.0"})
        with _make_network_opener().open(req, timeout=60) as resp:
            with open(tmp, "wb") as f:
                f.write(resp.read())

        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)
        with zipfile.ZipFile(tmp, "r") as zf:
            zf.extractall(extract_dir)

        inner = os.path.join(extract_dir, os.listdir(extract_dir)[0])
        if not os.path.exists(inner):
            messagebox.showerror("更新失败", "更新包结构异常")
            return True

        for item in os.listdir(inner):
            src = os.path.join(inner, item)
            dst = os.path.join(SCRIPT_DIR, item)
            if item == ".gitignore":
                continue
            if os.path.isfile(src):
                shutil.copy2(src, dst)

        os.remove(tmp)
        shutil.rmtree(extract_dir)

        _save_version(remote_sha)
        subprocess.Popen([sys.executable, os.path.join(SCRIPT_DIR, "main.py")])
        sys.exit(0)
    except Exception as e:
        messagebox.showerror("更新失败", str(e))
        return True
