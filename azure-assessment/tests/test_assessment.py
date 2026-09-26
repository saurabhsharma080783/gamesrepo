import json

import docx
import pptx
import pytest

from azure_assessment.analysis.assessor import assess
from azure_assessment.cli import generate_reports, main
from azure_assessment.collectors import file_collector, sample
from azure_assessment.models import Resource


def _res(type_, **kw):
    return Resource.from_dict({"id": f"/subscriptions/s1/resourceGroups/rg/providers/{type_}/x",
                               "name": "x", "type": type_, **kw})


def _rule_ids(resources, cfg=None):
    from azure_assessment.models import Inventory
    return {f.rule_id for f in assess(Inventory(resources), cfg).findings}


def test_storage_rules():
    bad = _res("Microsoft.Storage/storageAccounts", sku={"name": "Standard_LRS"}, tags={},
               properties={"supportsHttpsTrafficOnly": False, "allowBlobPublicAccess": True,
                           "minimumTlsVersion": "TLS1_0"})
    assert {"SEC-001", "SEC-002", "SEC-003", "REL-003", "GOV-001"} <= _rule_ids([bad])
    good = _res("Microsoft.Storage/storageAccounts", sku={"name": "Standard_ZRS"},
                tags={"environment": "p", "owner": "o", "costCenter": "c"},
                properties={"supportsHttpsTrafficOnly": True, "allowBlobPublicAccess": False,
                            "minimumTlsVersion": "TLS1_2"})
    assert _rule_ids([good]) == set()


@pytest.mark.parametrize("rule,expected", [
    ({"sourceAddressPrefix": "*", "destinationPortRange": "3389"}, True),
    ({"sourceAddressPrefix": "Internet", "destinationPortRange": "20-25"}, True),
    ({"sourceAddressPrefix": "0.0.0.0/0", "destinationPortRanges": ["443", "22"]}, True),
    ({"sourceAddressPrefix": "Internet", "destinationPortRange": "443"}, False),
    ({"sourceAddressPrefix": "10.0.0.0/8", "destinationPortRange": "22"}, False),
])
def test_nsg_management_ports(rule, expected):
    nsg = _res("Microsoft.Network/networkSecurityGroups", properties={"securityRules": [
        {"name": "r", "properties": {"direction": "Inbound", "access": "Allow", **rule}}]})
    assert ("SEC-007" in _rule_ids([nsg])) is expected


def test_vm_power_state_and_region():
    vm = _res("Microsoft.Compute/virtualMachines", location="brazilsouth",
              properties={"extended": {"instanceView": {"powerState": {"code": "PowerState/stopped"}}}})
    ids = _rule_ids([vm], {"allowed_locations": ["eastus"]})
    assert {"COST-003", "REL-001", "GOV-002"} <= ids
    assert "GOV-002" not in _rule_ids([vm])  # no allowed_locations configured -> rule skipped


def test_scores_and_aggregates():
    a = assess(sample.build(), customer="Contoso")
    assert a.total_resources > 100
    assert 0 <= a.overall_score <= 100
    assert sum(a.severity_counts.values()) == len(a.findings)
    assert all(0 <= p.score <= 100 for p in a.pillar_scores)
    assert a.key_observations()
    assert sum(len(v) for v in a.roadmap().values()) == len(a.rule_summaries)


def test_load_az_graph_output(tmp_path):
    p = tmp_path / "graph.json"
    p.write_text(json.dumps({"count": 1, "data": [{
        "id": "/subscriptions/abc/resourceGroups/RG/providers/Microsoft.Web/sites/app1",
        "name": "app1", "type": "microsoft.web/sites", "location": "eastus",
        "resourceGroup": "RG", "subscriptionId": "abc", "properties": {"httpsOnly": False}}]}))
    inv = file_collector.load(p)
    assert inv.resources[0].subscription_id == "abc"
    assert "SEC-006" in {f.rule_id for f in assess(inv).findings}


def test_roundtrip_export(tmp_path):
    inv = sample.build()
    loaded = file_collector.load(file_collector.save(inv, tmp_path / "inv.json"))
    assert len(loaded.resources) == len(inv.resources)
    assert loaded.subscriptions == inv.subscriptions


def test_generate_all_reports(tmp_path):
    out = generate_reports(sample.build(), tmp_path, "Contoso Ltd")
    assert set(out) == {"html", "docx", "pptx", "findings"}

    html = out["html"].read_text()
    assert "Contoso Ltd" in html and "Executive summary" in html and "<script" in html

    d = docx.Document(out["docx"])
    headings = [p.text for p in d.paragraphs if p.style.name.startswith("Heading")]
    assert "1. Executive summary" in headings and "5. Remediation roadmap" in headings
    assert len(d.inline_shapes) >= 4  # charts embedded

    prs = pptx.Presentation(out["pptx"])
    assert len(prs.slides) >= 9
    for i, slide in enumerate(prs.slides, 1):
        for sh in slide.shapes:  # nothing may spill off the slide
            assert sh.left >= 0 and sh.top >= 0, (i, sh.name)
            assert sh.left + sh.width <= prs.slide_width, (i, sh.name)
            assert sh.top + sh.height <= prs.slide_height, (i, sh.name)


def test_cli_demo(tmp_path, capsys):
    assert main(["demo", "-o", str(tmp_path), "-f", "html"]) == 0
    assert list(tmp_path.glob("*.html"))
    with pytest.raises(SystemExit):
        main(["demo", "-o", str(tmp_path), "-f", "pdf"])


def test_azure_collector_paginates(monkeypatch):
    from types import SimpleNamespace

    from azure_assessment.collectors import azure_collector

    pages = {
        "subs": [SimpleNamespace(data=[{"subscriptionId": "s1", "name": "Prod", "tenantId": "t"}], skip_token=None)],
        "res": [SimpleNamespace(data=[{"id": "/subscriptions/s1/resourceGroups/rg/providers/a/b/r1", "name": "r1",
                                       "type": "A/B"}], skip_token="next"),
                SimpleNamespace(data=[{"id": "/subscriptions/s1/resourceGroups/rg/providers/a/b/r2", "name": "r2",
                                       "type": "A/B"}], skip_token=None)],
    }

    class Client:
        def __init__(self, cred):
            pass

        def resources(self, req):
            return pages["subs" if "ResourceContainers" in req.query else "res"].pop(0)

    def make(**kw):
        return SimpleNamespace(**kw)

    monkeypatch.setattr(azure_collector, "_import_sdk", lambda: (lambda: None, Client, make, make))
    inv = azure_collector.collect(["s1"])
    assert [r.name for r in inv.resources] == ["r1", "r2"]
    assert inv.resources[0].subscription_name == "Prod" and inv.tenant_id == "t"
