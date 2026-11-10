# JobPilot chatbot NLP and model evaluation

## Pipeline

`training.csv`, `validation.csv`, and `test.csv` contain hand-authored, labeled
messages for 14 intents. The training split has 168 examples (12 per intent),
the validation split has 14 (one per intent), and the held-out test split has
36. Exact normalized-message checks found no duplicate messages across splits.
The current dataset is an initial seed and does not yet meet the suggested
100 training examples per intent; do not treat its scores as production
accuracy. Add reviewed examples before relying on the classifier for broader
language coverage.

The classifier lowercases, normalizes whitespace, and retains technical terms
and punctuation used in skills and job titles. It combines word TF-IDF
unigrams/bigrams and character TF-IDF 2-5 grams, then applies balanced
multinomial Logistic Regression. The training command compares Multinomial
Naive Bayes, Logistic Regression, and Linear SVM, tunes Logistic Regression's
`C` over 1, 4, and 8 against validation macro F1, and selects based on
validation rather than test results. A confidence threshold of `0.10` is
configurable with `JOBPILOT_CHATBOT_CONFIDENCE_THRESHOLD`; ambiguous requests
receive a clarification prompt.

## Current held-out evaluation

These are actual results from the checked-in 36-message test split, not a
claim about real-world accuracy. The test set is small and curated.

| Model | Accuracy | Macro precision | Macro recall | Macro F1 | Weighted F1 | Classifier ms/message |
|---|---:|---:|---:|---:|---:|---:|
| Multinomial Naive Bayes | 0.9444 | 0.9583 | 0.9464 | 0.9415 | 0.9426 | 0.125 |
| Logistic Regression (selected) | 0.9444 | 0.9524 | 0.9583 | 0.9469 | 0.9452 | 0.120 |
| Linear SVM | 0.9722 | 0.9762 | 0.9821 | 0.9755 | 0.9730 | 0.136 |

Validation macro F1 for Logistic Regression with `C=1`, `4`, and `8` was
0.7381, 0.8095, and 0.9048 respectively. `C=8` was therefore selected. The
SVM scored higher on this small test set but lower on validation (0.7381
macro F1), so it was not selected. The selected model's two test
misclassifications were “How do I raise my ATS rating” (labeled `ats_help`,
predicted `website_faq`) and “How many applicants do I have” (labeled
`recruiter_applicants`, predicted `application_status`). Recruiter role
context disambiguates the latter at response time.

`saved_models/evaluation.json` contains the per-intent classification report,
confusion matrix, split class counts, validation tuning results, and measured
latency. `saved_models/confusion_matrix.csv` and
`saved_models/confusion_matrix.svg` contain the selected Logistic Regression
confusion matrix. Latency is classifier prediction only on the local
environment; it excludes Django ORM and recommendation-engine work.

## Django integration

`intent_classifier.py` loads the persisted scikit-learn pipeline once per
process (and trains an in-memory model from `training.csv` if the artifact is
missing). `entity_extractor.py` uses cached job-title, skill, and location
dictionaries from active job records plus common terms. `services.py` connects
the predicted intent to the existing ORM models and recommendation engine.
Candidate matching delegates to `recommend_candidates_for_job` and only
includes discoverable resumes or applicants who consented to sharing. The
message API uses Django CSRF protection, validates JSON and message size,
limits request rate, and scopes conversation data, resumes, applications, and
recruiter queries to the signed-in user.

## Setup and validation

```powershell
python manage.py migrate
python -m core.chatbot_ml.train_intent_model
python manage.py check
python manage.py test core.test_chatbot
```

The chatbot uses local retrieval and scikit-learn; it requires no paid API or
external model service. Expand and review the training/validation/test data
before interpreting confidence scores as calibrated probabilities or
deploying for broad, unrestricted natural-language traffic.
