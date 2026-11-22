# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The "when" clause cues of a skill description, per language (``description-no-when`` in
:mod:`dokimi.external.validate`). Kept apart from the validator so that module stays under the
file-length cap; ``validate`` imports :func:`when_clause_found` from here.
"""

from __future__ import annotations

import re
from typing import Optional

# German `beim` is a cue only in a sentence with a use verb; sentences end at `.;!?` (not `3.14`, not
# `z. B.`), and plain searches per sentence keep a long description linear.
_DE_ABBREVIATION_RE = re.compile(
    r"\b(?:z\.\s?[bt]|d\.\s?h|u\.\s?[aä]|u\.\s?U|bzw|ca|usw|etc|vgl|evtl|ggf|ggfs|inkl|bspw|nr|abs|art|kap|tel|bd|hrsg|zzgl|exkl|mio|mrd)\.", re.I
)
_DE_SENTENCE_END_RE = re.compile(r"[;!?]+|\.+(?!\w)")
_DE_CLAUSE_OPENERS = ":：-–—•*>\"'„“”‚‘’([«»"
_DE_BEIM_RE = re.compile(r"\bbeim\b", re.I)
_DE_USE_RE = re.compile(r"(?<!beim )\b(?:verwend(?:e|en|est|et)|(?:be)?nutz(?:e|en|est|et|t)|einsetzen|aufrufen)\b", re.I)
_DE_USE_PARTICLE_RES = (
    (re.compile(r"\bruf(?:e|en|t)?\b", re.I), re.compile(r"\bauf\b", re.I)),
    (re.compile(r"\bsetz(?:e|en|t)?\b", re.I), re.compile(r"\bein\b", re.I)),
)

# A "when" clause or use phrase per language: the model chooses a skill from its description alone.
# Latin cues take word boundaries, CJK cues are substrings; bare 使用 (use) or 用于 (for) are too broad.
WHEN_CUES = {
    "en": r"\b(?:when\s+\w|use for\b|use (?:this skill )?to\s+\w|use this skill\b|trigger(?:s|ed)?\s*(?:on\b|:)|invoke only\b|activate\b|"
          r"invoke this\b|use (?:it |this |these )?(?:when|if|before|after|whenever|proactively|anytime)\b|"
          r"whenever\b)",
    "es": r"(?<!vez en )\bcuando\b|\bcuándo\b|\bsiempre que\b|\bcada vez que\b|\ben caso de\b|\bal (?:usar|utilizar|invocar|ejecutar)\b",
    "fr": r"\bquand\b(?!\s+même)|\blorsqu\w*|\bdès que\b|\bchaque fois que\b|\ben cas de\b|\blors (?:de|d')",
    "de": r"\bwenn\b|\bsobald\b|\bimmer dann\b",
    "pt": r"(?<!vez em )\bquando\b|\bsempre que\b|\bcada vez que\b|\bem caso de\b|\bao (?:usar|utilizar|invocar|executar)\b",
    "ru": r"\bкогда\b(?![-‑])|\bкогато\b|\bколи\b|\bвсякий раз\b|\bкаждый раз\b|"
          r"\bпри (?:работе|использовании|необходимости|запросе|наличии|вызове|запуске|получении)\b",
    "zh": r"(?<!任)何时|(?<!任)何時|什么时候|什麼時候|的时候|的時候|(?<![相适適恰应應妥得充担擔])[当當](?![前然即作做成地天日晚年初今中])(?:(?!事人)[^。；！？;!?]{1,80}?(?<![实實同隨随及定按即暂暫不准準])(?<!(?<![水])平)(?<!(?<![没沒])有)(?<!(?<![合来來])[适適临臨])(?<!(?<![较較很过過太更最])小)[时時](?![间間区區])|事人[^。；！？;!?]{0,77}?(?<![实實同隨随及定按即暂暫不准準耗延])(?<!(?<![使应應])用)(?<!(?<![水])平)(?<!(?<![没沒])有)(?<!(?<![合来來])[适適临臨])(?<!(?<![较較很过過太更最])小)(?:(?<!超)[时時]|(?<=超)[时時](?=[后後]))(?![间間区區]))|(?<![实實同隨随及定按即暂暫不准準耗超延])(?<!(?<![水])平)(?<!(?<![没沒])有)(?<!(?<![合来來])[适適临臨])(?<!(?<![较較很过過太更最])小)[时時][，,]?(?:请|請)?(?:使用|调用|調用|触发|觸發|启用|啟用|激活|运行|運行|执行|執行)|[耗超延][时時](?:处理|處理)?(?:[，,]\s*(?:请|請)?|[请請])(?:使用|调用|調用|触发|觸發|启用|啟用|激活|运行|運行|执行|執行)|"
          r"适用于[^。]{0,20}?(?:场景|场合|情况|(?<![实實同隨随及定按即暂暫不准準耗超延])(?<!(?<![水])平)(?<!(?<![没沒])有)(?<!(?<![合来來])[适適临臨])(?<!(?<![较較很过過太更最])小)时(?![间間段区區序代]))|適用於[^。]{0,20}?(?:場景|場合|情況|(?<![實実同隨随及定按即暫暂不準准耗超延])(?<!(?<![水])平)(?<!(?<![没沒])有)(?<!(?<![合来來])[适適临臨])(?<!(?<![较較很过過太更最])小)時(?![間间段區区序代]))|(?:适用|適用|使用)(?:场景|場景)[:：]|使用时机|使用時機|触发条件|觸發條件|用[户戶](?:[说說](?![明的])|提到(?!的)|要求(?!的)|[请請]求(?!的|数据|數據|时间|時間|参数|參數|内容|內容|日志|日誌|头|頭|量))",
    "ja": r"(?<!ひ)(?:とき(?!どき|おり|たま|には)|(?<=[^\s、。，,.!?はもがでにをとやか])ときには)|(?<![同当一平瞬即随適臨戦日])(?<!(?<![一-鿿])定)(?<!(?<![一-鿿])常)(?:時に(?!は)|(?<!ある)時は|(?<=[^\s、。，,.!?はもがでにをとやか])時には)|(?<!ほとんどの)(?<!多くの)(?<!大半の)場合(?!によ|分け|の数)|(?:場面|シーン)で(?:使う|起動|発動)|使(?:う|用)タイミング|トリガー[:：]|(?:など|」|』|）)で(?:起動|発動)|"
          r"(?<![実国交])際(?:に|は)|(?<![こそあど思追食つ])いつ(?!まで|も|でも|か|の間)(?=[^。！？!?]*?(?:使[うい用えわっ]|呼び出|起動|発動|実行|参照|適用|利用|用い))",
    "ko": r"(?:(?<=할 )|(?<=하는 )|(?<=한 )|(?<=될 )|(?<=되는 )|(?<=된 )|(?<=받을 )|(?<=받는 )|(?<=받은 )|(?<=인 )|(?<=일 )|(?<=있는 )|(?<=있을 )|(?<=없는 )|(?<=없을 ))경우"
          r"|(?<![그한이])(?<!그 )(?<!이 )때(?![문로때])|언제(?!나)|상황에서|상황이면|트리거(?:\s*(?:키워드|조건|문구|단어))?\s*[:：]|트리거(?:가|는)?\s*(?:되면|될 때|되었을 때)|사용 시점|사용 시기|(?<=[가-힣])(?<!(?<![가-힣])매)(?<!현대)(?<!고전)(?<!서울)(?<!부산)(?<!대구)(?<!인천)(?<!광주)(?<!대전)(?<!울산)(?<!세종) 시(?=[\s에,.:]|$)",
}
WHEN_RE = re.compile("|".join(WHEN_CUES.values()), re.I)
# Letters in a script some cue list covers (Latin with accents, Cyrillic, kana, Han, Hangul), and all letters.
_COVERED_LETTER_RE = re.compile(r"[A-Za-zÀ-ɏḀ-ỿЀ-ӿ぀-ヿ㐀-䶿一-鿿가-힯]")
_LETTER_RE = re.compile(r"[^\W\d_]")


def _char_before(text: str, pos: int) -> str:
    """The last non-space character of ``text[:pos]``, found without copying the prefix; empty at the start."""
    while pos and text[pos - 1].isspace():
        pos -= 1
    return text[pos - 1] if pos else ""


def _de_verb_in(pattern: "re.Pattern[str]", sentence: str) -> bool:
    """Whether `pattern` matches a verb: lowercase, or capitalised at the sentence start or after a label, bullet or
    opening quote (a capital elsewhere is a noun: `Ruf`)."""
    for m in pattern.finditer(sentence):
        before = _char_before(sentence, m.start())
        if m.group()[0].islower() or not before or before in _DE_CLAUSE_OPENERS:
            return True
    return False


def _de_beim_found(text: str) -> bool:
    """Whether a sentence holds `beim` and a use verb (`ruf … auf` and `setz … ein` count, apart or not)."""
    text = _DE_ABBREVIATION_RE.sub(lambda m: re.sub(r"[.\s]", "", m.group()), text)
    for sentence in _DE_SENTENCE_END_RE.split(text):
        if not _DE_BEIM_RE.search(sentence):
            continue
        if _de_verb_in(_DE_USE_RE, sentence) or any(
            _de_verb_in(v, sentence) and p.search(sentence) for v, p in _DE_USE_PARTICLE_RES
        ):
            return True
    return False


def when_clause_found(description: str) -> Optional[bool]:
    """Whether the description has a "when" cue; None when most of its letters are in a script no cue list covers."""
    text = " ".join(description.split())
    if WHEN_RE.search(text) or _de_beim_found(text):
        return True
    letters = len(_LETTER_RE.findall(text))
    covered = len(_COVERED_LETTER_RE.findall(text))
    return None if letters - covered > covered else False
