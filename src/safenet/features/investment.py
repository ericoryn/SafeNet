import re
from decimal import Decimal, InvalidOperation

from safenet.contracts import PreparedContent
from safenet.features.common import emit, find, near, reporting_context

GUARANTEE = r"гарантир\w*|фиксирован\w*|без риска|кепіл\w*|тәуекелсіз|бекітілген"
FINANCE = (
    r"доход\w*|прибыл\w*|удво\w*|выплат\w*|возврат\w*|заработ\w*|"
    r"табыс\w*|пайда\w*|екі ес\w*|қайтар\w*|төлем\w*"
)
QUICK_RETURN = (
    r"(?:вложи|отправь|внеси)[^.!?;]{0,100}(?:получи|забери)[^.!?;]{0,60}"
    r"(?:завтра|через день|за сутки|за день)|"
    r"(?:сал\w*|жібер\w*)[^.!?;]{0,100}(?:ертең|бір күннен соң)[^.!?;]{0,60}ал\w*"
)
RECRUITMENT = (
    r"пригласи\w*|приведи\w*|привлечени\w*|привед[её]нн\w*|бонус\w*\s+за\s+приглаш\w*|"
    r"дос\w*\s+шақыр\w*|адам\w*\s+әкел\w*|қатысушы\w*\s+әкел\w*|шақыру\w*\s+бонус\w*"
)
URGENCY = r"успей\w*|осталось\s+\d+\s+мест\w*|только\s+\d+\s+мест\w*|до вечера|закрываем набор|"
URGENCY += r"үлгер\w*|\d+\s+орын\s+қал\w*|тек\s+\d+\s+орын|қабылдау аяқтал\w*|кешке дейін"
PAYMENT = r"выплат\w*|перевод\w*|личн\w* карт\w*|жеке\w*\s+карт\w*|аудар\w*|төлем\w*"
DISCLAIMER = r"доход\w*\s+не гарант\w*|гаранти\w*\s+нет|не обеща\w*|табыс\w*\s+кепіл\w*\s+емес|"
DISCLAIMER += r"кепіл\w*\s+берілмей\w*|не переводите|ақша\s+жібермеңіз|уәде\w*\s+емес"

AMOUNT = r"\d+(?:[ \u00a0]\d{3})*(?:[,.]\d{1,2})?"
RETURN = (
    rf"(?:вложи\w*|отправь|внеси\w*)\s+{AMOUNT}[^.!?;\n]{{0,35}}"
    rf"(?:получи\w*|забери\w*)\s+{AMOUNT}|"
    rf"{AMOUNT}\s*(?:тг|теңге)?\s*(?:сал\w*|жібер\w*)[^.!?;\n]{{0,35}}"
    rf"{AMOUNT}\s*(?:тг|теңге)?\s*ал\w*"
)
TESTIMONIAL = r"отзыв\w*|я\s+получил\w*|уже\s+заработал\w*|пікір\w*|ақшамды\s+алдым"
ROLES = r"куратор\w*|организатор\w*|ұйымдастырушы\w*|жетекші\w*"


def numeric_returns(prepared: PreparedContent):
    """Compare explicit offered amounts, excluding negation and quoted reporting."""
    found = []
    for occurrence in find(prepared, RETURN):
        amounts = re.findall(AMOUNT, occurrence.evidence.quote)
        if len(amounts) != 2:
            continue
        try:
            deposit, payout = (Decimal(re.sub(r"\s", "", x).replace(",", ".")) for x in amounts)
        except InvalidOperation:
            continue
        # shortcut: only explicit returns of at least 50%; annualized models need time metadata.
        if deposit > 0 and payout / deposit >= Decimal("1.5"):
            found.append(occurrence)
    return tuple(found)


def extract(prepared: PreparedContent):
    guarantees = find(prepared, GUARANTEE)
    finance = find(prepared, FINANCE)
    promises = (
        near(guarantees, finance) or find(prepared, QUICK_RETURN) or numeric_returns(prepared)
    )
    recruitment = find(prepared, RECRUITMENT)
    urgency = find(prepared, URGENCY)
    payment = find(prepared, PAYMENT)
    if promises:
        payment = payment or near(promises, find(prepared, TESTIMONIAL))[1:]
        recruitment = recruitment or near(promises, find(prepared, ROLES))[1:]
    bundle = near(promises, recruitment)
    output = []
    if promises:
        output.append(emit("investment.profit_promise", promises[:1], promises[1:]))
    for feature_id, values in (
        ("investment.recruitment", recruitment),
        ("investment.urgency", urgency),
        ("investment.payment_evidence", payment),
    ):
        if values:
            output.append(emit(feature_id, values))
    if bundle:
        output.append(emit("investment.promise_recruitment", bundle[:1], bundle[1:]))
    disclaimer = find(prepared, DISCLAIMER)
    if disclaimer and not promises:
        output.append(emit("investment.risk_disclaimer", disclaimer))
    context = reporting_context(prepared)
    if context and not promises:
        output.append(emit("investment.reporting_context", context))
    return tuple(output)
