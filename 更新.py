"""一键更新脚本 - 双击运行即可"""
import os, sys, json, urllib.request, zipfile, shutil, tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from config_manager import load_config
from network_utils import build_network_opener, initialize_network_environment

REPO = "wenden1427/yaowo-uploader"
API = f"https://api.github.com/repos/{REPO}/commits/main"
ZIP = f"https://github.com/{REPO}/archive/refs/heads/main.zip"
VERSION_FILE = os.path.join(ROOT, "version.txt")

print("=" * 40)
print("  上传器 - 检查更新")
print("=" * 40)
print()

initialize_network_environment(load_config())
opener = build_network_opener("auto")

# Check remote
print("检查最新版本...")
try:
    req = urllib.request.Request(API, headers={
        "User-Agent": "YaoWo-Updater",
        "Accept": "application/vnd.github.v3+json",
    })
    with opener.open(req, timeout=10) as r:
        data = json.loads(r.read())
    remote_sha = data["sha"]
    print(f"最新版本: {remote_sha[:7]}")
except Exception as e:
    print(f"[失败] 无法连接GitHub: {e}")
    print("请检查网络或代理设置")
    input("\n按回车退出...")
    sys.exit(1)

# Check local
local = ""
try:
    with open(VERSION_FILE, "r") as f:
        local = f.read().strip()
except Exception:
    pass

if local == remote_sha:
    print("已是最新版本!")
    input("\n按回车退出...")
    sys.exit(0)

print(f"当前版本: {local[:7] if local else '未知'}")
print("发现新版本! 开始下载...")

# Download
tmp = os.path.join(tempfile.gettempdir(), "uploader_update.zip")
extract = os.path.join(tempfile.gettempdir(), "uploader_update_extract")
zip_req = urllib.request.Request(ZIP, headers={"User-Agent": "YaoWo-Updater"})
with opener.open(zip_req, timeout=60) as response:
    with open(tmp, "wb") as handle:
        shutil.copyfileobj(response, handle)

# Extract
if os.path.exists(extract):
    shutil.rmtree(extract)
with zipfile.ZipFile(tmp, "r") as zf:
    zf.extractall(extract)

# Apply
inner = os.path.join(extract, os.listdir(extract)[0])
for item in os.listdir(inner):
    src = os.path.join(inner, item)
    dst = os.path.join(ROOT, item)
    if item == ".gitignore":
        continue
    if os.path.isfile(src):
        shutil.copy2(src, dst)
        print(f"  {item}")

# Cleanup
os.remove(tmp)
shutil.rmtree(extract)

# Save version
with open(VERSION_FILE, "w") as f:
    f.write(remote_sha)

print("\n更新完成! 请重新启动上传器。")
input("\n按回车退出...")
