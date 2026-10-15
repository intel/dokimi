# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Generate persona proposals using LLM based on project analysis."""

import logging
from typing import Dict, Any, Optional
from pathlib import Path
import json

from dokimi.llm import ChatModel
from dokimi.llm import ChatOpenAI, ChatAnthropic, LANGCHAIN_AVAILABLE

logger = logging.getLogger(__name__)


PERSONA_GENERATION_PROMPT = """You are an expert at identifying user personas for technical libraries and frameworks.

Given the following information about a software library, identify 5-8 distinct user personas who would use this library.

Library Information:
- Name: {library_name}
- Description: {description}
- Topics: {topics}

README Summary:
{readme_summary}

Use Cases:
{use_cases}

Common User Questions (from GitHub issues):
{common_questions}

API Patterns:
{api_patterns}

For each persona, provide:
1. **id**: lowercase_snake_case identifier
2. **name**: Human-readable name (e.g., "HPC Developer", "ML Engineer")
3. **description**: 1-2 sentence description of this user type
4. **skill_level**: One of: beginner, intermediate, advanced
5. **concerns**: List of 3-5 key concerns/priorities for this persona
6. **typical_questions**: List of 2-3 example questions this persona would ask

Respond ONLY with a valid JSON object in this exact format:
{{
  "personas": [
    {{
      "id": "hpc_developer",
      "name": "HPC Developer",
      "description": "High-performance computing specialist working on parallel algorithms for scientific computing and simulations.",
      "skill_level": "advanced",
      "concerns": ["performance optimization", "scalability", "NUMA awareness", "low overhead"],
      "typical_questions": [
        "How do I minimize task scheduling overhead?",
        "What's the best way to handle NUMA systems?",
        "How can I profile parallel performance?"
      ]
    }}
  ]
}}

Important:
- Make personas DISTINCT (different roles, skill levels, use cases)
- Be SPECIFIC to this library (not generic software developers)
- Focus on REAL user types based on the provided data
- Include a mix of skill levels (beginner, intermediate, advanced)
"""


class PersonaGenerator:
    """
    Generate persona proposals using LLM analysis.
    """
    
    def __init__(
        self, 
        model: str = "gpt-4o-mini",
        provider: str = "openai",
        api_key: Optional[str] = None
    ):
        """
        Initialize persona generator.
        
        Args:
            model: Model name (e.g., "gpt-4o-mini", "claude-haiku")
            provider: LLM provider ("openai" or "anthropic")
            api_key: API key (optional, will use env var if not provided)
        """
        if not LANGCHAIN_AVAILABLE:
            raise ImportError(
                "LLM dependencies not available. "
                "Install: pip install litellm"
            )

        self.model = model
        self.provider = provider
        self.api_key = api_key

        self.llm: ChatModel
        if provider == "openai":
            self.llm = ChatOpenAI(model=model, api_key=api_key)
        elif provider == "anthropic":
            self.llm = ChatAnthropic(model=model, api_key=api_key)
        elif provider in ("google", "gemini", "google-vertex", "openrouter",
                          "amazon-bedrock", "vertex_ai"):
            from dokimi.utils import get_llm
            self.llm = get_llm(provider, model, api_key)
        else:
            raise ValueError(
                f"Unsupported provider: '{provider}'. "
                "Use 'openai', 'anthropic', 'google', 'openrouter', or 'amazon-bedrock'."
            )

        logger.info(f"Initialized PersonaGenerator with {provider}/{model}")
    
    def generate_personas(
        self, 
        library_name: str,
        analysis: Dict[str, Any],
        target_count: int = 5
    ) -> Dict[str, Any]:
        """
        Generate persona proposals based on project analysis.
        
        Args:
            library_name: Name of the library
            analysis: Analysis output from PersonaAnalyzer
            target_count: Target number of personas (will be adjusted to 5-8)
            
        Returns:
            Dictionary with generated personas:
            {
                "product": str,
                "generated_at": str,
                "model": str,
                "personas": [...]
            }
        """
        target_count = max(5, min(8, target_count))  # Clamp to 5-8
        
        # Prepare prompt data
        readme_summary = self._summarize_readme(analysis.get("readme_content", ""))
        use_cases_str = "\n".join(f"- {uc}" for uc in analysis.get("use_cases", [])[:10])
        questions_str = "\n".join(
            f"- {q}" for q in analysis.get("issues_analysis", {}).get("common_questions", [])[:10]
        )
        api_patterns_str = "\n".join(
            f"- {ap}" for ap in analysis.get("api_patterns", [])[:10]
        )
        topics_str = ", ".join(analysis.get("topics", []))
        
        prompt = PERSONA_GENERATION_PROMPT.format(
            library_name=library_name,
            description=analysis.get("description", ""),
            topics=topics_str or "N/A",
            readme_summary=readme_summary or "N/A",
            use_cases=use_cases_str or "N/A",
            common_questions=questions_str or "N/A",
            api_patterns=api_patterns_str or "N/A"
        )
        
        logger.info(f"Generating {target_count} personas for {library_name}...")
        
        try:
            # Call LLM
            response = self.llm.invoke(prompt)
            response_text = response.content or ""
            from dokimi.llm import extract_json_object
            personas_data = extract_json_object(response_text)
            
            # Validate structure
            if "personas" not in personas_data:
                raise ValueError("Response missing 'personas' key")
            
            personas = personas_data["personas"]
            
            if not isinstance(personas, list):
                raise ValueError("'personas' must be a list")
            
            # Validate each persona
            required_fields = {"id", "name", "description", "skill_level", "concerns", "typical_questions"}
            for persona in personas:
                missing = required_fields - set(persona.keys())
                if missing:
                    raise ValueError(f"Persona missing required fields: {missing}")
            
            result = {
                "product": library_name,
                "generated_at": self._get_timestamp(),
                "model": self.model,
                "provider": self.provider,
                "personas": personas
            }
            
            logger.info(f"✓ Generated {len(personas)} personas")
            return result
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.error(f"Response was: {response_text[:500]}")
            raise
        except Exception as e:
            logger.error(f"Persona generation failed: {e}")
            raise
    
    def _summarize_readme(self, readme: str, max_length: int = 1000) -> str:
        """Extract first N chars of README for context."""
        if not readme:
            return ""
        
        # Take first 1000 chars, try to end at sentence boundary
        if len(readme) <= max_length:
            return readme
        
        truncated = readme[:max_length]
        last_period = truncated.rfind('.')
        if last_period > max_length * 0.7:  # Keep if at least 70% through
            return truncated[:last_period + 1]
        return truncated + "..."
    
    def _get_timestamp(self) -> str:
        """Get ISO format timestamp."""
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    
    def save_personas(self, personas: Dict[str, Any], output_path: Path):
        """Save generated personas to JSON file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding="utf-8") as f:
            json.dump(personas, f, indent=2)
        logger.info(f"✓ Saved {len(personas['personas'])} personas to {output_path}")
    
    def load_personas(self, input_path: Path) -> Dict[str, Any]:
        """Load personas from JSON file."""
        with open(input_path, 'r', encoding="utf-8") as f:
            return json.load(f)
