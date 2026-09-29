"""LLM settings (local vs hosted), consultant review and rebuild, training export, and ai-check."""
import html
import json
import ssl
import subprocess
import threading
from http.server import HTTPServer

import pytest

from azure_assessment.ai import AIDraft
from azure_assessment.ai.settings import LLMSettings, SettingsError, load
from azure_assessment.cli import main

from test_ai import _MockLLM

KEY = "test-key-123"


class _KeyedLLM(_MockLLM):
    """Mock hosted model server that requires a bearer API key."""

    def _authorised(self):
        if self.headers.get("Authorization") == f"Bearer {KEY}":
            return True
        self.send_response(401)
        self.end_headers()
        return False

    def do_GET(self):
        if self._authorised():
            super().do_GET()

    def do_POST(self):
        if self._authorised():
            super().do_POST()


def _serve(handler, tls_dir=None):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    if tls_dir:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(tls_dir / "cert.pem", tls_dir / "key.pem")
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def plain_server():
    srv = _serve(_MockLLM)
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


@pytest.fixture
def hosted_server(tmp_path):
    """HTTPS with a self-signed certificate (trusted via ca_bundle) and a required API key."""
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
                    "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
                    "-keyout", str(tmp_path / "key.pem"), "-out", str(tmp_path / "cert.pem")],
                   check=True, capture_output=True)
    srv = _serve(_KeyedLLM, tmp_path)
    yield f"https://127.0.0.1:{srv.server_port}/v1", tmp_path / "cert.pem"
    srv.shutdown()


def _write(path, data):
    path.write_text(json.dumps(data))
    return str(path)


# ---- settings -------------------------------------------------------------------

def test_settings_file_and_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("AZURE_ASSESS_LLM_API_KEY", raising=False)
    (tmp_path / "k").write_text("secret\n")
    f = _write(tmp_path / "llm.json", {"url": "http://127.0.0.1:8080/v1", "model": "qwen2.5:7b-instruct",
                                       "hosting": "local", "api_key_file": str(tmp_path / "k")})
    s = load(f, model="other", timeout=None)
    assert (s.url, s.model, s.hosting, s.timeout, s.api_key) == ("http://127.0.0.1:8080/v1", "other", "local", 900,
                                                                 "secret")
    monkeypatch.setenv("AZURE_ASSESS_LLM_CONFIG", f)
    assert load().source == f
    bom = tmp_path / "notepad.json"   # Windows Notepad may save UTF-8 with a byte-order mark
    bom.write_bytes(b"\xef\xbb\xbf" + json.dumps({"url": "http://127.0.0.1:1/v1", "provider": "Caf\u00e9"}).encode())
    assert load(str(bom)).provider == "Caf\u00e9"
    with pytest.raises(SettingsError, match="unknown setting"):
        load(_write(tmp_path / "bad.json", {"url": "x", "api_key": "inline keys are not allowed"}))
    with pytest.raises(SettingsError, match="not found"):
        load(str(tmp_path / "missing.json"))


@pytest.mark.parametrize("kw, error", [
    ({"hosting": "hosted", "url": "http://llm.example.com/v1", "api_key_env": "K"}, "requires an https"),
    ({"hosting": "hosted", "url": "https://llm.example.com/v1", "api_key_env": "UNSET_VAR"}, "requires an API key"),
    ({"hosting": "local", "url": "http://10.0.0.5:8080/v1", "api_key_env": ""}, "needs an API key"),
    ({"hosting": "cloud"}, "must be 'local' or 'hosted'"),
    ({"url": "ftp://x"}, "invalid model server URL"),
])
def test_settings_validation(kw, error, monkeypatch):
    monkeypatch.setenv("K", "k")
    with pytest.raises(SettingsError, match=error):
        LLMSettings(**kw).validate()


def test_settings_valid_modes(monkeypatch):
    monkeypatch.setenv("K", "k")
    LLMSettings(hosting="local", url="http://127.0.0.1:8080/v1").validate()   # appliance: same machine, no key
    LLMSettings(hosting="hosted", url="https://llm.example.com/v1", api_key_env="K").validate()


# ---- local vs hosted in the reports ----------------------------------------------

def test_hosted_mode_over_https_with_key_and_ca(tmp_path, hosted_server, capsys, monkeypatch):
    url, cert = hosted_server
    monkeypatch.setenv("TEST_LLM_KEY", KEY)
    cfg = _write(tmp_path / "llm.json", {"url": url, "model": "mock-7b", "hosting": "hosted",
                                         "provider": "Assessor Ltd", "api_key_env": "TEST_LLM_KEY",
                                         "ca_bundle": str(cert)})
    assert main(["ai-check", "--llm-config", cfg]) == 0
    out = capsys.readouterr().out
    assert "OK: the model answered" in out and "API key: set" in out and KEY not in out

    assert main(["demo", "-c", "Fabrikam", "-o", str(tmp_path / "r"), "-f", "html", "--ai", "--llm-config", cfg]) == 0
    page = (tmp_path / "r" / "fabrikam-azure-assessment.html").read_text()
    assert "operated by Assessor Ltd" in page and "not yet been reviewed" in page and KEY not in page
    saved = json.loads((tmp_path / "r" / "fabrikam-azure-assessment-ai-draft.json").read_text())
    assert saved["hosting"] == "hosted" and KEY not in json.dumps(saved)

    monkeypatch.setenv("TEST_LLM_KEY", "wrong")
    assert main(["ai-check", "--llm-config", cfg]) == 1
    assert "HTTP 401" in capsys.readouterr().err


