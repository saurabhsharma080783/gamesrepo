"""Commands for the assessment appliance VM image (see deploy/appliance/README.md).

* ``assess-run``: start the local model, build the reports with AI narrative, stop the model, and
  optionally deallocate the VM so compute billing stops.
* ``assess-mode``: switch between the model on this VM (``local``) and a hosted model server (``hosted``).
* ``assess-idle-check``: run by a timer; deallocates the VM when nobody is logged in and nothing has
  run for a while, as a safety net against a VM left running.

Deallocation uses the VM's system-assigned managed identity through the Azure Instance Metadata
Service (IMDS); the deployment template grants that identity rights on this VM only. No credentials
are stored on the VM.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ETC = Path(os.environ.get("ASSESS_ETC", "/etc/azure-assess"))
DATA = Path(os.environ.get("ASSESS_DATA", "/srv/assessments"))
STATE = Path(os.environ.get("ASSESS_STATE", "/var/lib/azure-assess"))
LLM_UNIT = "llm-server.service"
LOCAL_URL = "http://127.0.0.1:8080/v1"
IMDS = os.environ.get("ASSESS_IMDS", "http://169.254.169.254")
ARM = os.environ.get("ASSESS_ARM", "https://management.azure.com")
COMPUTE_API = "2024-07-01"


class ApplianceError(RuntimeError):
    pass


# ---- Azure: deallocate this VM ----------------------------------------------------

def _imds(path: str) -> dict:
    req = urllib.request.Request(f"{IMDS}{path}", headers={"Metadata": "true"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise ApplianceError(f"Azure Instance Metadata Service not reachable ({exc}); is this an Azure VM?") from exc


def deallocate() -> str:
    """Ask Azure Resource Manager to deallocate this VM. Returns the VM resource ID."""
    vm_id = _imds("/metadata/instance/compute?api-version=2021-02-01")["resourceId"]
    token = _imds("/metadata/identity/oauth2/token?api-version=2018-02-01"
                  "&resource=https%3A%2F%2Fmanagement.azure.com%2F").get("access_token")
    if not token:
        raise ApplianceError("no managed identity token: enable the VM's system-assigned identity")
    req = urllib.request.Request(f"{ARM}{vm_id}/deallocate?api-version={COMPUTE_API}", method="POST", data=b"",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status not in (200, 202):
                raise ApplianceError(f"deallocate returned HTTP {r.status}")
    except urllib.error.HTTPError as exc:
        hint = (" The VM's managed identity needs the Virtual Machine Contributor role on this VM "
                "(the deployment template assigns it).") if exc.code == 403 else ""
        raise ApplianceError(f"deallocate failed with HTTP {exc.code}.{hint}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ApplianceError(f"cannot reach Azure Resource Manager: {exc}") from exc
    return vm_id


# ---- local model server -------------------------------------------------------------

def _systemctl(*args: str, check: bool = True) -> None:
    # Operators are not root: the image's sudoers rule lets the 'assess' group start/stop the model server.
    sudo = ["sudo", "-n"] if hasattr(os, "geteuid") and os.geteuid() != 0 else []
    subprocess.run([*sudo, "systemctl", *args], check=check)


def _llm_settings() -> dict:
    try:
        return json.loads((ETC / "llm.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplianceError(f"cannot read {ETC / 'llm.json'}: {exc}") from exc


def wait_for_server(url: str, timeout: float = 300) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(f"{url}/models", timeout=5):
                return
        except (urllib.error.URLError, OSError):
            if time.monotonic() > deadline:
                raise ApplianceError(f"the local model server did not start within {timeout:.0f}s; "
                                     f"see: journalctl -u {LLM_UNIT}") from None
            time.sleep(2)


# ---- assess-run -------------------------------------------------------------------

def run_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="assess-run",
        description="Build assessment reports on this appliance. Drafts the narrative with the configured model "
                    "unless --ai-draft is given (rebuild from a reviewed draft, no model needed).")
    ap.add_argument("inputs", nargs="+", metavar="FILE", help="Customer inventory export(s)")
    ap.add_argument("-c", "--customer", required=True)
    ap.add_argument("--config", help="Assessment policy JSON (required tags, allowed regions, ...)")
    ap.add_argument("-f", "--formats", default="docx,html")
    ap.add_argument("--inventory-date")
    ap.add_argument("-o", "--out", help=f"Output folder (default: {DATA}/<customer>/<date-time>)")
    ap.add_argument("--ai-draft", metavar="FILE", help="Rebuild from a reviewed *-ai-draft.json")
    ap.add_argument("--reviewed-by", metavar="NAME", default="")
    ap.add_argument("--no-ai", action="store_true", help="Standard wording only")
    ap.add_argument("--deallocate", action="store_true",
                    help="Deallocate this VM when finished, so compute billing stops")
    args = ap.parse_args(argv)

    from .cli import customer_slug, main as assess_main

    out = Path(args.out) if args.out else DATA / customer_slug(args.customer) / datetime.now().strftime("%Y%m%d-%H%M")
    cmd = ["report", *args.inputs, "-c", args.customer, "-f", args.formats, "-o", str(out)]
    for flag, value in (("--config", args.config), ("--inventory-date", args.inventory_date)):
        if value:
            cmd += [flag, value]
    local = False
    if args.ai_draft:
        cmd += ["--ai-draft", args.ai_draft] + (["--reviewed-by", args.reviewed_by] if args.reviewed_by else [])
    elif not args.no_ai:
        cmd += ["--ai"]
        local = _llm_settings().get("hosting") == "local"

    _touch_activity()
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "run.pid").write_text(str(os.getpid()))
    except OSError:
        pass
    rc = 1
    try:
        if local:
            print("Starting the local model (this can take a minute) ...")
            _systemctl("start", LLM_UNIT)
            wait_for_server(_llm_settings().get("url", LOCAL_URL))
        rc = assess_main(cmd)
    except ApplianceError as exc:
        print(f"error: {exc}", file=sys.stderr)
    finally:
        if local:
            _systemctl("stop", LLM_UNIT, check=False)   # frees ~5 GB of memory
        (STATE / "run.pid").unlink(missing_ok=True)
        _touch_activity()
    if rc == 0:
        print(f"\nReports are in {out}")
        if not args.ai_draft and not args.no_ai:
            print("Next: review the AI text in the *-ai-draft.json file, then rebuild with\n"
                  f"  assess-run {' '.join(args.inputs)} -c \"{args.customer}\" --ai-draft <file> "
                  "--reviewed-by \"<your name>\"")
    if args.deallocate:
        if rc != 0:
            print("Not deallocating because the run failed; fix the problem or deallocate from the portal.",
                  file=sys.stderr)
        else:
            os.sync()
            print("Deallocating this VM; compute billing stops once Azure finishes (your session will end).")
            try:
                deallocate()
            except ApplianceError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
    return rc


# ---- assess-mode ------------------------------------------------------------------

def _write_settings(data: dict) -> None:
    ETC.mkdir(parents=True, exist_ok=True)
    tmp = ETC / "llm.json.tmp"
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.chmod(0o644)
    tmp.replace(ETC / "llm.json")


def mode_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="assess-mode", description="Choose where the language model runs")
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("show", help="Show the current settings")
    sub.add_parser("local", help="Use the model built into this VM (no data leaves the VM)")
    h = sub.add_parser("hosted", help="Use a model server hosted by the assessor")
    h.add_argument("--url", required=True, help="https:// base URL of the hosted model server, ending in /v1")
    h.add_argument("--model", default="", help="Model name on that server")
    h.add_argument("--provider", required=True, help="Who operates the server (shown in the reports)")
    h.add_argument("--key-file", required=True, help="File holding the API key, or - to read it from stdin")
    h.add_argument("--ca-bundle", default="", help="Extra CA certificate (PEM) to trust")
    args = ap.parse_args(argv)

    from .ai.settings import LLMSettings, SettingsError

    if args.mode == "show":
        print((ETC / "llm.json").read_text() if (ETC / "llm.json").exists() else "not configured")
        return 0
    base = json.loads((ETC / "llm.local.json").read_text()) if (ETC / "llm.local.json").exists() else {}
    if args.mode == "local":
        if not base:
            print(f"error: {ETC / 'llm.local.json'} is missing; this image has no local model", file=sys.stderr)
            return 1
        _write_settings(base)
        (ETC / "llm.key").unlink(missing_ok=True)
        print("Mode: local. The model on this VM will be used; it starts only while a report is being drafted.")
        return 0

    key = sys.stdin.readline().strip() if args.key_file == "-" else Path(args.key_file).read_text().strip()
    if not key:
        print("error: the API key is empty", file=sys.stderr)
        return 1
    key_path = ETC / "llm.key"
    key_path.touch(mode=0o640, exist_ok=True)
    key_path.chmod(0o640)
    key_path.write_text(key + "\n")
    if shutil.which("chgrp"):
        subprocess.run(["chgrp", "assess", str(key_path)], check=False)
    data = {"hosting": "hosted", "url": args.url, "provider": args.provider, "api_key_file": str(key_path),
            "timeout": 300, "workers": 2}
    if args.model:
        data["model"] = args.model
    if args.ca_bundle:
        data["ca_bundle"] = args.ca_bundle
    try:
        LLMSettings(**data).validate()
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _write_settings(data)
    subprocess.run(["systemctl", "disable", "--now", LLM_UNIT], check=False)
    print(f"Mode: hosted ({args.provider}, {args.url}). Only summarised facts will be sent; test with: azure-assess ai-check")
    return 0


# ---- assess-idle-check ------------------------------------------------------------

def _touch_activity() -> None:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "last-activity").touch()
    except OSError:
        pass


def _logged_in_users() -> int:
    out = subprocess.run(["who"], capture_output=True, text=True, check=False).stdout
    return len([ln for ln in out.splitlines() if ln.strip()])


def _report_running() -> bool:
    """True while an assess-run holds the lock (its PID is still alive)."""
    try:
        pid = int((STATE / "run.pid").read_text())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def idle_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="assess-idle-check",
                                 description="Deallocate the VM after a period with no logins and no reports")
    ap.add_argument("--idle-minutes", type=int, default=int(os.environ.get("ASSESS_IDLE_MINUTES", "60")))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.idle_minutes <= 0:
        return 0   # disabled
    if _logged_in_users() or _report_running():
        _touch_activity()
        return 0
    marker = STATE / "last-activity"
    if not marker.exists():
        _touch_activity()   # first check after boot starts the clock
        return 0
    idle = (time.time() - marker.stat().st_mtime) / 60
    if idle < args.idle_minutes:
        return 0
    print(f"Idle for {idle:.0f} minutes with nobody logged in: deallocating to stop compute billing.")
    if args.dry_run:
        return 0
    try:
        deallocate()
    except ApplianceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
