import csv
import html
import json
import time
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.naive_bayes import MultinomialNB
from sklearn.svm import LinearSVC
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)

from core.chatbot_ml.intent_classifier import create_classifier

ROOT = Path(__file__).resolve().parent


def load_rows(name):
    with (ROOT / "datasets" / name).open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    return [row["text"] for row in rows], [row["intent"].strip() for row in rows]


def baseline_classifier(classifier):
    from core.chatbot_ml.intent_classifier import normalize_message

    features = FeatureUnion([
        ("word", TfidfVectorizer(
            ngram_range=(1, 2), min_df=1, max_features=12000,
            preprocessor=normalize_message, token_pattern=r"(?u)\b[\w+#.-]+\b",
        )),
        ("character", TfidfVectorizer(
            analyzer="char", ngram_range=(2, 5), min_df=1,
            max_features=20000, preprocessor=normalize_message,
        )),
    ])
    return Pipeline([("features", features), ("classifier", classifier)])


def evaluate(name, model, train_x, train_y, test_x, test_y):
    model.fit(train_x, train_y)
    started = time.perf_counter()
    predicted = model.predict(test_x)
    latency_ms = (time.perf_counter() - started) * 1000 / len(test_x)
    return {
        "model": name,
        "accuracy": accuracy_score(test_y, predicted),
        "precision_macro": precision_score(test_y, predicted, average="macro", zero_division=0),
        "recall_macro": recall_score(test_y, predicted, average="macro", zero_division=0),
        "f1_macro": f1_score(test_y, predicted, average="macro", zero_division=0),
        "f1_weighted": f1_score(test_y, predicted, average="weighted", zero_division=0),
        "latency_ms_per_message": latency_ms,
        "classification_report": classification_report(
            test_y, predicted, output_dict=True, zero_division=0
        ),
        "confusion_matrix": confusion_matrix(
            test_y, predicted, labels=sorted(set(train_y))
        ).tolist(),
        "labels": sorted(set(train_y)),
        "predictions": predicted.tolist(),
    }, model


def write_confusion_svg(labels, matrix, path):
    cell = 34
    left = 185
    top = 190
    width = left + cell * len(labels) + 30
    height = top + cell * len(labels) + 35
    maximum = max(max(row) for row in matrix) or 1
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fff"/>',
        '<text x="20" y="28" font-family="Arial" font-size="18" font-weight="bold">Intent classification confusion matrix</text>',
        f'<text x="{left + cell * len(labels) / 2}" y="58" text-anchor="middle" font-family="Arial" font-size="12">Predicted intent</text>',
        f'<text x="22" y="{top + cell * len(labels) / 2}" transform="rotate(-90 22 {top + cell * len(labels) / 2})" text-anchor="middle" font-family="Arial" font-size="12">Actual intent</text>',
    ]
    for index, label in enumerate(labels):
        x = left + index * cell + cell / 2
        y = top + index * cell + cell / 2
        escaped = html.escape(label)
        parts.append(
            f'<text x="{x}" y="{top - 8}" transform="rotate(-55 {x} {top - 8})" text-anchor="start" font-family="Arial" font-size="9">{escaped}</text>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{y + 3}" text-anchor="end" font-family="Arial" font-size="9">{escaped}</text>'
        )
    for row_index, row in enumerate(matrix):
        for column_index, value in enumerate(row):
            ratio = value / maximum
            red = int(246 - ratio * 131)
            green = int(241 - ratio * 55)
            blue = int(235 - ratio * 71)
            x = left + column_index * cell
            y = top + row_index * cell
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" fill="rgb({red},{green},{blue})" stroke="#fff"/>'
            )
            parts.append(
                f'<text x="{x + cell / 2}" y="{y + cell / 2 + 4}" text-anchor="middle" font-family="Arial" font-size="10">{value}</text>'
            )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def main():
    train_x, train_y = load_rows("training.csv")
    validation_x, validation_y = load_rows("validation.csv")
    test_x, test_y = load_rows("test.csv")
    validation_scores = []
    for c_value in (1.0, 4.0, 8.0):
        candidate = create_classifier(c=c_value)
        candidate.fit(train_x, train_y)
        predicted = candidate.predict(validation_x)
        validation_scores.append({
            "C": c_value,
            "f1_macro": f1_score(validation_y, predicted, average="macro", zero_division=0),
        })
    selected_c = max(validation_scores, key=lambda row: row["f1_macro"])["C"]
    models = [
        ("Multinomial Naive Bayes", baseline_classifier(MultinomialNB(alpha=0.25))),
        ("Logistic Regression", create_classifier(c=selected_c)),
        ("Linear SVM", baseline_classifier(LinearSVC(C=1.0, class_weight="balanced", random_state=42))),
    ]
    results = []
    fitted = {}
    for name, model in models:
        result, model = evaluate(name, model, train_x, train_y, test_x, test_y)
        fitted[name] = model
        results.append(result)

    selected = next(row for row in results if row["model"] == "Logistic Regression")
    output = ROOT / "saved_models"
    output.mkdir(parents=True, exist_ok=True)
    joblib.dump(fitted["Logistic Regression"], output / "jobpilot_intents.joblib")
    (output / "evaluation.json").write_text(
        json.dumps({
            "dataset": {
                "training_examples": len(train_x),
                "validation_examples": len(validation_x),
                "test_examples": len(test_x),
                "training_class_distribution": {
                    label: train_y.count(label) for label in sorted(set(train_y))
                },
                "test_class_distribution": {
                    label: test_y.count(label) for label in sorted(set(test_y))
                },
            },
            "selected_model": selected["model"],
            "selected_logistic_C": selected_c,
            "validation_tuning": validation_scores,
            "confidence_threshold": 0.10,
            "results": results,
        }, indent=2),
        encoding="utf-8",
    )
    with (output / "confusion_matrix.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["actual/predicted", *selected["labels"]])
        for label, row in zip(selected["labels"], selected["confusion_matrix"]):
            writer.writerow([label, *row])
    write_confusion_svg(
        selected["labels"],
        selected["confusion_matrix"],
        output / "confusion_matrix.svg",
    )
    print(json.dumps({
        "selected_model": selected["model"],
        "test_examples": len(test_x),
        "accuracy": selected["accuracy"],
        "precision_macro": selected["precision_macro"],
        "recall_macro": selected["recall_macro"],
        "f1_macro": selected["f1_macro"],
        "f1_weighted": selected["f1_weighted"],
        "latency_ms_per_message": selected["latency_ms_per_message"],
    }, indent=2))


if __name__ == "__main__":
    main()
