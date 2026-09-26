# Azure Inventory Assessment

Collects the resources in one or more Azure subscriptions and turns them into a professional
assessment report in three formats:

| Format | Audience | Contents |
|---|---|---|
| **Word (.docx)** | Full written report | Cover, table of contents, executive summary, scope & methodology, inventory charts, findings by pillar, 30/60/90-day roadmap, appendices (all findings and full inventory) |
| **PowerPoint (.pptx)** | Executive briefing | 16:9 deck: title, agenda, KPIs, inventory, scorecard, findings distribution, key findings, roadmap, next steps |
| **HTML** | Interactive, shareable | Self-contained single file: KPIs, posture score, charts, scorecard, findings, roadmap, searchable/sortable findings and inventory tables, light/dark theme |

A `*-findings.json` file is also written so the results can be fed into other tools.

## How it works

```
Azure Resource Graph ──► collectors ──► Inventory ──► analysis (rules + scoring) ──► reports (docx / pptx / html)
       or JSON file ──┘
```

* **Collection** is read-only through [Azure Resource Graph](https://learn.microsoft.com/azure/governance/resource-graph/),
  so one query covers every subscription the identity can see, and results are paged for large estates.
* **Assessment** runs rules aligned to the Azure Well-Architected Framework pillars (Security, Reliability,
  Cost Optimization, Operational Excellence) plus Governance. Each pillar gets a 0–100 score: the
  severity-weighted pass rate (High = 5, Medium = 3, Low = 1). 80+ is Good, 60–79 Fair, and below 60 Poor.

### Built-in checks

| ID | Pillar | Check |
|---|---|---|
| SEC-001…003 | Security | Storage: HTTP allowed, anonymous blob access, TLS < 1.2 |
| SEC-004 | Security | Key Vault purge protection disabled |
| SEC-005 | Security | SQL server public network access |
| SEC-006 | Security | App Service not HTTPS-only |
| SEC-007 | Security | NSG allows RDP/SSH from the Internet |
| REL-001…004 | Reliability | VM without zone/availability set, unmanaged disks, LRS storage, non-zone-redundant SQL DB |
| COST-001…005 | Cost | Unattached disks, unused public IPs, stopped (not deallocated) VMs, deallocated VMs, empty App Service plans |
| OPS-001…002 | Operational Excellence | Previous-generation VM sizes, short Log Analytics retention |
| GOV-001…003 | Governance | Missing required tags, resources outside allowed regions, classic (ASM) resources |

To add a check, add a `Rule(...)` entry in `azure_assessment/analysis/rules.py`.

## Install

```bash
cd azure-assessment
pip install -e '.[azure]'      # drop [azure] if you only build reports from JSON files
```

Python 3.10+ is required.

## Usage

### Try it without Azure

```bash
azure-assess demo --customer "Contoso" --out reports
```

### Assess a live environment

Sign in with any method `DefaultAzureCredential` supports (`az login`, a service principal through
`AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/`AZURE_CLIENT_SECRET`, or a managed identity). The identity needs
**Reader** on the subscriptions or management group.

```bash
# Collect and report in one step (all visible subscriptions)
azure-assess run --customer "Fabrikam" --out reports

# Scope to specific subscriptions or a management group
azure-assess run -s <sub-id-1> -s <sub-id-2> --customer "Fabrikam"
azure-assess run -m <management-group-id> --customer "Fabrikam"

# Or split the steps: collect once, report many times
azure-assess collect -s <sub-id> -o inventory.json
azure-assess report -i inventory.json --customer "Fabrikam" --formats docx,pptx
```

### Build reports from existing exports (no SDK needed)

`report -i` also accepts output from the Azure CLI:

```bash
az graph query -q "Resources | project id,name,type,location,resourceGroup,subscriptionId,kind,sku,tags,zones,properties" \
  --first 1000 -o json > inventory.json
azure-assess report -i inventory.json --customer "Fabrikam"
```

### Configuration

Pass `--config examples/config.json` to set assessment policy:

```json
{
  "required_tags": ["environment", "owner", "costCenter"],
  "allowed_locations": ["eastus", "westeurope", "uksouth"],
  "min_log_retention_days": 90,
  "disabled_rules": ["COST-004"]
}
```

## Notes

* The Word table of contents is a field. Word refreshes it on open (accept the prompt) or with **F9**.
* The Word appendices show up to 500 rows; the HTML report and JSON files always hold everything.

## Development

```bash
pip install -e '.[dev]'
pytest
```
