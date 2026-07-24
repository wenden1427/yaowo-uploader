# Author: Administrator
# Created: 2026-05-25
"""Entry point for 耀我科技上传器 v2.0. Startup check + launch GUI."""

import sys
import os
import hashlib
import shutil
import tkinter.messagebox as mb

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync_bundled_template():
    """Install the universal template delivered by legacy top-level updaters."""
    source = os.path.join(SKILL_DIR, "default_template.xlsx")
    destination = os.path.join(SKILL_DIR, "uploader", "韩国上传模板.xlsx")
    if not os.path.isfile(source):
        return None
    try:
        if os.path.isfile(destination) and _file_sha256(source) == _file_sha256(destination):
            return None
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy2(source, destination)
        return None
    except Exception as exc:
        return str(exc)


def check_python_version() -> bool:
    """Require Python 3.9+."""
    if sys.version_info < (3, 9):
        mb.showerror("Python 版本过低",
                     f"当前 Python {sys.version_info.major}.{sys.version_info.minor}，需要 3.9 或更高版本。")
        return False
    return True


def check_modules() -> bool:
    """Verify required third-party modules are importable."""
    missing = []
    for mod in ["openpyxl", "yaml"]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    try:
        import PIL.Image
    except ImportError:
        missing.append("Pillow")
    try:
        import tkinter
    except ImportError:
        missing.append("tkinter")
    if missing:
        mb.showerror("缺少依赖",
                     f"未安装以下模块:\n  {', '.join(missing)}\n\n请运行: pip install openpyxl pyyaml Pillow")
        return False
    return True


def check_files() -> dict:
    """Check required support files exist. Returns dict of statuses."""
    uploader_dir = os.path.join(SKILL_DIR, "uploader")
    files = {
        "template": os.path.join(uploader_dir, "韩国上传模板.xlsx"),
        "category": os.path.join(uploader_dir, "카테고리목록(类别代码K列).xls"),
        "banned": os.path.join(uploader_dir, "违禁词gmk.txt"),
    }
    status = {}
    for name, path in files.items():
        status[name] = os.path.exists(path)
    return status


def main(network_report=None):
    """Run startup checks, then launch GUI."""
    if not check_python_version():
        sys.exit(1)
    if not check_modules():
        sys.exit(1)

    template_sync_error = sync_bundled_template()
    file_status = check_files()
    warnings = []
    if network_report:
        warnings.extend(network_report.warnings)
    if template_sync_error:
        warnings.append(f"通用模板更新失败: {template_sync_error}")
    names = {"template": "上架模板 (韩国上传模板.xlsx)",
             "category": "类目代码表 (카테고리목록.xls)",
             "banned": "违禁词 (违禁词gmk.txt)"}
    for key, exists in file_status.items():
        if not exists:
            warnings.append(f"⚠ 未找到: {names[key]}")

    from config_manager import load_config
    cfg = load_config()
    if not cfg.get("deepseek_key"):
        warnings.append("⚠ 未配置 DeepSeek API Key (设置→API配置)")

    from gui import UploaderApp

    # Check for saved state
    restore_state = None
    if UploaderApp.has_saved_state():
        import tkinter.messagebox as mb
        if mb.askyesno("恢复会话", "检测到上次未完成的处理，是否继续？\n\n是 = 恢复上次进度\n否 = 重新开始"):
            restore_state = UploaderApp.load_saved_state()
        else:
            UploaderApp.clear_saved_state()

    app = UploaderApp(startup_warnings=warnings, restore_state=restore_state)
    app.run()


if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from config_manager import load_config
    from network_utils import initialize_network_environment
    network_report = initialize_network_environment(load_config())
    import tkinter as tk
    import updater
    root = tk.Tk(); root.withdraw()
    if updater.check_and_update(root):
        root.destroy()
        main(network_report=network_report)
    else:
        root.destroy()
