"""검증 하니스 CLI. 코퍼스를 로드→실행→요약하고 fail/error면 non-zero 종료."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness.executor import MYSQL_CONFIG, POSTGRES_CONFIG
from harness.loader import load_corpus
from harness.report import exit_code, summarize
from harness.runner import Runner
from harness.transform import PassThroughTransformer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQLBridge 검증 하니스")
    parser.add_argument("--cases-dir", type=Path, default=Path("corpus/cases"))
    parser.add_argument("--concepts", type=Path, default=Path("corpus/concepts.yaml"))
    args = parser.parse_args(argv)

    cases = load_corpus(args.cases_dir, args.concepts)
    runner = Runner(MYSQL_CONFIG, POSTGRES_CONFIG, PassThroughTransformer())
    results = [runner.run_case(c) for c in cases]
    print(summarize(results))
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
