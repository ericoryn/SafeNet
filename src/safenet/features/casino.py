from safenet.contracts import PreparedContent
from safenet.features.common import FeatureMatch, emit, find, near, reporting_context

GAMBLING = (
    r"казино|джекпот\w*|игр\w*\s+на\s+деньг\w*|ақшаға\s+ой\w*|ойын\s+слот\w*|"
    r"игров\w*\s+слот\w*|крут\w*[^.!?;]{0,20}слот\w*|"
    r"слот\w*[^.!?;]{0,20}(?:вращен\w*|джекпот\w*)|"
    r"игр\w*\s+на\s+денг\w*|игров\w*\s+автомат\w*|фриспин\w*|"
    r"kazino|dzhekpot\w*|igr\w*\s+na\s+deng\w*|"
    r"oyyn\w*\s+slot\w*|aqshagha\s+oy\w*"
)
PROMOTION = (
    r"переходи\w*|перейди\w*|жми\w*|регистр\w*|внос\w*\s+депозит\w*|"
    r"пополни\w*|активир\w*|промокод\w*|реферал\w*|партн[её]рск\w*\s+переход\w*|"
    r"сілтем\w*\s+өт\w*|сілтем\w*\s+бас\w*|тіркел\w*|депозит\w*\s+сал\w*|"
    r"толтыр\w*|жолдама\w*|серіктестік\w*\s+сілтем\w*|"
    r"пополн\w*|присоединя\w*|партн[её]р\w*|серіктес\w*|қосыл\w*|"
    r"perehodi\w*|perekhodi\w*|zhmi\w*|registr\w*|popoln\w*|aktivir\w*|"
    r"promokod\w*|referal\w*|partn[ey]or\w*|prisoediny\w*|"
    r"siltem\w*\s+(?:ot\w*|bas\w*)|tirkel\w*|toltyr\w*|zholdama\w*|"
    r"seriktes\w*|qosyl\w*"
)
PAYOUT = r"мой выигрыш|поднял\w*|скрин\w*\s+(?:вывод\w*|выплат\w*|джекпот\w*)|"
PAYOUT += r"ұтқан\w*|шығару сурет\w*|джекпот сурет\w*|төлем\w*\s+сурет\w*"
REFERRAL = (
    r"промокод\w*|реферал\w*|партн[её]р\w*|жолдама\w*|серіктес\w*|"
    r"promokod\w*|referal\w*|partn[ey]or\w*|zholdama\w*|seriktes\w*"
)
DENIAL = r"(?:игр\w*\s+на\s+деньг\w*|казино)[^.!?;]{0,30}(?:нет|не предлагают)|"
DENIAL += r"ақшаға\s+ой\w*[^.!?;]{0,25}(?:жоқ|ұсынылмайды)|ойнау[^.!?;]{0,30}ұсынылмайды"
FREE_PLAY = (
    r"без\s+(?:реальн\w*\s+)?денег|безденежн\w*|ақша\s+тікпей|ақша\s+салмай|"
    r"bez\s+(?:realn\w*\s+)?deneg|aqsha\s+tikpey|aqsha\s+salmay"
)
# Бесплатный вход или бонус без депозита сами по себе не исключают денежную игру.
PAID_OFFER = (
    r"пополн\w*|внес\w*|внос\w*|депозит\w*\s+сал\w*|толтыр\w*|"
    r"popoln\w*|vnes\w*|vnos\w*|deposit\w*\s+sal\w*|toltyr\w*"
)
SLOTS = r"\bслот\w*|\bslot\w*"
GAME_CONTEXT = (
    r"депозит\w*|ставк\w*|спин\w*|джекпот\w*|айналдыр\w*|"
    r"deposit\w*|stavk\w*|spin\w*|dzhekpot\w*|aynaldyr\w*"
)


def extract(prepared: PreparedContent):
    gambling = find(prepared, GAMBLING) or near(find(prepared, SLOTS), find(prepared, GAME_CONTEXT))
    promotion = find(prepared, PROMOTION)
    bundle = near(gambling, promotion)
    free_play = near(gambling, find(prepared, FREE_PLAY, asserted=False))
    if free_play and not near(gambling, find(prepared, PAID_OFFER)):
        bundle = ()
    output = []
    if gambling:
        output.append(emit("casino.gambling", gambling[:1], gambling[1:]))
    if bundle:
        output.append(emit("casino.promotion", bundle[1:]))
        output.append(emit("casino.gambling_promotion", bundle[:1], bundle[1:]))
    payout = find(prepared, PAYOUT)
    payout_pair = near(gambling, payout)
    if payout_pair:
        output.append(emit("casino.payout_claim", payout_pair[1:]))
    referral = find(prepared, REFERRAL)
    referral_pair = near(gambling, referral)
    if referral_pair:
        output.append(emit("casino.referral", referral_pair[1:]))
    elif bundle and len(set(prepared.redirect_domains)) > 1:
        output.append(FeatureMatch("casino.referral", (bundle[0].evidence, bundle[1].evidence)))
    denial = find(prepared, DENIAL)
    if (denial or free_play) and not bundle:
        output.append(emit("casino.no_gambling_offer", denial or free_play[1:]))
    context = reporting_context(prepared)
    if context and not bundle:
        output.append(emit("casino.reporting_context", context))
    return tuple(output)
