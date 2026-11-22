from dokimi.repos.shell_search import searched_files


def test_rg_dash_L_follows_links_and_still_shows_lines():
    assert searched_files("rg", ["-L", "warn", "AGENTS.md"]) == ["AGENTS.md"]


def test_grep_dash_L_lists_files_without_a_match_and_shows_no_line():
    assert searched_files("grep", ["-L", "warn", "AGENTS.md"]) == []
