# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Generate questions using LLM based on personas and seed topics."""

import logging
import json
from typing import List, Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

from dokimi.llm import ChatModel
from dokimi.llm import ChatOpenAI, ChatAnthropic, LANGCHAIN_AVAILABLE
from dokimi.defaults import DEFAULT_GENERATOR_MODEL, DEFAULT_GENERATOR_PROVIDER


QUESTION_GENERATION_PROMPT = """You are generating technical questions for documentation quality evaluation.

**Context:**
- Library: __LIBRARY_NAME__
- Persona: __PERSONA_NAME__ (__SKILL_LEVEL__)
- Persona description: __PERSONA_DESCRIPTION__
- Key concerns: __CONCERNS__
- Topic: __TOPIC__
- Question types to generate: __QUESTION_TYPES__

**Task:**
Generate __COUNT__ distinct questions that this persona would ask about __LIBRARY_NAME__.

**Requirements:**
- Each question must be specific to __LIBRARY_NAME__ (not answerable for any random library)
- Match the persona's skill level (__SKILL_LEVEL__)
- Include these question types: __QUESTION_TYPES__
- Be realistic — questions real users would ask when stuck or exploring

**FORBIDDEN patterns (reject these):**
- "What are the best practices for X?" — no single correct answer, model can hallucinate
- "What strategies can I use to optimize X?" — too open-ended, opinion-based
- "How can I improve X?" without a specific constraint or metric
- Generic how-to that applies to any library, not just __LIBRARY_NAME__

**PREFERRED patterns:**
- "What does __LIBRARY_NAME__ do when [specific edge case or condition]?"
- "How do I configure [specific API / parameter] to achieve [specific outcome]?"
- "What is the difference between [concept A] and [concept B] in __LIBRARY_NAME__?"
- "Which [algorithms / modes / APIs] in __LIBRARY_NAME__ support [specific feature]?"
- "How do I install/set up __LIBRARY_NAME__ to [specific goal]?"
- "What speedup / performance characteristic can I expect when using __LIBRARY_NAME__ for [task]?"
- "Why does __LIBRARY_NAME__ [specific behavior]?"
- "What happens if [specific condition] when using [specific API in __LIBRARY_NAME__]?"

**Output format:**
Respond with ONLY a JSON array of strings (no explanation):
["Question 1?", "Question 2?", ...]

Generate __COUNT__ questions now:"""

# Question type distributions for persona-based generation (60% of total)
QUESTION_TYPE_SETS = [
    "conceptual (what is, why, when to use)",
    "how-to (installation, setup, getting started)",
    "troubleshooting (error handling, common pitfalls)",
    "comparison (vs other tools, modes, algorithms)",
    "performance (expected speedups, limitations, trade-offs)",
]


