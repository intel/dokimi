# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Analyze project to gather signals for persona generation."""

import logging
from typing import Dict, List, Any, Optional
from pathlib import Path
import json

from dokimi import process

try:
    from github import Github, GithubException  # noqa: F401  (re-exported for callers)
    GITHUB_AVAILABLE = True
except ImportError:
    GITHUB_AVAILABLE = False

logger = logging.getLogger(__name__)


class PersonaAnalyzer:
    """
    Analyze a GitHub project to extract signals for persona discovery.
    
    Gathers information from:
    - README (use cases, getting started)
    - Documentation structure
    - GitHub issues (questions, problems)
    - Code examples
    """
    
    def __init__(self, github_token: Optional[str] = None, runner: process.Runner = process.run):
        """
        Initialize analyzer.
        
        Args:
            github_token: GitHub personal access token (optional, for higher rate limits)
            runner: spawns the ``gh`` CLI fallback (a fake one in tests)
        """
        self.github_token = github_token
        self.runner = runner
        self._github_client = None
        
        if not GITHUB_AVAILABLE:
            logger.debug("PyGithub not available. GitHub analysis will be limited.")
    
    @property
    def github_client(self):
        """Lazy-load GitHub client."""
        if self._github_client is None and GITHUB_AVAILABLE:
            if self.github_token:
                self._github_client = Github(self.github_token)
            else:
                self._github_client = Github()  # Anonymous (lower rate limit)
        return self._github_client
    
    def analyze_repository(self, repo_name: str) -> Dict[str, Any]:
        """
        Analyze a GitHub repository for persona signals.
        
        Args:
            repo_name: Repository in format "owner/repo"
            
        Returns:
            Dictionary with analysis results
        """
        # Try PyGithub first, fall back to gh CLI
        if GITHUB_AVAILABLE and self.github_client:
            return self._analyze_with_pygithub(repo_name)
        else:
            logger.info("PyGithub not available, using gh CLI fallback")
            return self._analyze_with_gh_cli(repo_name)

    @staticmethod
    def create_minimal_analysis(library_name: str, description: str) -> Dict[str, Any]:
        """
        Create a minimal analysis dict from a plain-text description.

        Use this when no GitHub repository is available.  The returned
        structure is compatible with ``PersonaGenerator.generate_personas()``.

        Args:
            library_name: Human-readable product name (e.g. "oneMKL").
            description:  Free-form description of the product, its purpose,
                          typical users, and key use-cases.

        Returns:
            Analysis dict with ``description`` populated and all other
            fields empty (the LLM will rely solely on the description text).
        """
        return {
            "library_name": library_name,
            "description": description,
            "readme_content": description,  # used as README summary fallback
            "use_cases": [],
            "issues_analysis": {
                "common_questions": [],
                "common_labels": [],
                "sample_issues": [],
            },
            "api_patterns": [],
            "topics": [],
        }

    def _analyze_with_pygithub(self, repo_name: str) -> Dict[str, Any]:
        try:
            logger.info(f"Analyzing repository: {repo_name}")
            repo = self.github_client.get_repo(repo_name)
            
            analysis = {
                "readme_content": self._get_readme(repo),
                "use_cases": self._extract_use_cases(repo),
                "issues_analysis": self._analyze_issues(repo),
                "api_patterns": self._extract_api_patterns(repo),
                "description": repo.description or "",
                "topics": repo.get_topics()
            }
            
            logger.info(f"Analysis complete: {len(analysis['use_cases'])} use cases, "
                       f"{len(analysis['issues_analysis']['sample_issues'])} sample issues")
            
            return analysis
            
        except Exception as e:
            logger.error(f"PyGithub analysis error: {e}, falling back to gh CLI")
            return self._analyze_with_gh_cli(repo_name)
    
    def _analyze_with_gh_cli(self, repo_name: str) -> Dict[str, Any]:
        """Analyze using gh CLI as fallback."""
        import json as json_lib
        
        try:
            logger.info(f"Analyzing repository via gh CLI: {repo_name}")
            
            # Get basic repo info (gh needs the caller's GH_TOKEN / config, so it inherits the env)
            result = self.runner(["gh", "repo", "view", repo_name, "--json", "description,languages"],
                                 timeout=30, env=process.Env.inherit())
            
            if not result.ok:
                logger.error(f"gh CLI error: {result.stderr}")
                return self._empty_analysis()
            
            repo_data = json_lib.loads(result.stdout)
            
            # Get README (first 2000 chars)
            readme_result = self.runner(["gh", "repo", "view", repo_name], timeout=30,
                                        env=process.Env.inherit())
            readme = readme_result.stdout if readme_result.ok else ""
            
            return {
                "readme_content": readme[:2000],
                "use_cases": self._extract_use_cases_from_text(readme),
                "issues_analysis": {
                    "common_questions": [],
                    "common_labels": [],
                    "sample_issues": []
                },
                "api_patterns": self._extract_api_patterns_from_text(readme),
                "description": repo_data.get("description", ""),
                "topics": []
            }
            
        except Exception as e:
            logger.error(f"gh CLI analysis failed: {e}")
            return self._empty_analysis()
    
    
    @staticmethod
    def _extract_use_cases_from_text(text: str) -> List[str]:
        """Extract use cases from text (fallback for gh CLI)."""
        if not text:
            return []
        
        use_cases = []
        lines = text.lower().split('\n')
        in_use_case_section = False
        
        for line in lines:
            if any(keyword in line for keyword in ['use case', 'application', 'when to use']):
                in_use_case_section = True
                continue
            
            if in_use_case_section:
                if line.startswith('#'):
                    in_use_case_section = False
                elif line.strip().startswith(('-', '*', '•')):
                    use_cases.append(line.strip().lstrip('-*• '))
        
        return use_cases[:10]
    
    @staticmethod
    def _extract_api_patterns_from_text(text: str) -> List[str]:
        """Extract API patterns from text (fallback for gh CLI)."""
        if not text:
            return []
        
        patterns = []
        in_code_block = False
        
        for line in text.split('\n'):
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                continue
            
            if in_code_block and ('::' in line or '.' in line):
                patterns.append(line.strip())
        
        return patterns[:20]
    
    def _get_readme(self, repo) -> str:
        """Extract README content."""
        try:
            readme = repo.get_readme()
            return readme.decoded_content.decode('utf-8')
        except Exception:
            return ""
    
    def _extract_use_cases(self, repo) -> List[str]:
        """
        Extract use cases from README and docs.
        
        Looks for sections like:
        - Use Cases
        - When to Use
        - Applications
        - Examples
        """
        use_cases = []
        readme = self._get_readme(repo)
        
        if not readme:
            return use_cases
        
        # Simple heuristic: look for list items after "use case" headers
        lines = readme.lower().split('\n')
        in_use_case_section = False
        
        for line in lines:
            if any(keyword in line for keyword in ['use case', 'application', 'when to use']):
                in_use_case_section = True
                continue
            
            if in_use_case_section:
                if line.startswith('#'):  # New section
                    in_use_case_section = False
                elif line.strip().startswith(('-', '*', '•')):
                    use_cases.append(line.strip().lstrip('-*• '))
        
        return use_cases[:10]  # Limit to top 10
    
    def _analyze_issues(self, repo, limit: int = 50) -> Dict[str, Any]:
        """
        Analyze recent issues for common question patterns.
        
        Focuses on:
        - Questions (label:question)
        - Help requests (label:help wanted, label:support)
        - Bug reports that reveal use cases
        """
        try:
            # Fetch issues with question-related labels
            question_labels = ['question', 'help wanted', 'support', 'documentation']
            issues_to_analyze = []
            
            for label in question_labels:
                try:
                    issues = repo.get_issues(state='all', labels=[label])
                    issues_to_analyze.extend(list(issues[:limit // len(question_labels)]))
                except Exception:
                    continue
            
            # Extract patterns
            common_questions = []
            sample_issues = []
            
            for issue in issues_to_analyze[:limit]:
                title = issue.title
                common_questions.append(title)
                
                sample_issues.append({
                    "title": title,
                    "labels": [label.name for label in issue.labels],
                    "url": issue.html_url
                })
            
            # Get common labels
            label_counts = {}
            for issue in issues_to_analyze:
                for label in issue.labels:
                    label_counts[label.name] = label_counts.get(label.name, 0) + 1
            
            common_labels = sorted(label_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            
            return {
                "common_questions": common_questions[:20],
                "common_labels": [label for label, _ in common_labels],
                "sample_issues": sample_issues[:10]
            }
            
        except Exception as e:
            logger.error(f"Issue analysis error: {e}")
            return {"common_questions": [], "common_labels": [], "sample_issues": []}
    
    def _extract_api_patterns(self, repo) -> List[str]:
        """
        Extract common API patterns from code examples.
        
        Looks for:
        - Frequently used class/function names
        - Import patterns
        - Code snippets in docs
        """
        # Simplified version: extract from README code blocks
        readme = self._get_readme(repo)
        if not readme:
            return []
        
        patterns = []
        in_code_block = False
        
        for line in readme.split('\n'):
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                continue
            
            if in_code_block:
                # Extract likely API calls (simplified heuristic)
                if '::' in line or '.' in line:
                    patterns.append(line.strip())
        
        return patterns[:20]
    
    def _empty_analysis(self) -> Dict[str, Any]:
        """Return empty analysis structure."""
        return {
            "readme_content": "",
            "use_cases": [],
            "issues_analysis": {
                "common_questions": [],
                "common_labels": [],
                "sample_issues": []
            },
            "api_patterns": [],
            "description": "",
            "topics": []
        }
    
    def save_analysis(self, analysis: Dict[str, Any], output_path: Path):
        """Save analysis to JSON file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding="utf-8") as f:
            json.dump(analysis, f, indent=2)
        logger.info(f"Saved analysis to {output_path}")
