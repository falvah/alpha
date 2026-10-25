"""CLI для демонстрации: JSON заявки → решение и расшифровка."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .config import CardConfig
from .engine import Decision, Scorer
from .models import LoanRequest


def _format_money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", " ")


def _summary(name: str, decision: Decision) -> str:
    verdict = {
        "approve": "ОДОБРИТЬ",
        "decline": "ОТКАЗ",
        "review": "НА РУЧНОЕ РЕШЕНИЕ",
    }[decision.decision]
    if decision.decision == "approve":
        terms = (
            f"{_format_money(float(decision.approved_amount))} "
            f"{decision.currency}, {decision.annual_rate:.2f}% годовых, "
            f"{decision.term_months} мес., платёж {_format_money(float(decision.monthly_payment))}"
        )
    elif decision.decision == "review":
        terms = (
            f"предложение {_format_money(float(decision.approved_amount))} "
            f"под {decision.annual_rate:.2f}%"
        )
    else:
        terms = "—"
    codes = ", ".join(reason.code for reason in decision.reasons) or "—"
    return (
        f"{name}: {verdict} | {terms} | PD {decision.pd:.2%} | "
        f"покрытие данных {decision.data_coverage:.0%} | {codes}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Экспертная скоринговая карта: расчёт решения по JSON заявки"
    )
    parser.add_argument("files", nargs="+", help="JSON-файлы заявок")
    parser.add_argument("--card", help="путь к YAML карты (по умолчанию встроенная)")
    parser.add_argument("--json", action="store_true", help="вывести полный JSON решения")
    args = parser.parse_args(argv)

    config = CardConfig.load(args.card) if args.card else CardConfig.load_default()
    scorer = Scorer(config)
    exit_code = 0
    result: dict[str, object] = {}

    for filename in args.files:
        path = Path(filename)
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
            request = LoanRequest.from_payload(payload)
            decision = scorer.score(request)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"{filename}: ОШИБКА: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        if args.json:
            result[path.name] = decision.to_dict()
        else:
            print(_summary(path.name, decision))

    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
