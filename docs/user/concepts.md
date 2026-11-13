# Concepts

The words you need before your first scorecard. Everything else is explained on the page that uses it.

<!-- anchor-rows -->
| Term                       | Meaning                                                                                                                              |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Case                       | One eval question, written in YAML: the input, the rubrics a right answer meets, and the checks to run.                              |
| Dataset                    | A directory of cases, run and tracked together. The directory name is the dataset name, and subfolders become tags you can run alone. |
| Sync                       | Copying a dataset directory to the server. Runs do it for you.                                                                       |
| Workspace                  | Where datasets, agents and experiments live: your own (named after you), or a shared one such as `dev`. Every user can read every workspace; only the owner changes a personal one, and admins and writers change a shared one.                              |
| Hosted evalkit             | The evalkit server InferEdge runs at `https://evalkit.inferedge.com`, open to any organisation. `evalkit login` uses it unless you pass `--url`. |
| Reference                  | `name@workspace`, for example `acme@dev`. A bare name means your own workspace.                                                      |
| Agent                      | Your agent, registered once on the server. A code-driven agent is run by your own code; this is the usual way to start.             |
| Task                       | The function `run_experiment` calls once per case. It calls your agent and returns the answer.                                       |
| Experiment                 | One run of a dataset against an agent.                                                                                               |
| Run                        | One case answered once.                                                                                                              |
| App run                    | A run of your agent outside any experiment: production, staging, or you trying it by hand. Its traces show on the Runs page. |
| Single sign-on             | Signing in through your organisation's identity provider. The first sign-in creates your evalkit user. See [Single sign-on](sso.md). |
| Subject                    | The stable id your identity provider assigns you; evalkit keys your user on it, not on your email. |
| Machine user               | A user bound to a machine client at the identity provider, for CI. No password, no browser. `evalkit users add <name> --subject <sub>`. |
| Trace, span, session       | What your agent records as it works: one span per model call or tool call. One run is one session.                                  |
| Trace source               | A third-party trace store, such as Phoenix, that an admin connects. You import past traces from it, and an agent that names it in its settings gets its experiments' trajectories from it instead of from the collector. |
| Mapping                    | Which span attribute holds the model, the messages, the tokens and each tool call.                                                   |
| Preset                     | A span mapping that ships with evalkit for a common framework or span convention, such as `gen_ai` or `crewai`. List them with `evalkit traces mapping presets`. |
| Trajectory                 | The step-by-step record of a run that evalkit builds from the spans.                                                                 |
| Judge                      | The LLM that grades an answer against the case's rubrics. By default it runs on the evalkit server, served by an LLM provider configured there; you can bring your own key instead. |
| Evaluator                  | One scoring check, for example the rubric check or "did the agent call this tool".                                                   |
| Scorecard                  | The table of scores for an experiment, per case and per bucket (content, trajectory, presentation, efficiency, custom). [Case schema](case-schema.md#evaluators) maps each bucket to its YAML key, e.g. presentation → `evaluators.output.presentation_judge`. A run that did not complete with an answer is errored and gets no score. The scorecard counts the cases where every run errored (`cases: 6, 3 errored`) and, with repetitions, the cases where only some did (`1 partly errored`, scored on the runs that completed). A bucket that covers fewer cases than the experiment selected says so (`content 1.00 (3 of 6 cases)`). |
| Unreviewed                 | A case in a shared dataset with no review decision at its current content. It runs like any other case; review decides promotion.  |
| Promotion                  | An admin links a shared workspace to a target (`evalkit workspaces promote-to dev prod`). Approving a case in `dev` adds it, approved, to the same-named dataset in `prod`. Datasets in `prod` are fed only that way. |
| `evalkit.yaml`             | The optional file in your repo that says where your cases are and which environment variables hold API keys. Without it, cases are read from `evalkit/cases/<dataset>/` and fixtures from `evalkit/fixtures/`. |

Next: [Try locally](try-locally.md) to see these on a running server, or the [Quickstart](quickstart.md) to connect your own agent.
