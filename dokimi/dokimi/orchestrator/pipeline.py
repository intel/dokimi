# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Pipeline orchestrator for end-to-end documentation evaluation."""

import hashlib
import logging
import json
from typing import List, Dict, Any, Optional
from pathlib import Path

from dokimi.defaults import DEFAULT_ANSWER_MODEL, DEFAULT_ANSWER_PROVIDER, DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER, DEFAULT_RETRIEVAL_TOP_K
from dokimi.mcp.factory import create_doc_source_client

logger = logging.getLogger(__name__)


def resolve_questions_from_path(questions_from: Path, product_slug: str) -> Path:
    """Resolve a --questions-from file or run directory for a target slug."""
    qf = Path(questions_from)
    if qf.is_dir():
        qf = qf / "questions" / f"{product_slug}.json"
    return qf


def compute_question_set_hash(questions: List[Dict[str, Any]]) -> str:
    """Return 12-char SHA-256 prefix over sorted question texts + ids."""
    fingerprints = sorted(
        f"{q.get('id', '')}:{q.get('question', q.get('question_text', q.get('text', '')))}"
        for q in questions
    )
    raw = "\n".join(fingerprints).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _canonical(questions: List[Dict[str, Any]]) -> List[str]:
    """The set's records as sorted ``(id, question)`` pairs, to compare two sets by content.

    Labels the run adds on reading (``source_type``, ``persona_id``) are left out, so a set
    is equal to what was read unless a record was added, dropped or reworded.
    """
    from dokimi.utils import question_id

    return sorted(json.dumps([repr(question_id(q)), q.get("question", q.get("text"))],
                             default=str) for q in questions)


def load_questions_payload(path: Path) -> List[Dict[str, Any]]:
    """The records of the question set at ``path``, in the questions.v2 shape.

    :func:`dokimi.questions.artifact.load_questions` reads it: v1, v2, a bare list, or a legacy
    shape (logged once); a set still invalid after that raises ``ArtifactValidationError`` (a
    ``ValueError``) naming the record. ``path`` is not written.
    """
    from dokimi.questions.artifact import load_questions

    return load_questions(path).questions


