# AI narrative: dry-run results (29 September 2026)

Dry run of `azure-assess --ai` with a real open-source model, plus the full test suite and every CLI
path. The build from the final run is in [`reports/ai-sample/`](../reports/ai-sample).

## Environment

| Item | Value |
|---|---|
| Model | Qwen 2.5 7B Instruct, Q4_K_M GGUF (4.68 GB, Apache 2.0), SHA-256 `7848e617…6b9b` verified against the registry digest |
| Model server | llama.cpp via `llama-cpp-python[server]` 0.3.35, OpenAI-compatible API on `127.0.0.1:8080`, context 8,192 tokens |
| Hardware | 4 CPU cores, 15 GB RAM, **no GPU** |
| Input | `examples/sample-inventory.csv` (152 resources, 3 subscriptions) with `examples/config.json` |
| Command | `azure-assess report examples/sample-inventory.csv -c Contoso --config examples/config.json -f docx,html,pptx --inventory-date 2026-09-20 --ai --llm-url http://127.0.0.1:8080/v1 --llm-workers 1 --llm-timeout 900 -o reports/ai-sample` |

Speed on this CPU: about 40 tokens/s reading the prompt and 4 tokens/s writing, so 1 to 2 minutes per
part. A GPU is roughly 10 to 20 times faster.

## Automated tests: 49 passed

`pytest` runs 49 tests. The 12 AI tests cover the grounding check (invented figures, invented rule IDs,
invented costs, drafts that are too short), cleanup of markdown, headings, backticks and preambles,
retry then fallback, an end-to-end CLI run against a mock OpenAI-compatible server, an unreachable
server, a missing model, and the content of the fact sheets. The 37 existing tests all still pass.

## CLI dry runs

| # | Scenario | Expected | Result |
|---|---|---|---|
| 1 | `template -o x.csv` / `x.json` | Sample input files | Pass: 152 resources each |
| 2 | Standard report, all formats, with config | docx, html, pptx and findings.json | Pass: score 76/100, 106 findings |
| 3 | Report from the generated JSON template | docx and html | Pass |
| 4 | `demo` | html | Pass |
| 5 | `--ai` with no server running | Warning, standard report, exit 0 | Pass |
| 6 | Invalid JSON input | Error, exit 2 | Pass |
| 7 | Unknown format `-f pdf` | Usage error | Pass |
| 8 | `--ai` with a real model (three runs, below) | AI narrative with fallback where needed | Pass |

**No regression without `--ai`.** Reports built on this branch were compared with reports from `main`
for the same input: the Word and PowerPoint text is identical, and the HTML differs only by one added
CSS rule. `findings.json` is byte-identical.

**Rendering.** LibreOffice converts the AI Word report to PDF (38 pages, against 31 for the standard
report) and the deck to 11 slides. The PDF has 19 "AI-drafted narrative" marks, one for each drafted
part. In Chromium the HTML report has 19 marks, and at 390 px wide the page has no horizontal
scrolling.

## Real-model runs

| Run | Code | Parts drafted by AI | Kept standard wording (guard) | Time |
|---|---|---|---|---|
| 1 | First version | 18 of 19 | Executive summary: cited `COST-007`, which doesn't exist (failed on retry too) | 31.5 min |
| 2 | No table rows in facts; stricter prompts | 17 of 19 | Regional footprint: worked out figures 55 and 84 itself; Compute: cited `OPS-002` from another section | 27.6 min |
| 3 | Headings, backticks and rule IDs stripped from executive facts | **19 of 19** | none | 24.6 min |

What each run changed:

* **Run 1**, Storage section: the model named the wrong accounts as LRS, called a dev account
  production, and said 2 accounts allow anonymous access (the facts say 4). Every number was in the
  facts, so the guard couldn't catch it. It had been given the per-resource table rows and mixed them
  up, so fact sheets now carry the summarised statements only. The prompts also now say to keep each
  figure attached to what it counts.
* **Run 2**, executive summary: plain-text headings came out as paragraphs, inline code had backticks,
  and rule IDs were cited even though the prompt said not to. The cleanup now removes headings and
  backticks, and the executive fact sheet no longer contains rule IDs, so any ID the model writes is
  caught as invented.
* **Tried and dropped:** a second "fact-check" pass by the same 7B model. It found one real error,
  missed another, flagged correct statements, and took 84 seconds per part, so it wasn't reliable
  enough to use.

## Human review of the final build

Each of the 19 AI parts was read against its fact sheet. The prose reads like a consultant wrote it:
it explains risk and business impact and links findings to recommendations. All figures and rule IDs
are real. These errors got past the automated check:

| Section | The AI wrote | The facts say |
|---|---|---|
| Executive summary | "86% of the estate rated as Good" | The *Governance pillar* scored 86/100 (Good) |
| Executive summary | Managing unattached disks belongs in the 60 to 90 day window | Cost items are in the 0 to 30 day window |
| Estate at a glance | "legacy application services (8%)" | 8% is application hosting, which isn't legacy |
| Security services | kv-dev-01 is the vault without purge protection | The facts don't say which vault |
| Security services | If one firewall is compromised the other still protects the environment | The two firewalls protect different regional hubs |
| Databases | Zone redundancy keeps data available if a region fails | It protects against the failure of a zone |
| Monitoring and management | 30-day retention is "sufficient", then says it is below target | Below target (OPS-002) |

Also speculative: "no tested failover plans" and that out-of-policy regions "incur unnecessary costs"
(an inventory can't show either), and that classic resources can "be exploited by attackers".

**Verdict.** The feature works end to end with a real open-source model: it runs fully offline, falls
back safely, and discloses AI use. With a **7B model**, expect about one factual slip in every two or
three sections, so **AI passages must be reviewed before a report goes to a customer**. This is why they
are marked, and why `*-ai-draft.json` puts each passage next to its facts.

## Recommendations

1. **Use a larger model for customer reports.** Qwen 2.5 14B or 32B on a GPU with 16 to 24 GB of memory
   should cut these errors and still take only minutes. Rerun this dry run with it before choosing.
2. **Keep human review in the process.** The reviewer checks each AI passage against the facts in
   `*-ai-draft.json`.
3. **Build a fine-tuning set.** Save each reviewer-corrected passage next to its facts. A few hundred
   pairs are enough for a LoRA fine-tune that learns these distinctions (pillar score versus share of
   the estate, zone versus region, which window a cost item goes in). See
   [AI-NARRATIVE.md](AI-NARRATIVE.md).
