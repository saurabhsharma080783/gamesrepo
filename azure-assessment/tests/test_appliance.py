"""Appliance commands: deallocate via managed identity, idle check, assess-run and assess-mode."""
import io
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from azure_assessment import appliance

VM_ID = "/subscriptions/s1/resourceGroups/rg-assess/providers/Microsoft.Compute/virtualMachines/vm-assess"


class _Azure(BaseHTTPRequestHandler):
    """Mock IMDS + ARM. ``status`` controls the deallocate response."""
    status = 202
    calls: list = []

    def log_message(self, *a):
        pass

    def _json(self, body, code=200):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        assert self.headers.get("Metadata") == "true"
        if self.path.startswith("/metadata/instance/compute"):
            self._json({"resourceId": VM_ID})
        elif self.path.startswith("/metadata/identity/oauth2/token"):
            assert "resource=https%3A%2F%2Fmanagement.azure.com%2F" in self.path
            self._json({"access_token": "tok"})

    def do_POST(self):
        type(self).calls.append((self.path, self.headers.get("Authorization")))
        self._json({}, type(self).status)


@pytest.fixture
def azure(monkeypatch):
    _Azure.status, _Azure.calls = 202, []
    srv = HTTPServer(("127.0.0.1", 0), _Azure)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    monkeypatch.setattr(appliance, "IMDS", url)
    monkeypatch.setattr(appliance, "ARM", url)
    yield _Azure
    srv.shutdown()


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    for name in ("ETC", "DATA", "STATE"):
        d = tmp_path / name.lower()
        d.mkdir()
        monkeypatch.setattr(appliance, name, d)
    return tmp_path


def test_deallocate_uses_managed_identity(azure):
    assert appliance.deallocate() == VM_ID
    assert azure.calls == [(f"{VM_ID}/deallocate?api-version={appliance.COMPUTE_API}", "Bearer tok")]


def test_deallocate_forbidden_explains_role(azure):
    azure.status = 403
    with pytest.raises(appliance.ApplianceError, match="Virtual Machine Contributor"):
        appliance.deallocate()


def test_deallocate_off_azure(monkeypatch):
    monkeypatch.setattr(appliance, "IMDS", "http://127.0.0.1:9")
    with pytest.raises(appliance.ApplianceError, match="Instance Metadata Service not reachable"):
        appliance.deallocate()


def test_idle_check(dirs, monkeypatch):
    called = []
    monkeypatch.setattr(appliance, "deallocate", lambda: called.append(1))
    monkeypatch.setattr(appliance, "_logged_in_users", lambda: 0)
    marker = appliance.STATE / "last-activity"

    assert appliance.idle_main(["--idle-minutes", "60"]) == 0 and marker.exists() and not called  # starts clock
    os.utime(marker, (time.time() - 61 * 60,) * 2)
    (appliance.STATE / "run.pid").write_text(str(os.getpid()))          # a report is running
    appliance.idle_main(["--idle-minutes", "60"])
    assert not called
    (appliance.STATE / "run.pid").write_text("999999999")                # stale lock: process gone
    os.utime(marker, (time.time() - 61 * 60,) * 2)
    appliance.idle_main(["--idle-minutes", "60"])
    assert called == [1]

    monkeypatch.setattr(appliance, "_logged_in_users", lambda: 1)        # someone logged in: reset the clock
    os.utime(marker, (time.time() - 120 * 60,) * 2)
    appliance.idle_main(["--idle-minutes", "60"])
    assert called == [1] and time.time() - marker.stat().st_mtime < 60
    assert appliance.idle_main(["--idle-minutes", "0"]) == 0              # disabled


def _local_mode(dirs, url):
    settings = {"hosting": "local", "url": url, "model": "mock-7b", "timeout": 60, "workers": 1}
    (appliance.ETC / "llm.json").write_text(json.dumps(settings))
    (appliance.ETC / "llm.local.json").write_text(json.dumps(settings))


