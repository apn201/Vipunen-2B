# Technical report — `project name`

A deeper write-up than the README: what you built, how it works, and what the
numbers say.

- **Track:** `Track 2B — project name`
- **Event:** Online
- **Team:** `team name` — `member`, `member`, `member`
- **Demo:** `link to video`

## 1. Summary

The problem, your approach, and the headline result in one paragraph.

## 2. Architecture

Components, data flow, and where each one runs. Put diagrams in `docs/` and
reference them here.

### Target architecture (mandatory)

State which of the three architectures your project is deployable in, and how
it meets that constraint:

- **a) On-premise** — on the organisation's own infrastructure, under its own administration.
- **b) Air-gapped** — with no external network connection at runtime.
- **c) Sovereign Swiss cloud** — on a cloud platform operated in Switzerland, under Swiss jurisdiction, with Swiss data residency.

List any external dependencies, and separate build time from runtime.

## 3. Use of Apertus

- **Model:** `e.g. swiss-ai/Apertus-v1.5-8B`
- **How it is used:** inference | fine-tuning | evaluation | red-teaming | agents / tool use
- **Where it runs:** `local weights, hosted endpoint, ...`

Prompts, adapters, quantisation, serving stack — whatever a reader needs to
rebuild your setup.

## 4. Data

What you used, where it came from, and its licence. Flag anything personal or
non-redistributable, and keep it out of the repository (see `.gitignore`).
If data comes from human subjects or contains personal information, describe
how consent was obtained.

## 5. Evaluation

How you measured success: task, metric, baseline.

| Setup    | Metric | Result |
|----------|--------|--------|
| Baseline |        |        |
| Ours     |        |        |

## 6. Limitations

Where it breaks, what you did not test, and known failure modes.

### Scope: planned but cut

The design (see `docs/`) sketched more than the entry needed. We cut the following
deliberately and record them here for honesty and for the next iteration:

- **Per-seed adaptive loop** (Ukko retrying a claim up to *N* times, one variable
  per attempt). Cut: in practice the operator drives iteration from the console, so
  the automatic loop added machinery without changing findings. The chain runs once;
  a `loop:` block in an old config is accepted and ignored.
- **Category scorers for bias / PII / IP** and the **hash-only ledger**, **Apertus
  severity opinion** and **end-of-loop tuning note** (planned "S5"). Cut: the
  deterministic factual scorer (history/culture) plus the operator's own verdict
  cover what the 1A findings need. The operator verdict, recorded per run, replaced
  the ledger/severity/tuning-note ideas.
- **Headless batch grid and cross-run planner** (planned "S7"). Cut: the 1A
  reproducibility harness (separate repo) already replays each finding across the
  model × temperature grid, which is the only grid the submission relies on.

Division of labour that survived: in the **console** the human gives the verdict;
on the **control/replay** side a per-finding regex scores answers deterministically.

## 7. Reproducibility

What a judge needs to get your numbers back: hardware, runtime, seeds, and the
exact commit. `make run` should do the rest.

## 8. Next steps

What you would build with another month.

## License

Creative Commons Attribution 4.0 (CC-BY-4.0). All HackApertus projects are open-sourced.

## References
