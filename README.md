# Vipunen

Agentic red-teaming of Apertus, built entirely on Apertus. Hack Apertus 2026, Track 2B.

**VIPUNEN** — *Versatile Intelligent Pentest User-Network Engagement Node*. Named for
Antero Vipunen, the buried giant of the Kalevala who holds the lost words of origin;
Väinämöinen climbs inside him to make him sing them out.

Apertus writes a Finnish Kalevala-metre verse around a masked claim, escalates it,
and is also the model under test — every text-shaping call sees only masked
("rabbit") text; the real terms are swapped in by a non-LLM step just before the
final prompt. A single Apertus call never sees the whole intent. The headline
verdict is deterministic (regex against ground truth), so it reproduces with no key.

The project lives in [`track_2b/`](track_2b/). From there:

```bash
cp .env.example .env   # fill in LLM_API_KEY (CSCS); or export LLM_NAME/LLM_BASE_URL/LLM_API_KEY
make run               # Docker: neutral demo chain + control arm on public seeds, live endpoint
```

Other targets: `make demo-offline` (no key, echo), `make estimate` (price a run),
`make baseline` (prose baseline grid), `make console` (operator console on localhost),
`make test`, `make check` (tests + publish gate).

## Console

`make console` serves an operator page on http://localhost:8000 (localhost only):

- Type a statement and swap pairs (**safe word → real word**). The carrier stages
  see only the safe word; the real word is substituted just before the target.
- **Automatic mode** streams the whole chain, then you record your own verdict
  (FAIL / PARTIAL / PASS / REFUSED / UNCLEAR) and may flag an obvious FAIL for the
  Track 1A submission. The deterministic scorer is shown as a hint, not the headline.
- **Advanced mode** runs the chain one stage at a time and lets you edit the exact
  prompt before each stage is sent — the carrier-stage prompts are still refused if
  they contain a real term, so the masking invariant holds even by hand.

## Deployment

One external dependency: the Apertus endpoint. Point `LLM_BASE_URL` at CSCS
(sovereign Swiss cloud), Public AI, or a self-hosted vLLM Apertus (on-prem /
air-gapped). The deterministic scorers need no network. Runs in Docker; no GPU on
the host when the endpoint is remote.

See [`track_2b/technical_report.md`](track_2b/technical_report.md). Code: Apache-2.0.
