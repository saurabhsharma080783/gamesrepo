# Azure Inventory Assessment

Turns an Azure resource inventory **exported by the customer** (CSV or JSON) into a professional
assessment report in three formats. The tool never connects to Azure and needs no credentials.
It only reads the files you give it.

| Format | Audience | Contents |
|---|---|---|
| **Word (.docx)** | Full written report | Cover, table of contents, executive summary, scope & methodology, data coverage, inventory charts, findings by pillar, 30/60/90-day roadmap, appendices (all findings and full inventory) |
| **PowerPoint (.pptx)** | Executive briefing | 16:9 deck: title, agenda, KPIs, scope, data coverage, inventory, scorecard, findings, roadmap, next steps |
| **HTML** | Interactive, shareable | Self-contained single file: KPIs, posture score, charts, scorecard, findings, roadmap, searchable/sortable tables, light/dark theme |

A `*-findings.json` file is also written so the results can be fed into other tools.

```
customer export (.csv / .json, one or many files) ──► loaders ──► assessment rules + scoring ──► docx / pptx / html
```

## Install

```bash
cd azure-assessment
pip install -e .
```

Python 3.10+ is required.

## Usage

```bash
# Build all three reports from the file(s) the customer sent
azure-assess report customer-inventory.csv --customer "Fabrikam" --out reports

# Several files (e.g. one per subscription or per export page) are merged; duplicates are dropped
azure-assess report inventory-001.json inventory-002.json --customer "Fabrikam"

# Choose formats, apply the customer's policy, and stamp the export date
azure-assess report inventory.json -c "Fabrikam" -f docx,pptx --config examples/config.json --inventory-date 2026-09-20

# Try it with built-in sample data
azure-assess demo --customer "Contoso"

# Produce an example input file to show a customer the expected format
azure-assess template -o inventory-template.csv     # or .json
```

## Input formats

Column and key names are matched loosely: case, spaces, `_` and `.` are ignored, and common
aliases such as `Resource Group`/`resourceGroup` or `Region`/`location` all work.

| Source | Format | What's assessed |
|---|---|---|
| **Resource Graph export (recommended).** Use the scripts in [`customer-export/`](customer-export) or Resource Graph Explorer → *Download as CSV* | JSON or CSV, with `properties`, `tags`, `sku` and `zones` columns (in CSV these are JSON strings) | All checks |
| `az resource list -o json` | JSON | Tags, SKU, regions and governance; most property-based checks can't run |
| Azure portal *All resources → Export to CSV* | CSV (`NAME`, `TYPE`, `RESOURCE GROUP`, `LOCATION`, `SUBSCRIPTION`); friendly type names such as "Virtual machine" are mapped to ARM types | Inventory and region/classic checks only |
| Hand-built spreadsheet | CSV with columns like `name`, `type`, `resourceGroup`, `location`, plus flattened `tags.owner` or `properties.diskState` | Whatever the columns cover |

CSV files can use comma, semicolon or tab delimiters (detected automatically), with or without a UTF-8 BOM.

**Missing data is never treated as a pass.** When the input lacks a field a check needs (for example,
a portal CSV has no `properties`), that check is listed under **Data coverage & limitations** in
every report. Pillars with no assessable data show *Not assessed* instead of a score, and the overall
score states how many pillars it is based on.

### What to ask the customer for

Send the customer one of the scripts in [`customer-export/`](customer-export). Both need only
**Reader** access and are easiest to run in [Azure Cloud Shell](https://shell.azure.com):

* **Bash / Azure CLI:** `./export-inventory.sh [subscription-id ...]` writes `inventory-001.json`, `inventory-002.json`, …
* **PowerShell:** `./Export-Inventory.ps1 [-SubscriptionId <id>,<id>]` writes `inventory.json`

Both run this Resource Graph query, which can also be pasted into Resource Graph Explorer in the
portal and downloaded as CSV:

```kusto
Resources
| project id, name, type, location, resourceGroup, subscriptionId, kind, sku, tags, zones, properties
| join kind=leftouter (ResourceContainers | where type =~ 'microsoft.resources/subscriptions'
    | project subscriptionId, subscriptionName = name) on subscriptionId
| project-away subscriptionId1
```

## Assessment

Rules are aligned to the Azure Well-Architected Framework pillars (Security, Reliability, Cost
Optimization, Operational Excellence) plus Governance. Each pillar gets a 0–100 score: the
severity-weighted pass rate (High = 5, Medium = 3, Low = 1). 80+ is Good, 60–79 Fair, and below 60 Poor.

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

To add a check, add a `Rule(...)` entry in `azure_assessment/analysis/rules.py`. Set `needs=` to the
input fields it reads so coverage is reported correctly.

### Configuration

Pass `--config examples/config.json` to apply the customer's policy:

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
* `sample-reports/` contains reports generated from the built-in demo data.

## Development

```bash
pip install -e '.[dev]'
pytest
```
