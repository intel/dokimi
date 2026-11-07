# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``description-no-when`` check reads the "when" cue of each language, not English only (A10)."""

from __future__ import annotations

import pytest

from dokimi.external import read_skill_manifest, review_skills
from dokimi.external.validate import when_clause_found

# Real-looking descriptions: a native "when" clause, the way a skill author writes it in that language.
HAS_WHEN = [
    pytest.param("生成发布说明。当用户要求写 release notes 时使用。", id="zh-dang-shi"),
    pytest.param("提交代码前执行检查。何时使用：准备 PR 时。", id="zh-heshi"),
    pytest.param("提交代码前执行检查。使用场景：准备 PR 时。", id="zh-shiyong-changjing-colon"),
    pytest.param("Използвай, когато има релийз.", id="bg-kogato"),
    pytest.param("Verwende dies beim Erstellen eines Release.", id="de-beim"),
    pytest.param("用户提到某公司财报分析。", id="zh-yonghu-tidao"),
    pytest.param("审查 Python 代码的安全问题，适用于代码审查场景。", id="zh-shiyong-yu"),
    pytest.param("帮助撰写技术文档，在需要编写 README 的时候调用。", id="zh-de-shihou"),
    pytest.param("数据库迁移助手。用户说要迁移数据库时触发。", id="zh-shi-chufa"),
    pytest.param("リリースノートを作成します。新しいタグを作る時に使用してください。", id="ja-toki-ni"),
    pytest.param("CI の失敗を分析します。ビルドが落ちた場合に使う。", id="ja-baai"),
    pytest.param("Dockerfile を最適化します。ビルドが遅いとき。", id="ja-toki"),
    pytest.param("ビルド時は警告します。", id="ja-build-toki-wa"),
    pytest.param("ある時に使う。Git 履歴を整理するツールです。", id="ja-aru-toki-ni"),
    pytest.param("エラーが出た時は再実行してください。", id="ja-deta-toki-wa"),
    pytest.param("코드 리뷰를 수행합니다. PR 리뷰를 요청받을 경우 사용하세요.", id="ko-gyeongu"),
    pytest.param("배포 스크립트를 작성합니다. 릴리스가 필요할 때 사용합니다.", id="ko-ttae"),
    pytest.param("배포를 수행합니다. 실패할 경우 롤백을 요청하세요.", id="ko-gyeongu-hal-gyeongu"),
    pytest.param("GAS の一覧を取得する。「GAS 一覧」「スクリプト一覧」などで起動。", id="ja-de-kidou"),
    pytest.param("Genera notas de versión. Úsalo cuando se publique una versión.", id="es-cuando"),
    pytest.param("Génère des notes de version. À utiliser quand une release est prête.", id="fr-quand"),
    pytest.param("Erstellt Release-Notizen. Verwende dies, wenn ein Release ansteht.", id="de-wenn"),
    pytest.param("Gera notas de versão. Use quando uma versão for publicada.", id="pt-quando"),
    pytest.param("Пишет отчёты о релизе. Используйте, когда нужно подготовить релиз.", id="ru-kogda"),
]

