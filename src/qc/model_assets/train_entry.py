"""Script d'entraînement embarqué dans le job SageMaker — conteneur SKLearn officiel.

Empaqueté par `qc.training._package_sourcedir()` avec une copie de `qc_optimized.secom`
(renommée `secom_model.py` dans l'archive). N'importe rien du paquet `qc` : le
conteneur ne le contient pas, seulement pandas / scikit-learn / joblib.

Le seuil de décision est choisi par validation croisée HORS-FOLD sur le train — jamais
sur le canal `validation`, qui reste réservé à l'évaluation finale (même règle que le
notebook `exploration-modelisation-secom-optim.ipynb`).
"""

from __future__ import annotations

import json
import os

import joblib
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict

from secom_model import build_logistic_model, operating_metrics, select_f2_threshold

TRAIN_DIR = "/opt/ml/input/data/train"
VALIDATION_DIR = "/opt/ml/input/data/validation"
MODEL_DIR = "/opt/ml/model"
RANDOM_STATE = 42


def _load(directory: str) -> tuple[pd.DataFrame, pd.Series]:
    """Lit le CSV du canal — label en colonne 0, sans en-tête (format imposé par
    `qc.storage`, partagé avec l'ancien conteneur XGBoost)."""
    fichier = sorted(os.listdir(directory))[0]
    df = pd.read_csv(os.path.join(directory, fichier), header=None)
    return df.iloc[:, 1:], df.iloc[:, 0]


def main() -> None:
    X_train, y_train = _load(TRAIN_DIR)
    X_val, y_val = _load(VALIDATION_DIR)

    weight = (y_train == 0).sum() / (y_train == 1).sum()
    model = build_logistic_model(weight)

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
    oof_proba = cross_val_predict(model, X_train, y_train, cv=cv, method="predict_proba")[:, 1]
    selected = select_f2_threshold(y_train, oof_proba)

    model.fit(X_train, y_train)
    proba_val = model.predict_proba(X_val)[:, 1]

    metrics = {
        "positive_weight": float(weight),
        "threshold": selected.threshold,
        "f2_score_oof": selected.f2_score,
        "average_precision": float(average_precision_score(y_val, proba_val)),
        "roc_auc": float(roc_auc_score(y_val, proba_val)),
        **operating_metrics(y_val, proba_val, selected.threshold),
    }

    joblib.dump(model, os.path.join(MODEL_DIR, "model.joblib"))
    with open(os.path.join(MODEL_DIR, "threshold.json"), "w") as handle:
        json.dump({"threshold": selected.threshold}, handle)
    with open(os.path.join(MODEL_DIR, "metrics.json"), "w") as handle:
        json.dump(metrics, handle, indent=2)

    # Lignes lues par SageMaker via les `MetricDefinitions` déclarées dans
    # `qc.training.submit()` — c'est ce qui alimente `Result.metrics`, exactement comme
    # `train:auc`/`validation:auc` l'étaient pour XGBoost.
    for cle in ("average_precision", "roc_auc", "recall", "precision"):
        print(f"validation:{cle} = {metrics[cle]:.4f}")


if __name__ == "__main__":
    main()
