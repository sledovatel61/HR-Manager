# -*- coding: utf-8 -*-
"""CI acceptance driver for the Tkinter GUI of the offline license issuer.

Runs with the BUNDLED python.exe of a freshly unzipped bundle (never a system
Python). It imports the real gui.py from the bundle, creates the real App
window and runs the real Tk mainloop; every step invokes the real ttk.Button
of the window (Button.invoke() = the widget's own Tcl command, the same code
path as a mouse click, minus the physical mouse) and types into the real
ttk.Entry widgets. Only the modal dialogs are replaced (file pickers return
paths inside the test directory; message boxes are recorded, never shown),
because nobody can click them in CI.

Never prints or records key material: message box texts are NOT recorded (the
"show private key" button is never invoked), only their kind and title.

usage: python.exe ci-gui-driver.py <app_dir> <work_dir> <result_json>
exit 0 = all steps passed.
ASCII-only source (non-ASCII UI strings are written as \\u escapes).
"""

from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path

APP_DIR = Path(sys.argv[1])
WORK = Path(sys.argv[2])
RESULT = Path(sys.argv[3])

# UI strings of gui.py (prefixes), as escapes to keep this file ASCII-only.
BTN_GENERATE = "\u0421\u0433\u0435\u043d"
BTN_SAVE_KEYS = "\u0421\u043e\u0445\u0440"
BTN_ISSUE = "\u0412\u044b\u043f\u0443\u0441\u0442"
BTN_VERIFY = "\u041f\u0440\u043e\u0432"
TXT_SIG_OK = "\u041f\u043e\u0434\u043f\u0438\u0441\u044c \u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u0430"
TXT_ERROR = "\u041e\u0448\u0438\u0431\u043a\u0430"

result: dict = {"ok": False, "steps": [], "python": sys.executable}


def finish(code: int) -> None:
    RESULT.write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding="utf-8")
    for s in result["steps"]:
        print(("[gui] PASS " if s["ok"] else "[gui] FAIL ") + s["step"] + " - " + s["detail"], flush=True)
    sys.exit(code)


try:
    sys.path.insert(0, str(APP_DIR))  # same as gui.py's own fallback
    import tkinter as tk
    from tkinter import ttk

    import gui  # the real module from the unzipped bundle
    if Path(gui.__file__).resolve().parent != APP_DIR.resolve():
        raise RuntimeError(f"gui.py imported from an unexpected place: {gui.__file__}")
except Exception:
    result["steps"].append({"step": "import tkinter + bundle gui.py", "ok": False, "detail": traceback.format_exc(limit=3)})
    finish(1)

keys_dir = WORK / "keys-gui"
lic_dir = WORK / "licenses"
keys_dir.mkdir(parents=True, exist_ok=True)
lic_dir.mkdir(parents=True, exist_ok=True)
lic_file = lic_dir / "gui-pilot.hrmlicense"
tampered_file = lic_dir / "gui-tampered.hrmlicense"

dialogs: list[tuple[str, str]] = []
open_queue: list[str] = []


def _record(kind):
    def _f(title=None, message=None, **_kw):
        dialogs.append((kind, str(title)))  # message deliberately not recorded
        return True if kind == "askyesno" else "ok"
    return _f


gui.messagebox.showinfo = _record("info")
gui.messagebox.showerror = _record("error")
gui.messagebox.askyesno = _record("askyesno")
gui.filedialog.askdirectory = lambda **_kw: str(keys_dir)
gui.filedialog.asksaveasfilename = lambda **_kw: str(lic_file)
gui.filedialog.askopenfilename = lambda **_kw: open_queue.pop(0) if open_queue else ""

app = gui.App()


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def button(prefix: str):
    found = [w for w in walk(app) if isinstance(w, ttk.Button) and str(w.cget("text")).startswith(prefix)]
    if len(found) != 1:
        raise LookupError(f"expected exactly one button starting with {prefix!r}, found {len(found)}")
    return found[0]


def entry_for(var) -> ttk.Entry:
    found = [w for w in walk(app) if isinstance(w, ttk.Entry) and str(w.cget("textvariable")) == str(var)]
    if len(found) != 1:
        raise LookupError(f"expected one entry bound to {var}, found {len(found)}")
    return found[0]


def type_into(var, text: str) -> None:
    e = entry_for(var)
    e.delete(0, tk.END)
    e.insert(0, text)
    app.update()
    if var.get() != text:
        raise AssertionError("entry text did not reach the bound variable")


def notebook():
    return [w for w in walk(app) if isinstance(w, ttk.Notebook)][0]


