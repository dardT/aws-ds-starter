"""Script d'inférence embarqué dans le conteneur SKLearn en service.

Contrat du `sagemaker-inference-toolkit` : `model_fn` charge une fois au démarrage,
`input_fn`/`predict_fn`/`output_fn` s'exécutent à chaque requête.

Contrainte à préserver impérativement : CSV sans en-tête en entrée, un score par ligne
en sortie — c'est le format que la data capture (`text/csv`) et le J3 attendent, et
c'était déjà celui du conteneur XGBoost qu'il remplace.
"""

from __future__ import annotations

import os

import joblib
import numpy as np


def model_fn(model_dir: str):
    return joblib.load(os.path.join(model_dir, "model.joblib"))


def input_fn(request_body: str, content_type: str) -> np.ndarray:
    if content_type != "text/csv":
        raise ValueError(f"Content-Type non supporté : {content_type} (attendu text/csv)")
    lignes = [ligne for ligne in request_body.strip().splitlines() if ligne.strip()]
    return np.array([[float(v) for v in ligne.split(",")] for ligne in lignes])


def predict_fn(input_data: np.ndarray, model) -> np.ndarray:
    return model.predict_proba(input_data)[:, 1]


def output_fn(prediction: np.ndarray, accept: str) -> tuple[str, str]:
    corps = "\n".join(f"{score:.6f}" for score in prediction)
    return corps, "text/csv"
