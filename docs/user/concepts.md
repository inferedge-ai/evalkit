# Concepts

The words you need before your first scorecard. Everything else is explained on the page that uses it.

| Term                       | Meaning                                                                                                                              |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Case                       | One eval question, written in YAML: the input, the rubrics a right answer meets, and the checks to run.                              |
| Dataset                    | A directory of cases, run and tracked together. The directory name is the dataset name, and subfolders become tags you can run alone. |
| Sync                       | Copying a dataset directory to the server. Runs do it for you.                                                                       |
| Workspace                  | Where datasets, agents and experiments live: your own (named after you), or a shared one such as `dev`. Every user can read every workspace; only the owner changes a personal one, and admins and writers change a shared one.                              |
| Reference                  | `name@workspace`, for example `acme@dev`. A bare name means your own workspace.                                                      |
| Agent                      | Your agent, registered once on the server. A code-driven agent is run by your own code; this is the usual way to start.             |
| Task                       | The function `run_experiment` calls once per case. It calls your agent and returns the answer.                                       |
| Experiment                 | One run of a dataset against an agent.                                                                                               |
| Run                        | One case answered once.                                                                                                              |
| Trace, span, session       | What your agent records as it works: one span per model call or tool call. One run is one session.                                  |
| Mapping                    | Which span attribute holds the model, the messages, the tokens and each tool call.                                                   |
| Trajectory                 | The step-by-step record of a run that evalkit builds from the spans.                                                                 |
| Judge                      | The LLM that grades an answer against the case's rubrics.                                                                            |
| Evaluator                  | One scoring check, for example the rubric check or "did the agent call this tool".                                                   |
| Scorecard                  | The table of scores for an experiment, per case and per bucket (content, trajectory, presentation, efficiency, custom).              |
| Draft                      | A case in a shared dataset that no reviewer has approved yet. `--include-drafts` runs it anyway.                                     |
| Promotion                  | An admin links a shared workspace to a target (`evalkit workspaces promote-to dev prod`). Approving a case in `dev` adds it, approved, to the same-named dataset in `prod`. Datasets in `prod` are fed only that way. |
| `evalkit.yaml`             | The file in your repo that says where your cases are and which environment variables hold API keys.                                  |

Next: [Try locally](try-locally.md) to see these on a running server, or the [Quickstart](quickstart.md) to connect your own agent.
