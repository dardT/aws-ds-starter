"""Régression logistique régularisée — LE modèle du fil rouge.

Né comme seconde modélisation candidate dans le notebook d'exploration, ce module est
devenu le modèle de production du J1 : validé contre l'ancienne baseline XGBoost
(rappel des défauts 57,7 % contre 7,7 % au seuil sélectionné, AP 0,236 contre 0,180),
puis confirmé sur un job SageMaker réel le 29/07/2026. `qc.training` l'embarque dans le
conteneur SKLearn — voir `qc/model_assets/train_entry.py`.

Chaque étape de prétraitement est encapsulée dans une pipeline afin qu'elle soit
ajustée uniquement sur les données d'entraînement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, fbeta_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

REGULARIZATION_C = 0.01
THRESHOLDS = np.arange(0.01, 1.00, 0.01)


@dataclass(frozen=True)
class ThresholdSelection:
    """Seuil choisi sur des prédictions de validation hors-fold."""

    threshold: float
    f2_score: float


def build_logistic_model(positive_weight: float) -> Pipeline:
    """Construit la candidate régularisée, sans ajuster ses transformeurs.

    ``positive_weight`` est calculé exclusivement sur le train de l'expérience en
    cours. L'imputation et la standardisation sont ensuite apprises à chaque ``fit``.
    """
    if positive_weight <= 0:
        raise ValueError("positive_weight doit être strictement positif")

    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    C=REGULARIZATION_C,
                    class_weight={0: 1, 1: positive_weight},
                    solver="liblinear",
                    max_iter=2_000,
                    random_state=42,
                ),
            ),
        ]
    )


def select_f2_threshold(
    y_true: pd.Series | np.ndarray,
    probabilities: np.ndarray,
) -> ThresholdSelection:
    """Choisit le seuil qui maximise F2, donc privilégie le rappel des défauts."""
    y = np.asarray(y_true)
    scores = np.asarray(probabilities)
    if y.ndim != 1 or scores.ndim != 1 or len(y) != len(scores):
        raise ValueError("y_true et probabilities doivent être deux vecteurs alignés")

    f2_scores = np.array(
        [fbeta_score(y, scores >= threshold, beta=2, zero_division=0) for threshold in THRESHOLDS]
    )
    best_index = int(f2_scores.argmax())
    return ThresholdSelection(
        threshold=float(THRESHOLDS[best_index]),
        f2_score=float(f2_scores[best_index]),
    )


def operating_metrics(
    y_true: pd.Series | np.ndarray,
    probabilities: np.ndarray,
    threshold: float,
) -> Mapping[str, float | int]:
    """Retourne les erreurs et métriques utiles à une décision de contrôle qualité."""
    if not 0 < threshold < 1:
        raise ValueError("threshold doit être strictement compris entre 0 et 1")

    tn, fp, fn, tp = confusion_matrix(y_true, probabilities >= threshold).ravel()
    return {
        "true_positives": int(tp),
        "false_negatives": int(fn),
        "false_positives": int(fp),
        "true_negatives": int(tn),
        "recall": float(tp / (tp + fn)) if tp + fn else float("nan"),
        "precision": float(tp / (tp + fp)) if tp + fp else float("nan"),
        "review_rate": float((tp + fp) / len(y_true)),
    }
