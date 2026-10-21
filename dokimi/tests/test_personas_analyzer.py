# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for PersonaAnalyzer."""

import sys
import types
import pytest
from unittest.mock import Mock, patch
import json

# Provide a mock 'github' module if PyGithub is not installed
if 'github' not in sys.modules:
    mock_github_module = types.ModuleType('github')
    mock_github_module.Github = Mock
    mock_github_module.GithubException = Exception
    sys.modules['github'] = mock_github_module

from dokimi.personas.analyzer import PersonaAnalyzer
from dokimi.process import FakeRunner


@pytest.fixture
def mock_github_repo():
    """Create a mock GitHub repository."""
    repo = Mock()
    repo.description = "Parallel programming library for C++"
    repo.get_topics.return_value = ["parallel-computing", "cpp", "threading"]
    
    # Mock README
    readme = Mock()
    readme.decoded_content = b"""# oneTBB
    
Threading Building Blocks for parallel programming.

## Use Cases
- High-performance computing
- Data analytics pipelines
- Game engine parallelization

## Getting Started
Use `tbb::parallel_for` for simple parallelism.
"""
    repo.get_readme.return_value = readme
    
    return repo


@pytest.fixture
def mock_github_issues():
    """Create mock GitHub issues."""
    issues = []
    
    # Issue 1: Question about parallel_for
    issue1 = Mock()
    issue1.title = "How to use parallel_for with custom iterators?"
    issue1.html_url = "https://github.com/uxlfoundation/oneTBB/issues/1"
    issue1.labels = [Mock(name="question")]
    issues.append(issue1)
    
    # Issue 2: Help wanted
    issue2 = Mock()
    issue2.title = "Performance issue with task arena"
    issue2.html_url = "https://github.com/uxlfoundation/oneTBB/issues/2"
    issue2.labels = [Mock(name="help wanted"), Mock(name="performance")]
    issues.append(issue2)
    
    # Issue 3: Documentation request
    issue3 = Mock()
    issue3.title = "Missing docs for flow graph"
    issue3.html_url = "https://github.com/uxlfoundation/oneTBB/issues/3"
    issue3.labels = [Mock(name="documentation")]
    issues.append(issue3)
    
    return issues