def test_hosted_mode_rejects_untrusted_certificate(tmp_path, hosted_server, capsys, monkeypatch):
    url, _ = hosted_server
    monkeypatch.setenv("TEST_LLM_KEY", KEY)
    cfg = _write(tmp_path / "llm.json", {"url": url, "hosting": "hosted", "api_key_env": "TEST_LLM_KEY"})
    assert main(["ai-check", "--llm-config", cfg]) == 1
    assert "CERTIFICATE_VERIFY_FAILED" in capsys.readouterr().err


def test_local_mode_note_and_bad_settings_fail_fast(tmp_path, plain_server, capsys):
    cfg = _write(tmp_path / "llm.json", {"url": plain_server, "model": "mock-7b", "hosting": "local"})
    assert main(["demo", "-c", "Fabrikam", "-o", str(tmp_path), "-f", "html", "--ai", "--llm-config", cfg]) == 0
    assert "inside Fabrikam's own environment" in html.unescape(next(tmp_path.glob("*.html")).read_text())
    bad = _write(tmp_path / "bad.json", {"hosting": "hosted", "url": "http://x/v1"})
    assert main(["demo", "-o", str(tmp_path / "b"), "--ai", "--llm-config", bad]) == 2
    assert "requires an https" in capsys.readouterr().err


# ---- consultant review ---------------------------------------------------------

def test_review_edit_and_rebuild(tmp_path, plain_server, capsys):
    out = tmp_path / "draft"
    assert main(["demo", "-c", "Contoso", "-o", str(out), "-f", "html,docx", "--ai", "--llm-url", plain_server,
                 "--llm-model", "mock-7b"]) == 0
    draft_file = out / "contoso-azure-assessment-ai-draft.json"
    data = json.loads(draft_file.read_text())
    parts = {p["title"]: p for p in data["parts"]}
    assert parts["Storage"]["original"] == parts["Storage"]["paragraphs"]   # model text kept for training
    parts["Storage"]["paragraphs"] = ["Reviewer wording: storage needs attention, per SEC-001 and SEC-002."]
    parts["Compute"]["paragraphs"] = []                                       # reviewer drops a passage
    parts["Databases"]["paragraphs"] = ["Reviewer says 77777 databases."]    # figure not in the facts
    draft_file.write_text(json.dumps(data))

    final = tmp_path / "final"
    assert main(["demo", "-c", "Contoso", "-o", str(final), "-f", "html,docx",
                 "--ai-draft", str(draft_file), "--reviewed-by", "A. Consultant"]) == 0
    io = capsys.readouterr()
    assert "Databases: check the reviewed text; it contains figures that are not in the facts: 77777" in io.err
    assert "Using reviewed AI draft" in io.out

    page = (final / "contoso-azure-assessment.html").read_text()
    assert "Reviewer wording: storage needs attention" in page
    assert "reviewed by A. Consultant" in page and "who corrected 2 of them" in page
    saved = AIDraft.load(final / "contoso-azure-assessment-ai-draft.json")
    assert saved.reviewed_by == "A. Consultant" and saved.edited == 2
    compute = next(p for p in saved.parts if p.title == "Compute")
    assert compute.status == "standard" and compute.reason == "removed by the reviewer"

    # Reviewed drafts become fine-tuning examples; unreviewed ones are skipped.
    train = tmp_path / "train.jsonl"
    assert main(["ai-training", str(final / "contoso-azure-assessment-ai-draft.json"), str(draft_file),
                 "-o", str(train)]) == 0
    rows = [json.loads(line) for line in train.read_text().splitlines()]
    assert len(rows) == saved.drafted and "not reviewed" in capsys.readouterr().err
    storage = next(r for r in rows if r["metadata"]["part"] == "section/storage")
    assert storage["metadata"]["edited"] and "Reviewer wording" in storage["messages"][2]["content"]
    assert "Facts:" in storage["messages"][1]["content"]


def test_rebuild_after_facts_change_uses_standard_wording(tmp_path, plain_server, capsys):
    assert main(["demo", "-c", "Contoso", "-o", str(tmp_path), "-f", "html", "--ai", "--llm-url", plain_server,
                 "--llm-model", "mock-7b"]) == 0
    draft_file = next(tmp_path.glob("*-ai-draft.json"))
    cfg = _write(tmp_path / "policy.json", {"disabled_rules": ["SEC-001"]})   # changes the Storage facts
    assert main(["demo", "-c", "Contoso", "-o", str(tmp_path / "again"), "-f", "html", "--config", cfg,
                 "--ai-draft", str(draft_file)]) == 0
    assert "Storage: the facts have changed since drafting" in capsys.readouterr().err
    d = AIDraft.load(next((tmp_path / "again").glob("*-ai-draft.json")))
    assert next(p for p in d.parts if p.title == "Storage").status == "standard"


def test_ai_and_ai_draft_are_exclusive(tmp_path):
    with pytest.raises(SystemExit):
        main(["demo", "-o", str(tmp_path), "--ai", "--ai-draft", "x.json"])
    with pytest.raises(SystemExit):
        main(["demo", "-o", str(tmp_path), "--reviewed-by", "Someone"])