def s_window():
    app.update()
    ws = app.tk.call("tk", "windowingsystem")
    if ws != "win32":
        raise AssertionError(f"windowingsystem={ws}")
    if not app.winfo_ismapped() or not app.winfo_viewable():
        raise AssertionError("main window is not mapped/viewable")
    title = app.title()
    if "License Issuer" not in title:
        raise AssertionError("unexpected window title")
    return (f"Tk {app.tk.call('info', 'patchlevel')} windowingsystem={ws}, window mapped+viewable "
            f"{app.winfo_width()}x{app.winfo_height()}, {len(notebook().tabs())} tabs, mainloop running")


def s_generate():
    notebook().select(0)
    button(BTN_GENERATE).invoke()
    priv, pub = app.private_key_hex.get(), app.public_key_b64.get()
    if not re.fullmatch(r"[0-9a-f]{64}", priv):
        raise AssertionError("private key in the form is not 64 hex")
    if len(pub) != 44:
        raise AssertionError("public key is not 44 base64 chars")
    if not dialogs or dialogs[-1][0] != "info":
        raise AssertionError(f"no confirmation dialog: {dialogs[-1:]}")
    return "button 'generate' -> 64-hex private key (masked entry, not printed), 44-char public key, info dialog"


def s_save_keys():
    button(BTN_SAVE_KEYS).invoke()
    pf, bf = keys_dir / "private_key.hex", keys_dir / "public_key.b64"
    if pf.read_text(encoding="utf-8").strip() != app.private_key_hex.get():
        raise AssertionError("saved private key differs from the form")
    if bf.read_text(encoding="utf-8").strip() != app.public_key_b64.get():
        raise AssertionError("saved public key differs from the form")
    return "button 'save keys' -> private_key.hex + public_key.b64 written to the chosen folder, contents match the form"


def s_fill_form():
    notebook().select(1)
    type_into(app.client_name, "Pilot Maria GUI")
    type_into(app.expires_at, "2026-12-31")
    type_into(app.max_users, "5")
    return "typed client/expiry/max-users into the real ttk.Entry widgets"


def s_issue():
    button(BTN_ISSUE).invoke()
    if not lic_file.exists():
        raise AssertionError(f"license file not written; dialogs={dialogs[-2:]}")
    data = json.loads(lic_file.read_text(encoding="utf-8"))
    if data["client_name"] != "Pilot Maria GUI" or data["expires_at"] != "2026-12-31" or data["max_active_users"] != 5:
        raise AssertionError("license fields do not match the form")
    if not re.fullmatch(r"[0-9a-f]{128}", data["signature"]):
        raise AssertionError("signature is not 128 hex")
    if data["license_id"] not in app.issue_result.get("1.0", tk.END):
        raise AssertionError("result pane does not show the issued license")
    return f"button 'issue' -> {lic_file.name} written (license_id {data['license_id']}), result pane updated"


def s_verify_ok():
    notebook().select(2)
    open_queue[:] = [str(lic_file)]
    button(BTN_VERIFY).invoke()
    text = app.verify_result.get("1.0", tk.END).strip()
    if not text.startswith(TXT_SIG_OK):
        raise AssertionError("verify pane does not report a correct signature")
    if dialogs[-1][0] != "info":
        raise AssertionError(f"expected info dialog, got {dialogs[-1]}")
    return "button 'verify' on the issued license -> 'signature correct' in the pane + info dialog"


def s_verify_tampered():
    data = json.loads(lic_file.read_text(encoding="utf-8"))
    data["max_active_users"] = 6
    tampered_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    open_queue[:] = [str(tampered_file)]
    button(BTN_VERIFY).invoke()
    text = app.verify_result.get("1.0", tk.END).strip()
    if not text.startswith(TXT_ERROR):
        raise AssertionError("tampered license was not rejected in the pane")
    if dialogs[-1][0] != "error":
        raise AssertionError(f"expected error dialog, got {dialogs[-1]}")
    return "button 'verify' on a tampered copy (max_active_users 5->6) -> rejected, error dialog"


STEPS = [
    ("window + Tk runtime", s_window),
    ("generate keypair", s_generate),
    ("save keys", s_save_keys),
    ("fill issue form", s_fill_form),
    ("issue license", s_issue),
    ("verify license", s_verify_ok),
    ("verify tampered license", s_verify_tampered),
]


def run(i: int = 0) -> None:
    if i >= len(STEPS):
        result["ok"] = True
        app.after(200, app.destroy)
        return
    name, fn = STEPS[i]
    try:
        result["steps"].append({"step": name, "ok": True, "detail": fn()})
        app.after(150, run, i + 1)
    except Exception as exc:
        result["steps"].append({"step": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
        app.after(100, app.destroy)


def watchdog() -> None:
    result["steps"].append({"step": "watchdog", "ok": False, "detail": "GUI flow did not finish within 90 s"})
    app.destroy()


app.after(700, run)
app.after(90000, watchdog)
app.mainloop()
result["dialogs"] = [f"{k}:{t}" for k, t in dialogs]  # kinds/titles only, never texts
finish(0 if result["ok"] else 1)