class EvaluationPipeline:
    """
    Orchestrate full documentation evaluation pipeline.
    
    Steps:
    1. Discover personas
    2. Generate questions (from personas + optional custom)
    3. Merge and deduplicate questions with source tracking
    4. Generate context-arm and baseline answers
    5. Evaluate answers (LLM-as-judge)
    6. Generate report
    """
    
    def __init__(
        self,
        product: str,
        output_dir: Path,
        repo: Optional[str] = None,
        description: Optional[str] = None,
        custom_questions_path: Optional[Path] = None,
        model: str = DEFAULT_ANSWER_MODEL,
        provider: str = DEFAULT_ANSWER_PROVIDER,
        judge_model: str = DEFAULT_JUDGE_MODEL,
        judge_provider: str = DEFAULT_JUDGE_PROVIDER,
        judge_temperature: Optional[float] = None,
        personas_count: int = 5,
        questions_per_topic: int = 2,
        top_k: int = DEFAULT_RETRIEVAL_TOP_K,
        rerank_threshold: float = 0.3,
        debug_retrieval: bool = False,
        doc_source: str = "context7",
        context7_id: Optional[str] = None,
        max_tokens_per_question: int = 4000,
        force_regen: bool = False,
        questions_from: Optional[Path] = None,
        product_key: Optional[str] = None,
        questions_root: Optional[Path] = None,
    ):
        """
        Initialize pipeline.

        Args:
            product: Product name (e.g., "oneDNN")
            output_dir: Base directory for outputs
            repo: GitHub repo (e.g., "oneapi-src/oneDNN").  Optional — when
                omitted, ``description`` must be provided so the LLM can
                generate personas without GitHub data.
            description: Plain-text description of the product.  Used as the
                sole signal for persona generation when ``repo`` is not given.
                Also useful to supplement GitHub data.
            custom_questions_path: Optional path to manual questions JSON
            model: LLM model for generation/answering
            provider: Provider for generation/answering
            judge_model: LLM model for evaluation
            judge_provider: Provider for evaluation
            judge_temperature: Optional judge sampling temperature; unset leaves
                the provider default
            personas_count: Target number of personas
            questions_per_topic: Questions per topic per persona
            top_k: Docs to retrieve before reranking
            rerank_threshold: Min relevance score
            debug_retrieval: Include retrieval metadata
            doc_source: Documentation source descriptor — 'context7' (default),
                'local:<path>', or 'url:<url>'
            context7_id: Explicit Context7 library ID (e.g., 'intel/mkl-dnn').
                Overrides the auto-resolved ID when doc_source is 'context7'.
            questions_from: Reuse an existing question set (file, or a run
                directory holding questions/<product_key>.json).
            product_key: Registry key used for output file names and golden
                question-set lookup (defaults to ``product``).
            questions_root: Directory holding golden question sets named
                ``<product_key>_golden.json``, for example a checkout of the
                evaluation-content repository. When given and the file is
                present it is the run's whole question set and generation is
                skipped; when it is absent a warning is logged and questions are
                generated. Without this argument (and without
                ``custom_questions_path``) the run has no golden set.
        """
        if not repo and not description:
            raise ValueError(
                "Either 'repo' or 'description' must be provided. "
                "Supply 'repo' for GitHub-based persona discovery, or "
                "'description' for products without a public repository."
            )

        self.product = product
        self.product_key = product_key or product
        self.repo = repo
        self.description = description
        self.output_dir = Path(output_dir)

        # Golden question set: explicit path wins; otherwise look for the
        # convention name <product_key>_golden.json under the caller-supplied
        # questions root (the question sets live outside this repository).
        self.questions_root = Path(questions_root) if questions_root else None
        # A golden set found under the root *is* the question set: generation is
        # skipped, so two runs on one root answer the same questions. An explicit
        # custom_questions_path is still merged into generated questions.
        self.golden_pinned = False
        if custom_questions_path:
            _custom_path = Path(custom_questions_path)
            if not _custom_path.exists():
                raise FileNotFoundError(
                    f"--custom-questions path does not exist: {_custom_path}"
                )
            self.custom_questions_path: Optional[Path] = _custom_path
        elif questions_from is None and self.questions_root is not None:
            _auto_golden = self.questions_root / f"{self.product_key.lower()}_golden.json"
            if _auto_golden.is_file():
                self.custom_questions_path = _auto_golden
                self.golden_pinned = True
                logger.info(f"Using golden questions: {_auto_golden}")
            else:
                self.custom_questions_path = None
                logger.warning(
                    f"questions root {self.questions_root} has no golden set for "
                    f"{self.product_key.lower()}; running without a golden question set"
                )
        else:
            self.custom_questions_path = None
            if questions_from is None and self.questions_root is None:
                logger.warning(
                    "no questions root given; questions will be generated for "
                    f"{self.product_key.lower()} instead of read from a curated golden "
                    "set. Pass --questions-root <checkout>/questions, or reuse an "
                    "earlier run's set, to measure against a fixed set"
                )

        self.model = model
        self.provider = provider
        self.judge_model = judge_model
        self.judge_provider = judge_provider
        self.judge_temperature = judge_temperature

        self.personas_count = personas_count
        self.questions_per_topic = questions_per_topic
        self.top_k = top_k
        self.rerank_threshold = rerank_threshold
        self.debug_retrieval = debug_retrieval
        self.doc_source = doc_source
        self.context7_id = context7_id
        self.max_tokens_per_question = max_tokens_per_question
        self.force_regen = force_regen

        # External questions source (skip generation, load from this path/dir)
        if questions_from is not None:
            # Accept either a directory (auto-find <target>.json) or a file path.
            qf = resolve_questions_from_path(Path(questions_from), self.product_key)
            if not qf.exists():
                raise FileNotFoundError(
                    f"--questions-from: questions file not found: {qf}"
                )
            self.external_questions_path: Optional[Path] = qf
            logger.info(f"Will reuse questions from: {qf}")
        else:
            self.external_questions_path = None

        # Output paths
        self.personas_path = self.output_dir / "personas" / f"{self.product_key}.json"
        self.questions_path = self.output_dir / "questions" / f"{self.product_key}.json"
        self.answers_path = self.output_dir / "answers" / f"{self.product_key}.json"
        self.eval_path = self.output_dir / "eval" / f"{self.product_key}.json"
        self.report_path = self.output_dir / "reports" / f"{self.product_key}.md"
        
        # Create output dirs
        for path in [self.personas_path, self.questions_path, self.answers_path, self.eval_path, self.report_path]:
            path.parent.mkdir(parents=True, exist_ok=True)
    
    def run(self, concurrency: int = 5) -> Dict[str, Any]:
        """
        Run full pipeline.
        
        Returns:
            Summary dict with results and paths
        """
        logger.info(f"Starting evaluation pipeline for {self.product}")
        
        results: Dict[str, Any] = {
            "product": self.product,
            "repo": self.repo,
            "steps": {}
        }
        
        # Every question set this run reuses is read (and refused, if unusable) before any
        # model call: --questions-from, the cached set, or --custom-questions.
        reused = self._read_question_inputs()

        # Step 1: Discover personas (cached if already exists)
        # Skip if using golden pinned or external questions (personas not needed)
        if self.golden_pinned or self.external_questions_path is not None:
            logger.info("Step 1/6: Skipping persona discovery (using pinned or external questions)")
            personas: Dict[str, Any] = {"personas": []}
            results["steps"]["personas"] = {
                "count": 0,
                "skipped": True
            }
        else:
            logger.info("Step 1/6: Discovering personas...")
            if (not self.force_regen) and self.personas_path.exists():
                personas = json.loads(self.personas_path.read_text(encoding="utf-8"))
                print(f"✓ Loaded {len(personas.get('personas', []))} personas (cached)")
            else:
                personas = self._discover_personas()
                print(f"✓ Discovered {len(personas.get('personas', []))} personas")
            results["steps"]["personas"] = {
                "count": len(personas.get("personas", [])),
                "path": str(self.personas_path)
            }

        # Step 2+3: Generate & merge questions (external / cached / fresh)
        write_over_input = False  # Default: never write over input files

        if self.external_questions_path is not None:
            # Reuse question set from another run — skip generation entirely
            logger.info(f"Step 2-3/6: Loading external questions from {self.external_questions_path}...")
            merged_questions = reused or []
            print(f"✓ Loaded {len(merged_questions)} questions (from --questions-from, skipping generation)")
            results["steps"]["questions_generated"] = {
                "count": len(merged_questions),
                "external_source": str(self.external_questions_path),
            }
            results["steps"]["questions_merged"] = {
                "total": len(merged_questions),
                "external_source": str(self.external_questions_path),
                "path": str(self.questions_path),
            }
        elif (not self.force_regen) and self.questions_path.exists():
            logger.info("Step 2-3/6: Loading cached questions (skipping generation)...")
            merged_questions = reused or []
            print(f"✓ Loaded {len(merged_questions)} questions (cached — skipping generation)")
            # Warn if cached questions differ from the golden set
            if self.golden_pinned and self.custom_questions_path is not None:
                try:
                    golden_questions = load_questions_payload(self.custom_questions_path)
                    cached_hash = compute_question_set_hash(merged_questions)
                    golden_hash = compute_question_set_hash(golden_questions)
                    if cached_hash != golden_hash:
                        cached_count = len(merged_questions)
                        golden_count = len(golden_questions)
                        logger.warning(
                            f"cached question set (n={cached_count}, hash={cached_hash}) differs from "
                            f"golden set (n={golden_count}, hash={golden_hash}); "
                            f"use a fresh output directory (or --force-regen with benchmark run) to use the golden set"
                        )
                except (json.JSONDecodeError, ValueError, TypeError, AttributeError, OSError) as e:
                    logger.warning(
                        f"Could not load golden set for comparison: {e}; "
                        f"using cached questions"
                    )
            results["steps"]["questions_generated"] = {"count": len(merged_questions), "cached": True}
            results["steps"]["questions_merged"] = {"total": len(merged_questions), "cached": True,
                                                     "path": str(self.questions_path)}
        elif self.golden_pinned:
            logger.info(f"Step 2-3/6: Loading golden questions from {self.custom_questions_path}...")
            # Read (and refused, if unusable) before any model call by _read_question_inputs.
            merged_questions = getattr(self, "_custom_questions", None) or self._load_custom_questions()
            if not merged_questions:
                raise ValueError(f"Golden set is empty: {self.custom_questions_path}")
            print(f"✓ Loaded {len(merged_questions)} golden questions (skipping generation)")
            results["steps"]["questions_generated"] = {
                "count": len(merged_questions),
                "golden_source": str(self.custom_questions_path),
            }
            results["steps"]["questions_merged"] = {
                "total": len(merged_questions),
                "manual": len(merged_questions),
                "golden_source": str(self.custom_questions_path),
                "path": str(self.questions_path),
            }
        else:
            logger.info("Step 2/6: Generating questions...")
            generated_questions = self._generate_questions(personas)
            results["steps"]["questions_generated"] = {
                "count": len(generated_questions),
                "path": "temp"
            }
            print(f"✓ Generated {len(generated_questions)} questions from personas")

            # Step 3: Merge with custom questions + deduplicate
            logger.info("Step 3/6: Merging and deduplicating questions...")
            read_custom = getattr(self, "_custom_questions", None)
            read_snapshot = None if read_custom is None else _canonical(read_custom)
            merged_questions = self._merge_questions(generated_questions)
            # A --custom-questions file that is this run's own output is written only when the
            # merged set differs from what was read from it; without a read set, never.
            write_over_input = read_snapshot is not None and _canonical(merged_questions) != read_snapshot

            results["steps"]["questions_merged"] = {
                "total": len(merged_questions),
                "generated": sum(1 for q in merged_questions if q.get("source_type") == "generated"),
                "manual": sum(1 for q in merged_questions if q.get("source_type") == "manual"),
                "path": str(self.questions_path)
            }
            print(f"✓ Merged to {len(merged_questions)} unique questions "
                  f"({results['steps']['questions_merged']['generated']} generated, "
                  f"{results['steps']['questions_merged']['manual']} manual)")

        # Compute and record question_set_hash for reproducibility
        question_set_hash = compute_question_set_hash(merged_questions)
        results["question_set_hash"] = question_set_hash
        self._save_questions_artifact(merged_questions, question_set_hash, write_over_input=write_over_input)
        print(f"  question_set_hash: {question_set_hash}")

        # Step 4: Generate answers
        logger.info("Step 4/6: Generating context-arm and baseline answers...")
        answers = self._generate_answers(merged_questions, concurrency=concurrency,
                                         question_set_hash=question_set_hash)
        results["steps"]["answers"] = {
            "count": len(answers),
            "path": str(self.answers_path)
        }
        print(f"✓ Generated answers for {len(answers)} questions")
        
        # Step 5: Evaluate answers
        logger.info("Step 5/6: Evaluating answers...")
        evaluations = self._evaluate_answers(answers, concurrency=concurrency,
                                              question_set_hash=question_set_hash)
        results["steps"]["evaluation"] = {
            "count": len(evaluations),
            "path": str(self.eval_path)
        }
        
        # Compute summary stats
        with_scores = [e["with_docs"]["aggregate"] for e in evaluations if e.get("with_docs")]
        without_scores = [e["without_docs"]["aggregate"] for e in evaluations if e.get("without_docs")]
        deltas = [e["delta"] for e in evaluations if e.get("delta") is not None]
        
        if with_scores and without_scores and deltas:
            results["steps"]["evaluation"]["summary"] = {
                "with_avg": round(sum(with_scores) / len(with_scores), 1),
                "without_avg": round(sum(without_scores) / len(without_scores), 1),
                "delta_avg": round(sum(deltas) / len(deltas), 1)
            }
            print(f"✓ Evaluated {len(evaluations)} answers: "
                  f"context={results['steps']['evaluation']['summary']['with_avg']}, "
                  f"baseline={results['steps']['evaluation']['summary']['without_avg']}, "
                  f"delta={results['steps']['evaluation']['summary']['delta_avg']:+.1f}")
        else:
            print(f"✓ Evaluated {len(evaluations)} answers (summary unavailable)")
        
        # Step 6: Generate report
        logger.info("Step 6/6: Generating report...")
        self._generate_report(evaluations, merged_questions)
        results["steps"]["report"] = {
            "path": str(self.report_path)
        }
        print(f"✓ Generated report: {self.report_path}")
        
        results["status"] = "success"
        return results
    
    def _discover_personas(self) -> Dict[str, Any]:
        """Discover personas — from GitHub repo or from a plain description."""
        import os
        from dokimi.personas import PersonaAnalyzer, PersonaGenerator

        generator = PersonaGenerator(model=self.model, provider=self.provider)

        if self.repo:
            # --- GitHub-based discovery ---
            github_token = os.getenv("GITHUB_TOKEN")
            analyzer = PersonaAnalyzer(github_token=github_token)
            logger.info(f"Analysing repository: {self.repo}")
            analysis = analyzer.analyze_repository(self.repo)
        else:
            # --- Description-only discovery (no GitHub repo) ---
            logger.info(
                f"No repo provided — generating personas from description for '{self.product}'"
            )
            analysis = PersonaAnalyzer.create_minimal_analysis(
                library_name=self.product,
                description=self.description or "",
            )

        personas = generator.generate_personas(
            library_name=self.product,
            analysis=analysis,
            target_count=self.personas_count
        )
        
        # Save personas
        generator.save_personas(personas, self.personas_path)
        
        return personas
    
    def _generate_questions(self, personas: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate questions from personas."""
        from dokimi.questions import RagasSeedExtractor, QuestionGenerator

        # Extract topics
        mcp_client = create_doc_source_client(self.doc_source)
        library_id = self.context7_id or mcp_client.resolve_library_id(self.product)
        
        extractor = RagasSeedExtractor(mcp_client=mcp_client, cache_dir=Path(".cache/topics"))
        topics = extractor.extract_topics(
            library_id=library_id,
            library_name=self.product,
            max_topics=20
        )
        
        # Generate questions — hybrid mode if doc_url available
        generator = QuestionGenerator(model=self.model, provider=self.provider)
        doc_url = self.doc_source if self.doc_source.startswith("url:") else None
        if doc_url:
            doc_url = doc_url[4:]  # strip "url:" prefix

        if doc_url:
            # Cap at a sane default; questions_per_topic * topics * personas can be huge
            target_total = min(
                self.questions_per_topic * len(topics) * len(personas["personas"]),
                100,  # hard cap — adjust via CLI if needed
            )
            questions = generator.generate_hybrid(
                library_name=self.product,
                personas=personas["personas"],
                topics=topics,
                doc_url=doc_url,
                total_questions=target_total,
                chunk_ratio=0.4,
                questions_per_topic=self.questions_per_topic,
            )
        else:
            questions = generator.generate_questions(
                library_name=self.product,
                personas=personas["personas"],
                topics=topics,
                questions_per_topic=self.questions_per_topic,
            )

        # Add source tracking
        for q in questions:
            q["source_type"] = "generated"
        
        return questions
    
    def _read_question_inputs(self) -> Optional[List[Dict[str, Any]]]:
        """Read the question sets this run reuses; return the set that replaces generation.

        ``--questions-from`` wins, then the cached ``questions/<product>.json`` (unless
        ``force_regen``); either is returned. Otherwise the ``--custom-questions`` set, if
        any, is read for :meth:`_merge_questions` and ``None`` is returned. Every read goes
        through :func:`load_questions_payload`, so a legacy file is upgraded in memory and an
        unusable one fails here, before any model call. No input file is written.
        """
        self._custom_questions: Optional[List[Dict[str, Any]]] = None
        if self.external_questions_path is not None:
            return load_questions_payload(self.external_questions_path)
        if (not self.force_regen) and self.questions_path.exists():
            return load_questions_payload(self.questions_path)
        self._custom_questions = self._load_custom_questions()
        return None

    def _load_custom_questions(self) -> List[Dict[str, Any]]:
        """The --custom-questions (or auto-included golden) set, tagged ``source_type: manual``.

        A custom set that is this run's own ``questions/<product>.json`` (a ``--force-regen``
        run through the Python API) keeps the ``source_type`` a previous run wrote, so its
        generated records are not relabelled manual and protected from dedup on the next run.
        """
        path = self.custom_questions_path
        if not (path and path.exists()):
            return []
        custom_questions = load_questions_payload(path)
        output = getattr(self, "questions_path", None)
        own_output = output is not None and Path(path).resolve() == Path(output).resolve()
        for q in custom_questions:  # records read from the file, not the file
            if own_output:
                q.setdefault("source_type", "manual")
            else:
                q["source_type"] = "manual"
            # A generated record of the run's own file keeps its personas; it is not manual.
            if q.get("source_type") == "manual" and "persona_id" not in q:
                q["persona_id"] = "manual"
        logger.info(f"Loaded {len(custom_questions)} custom questions")
        return custom_questions

    def _merge_questions(self, generated_questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge generated + custom questions, deduplicate."""
        from dokimi.questions import QuestionValidator

        all_questions = generated_questions.copy()

        # Custom questions: read before the run's first model call by _read_question_inputs.
        custom_questions = getattr(self, "_custom_questions", None)
        self._custom_questions = None
        if custom_questions is None:
            custom_questions = self._load_custom_questions()

        all_questions.extend(custom_questions)

        # Deduplicate. Curated questions are never the ones dropped: a merge
        # keeps the longest text, which would otherwise let a generated
        # paraphrase replace a manual golden question.
        validator = QuestionValidator()  # threshold follows the embedding backend
        unique_questions, groups = validator._deduplicate(
            all_questions, protect=lambda q: q.get("source_type") == "manual"
        )
        if groups:
            logger.info(
                f"Deduplication merged {len(all_questions) - len(unique_questions)} "
                f"questions in {len(groups)} groups"
            )
            for group in groups:
                logger.debug("duplicate group: %s", group)

        return unique_questions

    def _save_questions_artifact(
        self,
        questions: List[Dict[str, Any]],
        question_set_hash: Optional[str] = None,
        write_over_input: bool = False,
    ) -> None:
        """Persist the merged set as a questions.v2 artifact with reproducibility metadata.

        This is the run's own copy: the cached file may be rewritten (it is this run's
        output), but a ``--questions-from`` or ``--custom-questions`` file is never written
        unless the merge added records (write_over_input=True from force-regen merge).
        Raises ``ArtifactValidationError`` -- before any answer is generated -- when the set
        is not a valid artifact; the set is kept at ``<name>.rejected``. Records written with
        ``text`` are saved as ``question``.

        Args:
            questions: The question records to save
            question_set_hash: Optional hash for reproducibility
            write_over_input: If False, skip save when questions_path equals an input file path
        """
        from dokimi.questions.artifact import save_questions_artifact

        inputs = [p for p in (getattr(self, "external_questions_path", None),
                              getattr(self, "custom_questions_path", None)) if p is not None]
        target = self.questions_path.resolve()

        if not write_over_input and any(Path(p).resolve() == target for p in inputs):
            logger.info("questions output %s is an input of this run; leaving it unchanged",
                        self.questions_path)
            return

        output = {
            "product": self.product,
            "total_questions": len(questions),
            "sources": {
                "generated": sum(1 for q in questions if q.get("source_type") == "generated"),
                "manual": sum(1 for q in questions if q.get("source_type") == "manual")
            },
            "questions": questions
        }
        if question_set_hash:
            output["question_set_hash"] = question_set_hash
            output["question_set_id"] = question_set_hash

        save_questions_artifact(output, self.questions_path)
    
    def _generate_answers(self, questions: List[Dict[str, Any]], concurrency: int = 5,
                          question_set_hash: Optional[str] = None) -> List[Dict[str, Any]]:
        """Generate context-arm and baseline answers."""
        from dokimi.eval import Answerer

        # Setup documentation source client
        mcp_client = create_doc_source_client(self.doc_source)
        library_id = self.context7_id or mcp_client.resolve_library_id(self.product)
        
        # Generate answers
        answerer = Answerer(
            mcp_client=mcp_client,
            model=self.model,
            provider=self.provider,
            top_k=self.top_k,
            rerank_threshold=self.rerank_threshold,
            debug_retrieval=self.debug_retrieval
        )
        
        answers = answerer.generate_answers(
            library_name=self.product,
            library_id=library_id,
            questions=questions,
            max_tokens_per_question=self.max_tokens_per_question,
            concurrency=concurrency,
        )
        
        # Save answers
        answerer.save_answers(answers, self.answers_path, question_set_hash=question_set_hash)
        
        return answers
    
    def _evaluate_answers(self, answers: List[Dict[str, Any]] | Dict[str, Any],
                          concurrency: int = 5,
                          question_set_hash: Optional[str] = None) -> List[Dict[str, Any]]:
        """Evaluate answers using LLM-as-judge."""
        from dokimi.eval import Judge
        
        # Use in-memory answers from the previous step (avoid stale disk read)
        if isinstance(answers, dict):
            answers_list = answers.get("answers", answers)
        elif answers:
            answers_list = answers
        else:
            # Fallback: read from disk if not provided
            answers_data = json.loads(self.answers_path.read_text(encoding="utf-8"))
            answers_list = answers_data.get("answers", answers_data)
        
        # Evaluate
        judge = Judge(
            model=self.judge_model,
            provider=self.judge_provider,
            temperature=self.judge_temperature,
            run_metadata={
                "question_model": self.model,
                "question_provider": self.provider,
                "answer_model": self.model,
                "answer_provider": self.provider,
                "judge_model": self.judge_model,
                "judge_provider": self.judge_provider,
                "question_set_hash": question_set_hash,
                "evaluator_independence_warning": (
                    str(self.model).strip().lower() == str(self.judge_model).strip().lower()
                    and str(self.provider).strip().lower() == str(self.judge_provider).strip().lower()
                ),
            },
        )
        
        evaluations = judge.evaluate_answers(self.product, answers_list, concurrency=concurrency)
        
        # Save evaluations
        judge.save_evaluations(evaluations, self.eval_path)
        
        return evaluations
    
    def _generate_report(
        self,
        evaluations: List[Dict[str, Any]],
        questions: List[Dict[str, Any]]
    ) -> str:
        """Generate comprehensive report."""
        from dokimi.report import ReportGenerator
        
        # Load data
        eval_data = json.loads(self.eval_path.read_text(encoding="utf-8"))
        questions_data = {
            "product": self.product,
            "questions": questions
        }
        
        # Generate report
        generator = ReportGenerator()
        report = generator.generate_report(
            eval_data=eval_data,
            questions_data=questions_data,
            output_format="markdown"
        )
        
        # Save report
        self.report_path.write_text(report, encoding="utf-8")
        
        return report
