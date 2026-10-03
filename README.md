# Vipunen

Agentic red-teaming of Apertus, built entirely on Apertus. Hack Apertus 2026, Track 2B.

**VIPUNEN** stands for *Versatile Intelligent Pentest User-Network Engagement Node*.
Named for Antero Vipunen of the Kalevala, the buried giant who holds the lost words;
Väinämöinen climbs inside him to make him sing them out.

I wanted to test the safeguards of Apertus, and also what the models can do. One team
might only get one submission, so I built the whole thing on Apertus itself. The idea
was that Finnish in poetic form confuses the safeguards. That, together with the
framing on the final prompt, is what found the flaws. The work is split into agents,
so no single Apertus call sees the whole task and refuses. This track is the tool;
the red-teaming results it produced are Track 1A. The real payloads are not in this
public repo, since they can be used for malicious purposes. They are replaced with
vanilla examples, which effectively makes the tool universal. Anyone can write their
own `mutations.yaml` and use the tool as they see fit, but must follow laws, rules
and good habits.

Apertus writes a Finnish Kalevala-metre verse around a masked claim, escalates it,
and is also the model under test. Every text-shaping call sees only masked
("rabbit") text; the real terms are swapped in by a non-LLM step just before the
final prompt. The headline verdict is deterministic (regex against ground truth), so
it reproduces with no key.

The verse form is old. The Kalevala songs were sung for well over a thousand years
before Elias Lönnrot wrote them down in the 1800s. In the Track 1A findings the direct
ask is refused 0 of 12 times and the verse gets through 4 to 10 of 12, so folk poetry
that old beat the safety training of Apertus v1.5 on those claims. The agents are named
after the Kalevala characters: Väinämöinen writes the verse, Louhi escalates it,
Joukahainen swaps the words, Lemminkäinen gives the verdict.

The code was written with Claude Code from my design documents. Apertus is the only
model that runs in the tool.

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

- Type a statement and swap pairs (**safe word to real word**). The carrier stages
  see only the safe word; the real word is substituted just before the target.
- **Automatic mode** streams the whole chain, then you record your own verdict
  (FAIL / PARTIAL / PASS / REFUSED / UNCLEAR) and may flag an obvious FAIL for the
  Track 1A submission. The deterministic scorer is shown as a hint, not the headline.
- **Advanced mode** runs the chain one stage at a time and lets you edit the exact
  prompt before each stage is sent. The carrier-stage prompts are still refused if
  they contain a real term, so the masking invariant holds even by hand.

## Deployment

One external dependency: the Apertus endpoint. Point `LLM_BASE_URL` at CSCS
(sovereign Swiss cloud), Public AI, or a self-hosted vLLM Apertus (on-prem /
air-gapped). The deterministic scorers need no network. Runs in Docker; no GPU on
the host when the endpoint is remote.

See [`track_2b/technical_report.md`](track_2b/technical_report.md). Code: Apache-2.0.
