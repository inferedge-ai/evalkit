# /// script
# requires-python = ">=3.13"
# dependencies = ["evalkit[otel]"]
#
# [[tool.uv.index]]
# url = "http://localhost:8020/api/cli/simple"
#
# [tool.uv]
# upgrade-package = ["evalkit"]
# ///
import argparse
from pathlib import Path

from agent import Analyst
from dotenv import load_dotenv

from evalkit.sdk import run_experiment, tracing


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the acme dataset against the sample agent.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Run one case without creating an experiment."
    )
    args = parser.parse_args()
    load_dotenv(Path(__file__).parent / ".env")
    tracing.register(agent="analyst")
    analyst = Analyst()

    def task(input: dict) -> str:
        return analyst.ask(input["question"])

    result = run_experiment(dataset="acme", agent="analyst", task=task, dry_run=args.dry_run)
    if result.url:
        print(result.url)


if __name__ == "__main__":
    main()
