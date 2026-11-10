import csv
import logging
import threading
from functools import lru_cache
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.linear_model import LogisticRegression

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "saved_models" / "jobpilot_intents.joblib"
_model_lock = threading.Lock()


def normalize_message(text):
    """Normalize text without removing job titles, skills, or location terms."""
    import re

    text = str(text or "").casefold()
    text = re.sub(r"[^\w\s+#./-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def create_classifier(c=4.0):
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
    return Pipeline([
        ("features", features),
        ("classifier", LogisticRegression(
            C=c, class_weight="balanced", max_iter=1500,
            solver="liblinear", random_state=42,
        )),
    ])


def _training_rows():
    with (BASE_DIR / "datasets" / "training.csv").open(
        newline="", encoding="utf-8"
    ) as dataset:
        rows = list(csv.DictReader(dataset))
    return [row["text"] for row in rows], [row["intent"].strip() for row in rows]


@lru_cache(maxsize=1)
def get_classifier():
    with _model_lock:
        if MODEL_PATH.is_file():
            return joblib.load(MODEL_PATH)
        texts, labels = _training_rows()
        model = create_classifier()
        model.fit(texts, labels)
        logger.info("Using in-memory chatbot intent model; train artifact with the ML command.")
        return model


def classify_intent(text):
    model = get_classifier()
    probabilities = model.predict_proba([text])[0]
    best_index = probabilities.argmax()
    return model.classes_[best_index], float(probabilities[best_index])
