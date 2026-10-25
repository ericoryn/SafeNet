"""Stable feature metadata; numerical weights live only in detector.toml."""

from safenet.contracts import Track

# ID -> track, display description, contribution direction.
CATALOG = {
    "lottery.payment": (Track.LOTTERY, "Признаки оплаты участия", 1),
    "lottery.random_selection": (Track.LOTTERY, "Признаки случайного выбора", 1),
    "lottery.organizer_income": (Track.LOTTERY, "Упоминание сбора или дохода организатора", 1),
    "lottery.paid_random": (Track.LOTTERY, "Оплата и случайный выбор рядом в тексте", 1),
    "lottery.free_participation": (Track.LOTTERY, "Явно указано бесплатное участие", -1),
    "lottery.reporting_context": (Track.LOTTERY, "Контекст новости или учебного разбора", -1),
    "casino.gambling": (Track.CASINO, "Упоминание игры на деньги", 1),
    "casino.promotion": (Track.CASINO, "Призыв перейти, зарегистрироваться или внести депозит", 1),
    "casino.payout_claim": (Track.CASINO, "Демонстрация выигрыша или вывода", 1),
    "casino.referral": (Track.CASINO, "Промокод или партнёрский переход рядом с игрой", 1),
    "casino.gambling_promotion": (Track.CASINO, "Игра на деньги и её продвижение рядом", 1),
    "casino.no_gambling_offer": (Track.CASINO, "Явное отсутствие предложения игры на деньги", -1),
    "casino.reporting_context": (Track.CASINO, "Контекст новости или учебного разбора", -1),
    "investment.profit_promise": (
        Track.INVESTMENT,
        "Признаки обещания дохода или быстрого возврата",
        1,
    ),
    "investment.recruitment": (Track.INVESTMENT, "Призыв привлекать новых участников", 1),
    "investment.urgency": (Track.INVESTMENT, "Давление сроком или числом мест", 1),
    "investment.payment_evidence": (Track.INVESTMENT, "Упоминание выплат или личного перевода", 1),
    "investment.promise_recruitment": (
        Track.INVESTMENT,
        "Обещание дохода и привлечение участников рядом",
        1,
    ),
    "investment.risk_disclaimer": (
        Track.INVESTMENT,
        "Явное предупреждение об отсутствии гарантий",
        -1,
    ),
    "investment.reporting_context": (Track.INVESTMENT, "Контекст новости или учебного разбора", -1),
}
