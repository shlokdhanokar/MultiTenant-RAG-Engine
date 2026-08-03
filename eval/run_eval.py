"""
Retrieval evaluation harness.

Runs a hand-written question set against the live retrieval pipeline and
reports Hit@1 / Hit@3 / MRR, so retrieval regressions surface as a number
instead of being noticed anecdotally in chat. Also reports vector-only vs.
hybrid re-ranked scores side by side, which is what justifies keeping (or
dropping) the keyword blend.

Usage:
    python eval/run_eval.py                          # default eval set
    python eval/run_eval.py eval/eval_set_tourism.json
    python eval/run_eval.py --keyword-weight 0.5     # sweep the blend
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import perform_semantic_retrieval
from phase2_retrieval.rag_logic import generate_text_embedding, rerank_chunks, EmbeddingGenerationError

DEFAULT_EVAL_SET = os.path.join(os.path.dirname(__file__), "eval_set_tourism.json")


def _rank_of(expected_topic, chunks):
    """1-based rank of the expected topic, or None if absent."""
    for i, chunk in enumerate(chunks, start=1):
        if chunk.get("topic_name") == expected_topic:
            return i
    return None


def _metrics(ranks, total):
    hit1 = sum(1 for r in ranks if r == 1)
    hit3 = sum(1 for r in ranks if r is not None and r <= 3)
    mrr = sum(1 / r for r in ranks if r is not None) / total if total else 0.0
    return hit1, hit3, mrr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("eval_set", nargs="?", default=DEFAULT_EVAL_SET)
    parser.add_argument("--keyword-weight", type=float, default=0.3)
    parser.add_argument("--top-n", type=int, default=4)
    args = parser.parse_args()

    with open(args.eval_set, "r", encoding="utf-8") as f:
        spec = json.load(f)

    kb_id = spec["knowledge_base_id"]
    cases = spec["cases"]

    print(f"Knowledge base : {kb_id}")
    print(f"Eval set       : {args.eval_set} ({len(cases)} cases)")
    print(f"keyword_weight : {args.keyword_weight}  top_n: {args.top_n}")
    print("=" * 78)

    vector_ranks, hybrid_ranks = [], []
    failures = []

    for case in cases:
        query, expected = case["query"], case["expected_topic"]
        try:
            embedding = generate_text_embedding(query)
        except EmbeddingGenerationError as e:
            print(f"ABORTED — embedding failed: {e}")
            print("(If this is a quota error, add OpenAI credits and re-run.)")
            return 1

        candidates = perform_semantic_retrieval(embedding, kb_id, n=args.top_n)
        vector_only = sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)[: args.top_n]
        hybrid = rerank_chunks(query, candidates, n=args.top_n, keyword_weight=args.keyword_weight)

        v_rank = _rank_of(expected, vector_only)
        h_rank = _rank_of(expected, hybrid)
        vector_ranks.append(v_rank)
        hybrid_ranks.append(h_rank)

        mark = "OK " if h_rank == 1 else ("~  " if h_rank else "MISS")
        print(f"{mark} vec_rank={str(v_rank):>4}  hybrid_rank={str(h_rank):>4}  {query[:52]}")
        if h_rank != 1:
            failures.append((query, expected, [c.get("topic_name") for c in hybrid]))

    total = len(cases)
    print("=" * 78)
    for label, ranks in (("vector-only", vector_ranks), ("hybrid     ", hybrid_ranks)):
        hit1, hit3, mrr = _metrics(ranks, total)
        print(f"{label}  Hit@1 {hit1}/{total} ({hit1/total:.0%})   "
              f"Hit@3 {hit3}/{total} ({hit3/total:.0%})   MRR {mrr:.3f}")

    if failures:
        print("\nCases where the expected topic was not ranked #1:")
        for query, expected, got in failures:
            print(f"  q: {query}")
            print(f"     expected: {expected}")
            print(f"     got:      {got}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