def test_assess_run_local_model_then_deallocate(dirs, monkeypatch, capsys):
    from test_ai_deploy import _MockLLM, _serve
    srv = _serve(_MockLLM)
    url = f"http://127.0.0.1:{srv.server_port}/v1"
    _local_mode(dirs, url)
    monkeypatch.setenv("AZURE_ASSESS_LLM_CONFIG", str(appliance.ETC / "llm.json"))
    systemctl, deallocated = [], []
    monkeypatch.setattr(appliance, "_systemctl", lambda *a, **kw: systemctl.append(a))
    monkeypatch.setattr(appliance, "deallocate", lambda: deallocated.append(1))

    from azure_assessment.loaders import csv_loader, sample
    inv = dirs / "inventory.csv"
    csv_loader.save(sample.build().resources, inv)
    policy = dirs / "policy.json"
    policy.write_text(json.dumps({"required_tags": ["owner"]}))
    rc = appliance.run_main([str(inv), "-c", "Fabrikam", "-f", "html", "--config", str(policy), "--deallocate"])
    srv.shutdown()
    out = capsys.readouterr().out
    assert rc == 0 and deallocated == [1]
    assert ("start", appliance.LLM_UNIT) in systemctl and ("stop", appliance.LLM_UNIT) in systemctl
    run_dir = next((appliance.DATA / "fabrikam").iterdir())
    assert {p.name for p in run_dir.iterdir()} >= {"fabrikam-azure-assessment.html",
                                                   "fabrikam-azure-assessment-ai-draft.json"}
    assert not (appliance.STATE / "run.pid").exists()
    hint = next(ln for ln in out.splitlines() if ln.strip().startswith("assess-run"))
    assert f"--config {policy}" in hint and "--ai-draft " + str(next(run_dir.glob("*-ai-draft.json"))) in hint

    # Rebuild from the (reviewed) draft: no model is started.
    systemctl.clear()
    draft = next(run_dir.glob("*-ai-draft.json"))
    assert appliance.run_main([str(inv), "-c", "Fabrikam", "-f", "html", "--config", str(policy), "--ai-draft",
                               str(draft), "--reviewed-by", "A. Consultant", "-o", str(dirs / "final")]) == 0
    assert "facts have changed" not in capsys.readouterr().err   # same options -> the AI text is kept
    assert systemctl == [] and "reviewed by A. Consultant" in (dirs / "final" / "fabrikam-azure-assessment.html").read_text()


def test_assess_run_failure_does_not_deallocate(dirs, monkeypatch, capsys):
    monkeypatch.setattr(appliance, "deallocate", lambda: pytest.fail("must not deallocate after a failure"))
    assert appliance.run_main([str(dirs / "missing.csv"), "-c", "X", "--no-ai", "--deallocate"]) != 0
    assert "Not deallocating" in capsys.readouterr().err


def test_assess_mode_hosted_and_back_to_local(dirs, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(appliance.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    _local_mode(dirs, appliance.LOCAL_URL)

    monkeypatch.setattr("sys.stdin", io.StringIO("s3cret\n"))
    assert appliance.mode_main(["hosted", "--url", "https://llm.assessor.example/v1", "--provider", "Assessor Ltd",
                                "--key-file", "-"]) == 0
    s = json.loads((appliance.ETC / "llm.json").read_text())
    key = appliance.ETC / "llm.key"
    assert s["hosting"] == "hosted" and s["api_key_file"] == str(key) and "s3cret" not in json.dumps(s)
    assert key.read_text().strip() == "s3cret" and (key.stat().st_mode & 0o777) == 0o640
    assert ["systemctl", "disable", "--now", appliance.LLM_UNIT] in calls

    monkeypatch.setattr("sys.stdin", io.StringIO("k\n"))
    assert appliance.mode_main(["hosted", "--url", "http://insecure/v1", "--provider", "X", "--key-file", "-"]) == 1
    assert "requires an https" in capsys.readouterr().err
    assert json.loads((appliance.ETC / "llm.json").read_text())["url"].startswith("https://")  # unchanged

    assert appliance.mode_main(["local"]) == 0
    assert json.loads((appliance.ETC / "llm.json").read_text())["hosting"] == "local" and not key.exists()
