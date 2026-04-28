# Design review follow-ups — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or `/team-feature` to implement this plan task-by-task, per the Execution Strategy below. Steps use checkbox (`- [ ]`) syntax for tracking.

**Date:** 2026-04-28
**Companion to:** [2026-04-27-slopmortem-design.md](2026-04-27-slopmortem-design.md)
**Supersedes:** the open-issues triage previously at this path (preserved in git at commit `c8b50c8`; Issue #6 detail kept verbatim in the appendix below so existing spec anchors still resolve). The filename keeps `design-review-issues` for stability — the spec links to this path; renaming would invalidate them.

**Goal:** Close the open items from the 2026-04-28 design review. The substantive work is a candid `LIMITATIONS.md` covering three conceptual blockers (slop indistinguishability, evaluation impossibility, substitutability vs `claude -p`). Issue #6 (DNS guard) is already spec-resolved with v2 implementation tracked via `TODO(v2)` comments; this plan only verifies that the spec language and the `#6` anchor remain consistent.

**Architecture:** One Markdown file at the repo root, voiced to match the existing README (direct, first person, no marketing). Linked from the README's "Design notes" footer and from the design spec, so a reviewer landing on the repo finds it within one click. Issue #6 stays in v2-deferred status; no code lands in v1 from this plan.

**Tech Stack:** Markdown.

## Execution Strategy

**Parallel subagents.** The work splits into a writing task and two verification tasks with no shared file ownership and no runtime coupling. A fresh subagent per task plus one final integration review covers the coordination need; a persistent team would be overkill.

## Agent Assignments

- Task 1: Write `LIMITATIONS.md` → general-purpose (Markdown / technical writing)
- Task 2: Cross-link from README and design spec → general-purpose (Markdown)
- Task 3: Verify Issue #6 spec consistency and the `#6` anchor → general-purpose (Markdown / grep)

---

## Background — what's open

| Item | Status going into this plan |
|---|---|
| Issue #6 — DNS-rebinding guard cannot bind to SDK pool | spec-fixed; v1 deferral confirmed; impl deferred to v2 (TODO comments in spec) |
| Issue #7 — Prompt-injection defenses 2023-era; missing spotlighting / lethal-trifecta gate / AgentDojo test corpus / output URL hardening | v1: ships baseline (`<untrusted_document>` wrap of bodies + tool results, host allowlist, autolink stripping, Tavily ≤2 calls/synthesis budget, basic injection regression test). **v2-deferred**: spotlighting/datamarking, `--ack-trifecta`, base64+entropy URL checks, AgentDojo+tldrsec corpus, Tavily query hashing, dual-LLM/CaMeL. See §Open questions → "v2 hardening". |
| Issue #8 — Slop indistinguishability: LIMITATIONS callout alone is insufficient; need ingest-side classifier + real-only seed floor | v1: ships Binoculars classifier at ingest with quarantine bucket + `provenance="curated_real"` payload tag. **v2-deferred**: real-only retrieval floor (M_real prefetch), tail-preservation eval rubric, RAID adversarial canary. |
| Issue #9 — Soft-boost via 3rd Prefetch is idiosyncratic; Qdrant 1.14 ships FormulaQuery as the documented primitive | spec-fixed (2026-04-28); qdrant-client pin bumped to ≥1.14 |
| Issue #10 — Entity resolution misses M&A/rebrands, parent/subsidiary collisions, custom-domain SaaS, no human-review queue | v1: alias-graph (auto-merge blocked, written for audit) + suffix-delta parent/subsidiary detection + `pending_review` journal rows + `--list-review` printout + founding-year delta heuristic for recycled domains. **v2-deferred**: Wayback ownership-discontinuity check, CNAME lookup for custom-domain SaaS, interactive `--review` accept/reject/split workflow. |
| Issue #11 — Forced `tool_choice` output tools is now legacy; Claude's `output_config.format` (json_schema) is the GA primitive | spec-fixed (2026-04-28); both rerank and synthesize migrated to `output_config.format` |
| Issue #12 — Latency band 21–43s is too optimistic at Sonnet 4.6's measured ~47 t/s output speed; realistic is 40–90s | open — needs decision: bump latency band, drop synthesize output cap, or switch synthesize to Haiku |
| Limitations writeup (`LIMITATIONS.md`) | open — addressed by Task 1 |

The design review note this plan answers:

> The single highest-leverage move: put a `LIMITATIONS.md` (or top of README) listing the conceptual blockers from the analysis — slop indistinguishability, evaluation impossibility, substitutability vs `claude -p`. Reviewers love candidates who can argue against their own work. That writeup will move you up more than another stage of pipeline will.

That paragraph is the source of Task 1. Tasks 2 and 3 are housekeeping so the new doc is discoverable and the prior issue triage stays referentially valid.

## File structure

- **Create:** `LIMITATIONS.md` — repo root, three sections matching the source note
- **Modify:** `README.md` — one line added in the "Design notes" footer
- **Modify:** `docs/specs/2026-04-27-slopmortem-design.md` — one sentence linking `LIMITATIONS.md` near the top of the spec
- **No spec edits required for Issue #6:** the spec's two `TODO(v2)` comments at lines ~636 and ~741 already point at `docs/specs/2026-04-28-design-review-issues.md#6`. The appendix below carries an explicit `<a id="6"></a>` anchor so those links resolve.

### Standalone file vs README section — pros and cons

- **Standalone `LIMITATIONS.md` (chosen).** Visible at repo root next to `README.md` and `LICENSE`, which is the convention reviewers expect. Doesn't reshape the README's tone (the README is currently usage-and-architecture focused; bolting a candor section onto the top would jar). Easy to grow if more conceptual limits surface later. Cost: one extra click for a reviewer scrolling the README.
- **Top-of-README section (rejected).** Reviewers see it without clicking, but they also see it before the rest of the README, which buries the usage narrative. Harder to extend without making the README sprawl.

Chosen: standalone file. The README link in Task 2 covers the "reviewers see it without hunting" need; the extra click is the price.

---

## Task 1: Write `LIMITATIONS.md`

**Files:**
- Create: `LIMITATIONS.md`

The doc has three sections matching the source note, plus a one-paragraph header. Each section names the blocker, explains why it bites this project specifically, states what the project does anyway, and avoids hedging. Match the README's voice: direct, first-person where applicable. The README rarely uses em-dashes — it favors short declarative sentences and semicolons. Mirror that.

The drafted prose below is the implementer's starting point. Paste it, then read it aloud and rewrite anything that sounds like a brochure.

- [ ] **Step 1: Create the file with the header**

```markdown
# Limitations

slopmortem is a candidate-retrieval system over a corpus of failed-startup write-ups. Three conceptual problems sit underneath it that no amount of pipeline engineering removes. Reviewers should know about them before judging the rest of the design.
```

- [ ] **Step 2: Append the "Slop indistinguishability" section**

```markdown
## Slop indistinguishability

The synthesis output and its self-rated similarity scores are both LLM-generated. A confidently wrong "this is just like Pets.com" reads identical to a confidently right one. There is no signal inside the report that separates the two. The only ground truth is the reader's own judgement about whether the analogy holds, and a reader who could already make that judgement reliably wouldn't need the tool.

The pipeline pushes back on this in three small ways. A required `where_diverged` field forces the model to name at least one non-trivial difference, so it can't just cheerlead the input. Source URLs are filtered against the candidate's own cited hosts before rendering, so fabricated citations get dropped. Per-perspective scores live in a struct rather than buried in prose, so the reader can inspect each axis on its own.

None of this proves the output isn't slop. It just makes slop slightly less convenient to produce. If you can't sanity-check the output yourself, this tool will mislead you with the same confidence it informs you. That is a usage limitation, not a bug to file.
```

- [ ] **Step 3: Append the "Evaluation impossibility" section**

```markdown
## Evaluation impossibility

There is no objective metric for "did this report help this user?" The eval runner ships with structural assertions like `where_diverged_nonempty` and `lifespan_months_positive`, and those catch regressions in the shape of the output. They say nothing about whether the analogy was actually useful. Quality there is taste-driven and non-stationary; it reduces to "the user read the report and decided it was useful," which doesn't roll up into a benchmark.

The bite for this project specifically: the user's whole reason for running the tool is to get a judgement they don't already have. So the user can't easily check whether they're getting a good judgement, only whether they're getting a plausible one. Plausibility is the easy thing for an LLM to manufacture.

The eval runner exists anyway, because it stops obvious regressions: empty `where_diverged`, fabricated citations, wrong-shape Pydantic. It does not let me claim a later version of this pipeline is better at finding similar failures than an earlier one. I don't claim that.
```

- [ ] **Step 4: Append the "Substitutability vs `claude -p`" section**

```markdown
## Substitutability vs `claude -p`

A user with Claude Code installed and a folder of bookmarks could paste their pitch into `claude -p`, attach a few post-mortems they remember, and get a comparable analysis. They wouldn't have the curated corpus, the dense-plus-sparse retrieval, the rerank, or the per-candidate parallel synthesis. But on a small enough sample, what they got would be roughly as useful.

What slopmortem adds is scale (around 500 URLs against the user's working memory), structure (RRF retrieval and multi-perspective rerank, with anti-cheerleading guards inside the synthesis prompt), and reproducibility (replay datasets, an eval runner, and cost-and-latency traces on every run). For a user evaluating five startup ideas a year, the working-memory and `claude -p` path is probably enough. For a user looking at fifty ideas a year, or one wanting their analysis to be inspectable after the fact, the structure starts to pay back.

An earlier version of this design used `claude -p` as the LLM transport, and I dropped it. Not because the substrate was wrong, but because subprocess cold-starts and unmeasurable cache hits made the per-query latency and cost numbers untrustworthy. The substitutability point itself is unchanged: a less-disciplined version of this tool exists in any Claude Code session.
```

- [ ] **Step 5a: Scrub banned vocabulary**

Search the drafted file for any of these and rewrite the surrounding sentence if you find them:

- "robust", "comprehensive", "leverage", "delve", "navigate", "intricate", "vital", "essential", "key" (as adjective), "critical" (as adjective), "seamless", "ensure" (use "make sure"), "utilize" (use "use")

Run: `grep -nwiE 'robust|comprehensive|leverage|delve|navigate|intricate|vital|essential|seamless|utilize' LIMITATIONS.md`
Expected: zero matches.

- [ ] **Step 5b: Cap em-dashes at one per paragraph**

Run: `awk '/^$/{print NR": "c; c=0; next} {c+=gsub(/—/,"&")} END{print NR": "c}' LIMITATIONS.md`
Expected: every count value `≤ 1`. Where it exceeds 1, replace surplus em-dashes with periods or semicolons.

- [ ] **Step 5c: Trim rule-of-three lists where two would do**

Read every comma-separated triple in the file. For each one, ask: "is the third item carrying real information, or is it filler that makes the sentence feel complete?" If filler, drop it. The "scale, structure, reproducibility" list in Substitutability is the substantive claim of that section and stays. The three-element parenthetical lists inside it are product claims and stay. Anywhere else, two is fine.

- [ ] **Step 5d: Read it aloud against the README**

Open `README.md` and read its first paragraph aloud, then read the LIMITATIONS doc's first paragraph aloud. Same person? If the LIMITATIONS doc sounds more formal or more hedged, rewrite it down to the README's register.

- [ ] **Step 6: Verify all three blockers are covered, and each one argues against the project**

Re-read each section. Each one must (a) name the blocker, (b) explain why it bites this project specifically, (c) describe what the project does anyway without claiming it solves the blocker. The source note flagged this is the value: "Reviewers love candidates who can argue against their own work."

For each section, ask: "would a skeptical reviewer read this as candor or as deflection?" If deflection, rewrite. Acceptance: candor on all three.

- [ ] **Step 7: Verify file structure with grep**

Run:

```
test -f LIMITATIONS.md && echo OK || echo MISSING
grep -cE '^## (Slop indistinguishability|Evaluation impossibility|Substitutability vs)' LIMITATIONS.md
```

Expected: `OK` and `3`. If the count is not 3, a section heading is misspelled or missing.

---

## Task 2: Cross-link `LIMITATIONS.md` from README and spec

**Files:**
- Modify: `README.md` (last "Design notes" section, currently at line 153)
- Modify: `docs/specs/2026-04-27-slopmortem-design.md` (Summary block, currently at line 6)

- [ ] **Step 1: Update README "Design notes" footer**

The README's last section currently reads:

```markdown
## Design notes

Full spec is in [`docs/specs/2026-04-27-slopmortem-design.md`](docs/specs/2026-04-27-slopmortem-design.md). Open issues against the spec live in [`docs/specs/2026-04-28-design-review-issues.md`](docs/specs/2026-04-28-design-review-issues.md).
```

Replace with:

```markdown
## Design notes

Full spec is in [`docs/specs/2026-04-27-slopmortem-design.md`](docs/specs/2026-04-27-slopmortem-design.md). What this tool *can't* do is in [`LIMITATIONS.md`](LIMITATIONS.md); read that first if you're reviewing. Spec-level follow-ups live in [`docs/specs/2026-04-28-design-review-issues.md`](docs/specs/2026-04-28-design-review-issues.md).
```

Run: `grep -n "LIMITATIONS" README.md`
Expected: one match in the "Design notes" section.

- [ ] **Step 2: Add a one-sentence pointer in the design spec Summary**

The spec's Summary is one paragraph at lines 6–8. Append a single sentence to that paragraph (or as a new paragraph immediately after) pointing to `LIMITATIONS.md`:

```markdown
Conceptual blockers this design does not solve — slop indistinguishability, evaluation impossibility, substitutability vs `claude -p` — are tracked in [`LIMITATIONS.md`](../../LIMITATIONS.md) at the repo root. Read that before judging the rest.
```

The relative path `../../LIMITATIONS.md` is correct: the spec lives at `docs/specs/`, two levels below the repo root.

Run: `grep -n "LIMITATIONS" docs/specs/2026-04-27-slopmortem-design.md`
Expected: one match in or near the Summary section.

- [ ] **Step 3: No edit needed to this plan file**

This file already records the Limitations item under Background as the source for Task 1, and the appendix below preserves Issue #6. No further self-modification.

---

## Task 3: Verify Issue #6 spec consistency and the `#6` anchor

**Files:**
- Read-only: `docs/specs/2026-04-27-slopmortem-design.md`
- Read-only: `docs/specs/2026-04-28-design-review-issues.md` (this file)

The previous issues doc claimed the spec was edited to drop the false TOCTOU language and to add `TODO(v2)` markers pointing at `#6` in this file. Spec line numbers drift between commits; this task confirms the language and the anchor are still in place. No fix unless something is off; if the deferral itself needs to be reversed, that warrants a separate plan, not a task in this one.

- [ ] **Step 1: Confirm the false-TOCTOU language is gone**

Run:

```
grep -n "DNS lookup is repeated per outbound request" docs/specs/2026-04-27-slopmortem-design.md
```

Expected: zero matches. If it matches, the spec still has the unimplementable claim — open a follow-up to delete it.

- [ ] **Step 2: Confirm the corrected language is present**

Run:

```
grep -n "once at init" docs/specs/2026-04-27-slopmortem-design.md
grep -n "rebinding" docs/specs/2026-04-27-slopmortem-design.md
```

Expected: at least one match each, both inside the Tracing or Security sections (current spec phrases the timing as "resolved and validated once at init" and "Resolution happens once at init", both of which match the loose `once at init` pattern). If either grep returns zero, the spec needs to be re-edited from the recommendation in the appendix.

- [ ] **Step 3: Confirm the `TODO(v2)` comments still point at this file**

Run:

```
grep -n "TODO(v2)" docs/specs/2026-04-27-slopmortem-design.md
grep -n "design-review-issues.md#6" docs/specs/2026-04-27-slopmortem-design.md
```

Expected: two matches each (the same two lines: ~636 and ~741). If counts differ, a comment was edited or removed; restore from git or update the new wording to keep the `#6` anchor.

- [ ] **Step 4: Confirm the `#6` anchor itself resolves**

GitHub auto-slugs strip leading `#` from headings, so `## #6 — DNS-rebinding…` does *not* slugify to `#6`. The appendix below carries an explicit `<a id="6"></a>` anchor for that reason.

Run (whole-line match — `-x` — so prose mentions of `id="6"` don't trigger false matches):

```
grep -nxF '<a id="6"></a>' docs/specs/2026-04-28-design-review-issues.md
```

Expected: exactly one match, immediately above the appendix's `### #6` heading.

If zero matches, the anchor was deleted — restore it from this plan; otherwise the spec's `…#6` links silently point to nothing.

For final confirmation, push to a branch and click both `TODO(v2)` links in the rendered spec on GitHub. They should jump to the appendix heading. If they land at the top of the file instead, the anchor is broken even though the `id="6"` is present (rare; usually means a Markdown processor stripped raw HTML).

---

## Self-review checklist

Run at the end of execution, before marking the plan done.

- [ ] `LIMITATIONS.md` exists at repo root with three sections (Step 7 of Task 1 verified this)
- [ ] Each section argues against the project, not for it
- [ ] Voice matches the README — re-read both back-to-back; they should sound like the same author
- [ ] No banned vocabulary (Step 5a) and ≤1 em-dash per paragraph (Step 5b) survived
- [ ] `README.md` and `docs/specs/2026-04-27-slopmortem-design.md` both link to `LIMITATIONS.md`
- [ ] The two `TODO(v2)` comments in the spec still resolve to the `<a id="6">` anchor in the appendix
- [ ] No code committed (this plan is docs-only; if Path A becomes warranted, write a new plan)
- [ ] Issue #6 status remains "spec-fixed; impl deferred to v2"

---

## Appendix — Issue #6 (carried over from prior issues doc, commit `c8b50c8`)

> Preserved verbatim so the spec's `TODO(v2) … #6` anchors keep resolving. Heading levels demoted by one (from `##`/`###` to `###`/`####`) so the appendix nests under this file's structure; prose untouched. Do not edit further unless the v1 deferral is being reversed.

<a id="6"></a>

### #6 — DNS-rebinding guard cannot bind to SDK pool

**Severity:** should-fix — the spec sentence is unimplementable as written,
but on the loopback-default deployment (the spec's normal case, see
spec:185–186, 254, 331) the rebinding window is mostly cosmetic. Original
review framed this as a blocker; the architectural concern is real, the
runtime exposure is small.

**v1 decision (2026-04-28):** spec edited to drop the false TOCTOU claim
and document the residual window on the `LMNR_ALLOW_REMOTE=1` path.
Implementation of Path A (IP-pinning) deferred to v2 — see TODO comments
at spec:619 and spec:722.

#### Problem

spec:597 (review's "spec:558"/"spec:664" — the line numbers are off, the
sentence appears once):

> The DNS lookup is repeated per outbound request (TOCTOU mitigation)
> since the initial resolve can change.

What actually happens:

```
user code                    Laminar SDK                  network
─────────                    ───────────                  ───────
Laminar.init(url)  ──►  ┌──────────────────┐
                        │ httpx.Client(...)│
                        │ OTel exporter    │
                        │   keeps own conn │  ──► resolves once
                        │   pool, own DNS  │  ──► caches IP
                        └──────────────────┘  ──► reuses keep-alive
                                │
                                ▼
                        you don't get a hook here
                        ────────────────────────
```

The Laminar SDK manages its own httpx client and OTel exporter. Calling
`socket.gethostbyname()` once at `tracing.init()` does not bind the
result to the SDK's connection pool, and the SDK's later requests will
re-resolve (or use cached connections) without consulting our guard.

#### Recommendation

**Path A: fail closed by hard-pinning the resolved IP into the URL.**

```python
def init_tracing(base_url: str, allow_remote: bool = False) -> None:
    parsed = urlparse(base_url)
    host = parsed.hostname
    resolved = socket.gethostbyname(host)
    ip = ipaddress.ip_address(resolved)

    if not (ip.is_loopback or host in PRIVATE_HOST_ALLOWLIST):
        if not allow_remote:
            raise SecurityError(f"refusing tracing to non-loopback {host}")

    # rewrite URL to use the resolved IP, bypassing further DNS
    pinned = parsed._replace(netloc=f"{resolved}:{parsed.port or 443}")
    Laminar.init(base_url=urlunparse(pinned), ...)
```

After this, the SDK never resolves again — there is no second resolution
to TOCTOU. Mention in span attributes that the IP is pinned.

**Caveat for the `LMNR_ALLOW_REMOTE=1` path:** an IP-form URL fails standard
TLS hostname verification because the cert SAN is issued for the hostname,
not the IP. For loopback (the default), the spec uses plain HTTP, so this
doesn't bite. For remote, pair the IP-pinned URL with an explicit
`server_hostname=` SNI override on the underlying transport, or document
that remote deployments accept the (small) rebinding window.

**An earlier Path B (inject a custom httpx transport via `http_client=`) was
considered and dropped:** the Laminar Python SDK's `Laminar.initialize()`
signature does not accept an `http_client` / `transport` parameter (verified
against `lmnr-ai/lmnr-python` `src/lmnr/sdk/laminar.py`). Implementing it
would require either upstreaming the parameter or replacing the OTLP
exporter through OTel internals. Path A is the pragmatic choice.

#### Spec edits required

- spec:597 — replace "DNS lookup repeated per outbound request" with "host
  resolved once at init; resolved IP is pinned into `LMNR_BASE_URL` so
  subsequent requests bypass DNS entirely. For `LMNR_ALLOW_REMOTE=1`,
  document the SNI implication."
- Task #1 (Gate 1) — `tracing.py` deliverable: IP-pinning at init,
  explicit test that `Laminar.init` receives an IP-form URL
