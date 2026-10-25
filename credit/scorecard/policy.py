"""Local illustrative coefficients: the supplied documents contain no card.yaml.

Version identifies this implementation, not a recovered bank scorecard.
"""
from decimal import Decimal as D

VERSION = "project-1.0.0"
INTERCEPT = -2.50
REFERENCE_RATE = D("24")
INFLOW_HAIRCUT = D("0.8")
MAX_PTI = D("0.30")
MAX_PD = 0.30
REVIEW_PD = 0.20
MIN_COVERAGE = 0.60
MIN_AMOUNT = D("100000")
COLLATERAL_LTV = {"недвижимость": D("0.7"), "транспорт": D("0.5"), "оборудование": D("0.5"), "прочее": D("0.3")}
CRITICAL = ("address_mass", "address_unreliable", "director_disqualified", "director_mass", "nominal_owner")
REASON_TITLES = {
    "ADDRESS_MASS": "Массовый адрес регистрации", "ADDRESS_UNRELIABLE": "Недостоверный адрес",
    "DIRECTOR_DISQUALIFIED": "Руководитель дисквалифицирован", "MASS_MANAGEMENT": "Массовый руководитель",
    "NOMINAL_OWNER": "Номинальный владелец", "CRITICAL_OVERDUE": "Просрочка более 90 дней",
    "DOCUMENT_MISSING": "Нет данных документа", "MISSING_REQUIRED_DATA": "Не заполнены обязательные данные",
    "CRITICAL_DATA_MISSING": "Не завершены критичные проверки", "LOW_DATA_COVERAGE": "Недостаточное покрытие данных",
    "BRANCH_MISMATCH": "Для зрелой ветки нужны поступления минимум за 12 месяцев",
    "HIGH_PD": "Риск превышает допустимый уровень", "GREY_ZONE": "Нужна ручная проверка риска",
    "INSUFFICIENT_LIMIT": "Дохода и обеспечения недостаточно для минимальной суммы",
    "AMOUNT_REDUCED": "Запрошенная сумма превышает доступный лимит", "APPROVED": "Проверки пройдены",
}
