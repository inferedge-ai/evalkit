# 13. Trace mappings and checks

A developer checks how one OTel session of their agent turns into a trajectory, and changes the agent's span mapping until it looks right.

## Actors

- **Developer**: can write the agent's workspace: the owner of a personal workspace, or a `writer` or admin on a shared one.
- **CLI**: `evalkit traces check`, `evalkit traces mapping push|list`.
- **UI**: the agent page, Mappings tab.
- **evalkit server**: holds the agent's spans and mapping versions, and builds trajectories on read.

Terms:
- **Session:** the spans of one run.
- **Mapping:** a JSON document that says which span attribute holds each ESF field, plus `answer` and `exclude_spans`. Every OTel agent has its own mapping, created with the agent (`evalkit agents create … --trajectory-source otel --mapping file.json` stores v1 as active). Versions are `draft`, `active` or `retired`. An agent has at most one draft and one active version.
- **Starting points:** the `connect-traces` skill ships `gen_ai.json` and `openinference.json`. They hold the common session id attributes (`session.id`, `gen_ai.conversation.id`, `langfuse.session.id`). evalkit's code has no built-in mapping.
- **Agent references:** the API and UI always use `name@workspace` (`research-agent@dev`). The CLI also takes a bare name, meaning your personal workspace.

## Sequence

```mermaid
sequenceDiagram
  actor Dev as Developer
  participant CLI
  participant UI
  participant S as evalkit server

  Dev->>CLI: evalkit traces check research-agent SESSION [--mapping active|draft] [--dump x-spans.json]
  CLI->>S: GET session trajectory (mapping, with ESF)
  S-->>CLI: settled?, span count, ESF spans, unmapped attributes, mapping version, ATIF
  opt --dump
    CLI->>S: GET raw spans (only when the file ends in spans.json)
    CLI->>CLI: write spans / ESF / trajectory JSON to the file
  end
  CLI-->>Dev: check lines: session, root, mapping, llm, tools, unmapped, trajectory

  Dev->>CLI: evalkit traces mapping push research-agent mapping.json [--activate]
  CLI->>S: PUT draft mapping (activate=true|false)
  S-->>CLI: "draft vN for research-agent" (or "active vN …")
  Dev->>CLI: evalkit traces check research-agent SESSION --mapping draft
  CLI-->>Dev: same check lines, built with the draft

  Dev->>UI: Agents → research-agent@akshat → Mappings
  UI->>S: list versions, last 10 runs
  UI->>S: GET session trajectory (active) and (draft, if one exists)
  UI-->>Dev: side-by-side preview: active vs draft, trajectory viewer
  Dev->>UI: Activate (on the draft row)
  UI->>S: POST activate version N
  S-->>UI: vN active, the previous active becomes retired
```

## Expected outcomes

- **`traces check`:**
  - Prints one line each for `session`, `root`, `mapping`, `llm`, `tools`, `unmapped` and `trajectory`, marked ✓ or ! or ✗.
  - **`session`:** `<n> spans in <k> traces, settled`, or `not settled yet` while spans are still arriving.
  - **`mapping`:** `vN`, the version used. With no active mapping it warns `no active mapping; the agent's spans are unmapped`.
  - **`llm`:**
    - Shows span count, models and token totals (in, out, cached).
    - Warns about LLM spans with no messages ("is message content capture on?") and about missing token counts.
    - Warns when there are no LLM spans.
  - **`tools`:** counts tool spans matched to tool calls by id, and warns about spans placed in no step.
  - **Exit code:** 1 when the session has no root span; otherwise 0, even with warnings.
- **`--dump`:**
  - The file name picks the content: `…spans.json` gives raw spans, `…esf.json` gives ESF, `…trajectory.json` gives ATIF. Any other name is an error.
  - Parent directories are created.
- **`--mapping draft`** previews the draft, including its `answer` and `exclude_spans`.
- **`mapping push`:**
  - Validates the document, then stores it as a draft with the next version number.
  - An existing draft becomes `retired`.
  - With `--activate`, the version becomes active and the old active one is retired.
- **`mapping list`:** prints `vN  status  by <user>  <created_at>` for every version.
- **UI Mappings tab:**
  - Shown only for agents whose trajectory source is `otel`.
  - Lists the agent's versions and previews active vs draft over recent runs.
  - The Activate button appears only on the draft row.
  - The UI cannot upload a mapping; uploads go through the CLI.
- **Activating a mapping changes:**
  - how trajectories are built on read, immediately, including for sessions already stored
  - which attribute supplies the session id, for spans *received after* activation only

## Error cases

- **Unknown agent:** 404 `unknown agent: <ref> — create it with evalkit agents create`.
- **`--mapping draft` with no draft:** 404 `no draft mapping for <ref>; push one with evalkit traces mapping push`.
- **Mapping document fails validation:** 400 `<field.path>: <message>`.
- **Activating a version that doesn't exist:** 404 `no mapping version N`.
- **Push or activate without write access to the agent's workspace:** 403 `you cannot change <ref>'s mappings or traces`. Shared workspace: admin or `writer`. Personal workspace: the owner only; admins are read-only there.
- **The agent's workspace is archived:** 409 `workspace <name> is archived`.
- **Session over `otel.max_session_bytes`:** 413 naming the size and the limit.
- **Unknown session id:** zero spans; the `root` line fails (✗), exit 1.
