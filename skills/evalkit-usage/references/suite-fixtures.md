# Suite fixtures: cases with input files

A case that needs an input file names a **fixture key** and the suite
directory declares the key once in `fixtures.yaml`:

```yaml
# <suite-dir>/fixtures.yaml
fixtures:
- key: q3-board-pack.pdf
  sha256: <sha256 of the file>
  context: >-
    Board pack for Q3 FY2026: management accounts, cash forecast, covenant
    schedule. 42 pages.
```

```yaml
# <suite-dir>/cases/board.yaml
- id: board-pack-covenant-headroom
  fixtures: [q3-board-pack.pdf]
  input:
    question: What is the covenant headroom at quarter end?
```

Put the bytes in the `fixtures` directory named in your `evalkit.yaml`.
`evalkit run` and `evalkit sync` upload every fixture the synced cases
reference, keyed by its `sha256` (`PUT /api/fixtures/sha256/<sha256>`). They
skip bytes the server already holds, then upload the suite's YAML and import
the cases. The sync fails if a file is missing from the directory or its bytes
do not match the registry `sha256`. The same bytes serve every workspace, so
syncing the suite into a shared workspace needs no copy step, and nobody
uploads to blob storage by hand. At run time evalkit
hands the harness each file by url or as a multipart attachment, with the
file's `context` alongside it; the case's `input` goes through unchanged.
Rubric judges see the question with an "Attached files" block listing each
file's `context`.

Rules: keys are flat filenames. Replacing a file means changing the bytes in
your `fixtures` directory, updating its `sha256`, and running or syncing
again, which re-versions the cases that use it. Two branches with different
bytes under the same key never overwrite each other.
`evalkit lint <suite-dir>` checks keys against the registry before you sync;
`evalkit lint --offline` runs the same fixture checks without a server.