class TestPersonaAnalyzer:
    """Test suite for PersonaAnalyzer."""
    
    def test_init_no_github_token(self):
        """Test initialization without GitHub token."""
        analyzer = PersonaAnalyzer()
        assert analyzer.github_token is None
        assert analyzer._github_client is None
    
    def test_github_unavailable(self):
        """Test behavior when PyGithub is not available."""
        with patch('dokimi.personas.analyzer.GITHUB_AVAILABLE', False):
            analyzer = PersonaAnalyzer()
            assert analyzer.github_client is None
    
    def test_github_client_lazy_load(self):
        """Test lazy loading of GitHub client."""
        mock_github_cls = Mock()
        with patch('dokimi.personas.analyzer.Github', mock_github_cls, create=True), \
             patch('dokimi.personas.analyzer.GITHUB_AVAILABLE', True):
            analyzer = PersonaAnalyzer(github_token="test_token")
            client = analyzer.github_client
            mock_github_cls.assert_called_once_with("test_token")
            assert client is not None
    
    def test_analyze_repository_success(self, mock_github_repo, mock_github_issues):
        """Test successful repository analysis."""
        mock_github_cls = Mock()
        mock_github_cls.return_value.get_repo.return_value = mock_github_repo
        mock_github_repo.get_issues.return_value = mock_github_issues
        
        with patch('dokimi.personas.analyzer.Github', mock_github_cls, create=True), \
             patch('dokimi.personas.analyzer.GITHUB_AVAILABLE', True):
            analyzer = PersonaAnalyzer(github_token="test")
            result = analyzer.analyze_repository("uxlfoundation/oneTBB")
        
        # Verify structure
        assert "readme_content" in result
        assert "use_cases" in result
        assert "issues_analysis" in result
        assert "api_patterns" in result
        assert "description" in result
        assert "topics" in result
        
        # Verify content
        assert "Threading Building Blocks" in result["readme_content"]
        assert result["description"] == "Parallel programming library for C++"
        assert "parallel-computing" in result["topics"]
    
    def test_get_readme_success(self, mock_github_repo):
        """Test README extraction."""
        analyzer = PersonaAnalyzer()
        readme = analyzer._get_readme(mock_github_repo)
        
        assert "oneTBB" in readme
        assert "Use Cases" in readme
    
    def test_get_readme_failure(self):
        """Test README extraction when file is missing."""
        repo = Mock()
        repo.get_readme.side_effect = Exception("Not found")
        
        analyzer = PersonaAnalyzer()
        readme = analyzer._get_readme(repo)
        
        assert readme == ""

    def test_get_readme_lets_keyboard_interrupt_through(self):
        """Ctrl-C while fetching the README stops the run, not just the README."""
        repo = Mock()
        repo.get_readme.side_effect = KeyboardInterrupt

        analyzer = PersonaAnalyzer()
        with pytest.raises(KeyboardInterrupt):
            analyzer._get_readme(repo)

    def test_extract_use_cases(self, mock_github_repo):
        """Test use case extraction from README."""
        analyzer = PersonaAnalyzer()
        analyzer._get_readme = Mock(return_value=mock_github_repo.get_readme().decoded_content.decode())
        
        use_cases = analyzer._extract_use_cases(mock_github_repo)
        
        assert len(use_cases) > 0
        assert any("computing" in uc.lower() for uc in use_cases)
    
    def test_extract_use_cases_empty_readme(self):
        """Test use case extraction with empty README."""
        repo = Mock()
        analyzer = PersonaAnalyzer()
        analyzer._get_readme = Mock(return_value="")
        
        use_cases = analyzer._extract_use_cases(repo)
        assert use_cases == []
    
    def test_analyze_issues(self, mock_github_repo, mock_github_issues):
        """Test issue analysis."""
        mock_github_repo.get_issues.return_value = mock_github_issues
        
        analyzer = PersonaAnalyzer()
        result = analyzer._analyze_issues(mock_github_repo, limit=10)
        
        assert "common_questions" in result
        assert "common_labels" in result
        assert "sample_issues" in result
        
        # Check questions extracted
        questions = result["common_questions"]
        assert len(questions) > 0
        assert any("parallel_for" in q for q in questions)
        
        # Check labels (common_labels is a list of label name strings)
        labels = result["common_labels"]
        assert len(labels) > 0
        
        # Check sample issues
        samples = result["sample_issues"]
        assert len(samples) > 0
        assert "title" in samples[0]
        assert "url" in samples[0]
    
    def test_analyze_issues_error_handling(self, mock_github_repo):
        """Test issue analysis error handling."""
        mock_github_repo.get_issues.side_effect = Exception("API error")
        
        analyzer = PersonaAnalyzer()
        result = analyzer._analyze_issues(mock_github_repo)
        
        # Should return empty structure on error
        assert result["common_questions"] == []
        assert result["common_labels"] == []
        assert result["sample_issues"] == []

    def test_analyze_issues_lets_keyboard_interrupt_through(self, mock_github_repo):
        """Ctrl-C while fetching issues stops the run, not just that label."""
        mock_github_repo.get_issues.side_effect = KeyboardInterrupt

        analyzer = PersonaAnalyzer()
        with pytest.raises(KeyboardInterrupt):
            analyzer._analyze_issues(mock_github_repo)

    def test_extract_api_patterns(self, mock_github_repo):
        """Test API pattern extraction."""
        analyzer = PersonaAnalyzer()
        analyzer._get_readme = Mock(return_value="""
```cpp
tbb::parallel_for(range, [](int i) {
    // do work
});

tbb::task_arena arena;
arena.initialize();
```
""")

        patterns = analyzer._extract_api_patterns(mock_github_repo)

        # Code-block lines with :: or . are kept; the comment and the closing brace are not.
        assert patterns == [
            "tbb::parallel_for(range, [](int i) {",
            "tbb::task_arena arena;",
            "arena.initialize();",
        ]
    
    def test_empty_analysis(self):
        """Test empty analysis structure."""
        analyzer = PersonaAnalyzer()
        result = analyzer._empty_analysis()
        
        assert result["readme_content"] == ""
        assert result["use_cases"] == []
        assert result["issues_analysis"]["common_questions"] == []
        assert result["api_patterns"] == []
    
    def test_save_analysis(self, tmp_path):
        """Test saving analysis to file."""
        analyzer = PersonaAnalyzer()
        analysis = {
            "readme_content": "Test content",
            "use_cases": ["Use case 1"],
            "issues_analysis": {"common_questions": ["Q1"]},
            "api_patterns": ["pattern1"],
            "description": "Test library",
            "topics": ["topic1"]
        }
        
        output_path = tmp_path / "analysis.json"
        analyzer.save_analysis(analysis, output_path)
        
        assert output_path.exists()
        
        loaded = json.loads(output_path.read_text())
        assert loaded["readme_content"] == "Test content"
        assert loaded["use_cases"] == ["Use case 1"]
    
    def test_analyze_repository_github_unavailable(self):
        """Test analysis when GitHub client is unavailable."""
        def no_gh(call):
            raise FileNotFoundError(call.argv[0])

        with patch('dokimi.personas.analyzer.GITHUB_AVAILABLE', False):
            analyzer = PersonaAnalyzer(runner=FakeRunner(no_gh))
            result = analyzer.analyze_repository("uxlfoundation/oneTBB")
            # Should return empty analysis
            assert result == analyzer._empty_analysis()

    def test_analyze_repository_via_gh_cli(self, monkeypatch):
        """Without PyGithub, gh gives the description and the README, with the caller's token."""
        from dokimi.process import reply

        monkeypatch.setenv("GH_TOKEN", "t")
        readme = "# lib\n## Use cases\n- Batch jobs\n```\nlib::run()\n```\n"
        fake = FakeRunner(lambda call: reply(json.dumps({"description": "A lib"}) if "--json" in call.argv
                                             else readme))
        with patch('dokimi.personas.analyzer.GITHUB_AVAILABLE', False):
            result = PersonaAnalyzer(runner=fake).analyze_repository("o/lib")
        assert result["description"] == "A lib" and result["readme_content"] == readme
        assert result["use_cases"] == ["batch jobs"] and result["api_patterns"] == ["lib::run()"]
        assert [c.argv[:3] for c in fake.calls] == [("gh", "repo", "view")] * 2
        assert all(c.env.get("GH_TOKEN") == "t" for c in fake.calls)

def test_create_minimal_analysis():
    """Test creating synthetic analysis without GitHub repo."""
    analysis = PersonaAnalyzer.create_minimal_analysis(
        library_name="oneMKL",
        description="Math Kernel Library for high performance computing"
    )
    assert analysis["library_name"] == "oneMKL"
    assert analysis["description"] == "Math Kernel Library for high performance computing"
    assert analysis["readme_content"] == "Math Kernel Library for high performance computing"
    assert analysis["use_cases"] == []
    assert analysis["issues_analysis"] == {"common_questions": [], "common_labels": [], "sample_issues": []}
    assert analysis["api_patterns"] == []
    assert analysis["topics"] == []