# No "when" clause in the language's own words; also the wording a bare cue would wrongly accept.
NO_WHEN = [
    pytest.param("生成发布说明和变更日志，支持多种格式。", id="zh-plain"),
    pytest.param("使用这个工具可以生成图表和报告。", id="zh-shiyong-alone"),
    pytest.param("用于生成 API 文档的命令行工具。", id="zh-yongyu-alone"),
    pytest.param("当前项目的依赖分析工具。", id="zh-dangqian-not-when"),
    pytest.param("コードを整形するツールです。使用する言語は Python と Go です。", id="ja-plain"),
    pytest.param("実際にデータを変換します。", id="ja-jissai-not-sai"),
    pytest.param("任意のタイミングでの自動圧縮ではなく、手動で行う。", id="ja-taimingu-not-when"),
    pytest.param("ときどきエラーを直す補助ツールです。", id="ja-tokidoki-not-toki"),
    pytest.param("그때그때 빌드 결과를 정리합니다.", id="ko-geuttae-not-ttae"),
    pytest.param("当前项目支持按时间排序的依赖分析工具。", id="zh-dangqian-shijian-not-when"),
    pytest.param("Formats output and falls back to plain text on error.", id="en-falls-not-german-falls"),
    pytest.param("相当于一个代码格式化工具，支持实时预览。", id="zh-xiangdang-shishi-not-when"),
    pytest.param("用户说明生成的报告格式，并输出 JSON。", id="zh-yonghu-shuoming-not-when"),
    pytest.param("ビルドの場合によっては失敗するツールです。", id="ja-baai-niyotte-not-when"),
    pytest.param("いつか使えるように整理したライブラリです。", id="ja-itsuka-not-when"),
    pytest.param("모든 경우에 적용되는 린터입니다.", id="ko-modeun-gyeongu-not-when"),
    pytest.param("보고서를 생성하는 도구입니다. 때로는 오류를 출력합니다.", id="ko-ttaeroneun-not-when"),
    pytest.param("国際化対応の文字列を抽出します。", id="ja-kokusai-not-sai"),
    pytest.param("배포를 자동화하는 도구입니다. 파이프라인을 구성합니다.", id="ko-plain"),
    pytest.param("오류가 많기 때문에 도구를 만들었습니다.", id="ko-ttaemun-not-ttae"),
    pytest.param("많은 경우에 유용한 도구입니다.", id="ko-manheun-gyeongu-not-when"),
    pytest.param("该工具用于恰当地处理数据，并在执行时输出日志。", id="zh-qiadang-cross-clause-not-when"),
    pytest.param("适合各种场合的文档生成工具。", id="zh-changhe-not-when"),
    pytest.param("提供实时调用接口的数据服务工具。", id="zh-shishi-diaoyong-not-when"),
    pytest.param("该工具应当在部署时检查配置。", id="zh-yingdang-not-when"),
    pytest.param("用户要求的格式可配置。", id="zh-yonghu-yaoqiu-de-not-when"),
    pytest.param("支持同时使用多个模型的代码生成工具。", id="zh-tongshi-shiyong-not-when"),
    pytest.param("同时调用多个 API 的封装库。", id="zh-tongshi-diaoyong-not-when"),
    pytest.param("定时调用接口的监控工具。", id="zh-dingshi-diaoyong-not-when"),
    pytest.param("支持多种使用场景的格式工具。", id="zh-shiyong-changjing-noun-not-when"),
    pytest.param("요청 시스템을 구축합니다.", id="ko-yeongcheong-siseutem-not-when"),
    pytest.param("ブラウザで起動するビューアです。", id="ja-browser-kidou-not-when"),
    pytest.param("该工具在当天的备份时执行清理。", id="zh-dangtian-not-when"),
    pytest.param("用户说的格式可配置。", id="zh-yonghu-shuo-de-not-when"),
    pytest.param("解析用户请求数据并输出 JSON。", id="zh-yonghu-qingqiu-noun-not-when"),
    pytest.param("该工具用于临时使用的文档生成。", id="zh-linshi-shiyong-not-when"),
    pytest.param("有时调用此工具生成报告。", id="zh-youshi-diaoyong-not-when"),
    pytest.param("トリガーを設定して通知するツールです。", id="ja-torigaa-noun-not-when"),
    pytest.param("当今时代的数据分析工具。", id="zh-dangjin-not-when"),
    pytest.param("보고서를 만듭니다. 때때로 사용하세요.", id="ko-ttaettaero-not-when"),
    pytest.param("Génère des notes de version quand même.", id="fr-quand-meme-not-when"),
    pytest.param("用户提到的文件路径会被解析。", id="zh-yonghu-tidao-noun-not-when"),
    pytest.param("适用于 Python 项目的格式化工具。", id="zh-shiyong-yu-scope-not-when"),
    pytest.param("いつの間にか動く。", id="ja-itsunoma-not-when"),
    pytest.param("适用于所有时间段的工具。", id="zh-shiyong-yu-shijianduan-not-when"),
    pytest.param("用于处理用户请求时间序列数据的工具。", id="zh-yonghu-qingqiu-shijian-not-when"),
    pytest.param("任何时候都可以用这个工具。", id="zh-renhe-shihou-not-when"),
    pytest.param("适时使用本工具整理笔记。", id="zh-shi-shiyong-not-when"),
    pytest.param("ほとんどの場合、このツールは正常に動作します。", id="ja-hotondo-no-baai-not-when"),
    pytest.param("当作文档生成工具，支持按时输出。", id="zh-dangzuo-not-when"),
    pytest.param("様々なシーンで使える整形ツールです。", id="ja-scene-usable-not-when"),
    pytest.param("Genera informes de publicación a partir de registros.", id="es-plain"),
    pytest.param("Génère des notes de version à partir de l'historique.", id="fr-plain"),
    pytest.param("Erzeugt Release-Notizen aus dem Git-Verlauf.", id="de-plain"),
    pytest.param("Gera notas de versão a partir do histórico.", id="pt-plain"),
    pytest.param("Создаёт отчёты о релизах из логов.", id="ru-plain"),
    pytest.param("Выпускает сборки, которые когда-нибудь понадобятся.", id="ru-kogda-nibud-not-when"),
    pytest.param("いつまでも使えるツールです。", id="ja-itsumade-not-when"),
    pytest.param("交際相手の情報を整理するツールです。", id="ja-kousai-not-sai"),
    pytest.param("時には失敗するが、通常は動作します。", id="ja-tokini-wa-not-when"),
    pytest.param("ある時は動かないツールです。", id="ja-aru-toki-wa-not-when"),
    pytest.param("一時は失敗したが、今は動きます。", id="ja-ichiji-wa-not-when"),
    pytest.param("平時は通常通りに動作するツールです。", id="ja-heiji-wa-not-when"),
    pytest.param("一時に複数のファイルを処理します。", id="ja-ichiji-ni-not-when"),
    pytest.param("平時に動くが、負荷は小さいツールです。", id="ja-heiji-ni-not-when"),
    pytest.param("Genera informes de vez en cuando.", id="es-vez-en-cuando-not-when"),
    pytest.param("Gera relatórios de vez em quando.", id="pt-vez-em-quando-not-when"),
    pytest.param("充当代码格式化工具，并在执行时输出日志。", id="zh-chongdang-shi-not-when"),
]


