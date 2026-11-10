import json
import math
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.ai_engine import recommend_jobs_for_text


def _candidate_metrics(ranked_keys, relevant_keys):
    relevant = set(relevant_keys)
    ranked = list(dict.fromkeys(ranked_keys))
    if not relevant:
        raise ValueError("Each evaluation candidate needs at least one relevant job key.")

    def precision_at(limit):
        return sum(key in relevant for key in ranked[:limit]) / limit

    hits_at_10 = sum(key in relevant for key in ranked[:10])
    dcg = sum(
        1 / math.log2(position + 1)
        for position, key in enumerate(ranked[:10], start=1)
        if key in relevant
    )
    ideal_count = min(len(relevant), 10)
    ideal_dcg = sum(
        1 / math.log2(position + 1)
        for position in range(1, ideal_count + 1)
    )
    reciprocal_rank = next(
        (1 / position for position, key in enumerate(ranked, start=1)
         if key in relevant),
        0.0,
    )
    return {
        "precision_at_5": precision_at(5),
        "precision_at_10": precision_at(10),
        "recall_at_10": hits_at_10 / len(relevant),
        "ndcg_at_10": dcg / ideal_dcg if ideal_dcg else 0.0,
        "mean_reciprocal_rank": reciprocal_rank,
    }


class Command(BaseCommand):
    help = "Evaluate recommendations against a manually labelled JSON dataset."

    def add_arguments(self, parser):
        parser.add_argument("--labels", required=True, help="Path to labelled JSON.")
        parser.add_argument("--output", help="Optional path for the JSON report.")

    def handle(self, *args, **options):
        labels_path = Path(options["labels"])
        try:
            data = json.loads(labels_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Could not read evaluation labels: {exc}") from exc
        candidates = data.get("candidates") if isinstance(data, dict) else None
        if not isinstance(candidates, list) or not candidates:
            raise CommandError("Labels must contain a non-empty candidates list.")

        grouped = {"validation": [], "test": []}
        seen_ids = set()
        for row in candidates:
            if not isinstance(row, dict):
                raise CommandError("Each candidate label must be a JSON object.")
            candidate_id = str(row.get("candidate_id", "")).strip()
            split = str(row.get("split", "")).strip().casefold()
            resume_text = str(row.get("resume_text", "")).strip()
            relevant = row.get("relevant_job_keys")
            if not candidate_id or candidate_id in seen_ids:
                raise CommandError("Candidate IDs must be present and unique.")
            if split not in grouped:
                raise CommandError(
                    f"Candidate {candidate_id!r} split must be validation or test."
                )
            if not resume_text or not isinstance(relevant, list) or not relevant:
                raise CommandError(
                    f"Candidate {candidate_id!r} needs resume_text and relevant_job_keys."
                )
            if any(not isinstance(key, str) or ":" not in key for key in relevant):
                raise CommandError(
                    f"Candidate {candidate_id!r} job keys must use source:key format."
                )
            seen_ids.add(candidate_id)
            grouped[split].append(row)
        if not grouped["validation"] or not grouped["test"]:
            raise CommandError("Provide separate, non-empty validation and test splits.")

        report = {
            "labels_file": str(labels_path.resolve()),
            "validation_candidates": len(grouped["validation"]),
            "test_candidates": len(grouped["test"]),
            "metrics": {},
            "note": (
                "Metrics apply only to the supplied human-labelled candidates "
                "and the current active database/dataset listings."
            ),
        }
        for split, rows in grouped.items():
            candidate_results = []
            for row in rows:
                ranked, _ = recommend_jobs_for_text(
                    row["resume_text"], top_n=10, include_dataset=True
                )
                ranked_keys = [
                    f'{record["source"]}:{record["source_key"]}'
                    for record in ranked
                ]
                relevant_keys = [
                    key.strip() for key in row["relevant_job_keys"]
                ]
                candidate_results.append(
                    _candidate_metrics(ranked_keys, relevant_keys)
                )
            report["metrics"][split] = {
                name: round(
                    sum(item[name] for item in candidate_results)
                    / len(candidate_results),
                    4,
                )
                for name in candidate_results[0]
            }

        rendered = json.dumps(report, indent=2)
        if options.get("output"):
            output_path = Path(options["output"])
            try:
                output_path.write_text(rendered + "\n", encoding="utf-8")
            except OSError as exc:
                raise CommandError(f"Could not write evaluation report: {exc}") from exc
            self.stdout.write(f"Evaluation report written to {output_path}")
        else:
            self.stdout.write(rendered)
