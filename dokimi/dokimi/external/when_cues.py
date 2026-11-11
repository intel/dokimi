# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The "when" clause cues of a skill description, per language (``description-no-when`` in
:mod:`dokimi.external.validate`). Kept apart from the validator so that module stays under the
file-length cap; ``validate`` imports :func:`when_clause_found` from here.
"""

from __future__ import annotations

import re
from typing import Optional

# A "when" clause or use phrase per language: the model chooses a skill from its description alone.
# Latin cues take word boundaries, CJK cues are substrings; bare 使用 (use) or 用于 (for) are too broad.
WHEN_CUES = {
    "en": r"\b(?:when\s+\w|use for\b|use this skill\b|trigger(?:s|ed)?\s*(?:on\b|:)|invoke only\b|activate\b|"
          r"invoke this\b|use (?:it |this |these )?(?:when|if|before|after|whenever|proactively|anytime)\b|"
          r"whenever\b)",
    "es": r"(?<!vez en )\bcuando\b|\bcuándo\b|\bsiempre que\b|\bcada vez que\b|\ben caso de\b|\bal (?:usar|utilizar|invocar|ejecutar)\b",
    "fr": r"\bquand\b(?!\s+même)|\blorsqu\w*|\bdès que\b|\bchaque fois que\b|\ben cas de\b|\blors (?:de|d')",
    "de": r"\bwenn\b|\bbeim\b|\bsobald\b|\bimmer dann\b",
    "pt": r"(?<!vez em )\bquando\b|\bsempre que\b|\bcada vez que\b|\bem caso de\b|\bao (?:usar|utilizar|invocar|executar)\b",
    "ru": r"\bкогда\b(?![-‑])|\bкогато\b|\bколи\b|\bвсякий раз\b|\bкаждый раз\b|"
          r"\bпри (?:работе|использовании|необходимости|запросе|наличии|вызове|запуске|получении)\b",
    "zh": r"(?<!任)何时|(?<!任)何時|什么时候|什麼時候|的时候|的時候|(?<![相适適恰应應妥得充担擔])[当當](?![前然即作做成地天日晚年初今中])[^。；！？;!?]{1,80}?(?<![实實同及定按准])[时時](?![间間区區])|(?<![实實同隨随及定按即平暂暫不临臨有适適小准準])[时時][，,]?(?:请|請)?(?:使用|调用|調用|触发|觸發|启用|啟用|激活|运行|運行|执行|執行)|"
          r"适用于[^。]{0,20}?(?:场景|场合|情况|时(?![间間段区區]))|適用於[^。]{0,20}?(?:場景|場合|情況|時(?![間间段區区]))|(?:适用|適用|使用)(?:场景|場景)[:：]|使用时机|使用時機|触发条件|觸發條件|用户(?:说(?![明的])|提到(?!的)|要求(?!的)|请求(?!的|数据|时间|参数|内容))",
    "ja": r"(?<!ひ)(?:とき(?!どき|おり|たま|には)|(?<=[^\s、。，,.!?])ときには)|(?<![同当一平瞬即随適臨戦日])(?<!(?<![一-鿿])定)(?<!(?<![一-鿿])常)(?:時に(?!は)|(?<!ある)時は|(?<=[^\s、。，,.!?])時には)|(?<!ほとんどの)(?<!多くの)(?<!大半の)場合(?!によ|分け)|(?:場面|シーン)で(?:使う|起動|発動)|使(?:う|用)タイミング|トリガー[:：]|(?:など|」|』|）)で(?:起動|発動)|"
          r"(?<![実国交])際(?:に|は)|いつ(?!まで|も|でも|か|の間)",
    "ko": r"(?:(?<=할 )|(?<=하는 )|(?<=한 )|(?<=될 )|(?<=되는 )|(?<=된 )|(?<=받을 )|(?<=받는 )|(?<=받은 )|(?<=인 )|(?<=일 )|(?<=있는 )|(?<=있을 )|(?<=없는 )|(?<=없을 ))경우"
          r"|(?<![그한])때(?![문로때])|언제(?!나)|상황에서|상황이면|트리거(?:\s*(?:키워드|조건|문구|단어))?\s*[:：]|트리거(?:가|는)?\s*(?:되면|될 때|되었을 때)|사용 시점|사용 시기|(?<=[가-힣]) 시(?=[\s에,.:]|$)",
}
WHEN_RE = re.compile("|".join(WHEN_CUES.values()), re.I)
# Letters in a script some cue list covers (Latin with accents, Cyrillic, kana, Han, Hangul), and all letters.
_COVERED_LETTER_RE = re.compile(r"[A-Za-zÀ-ɏḀ-ỿЀ-ӿ぀-ヿ㐀-䶿一-鿿가-힯]")
_LETTER_RE = re.compile(r"[^\W\d_]")


def when_clause_found(description: str) -> Optional[bool]:
    """Whether the description has a "when" cue; None when most of its letters are in a script no cue list covers."""
    text = " ".join(description.split())
    if WHEN_RE.search(text):
        return True
    letters = len(_LETTER_RE.findall(text))
    covered = len(_COVERED_LETTER_RE.findall(text))
    return None if letters - covered > covered else False