class QuestionGenerator:
    """
    Generate questions per persona and topic using LLM.
    """
    
    def __init__(
        self,
        model: str = DEFAULT_GENERATOR_MODEL,
        provider: str = DEFAULT_GENERATOR_PROVIDER,
        api_key: Optional[str] = None
    ):
        """
        Args:
            model: LLM model name
            provider: "openai" or "anthropic"
            api_key: Optional API key (uses env var if not provided)
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
            import os
            fallback_key = os.environ.get("OPENROUTER_API_KEY") if provider == "openrouter" else None
            self.llm = get_llm(provider, model, api_key or fallback_key)
        else:
            raise ValueError(
                f"Unsupported provider: '{provider}'. "
                "Use 'openai', 'anthropic', 'google', 'openrouter', or 'amazon-bedrock'."
            )
        
        logger.info(f"QuestionGenerator initialized: {provider}/{model}")
    
    def generate_questions(
        self,
        library_name: str,
        personas: List[Dict[str, Any]],
        topics: List[str],
        questions_per_topic: int = 2,
        difficulty_distribution: Optional[Dict[str, int]] = None
    ) -> List[Dict[str, Any]]:
        """
        Generate questions for all personas × topics.
        
        Args:
            library_name: Name of library (e.g., "oneTBB")
            personas: List of persona dicts (from personas JSON)
            topics: List of seed topics (from RagasSeedExtractor)
            questions_per_topic: Questions to generate per persona × topic
            difficulty_distribution: Target counts by difficulty level
                e.g., {"beginner": 2, "intermediate": 3, "advanced": 3}
        
        Returns:
            List of question dicts:
            [
                {
                    "id": "q_001",
                    "text": "How do I use parallel_for?",
                    "personas": ["hpc_developer"],
                    "difficulty": "beginner",
                    "topics": ["parallel_for"],
                    "metadata": {...}
                }
            ]
        """
        if difficulty_distribution is None:
            difficulty_distribution = {
                "beginner": 2,
                "intermediate": 3,
                "advanced": 3
            }
        
        all_questions = []
        question_id = 1
        
        for persona in personas:
            logger.info(
                f"Generating questions for persona: {persona['name']} "
                f"({persona['skill_level']})"
            )
            
            persona_questions = self._generate_for_persona(
                library_name=library_name,
                persona=persona,
                topics=topics,
                questions_per_topic=questions_per_topic,
                difficulty_distribution=difficulty_distribution
            )
            
            # Assign IDs and add to collection
            for q in persona_questions:
                q["id"] = f"q_{question_id:03d}"
                question_id += 1
                all_questions.append(q)
        
        logger.info(f"Generated {len(all_questions)} total questions")
        return all_questions

    def generate_hybrid(
        self,
        library_name: str,
        personas: List[Dict[str, Any]],
        topics: List[str],
        doc_url: str,
        total_questions: int = 50,
        chunk_ratio: float = 0.4,
        questions_per_topic: int = 2,
    ) -> List[Dict[str, Any]]:
        """
        Hybrid generation: 60% persona-based + 40% chunk-grounded.

        Args:
            library_name:       Library name (e.g., "oneDAL")
            personas:           Persona dicts
            topics:             Seed topics
            doc_url:            Documentation URL for chunk-based generation
            total_questions:    Total target question count
            chunk_ratio:        Fraction from doc chunks (default 0.4 = 40%)
            questions_per_topic: Questions per persona×topic

        Returns:
            Mixed list of question dicts with 'question_source' field.
        """
        from dokimi.questions.chunk_gen import ChunkBasedQuestionGenerator, to_question_dicts
        from dokimi.questions.normalizer import normalize_questions

        n_chunk = round(total_questions * chunk_ratio)
        n_persona = total_questions - n_chunk

        logger.info(f"Hybrid generation: {n_persona} persona-based + {n_chunk} chunk-based")

        # ── Persona-based (60%) ───────────────────────────────────────────────
        persona_qs = self.generate_questions(library_name, personas, topics, questions_per_topic)
        persona_qs = persona_qs[:n_persona]
        for q in persona_qs:
            q.setdefault("question_source", "persona")
            if "question" not in q and "text" in q:
                q["question"] = q.pop("text")

        # If persona generation returned fewer than planned, give the gap to chunks
        actual_persona = len(persona_qs)
        n_chunk_actual = total_questions - actual_persona
        if actual_persona < n_persona:
            logger.info(
                f"Persona generation returned {actual_persona}/{n_persona}; "
                f"expanding chunk budget to {n_chunk_actual}"
            )

        # ── Chunk-based (40% or more if persona was short) ────────────────────
        chunk_gen = ChunkBasedQuestionGenerator(
            model=self.model,
            provider=self.provider,
            questions_per_chunk=2,
            max_chunks=25,
        )
        chunk_result = chunk_gen.generate(library_name, doc_url, n_chunk_actual)
        chunk_qs = to_question_dicts(chunk_result)

        # ── Merge, normalize, assign IDs ─────────────────────────────────────
        all_questions = normalize_questions(persona_qs + chunk_qs)
        for i, q in enumerate(all_questions, 1):
            q["id"] = f"q_{i:03d}"

        logger.info(
            f"Hybrid result: {len(persona_qs)} persona + {len(chunk_qs)} chunk "
            f"= {len(all_questions)} total"
        )
        return all_questions
    
    def _generate_for_persona(
        self,
        library_name: str,
        persona: Dict[str, Any],
        topics: List[str],
        questions_per_topic: int,
        difficulty_distribution: Dict[str, int]
    ) -> List[Dict[str, Any]]:
        """Generate questions for a single persona across topics."""
        persona_questions = []
        
        # Sample topics (don't need all topics for every persona)
        import random
        target_total = sum(difficulty_distribution.values())
        topics_to_use = random.sample(topics, min(len(topics), target_total // questions_per_topic))
        
        for topic in topics_to_use:
            try:
                questions_text = self._call_llm(
                    library_name=library_name,
                    persona=persona,
                    topic=topic,
                    count=questions_per_topic
                )
                
                for text in questions_text:
                    persona_questions.append({
                        "text": text,
                        "personas": [persona["id"]],
                        "difficulty": persona["skill_level"],  # Inherit from persona
                        "topics": [topic],
                        "metadata": {
                            "generated_for_persona": persona["name"],
                            "model": self.model,
                            "provider": self.provider
                        }
                    })
                    
            except Exception as e:
                logger.error(
                    f"Failed to generate questions for {persona['name']} × {topic}: {e}"
                )
                continue
        
        return persona_questions
    
    def _call_llm(
        self,
        library_name: str,
        persona: Dict[str, Any],
        topic: str,
        count: int
    ) -> List[str]:
        """Call LLM to generate questions."""
        import random
        q_types = random.choice(QUESTION_TYPE_SETS)
        prompt = (QUESTION_GENERATION_PROMPT
            .replace("__LIBRARY_NAME__", library_name)
            .replace("__PERSONA_NAME__", persona["name"])
            .replace("__SKILL_LEVEL__", persona["skill_level"])
            .replace("__PERSONA_DESCRIPTION__", persona.get("description", ""))
            .replace("__CONCERNS__", ", ".join(persona.get("concerns", [])))
            .replace("__TOPIC__", topic)
            .replace("__QUESTION_TYPES__", q_types)
            .replace("__COUNT__", str(count))
        )
        
        response = self.llm.invoke(prompt)
        raw = response.content if hasattr(response, "content") else str(response)
        
        from dokimi.llm import extract_json_array
        questions = extract_json_array(raw)
        
        # Clean
        return [str(q).strip() for q in questions if q]
    
    def save_questions(self, questions: List[Dict[str, Any]], output_path: Path):
        """Save questions to JSON file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        output = {
            "schema_version": "questions.v1",
            "generated_at": self._get_timestamp(),
            "model": self.model,
            "provider": self.provider,
            "total_questions": len(questions),
            "questions": questions
        }
        
        with open(output_path, 'w', encoding="utf-8") as f:
            json.dump(output, f, indent=2)
        
        logger.info(f"✓ Saved {len(questions)} questions to {output_path}")
    
    @staticmethod
    def _get_timestamp() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
