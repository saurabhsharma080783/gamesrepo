# AI-drafted report narrative

`azure-assess ... --ai` uses a **self-hosted, open-source language model** to write the report's
narrative the way a consultant would: it explains what the findings mean and why they matter, instead of
only restating counts. Everything else in the report (scores, findings, tables, diagrams, roadmap) still
comes from the deterministic assessment engine.

No customer data goes to Azure OpenAI, OpenAI, Anthropic or any other hosted AI service. The model
runs on a server you control, and the tool talks only to the URL you give it.

## How it works

```
customer export ─► loaders ─► rules + scoring ─► architecture + narrative analysis ─┬─► reports (docx / html / pptx)
                                                                                     │        ▲
                                                           fact sheets (text) ───────┘        │
                                                                │                             │
                                                                ▼                             │
                                          self-hosted open-source LLM (Ollama / vLLM / llama.cpp)
                                                                │                             │
                                                                ▼                             │
                                          grounding check ── pass ──► AI paragraphs ──────────┤
                                                │                                             │
                                                └── fail (after one retry) ─► standard wording┘
```

1. **Fact sheets.** For each part of the report, the tool builds a short plain-text sheet from the
   engine's output: scores, findings with affected counts and recommendations, the architecture pattern
   and its evidence, and the standard narrative statements. The model sees only these facts, never the
   raw inventory.
2. **Drafting.** The model is asked to write consultant-style paragraphs from those facts only. It drafts:
   * the executive summary (posture, main risks and business impact, strengths, priorities),
   * the summary of each architecture area (network topology, hybrid connectivity, and so on),
   * the opening paragraphs of each infrastructure overview part (compute, networking, storage, and so on).
3. **Grounding check.** Every number and rule ID in a draft must appear in its fact sheet. A draft that
   invents a figure (a cost estimate, a wrong count, a rule that doesn't exist) is sent back once with the
   problem stated; if it fails again, that part keeps the standard wording. Markdown and preambles are
   stripped.
4. **Disclosure and audit.** AI-drafted passages are marked in the reports, and a "Use of AI in this
   report" section explains the process. `<customer>-azure-assessment-ai-draft.json` records, for every
   part, the facts, the draft, whether it was used and why not.

If the LLM server is unreachable or the model isn't loaded, the tool prints a warning and produces the
standard reports.

> The check catches invented figures and rule IDs, not every qualitative statement. Review AI-drafted
> passages before a report goes to the customer, as you would a junior consultant's draft.

## Quick start

```bash
# 1. Run the model server (Docker). Add the GPU block in the file if you have an NVIDIA GPU.
docker compose -f deploy/llm/docker-compose.yml up -d

# 2. Build reports with AI narrative
azure-assess report inventory.json -c "Fabrikam" --ai
```

Without Docker: install [Ollama](https://ollama.com), then `ollama pull qwen2.5:7b-instruct`, and set
`OLLAMA_CONTEXT_LENGTH=8192` (prompts are about 1,500 tokens plus the reply). Other servers need a context
window of at least 4,096 tokens.

Options (also settable through environment variables):

| Option | Environment variable | Default |
|---|---|---|
| `--llm-url` | `AZURE_ASSESS_LLM_URL` | `http://localhost:11434/v1` (Ollama) |
| `--llm-model` | `AZURE_ASSESS_LLM_MODEL` | `qwen2.5:7b-instruct` |
| (none) | `AZURE_ASSESS_LLM_API_KEY` | none; sent as a bearer token if the server requires one |
| `--llm-timeout` | | 180 seconds per request |
| `--llm-workers` | | 2 parallel requests |

The client speaks the OpenAI-compatible chat API, so any of these servers works unchanged:
Ollama, llama.cpp `llama-server`, vLLM, LM Studio, LocalAI, or Hugging Face TGI.

## Choosing a model

Use an open-weight *instruct* model of about 7 to 14 billion parameters. Smaller models (1 to 4B) run
anywhere but fail the grounding check more often, so more parts fall back to standard wording.

| Model (Ollama tag) | Licence | Notes |
|---|---|---|
| `qwen2.5:7b-instruct` (default) | Apache 2.0 | Follows the "facts only" rules well; good default |
| `qwen2.5:14b-instruct` | Apache 2.0 | Better prose; needs about 10 GB of GPU memory |
| `mistral:7b-instruct` | Apache 2.0 | Fully permissive alternative |
| `llama3.1:8b` | Llama 3.1 Community Licence | Strong writing; check the licence terms fit your use |
| `phi3.5` | MIT | 3.8B; runs on modest hardware |

Check each model's licence before commercial use. Reasoning models that emit `<think>` blocks also work;
the blocks are removed.

**Sizing.** A 7B model quantised to 4 bits needs about 5 GB of memory. On a GPU with 8 GB or more, a full
report (about 20 parts) takes 1 to 3 minutes. CPU-only works but can take 10 to 30 minutes.

## Why not build our own model from scratch?

Training a language model from scratch needs trillions of words of text and very large GPU budgets, and
the result would write worse English than existing open models. The practical way to have "our own"
model is to **start from an open-weight model and fine-tune it** on our reports:

1. **Now:** use an off-the-shelf open model (this feature). Consultants review and edit the AI passages.
2. **Collect data:** each run writes `*-ai-draft.json` with the facts and the draft. Pair each fact sheet
   with the consultant-approved final text. A few hundred reviewed examples are enough to start.
3. **Fine-tune:** train a LoRA adapter on those pairs (for example with Hugging Face PEFT, Axolotl or
   Unsloth) on a single GPU in hours, merge it, and convert to GGUF.
4. **Serve:** load it into Ollama (`ollama create our-assessor -f Modelfile`) and run with
   `--llm-model our-assessor`. The grounding check and fallback stay in place.

This gives a model that writes in our house style and terminology, owned and hosted by us, while
keeping the engine as the source of truth for every number.
