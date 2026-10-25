"""Загрузка и валидация экспертной карты из YAML.

Карта — это данные, а не код: риск-подразделение меняет границы, баллы,
стоп-факторы и политику без изменения Python-кода. Загрузчик проверяет
структуру, чтобы ошибка конфигурации падала при старте, а не в проде.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import yaml

from .normalization import normalize_text


class ConfigError(ValueError):
    """Некорректная конфигурация карты."""


DEFAULT_CARD_PATH = Path(__file__).with_name("card.yaml")

_ALLOWED_KINDS = {"range", "tri", "category", "okved"}
_ALLOWED_APPLIES = {"all", "mature", "young"}
_ALLOWED_OPS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "is_null",
    "is_not_null",
    "is_true",
    "is_false",
}

# Полный набор признаков, которые ядро кладёт в плоский словарь.
# Нужен, чтобы опечатка в source/field не отключала фактор или стоп молча.
KNOWN_FEATURES = frozenset(
    {
        "branch",
        "requested_amount",
        "term_months",
        "legal_form",
        "purpose",
        "okved_section",
        "months_since_registration",
        "address_mass",
        "address_unreliable",
        "director_disqualified",
        "director_mass",
        "companies_per_director",
        "has_credit_history",
        "max_overdue_12m",
        "active_loans",
        "owners_count",
        "nominal_owner",
        "related_parties",
        "months_with_inflows",
        "avg_monthly_inflow",
        "inflow_variability",
        "pti",
        "guarantee_present",
        "guarantee_coverage",
        "collateral_coverage",
        "collateral_type",
        "guarantor_pti",
    }
)


@dataclass(frozen=True, slots=True)
class Bin:
    points: float
    label: str
    min: float | None = None
    max: float | None = None

    def matches(self, value: float) -> bool:
        return (self.min is None or value >= self.min) and (self.max is None or value < self.max)


@dataclass(frozen=True, slots=True)
class OkvedSection:
    min: int
    max: int
    points: float
    label: str


@dataclass(frozen=True, slots=True)
class Factor:
    id: str
    title: str
    source: str
    kind: str
    applies_to: str
    weight: float
    missing_points: float
    bins: tuple[Bin, ...] = ()
    yes_points: float | None = None
    no_points: float | None = None
    categories: Mapping[str, float] = field(default_factory=dict)
    default_points: float | None = None
    sections: tuple[OkvedSection, ...] = ()

    def applies(self, branch: str) -> bool:
        return self.applies_to == "all" or self.applies_to == branch


@dataclass(frozen=True, slots=True)
class RateBand:
    max_pd: float
    premium: float


@dataclass(frozen=True, slots=True)
class Policy:
    currency: str
    min_amount: Decimal
    max_amount: Decimal
    min_term_months: int
    max_term_months: int
    max_pd: float
    review_pd: float | None
    min_coverage: float
    pti_limit: float
    inflow_haircut: float
    base_rate: float
    reference_rate: float
    min_rate: float
    max_rate: float
    rate_rounding: float
    amount_rounding: Decimal
    rate_grid: tuple[RateBand, ...]
    collateral_ltv: Mapping[str, float]
    collateral_ltv_default: float
    collateral_discount: Mapping[str, float]
    critical_features: tuple[str, ...]

    def rate_premium(self, pd: float) -> float:
        for band in self.rate_grid:
            if pd <= band.max_pd + 1e-12:
                return band.premium
        return self.rate_grid[-1].premium


@dataclass(frozen=True, slots=True)
class HardStop:
    id: str
    code: str
    title: str
    when: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CardConfig:
    version: str
    name: str
    updated: str | None
    intercept: float
    policy: Policy
    factors: tuple[Factor, ...]
    hard_stops: tuple[HardStop, ...]
    required_features: Mapping[str, tuple[str, ...]]
    source_path: str | None = None

    @classmethod
    def load_default(cls) -> "CardConfig":
        return cls.load(DEFAULT_CARD_PATH)

    @classmethod
    def load(cls, path: str | Path) -> "CardConfig":
        path = Path(path)
        try:
            with path.open("r", encoding="utf-8") as handle:
                raw = yaml.safe_load(handle)
        except OSError as exc:
            raise ConfigError(f"Не удалось прочитать карту {path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise ConfigError(f"Ошибка YAML в {path}: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise ConfigError(f"{path}: ожидался объект на верхнем уровне")
        return cls.from_dict(raw, source_path=str(path))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, source_path: str | None = None) -> "CardConfig":
        version = _text(raw.get("version"), "version")
        name = _text(raw.get("name", "Экспертная карта"), "name")
        updated = raw.get("updated")
        updated = str(updated) if updated is not None else None

        model = _mapping(raw.get("model"), "model")
        intercept = _number(model.get("intercept"), "model.intercept")

        policy = _parse_policy(_mapping(raw.get("policy"), "policy"))
        factors = _parse_factors(raw.get("factors"))
        hard_stops = _parse_hard_stops(raw.get("hard_stops"))
        required = _parse_required_features(raw.get("required_features"))

        for factor in factors:
            if factor.source not in KNOWN_FEATURES:
                raise ConfigError(
                    f"factor {factor.id!r}: неизвестный source {factor.source!r}"
                )
        for group, feature_ids in required.items():
            for feature_id in feature_ids:
                if feature_id not in KNOWN_FEATURES:
                    raise ConfigError(
                        f"required_features.{group}: неизвестный признак {feature_id!r}"
                    )

        return cls(
            version=version,
            name=name,
            updated=updated,
            intercept=intercept,
            policy=policy,
            factors=factors,
            hard_stops=hard_stops,
            required_features=required,
            source_path=source_path,
        )

    def factor(self, factor_id: str) -> Factor:
        for factor in self.factors:
            if factor.id == factor_id:
                return factor
        raise KeyError(factor_id)


# Признаки, которые используются в policy/required, но не являются факторами карты.
def _parse_policy(raw: Mapping[str, Any]) -> Policy:
    min_amount = _decimal(raw.get("min_amount"), "policy.min_amount")
    max_amount = _decimal(raw.get("max_amount"), "policy.max_amount")
    if min_amount <= 0 or max_amount <= 0:
        raise ConfigError("policy.min_amount/max_amount: должны быть больше нуля")
    if min_amount > max_amount:
        raise ConfigError("policy.min_amount: не может быть больше max_amount")

    min_term = _integer(raw.get("min_term_months"), "policy.min_term_months")
    max_term = _integer(raw.get("max_term_months"), "policy.max_term_months")
    if min_term <= 0 or max_term <= 0 or min_term > max_term:
        raise ConfigError("policy.min_term_months/max_term_months: некорректный диапазон")

    max_pd = _probability(raw.get("max_pd"), "policy.max_pd")
    review_pd_raw = raw.get("review_pd")
    review_pd = (
        _probability(review_pd_raw, "policy.review_pd") if review_pd_raw is not None else None
    )
    if review_pd is not None and review_pd <= max_pd:
        raise ConfigError("policy.review_pd: должен быть больше max_pd")
    if review_pd is not None and review_pd > 1:
        raise ConfigError("policy.review_pd: должен быть ≤ 1")

    min_coverage = _probability(raw.get("min_coverage"), "policy.min_coverage")
    pti_limit = _probability(raw.get("pti_limit"), "policy.pti_limit")
    haircut = _probability(raw.get("inflow_haircut"), "policy.inflow_haircut")
    if haircut == 0:
        raise ConfigError("policy.inflow_haircut: должен быть больше нуля")

    base_rate = _number(raw.get("base_rate"), "policy.base_rate")
    reference_rate = _number(raw.get("reference_rate"), "policy.reference_rate")
    min_rate = _number(raw.get("min_rate"), "policy.min_rate")
    max_rate = _number(raw.get("max_rate"), "policy.max_rate")
    if not 0 <= min_rate <= max_rate:
        raise ConfigError("policy.min_rate/max_rate: некорректный диапазон")
    if not min_rate <= base_rate <= max_rate:
        raise ConfigError("policy.base_rate: должен попадать в [min_rate, max_rate]")
    if reference_rate <= 0:
        raise ConfigError("policy.reference_rate: должен быть больше нуля")

    rate_rounding = _number(raw.get("rate_rounding", 0.5), "policy.rate_rounding")
    if rate_rounding <= 0:
        raise ConfigError("policy.rate_rounding: должен быть больше нуля")

    amount_rounding = _decimal(raw.get("amount_rounding", 1), "policy.amount_rounding")
    if amount_rounding <= 0:
        raise ConfigError("policy.amount_rounding: должен быть больше нуля")

    grid_raw = raw.get("rate_grid")
    if not isinstance(grid_raw, list) or not grid_raw:
        raise ConfigError("policy.rate_grid: нужен непустой список")
    bands: list[RateBand] = []
    for index, item in enumerate(grid_raw):
        item = _mapping(item, f"policy.rate_grid[{index}]")
        bands.append(
            RateBand(
                max_pd=_probability(item.get("max_pd"), f"policy.rate_grid[{index}].max_pd"),
                premium=_number(item.get("premium"), f"policy.rate_grid[{index}].premium"),
            )
        )
    bands.sort(key=lambda band: band.max_pd)
    for previous, current in zip(bands, bands[1:]):
        if current.max_pd <= previous.max_pd:
            raise ConfigError("policy.rate_grid: границы max_pd должны возрастать")
        if current.premium < previous.premium:
            raise ConfigError("policy.rate_grid: премия не может падать с ростом PD")
    effective_max = max(max_pd, review_pd or 0)
    if bands[-1].max_pd < effective_max:
        raise ConfigError("policy.rate_grid: последняя граница должна покрывать review_pd")

    ltv_raw = _mapping(raw.get("collateral_ltv", {}), "policy.collateral_ltv")
    ltv = {normalize_text(key): _probability(value, f"policy.collateral_ltv.{key}")
           for key, value in ltv_raw.items()}
    ltv_default = _probability(
        raw.get("collateral_ltv_default", 0.25), "policy.collateral_ltv_default"
    )

    discount_raw = _mapping(
        raw.get("collateral_discount", {}), "policy.collateral_discount"
    )
    discount = {normalize_text(key): _number(value, f"policy.collateral_discount.{key}")
                for key, value in discount_raw.items()}

    critical_raw = raw.get("critical_features", [])
    if not isinstance(critical_raw, list) or not all(
        isinstance(feature, str) for feature in critical_raw
    ):
        raise ConfigError("policy.critical_features: ожидался список строк")
    for feature in critical_raw:
        if feature not in KNOWN_FEATURES:
            raise ConfigError(
                f"policy.critical_features: неизвестный признак {feature!r}"
            )

    return Policy(
        currency=_text(raw.get("currency", "RUB"), "policy.currency"),
        min_amount=min_amount,
        max_amount=max_amount,
        min_term_months=min_term,
        max_term_months=max_term,
        max_pd=max_pd,
        review_pd=review_pd,
        min_coverage=min_coverage,
        pti_limit=pti_limit,
        inflow_haircut=haircut,
        base_rate=base_rate,
        reference_rate=reference_rate,
        min_rate=min_rate,
        max_rate=max_rate,
        rate_rounding=rate_rounding,
        amount_rounding=amount_rounding,
        rate_grid=tuple(bands),
        collateral_ltv=ltv,
        collateral_ltv_default=ltv_default,
        collateral_discount=discount,
        critical_features=tuple(critical_raw),
    )


def _parse_factors(raw: Any) -> tuple[Factor, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("factors: нужен непустой список")

    factors: list[Factor] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        path = f"factors[{index}]"
        item = _mapping(item, path)
        factor_id = _text(item.get("id"), f"{path}.id")
        if factor_id in seen:
            raise ConfigError(f"{path}.id: дубликат {factor_id!r}")
        seen.add(factor_id)

        kind = _text(item.get("kind", "range"), f"{path}.kind")
        if kind not in _ALLOWED_KINDS:
            raise ConfigError(f"{path}.kind: неизвестный тип {kind!r}")
        applies_to = _text(item.get("applies_to", "all"), f"{path}.applies_to")
        if applies_to not in _ALLOWED_APPLIES:
            raise ConfigError(f"{path}.applies_to: неизвестная ветка {applies_to!r}")

        weight = _number(item.get("weight"), f"{path}.weight")
        if weight <= 0:
            raise ConfigError(f"{path}.weight: должен быть больше нуля")
        missing_points = _number(item.get("missing_points"), f"{path}.missing_points")

        kwargs: dict[str, Any] = {}
        if kind == "range":
            kwargs["bins"] = _parse_bins(item.get("bins"), path)
        elif kind == "tri":
            kwargs["yes_points"] = _number(item.get("yes_points"), f"{path}.yes_points")
            kwargs["no_points"] = _number(item.get("no_points"), f"{path}.no_points")
        elif kind == "category":
            category_raw = _mapping(item.get("categories"), f"{path}.categories")
            if not category_raw:
                raise ConfigError(f"{path}.categories: нужен непустой объект")
            kwargs["categories"] = {
                normalize_text(key): _number(value, f"{path}.categories.{key}")
                for key, value in category_raw.items()
            }
            kwargs["default_points"] = _number(
                item.get("default_points", missing_points), f"{path}.default_points"
            )
        elif kind == "okved":
            kwargs["sections"] = _parse_okved_sections(item.get("sections"), path)
            kwargs["default_points"] = _number(
                item.get("default_points", missing_points), f"{path}.default_points"
            )

        factors.append(
            Factor(
                id=factor_id,
                title=_text(item.get("title"), f"{path}.title"),
                source=_text(item.get("source"), f"{path}.source"),
                kind=kind,
                applies_to=applies_to,
                weight=weight,
                missing_points=missing_points,
                **kwargs,
            )
        )
    return tuple(factors)


def _parse_bins(raw: Any, path: str) -> tuple[Bin, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{path}.bins: нужен непустой список")
    bins: list[Bin] = []
    for index, item in enumerate(raw):
        item = _mapping(item, f"{path}.bins[{index}]")
        lower = item.get("min")
        upper = item.get("max")
        lower_value = float(lower) if lower is not None else None
        upper_value = float(upper) if upper is not None else None
        if lower_value is not None and upper_value is not None and lower_value >= upper_value:
            raise ConfigError(f"{path}.bins[{index}]: min должен быть меньше max")
        bins.append(
            Bin(
                min=lower_value,
                max=upper_value,
                points=_number(item.get("points"), f"{path}.bins[{index}].points"),
                label=_text(
                    item.get("label", _interval_label(lower_value, upper_value)),
                    f"{path}.bins[{index}].label",
                ),
            )
        )

    if bins[0].min is not None:
        raise ConfigError(f"{path}.bins: первая граница должна быть открытой (min отсутствует)")
    if bins[-1].max is not None:
        raise ConfigError(f"{path}.bins: последняя граница должна быть открытой (max отсутствует)")
    for index in range(len(bins) - 1):
        if bins[index].max is None:
            raise ConfigError(f"{path}.bins[{index}]: max не может отсутствовать в середине")
        if bins[index].max != bins[index + 1].min:
            raise ConfigError(
                f"{path}.bins: разрыв/перекрытие между {bins[index].label!r} "
                f"и {bins[index + 1].label!r}"
            )
    return tuple(bins)


def _parse_okved_sections(raw: Any, path: str) -> tuple[OkvedSection, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{path}.sections: нужен непустой список")
    sections: list[OkvedSection] = []
    for index, item in enumerate(raw):
        item = _mapping(item, f"{path}.sections[{index}]")
        lower = _integer(item.get("min"), f"{path}.sections[{index}].min")
        upper = _integer(item.get("max"), f"{path}.sections[{index}].max")
        if not 1 <= lower <= upper <= 99:
            raise ConfigError(f"{path}.sections[{index}]: диапазон должен быть в 1..99")
        sections.append(
            OkvedSection(
                min=lower,
                max=upper,
                points=_number(item.get("points"), f"{path}.sections[{index}].points"),
                label=_text(item.get("label", f"{lower:02d}–{upper:02d}"),
                            f"{path}.sections[{index}].label"),
            )
        )
    sections.sort(key=lambda section: section.min)
    for previous, current in zip(sections, sections[1:]):
        if current.min <= previous.max:
            raise ConfigError(f"{path}.sections: пересечение диапазонов ОКВЭД")
    return tuple(sections)


def _parse_hard_stops(raw: Any) -> tuple[HardStop, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError("hard_stops: ожидался список")
    stops: list[HardStop] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        path = f"hard_stops[{index}]"
        item = _mapping(item, path)
        stop_id = _text(item.get("id"), f"{path}.id")
        if stop_id in seen:
            raise ConfigError(f"{path}.id: дубликат {stop_id!r}")
        seen.add(stop_id)
        when = _mapping(item.get("when"), f"{path}.when")
        _validate_predicate(when, f"{path}.when")
        stops.append(
            HardStop(
                id=stop_id,
                code=_text(item.get("code"), f"{path}.code"),
                title=_text(item.get("title"), f"{path}.title"),
                when=when,
            )
        )
    return tuple(stops)


def _validate_predicate(node: Mapping[str, Any], path: str) -> None:
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        children = node[key]
        if not isinstance(children, list) or not children:
            raise ConfigError(f"{path}.{key}: нужен непустой список условий")
        for index, child in enumerate(children):
            _validate_predicate(
                _mapping(child, f"{path}.{key}[{index}]"), f"{path}.{key}[{index}]"
            )
        return

    field_name = node.get("field")
    if not isinstance(field_name, str) or not field_name:
        raise ConfigError(f"{path}.field: обязательное поле")
    if field_name not in KNOWN_FEATURES:
        raise ConfigError(f"{path}.field: неизвестный признак {field_name!r}")
    op = node.get("op")
    if op not in _ALLOWED_OPS:
        raise ConfigError(f"{path}.op: неизвестная операция {op!r}")
    if op not in {"is_null", "is_not_null", "is_true", "is_false"} and "value" not in node:
        raise ConfigError(f"{path}.value: обязательное поле для операции {op!r}")
    if op == "in" and not isinstance(node.get("value"), list):
        raise ConfigError(f"{path}.value: для операции 'in' нужен список")


def _parse_required_features(raw: Any) -> Mapping[str, tuple[str, ...]]:
    if raw is None:
        return {"all": (), "mature": (), "young": ()}
    raw = _mapping(raw, "required_features")
    result: dict[str, tuple[str, ...]] = {}
    for group in ("all", "mature", "young"):
        values = raw.get(group, [])
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ConfigError(f"required_features.{group}: ожидался список строк")
        result[group] = tuple(values)
    return result


def _interval_label(lower: float | None, upper: float | None) -> str:
    if lower is None and upper is not None:
        return f"< {upper:g}"
    if lower is not None and upper is None:
        return f"≥ {lower:g}"
    if lower is not None and upper is not None:
        return f"{lower:g}–{upper:g}"
    return "любое"


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path}: ожидался объект")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: ожидалась непустая строка")
    return value.strip()


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path}: ожидалось число")
    return float(value)


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path}: ожидалось целое число")
    return value


def _decimal(value: Any, path: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ConfigError(f"{path}: ожидалось число")
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - сообщение важнее типа
        raise ConfigError(f"{path}: не удалось разобрать число {value!r}") from exc


def _probability(value: Any, path: str) -> float:
    number = _number(value, path)
    if not 0 <= number <= 1:
        raise ConfigError(f"{path}: вероятность должна быть в [0, 1]")
    return number