@pytest.mark.parametrize("description", HAS_WHEN)
def test_a_native_when_cue_is_accepted(description):
    assert when_clause_found(description) is True


@pytest.mark.parametrize("description", NO_WHEN)
def test_a_description_without_a_when_cue_is_flagged(description):
    assert when_clause_found(description) is False


@pytest.mark.parametrize(
    "description",
    [
        pytest.param("يساعد في كتابة ملاحظات الإصدار من السجل.", id="arabic"),
        pytest.param("ช่วยเขียนบันทึกการเผยแพร่จากประวัติ", id="thai"),
        pytest.param("מסייע בכתיבת הערות שחרור מתוך היסטוריית הקוד.", id="hebrew"),
    ],
)
def test_a_script_no_cue_list_covers_is_not_checked(description):
    assert when_clause_found(description) is None


def test_an_english_when_clause_still_passes_in_a_cjk_description():
    assert when_clause_found("发布说明生成器。Use when a release is cut.") is True


def test_a_cjk_skill_without_a_cue_gets_the_warning_end_to_end(tmp_path):
    skill_dir = tmp_path / "skills" / "release"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: release\ndescription: 生成发布说明和变更日志，支持多种格式。\n---\n# Body\n",
        encoding="utf-8",
    )
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert [f.code for f in review.findings] == ["description-no-when"]


def test_a_cjk_skill_with_a_native_cue_gets_no_warning_end_to_end(tmp_path):
    skill_dir = tmp_path / "skills" / "release"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: release\ndescription: 生成发布说明。当用户要求写发布说明时使用。\n---\n# Body\n",
        encoding="utf-8",
    )
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert "description-no-when" not in [f.code for f in review.findings]
