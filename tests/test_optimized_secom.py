"""Contrats de la seconde modélisation SECOM."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qc_optimized import (
    build_logistic_model,
    operating_metrics,
    select_f2_threshold,
)


def test_la_pipeline_apprend_imputation_et_standardisation() -> None:
    X = pd.DataFrame({"sensor_a": [0.0, 1.0, None, 3.0], "sensor_b": [1.0, 0.0, 1.0, 0.0]})
    y = pd.Series([0, 0, 1, 1])

    model = build_logistic_model(positive_weight=2.0)
    model.fit(X, y)

    assert model.named_steps["imputer"].statistics_.shape == (2,)
    assert model.predict_proba(X).shape == (4, 2)


def test_le_seuil_f2_privilegie_la_detection_des_defauts() -> None:
    selection = select_f2_threshold(
        np.array([0, 0, 1, 1]),
        np.array([0.10, 0.40, 0.60, 0.90]),
    )

    assert 0.40 < selection.threshold <= 0.60
    assert selection.f2_score == pytest.approx(1.0)


def test_les_metriques_operationnelles_comptent_chaque_erreur() -> None:
    metrics = operating_metrics(
        np.array([0, 0, 1, 1]),
        np.array([0.10, 0.80, 0.40, 0.90]),
        threshold=0.50,
    )

    assert metrics == {
        "true_positives": 1,
        "false_negatives": 1,
        "false_positives": 1,
        "true_negatives": 1,
        "recall": 0.5,
        "precision": 0.5,
        "review_rate": 0.5,
    }
