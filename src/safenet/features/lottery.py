import re

from safenet.contracts import PreparedContent
from safenet.features.common import emit, find, near, reporting_context

PAYMENT = (
    r"взнос\w*|жарна\w*|(?:оплат|плат)\w*[^.!?;]{0,35}(?:участ\w*|вход\w*|номер\w*|билет\w*)|"
    r"платн\w*\s+(?:участ\w*|список\w*|вход\w*)|ақылы|"
    r"(?:билет\w*|номер\w*|слот\w*|нөмір\w*|орын\w*)[^.!?;]{0,45}"
    r"(?:стоит|цен[аы]|по\s+\d|за\s+\d|тұрад\w*|теңге|тг)|"
    r"(?:купи\w*|купите|сатып ал\w*)[^.!?;]{0,30}(?:билет\w*|номер\w*|слот\w*|орын\w*)|"
    r"участ\w*(?![^.!?;]{0,30}\bбез\b)[^.!?;]{0,30}(?:оплат\w*|перевод\w*)"
)
# Tesseract can lose қ's descender; keep this alias local instead of folding Kazakh globally.
RANDOM = r"рандом\w*|колес\w*|жереб\w*|случайн\w*|дөңгелек\w*|кездейсо[қк]\w*|сандар генератор\w*"
INCOME = r"комисси\w*|сервис\w* сбор\w*|доля продаж|доля сбор\w*|часть сбор\w*|"
INCOME += r"остаток выруч\w*|ұйымдастырушыда қал\w*|ұйымдастырушыға түс\w*|алым\w*|комиссия\w*"
FREE = (
    r"бесплатн\w*\s+(?:участ\w*|конкурс\w*|розыгрыш\w*)|участ\w*\s+бесплатн\w*|"
    r"без оплат\w*|без взнос\w*|участ\w*\s+без\s+(?:оплат\w*|перевод\w*)|"
    r"тегін\s+(?:байқау\w*|қатыс\w*)|қатыс\w*\s+тегін|төлемсіз|жарна\s+керек емес"
)


def extract(prepared: PreparedContent):
    payments = tuple(
        item
        for item in find(prepared, PAYMENT)
        if not re.search(r"концерт|театр|автобус|поезд", item.clause)
    )
    random = find(prepared, RANDOM)
    income = find(prepared, INCOME)
    bundle = near(payments, random)
    output = []
    for feature_id, values in (
        ("lottery.payment", payments),
        ("lottery.random_selection", random),
        ("lottery.organizer_income", income),
    ):
        if values:
            output.append(emit(feature_id, values))
    if bundle:
        output.append(emit("lottery.paid_random", bundle[:1], bundle[1:]))
    free = find(prepared, FREE)
    if free and not payments:
        output.append(emit("lottery.free_participation", free))
    context = reporting_context(prepared)
    if context and not bundle:
        output.append(emit("lottery.reporting_context", context))
    return tuple(output)
