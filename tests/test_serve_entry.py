"""Tests du script de service embarqué (`qc/model_assets/serve_entry.py`).

Ce script ne s'exécute jamais ici : il vit dans le conteneur SKLearn de l'endpoint. Mais
son contrat — CSV sans en-tête en entrée, un score par ligne en sortie — est ce que la
data capture enregistre et ce que le J3 compare à la baseline. Une régression ici ne se
verrait qu'en production, dans un conteneur qu'on ne débogue pas en salle.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import joblib
import numpy
import pytest

from qc_optimized import build_logistic_model

_CHEMIN = Path(__file__).parents[1] / "src" / "qc" / "model_assets" / "serve_entry.py"
_spec = importlib.util.spec_from_file_location("serve_entry", _CHEMIN)
serve_entry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve_entry)


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory):
    """Un artefact comme le job le produit : un pipeline joblib dans un répertoire."""
    graine = numpy.random.default_rng(42)
    X = graine.normal(size=(80, 3))
    y = (X[:, 0] > 0).astype(int)
    model = build_logistic_model(positive_weight=1.0)
    model.fit(X, y)

    dossier = tmp_path_factory.mktemp("model")
    joblib.dump(model, dossier / "model.joblib")
    return dossier


def test_model_fn_charge_le_pipeline_depuis_le_repertoire(model_dir):
    model = serve_entry.model_fn(str(model_dir))
    assert hasattr(model, "predict_proba")


def test_input_fn_lit_du_csv_sans_en_tete():
    donnees = serve_entry.input_fn("1.5,2.5,0.1\n3.5,4.5,0.2\n", "text/csv")
    assert donnees.shape == (2, 3)
    assert donnees[0][0] == pytest.approx(1.5)


def test_input_fn_refuse_un_autre_content_type():
    """Refuser explicitement vaut mieux qu'un JSON parsé comme du CSV : l'erreur remonte
    au client avec la cause, au lieu d'un score faux."""
    with pytest.raises(ValueError) as exc:
        serve_entry.input_fn('{"pieces": []}', "application/json")
    assert "text/csv" in str(exc.value)


def test_le_score_est_la_probabilite_de_la_classe_defaut(model_dir):
    """`predict_proba[:, 1]` — la colonne de la classe 1 (défaut). Prendre la colonne 0
    inverserait tous les scores, et l'endpoint paraîtrait sain : des nombres entre 0
    et 1, simplement faux."""
    model = serve_entry.model_fn(str(model_dir))
    donnees = serve_entry.input_fn("5.0,0.0,0.0\n-5.0,0.0,0.0\n", "text/csv")
    scores = serve_entry.predict_fn(donnees, model)

    # Sur ce modèle jouet, la première mesure décide : très positive → défaut probable.
    assert scores[0] > 0.5 > scores[1]


def test_output_fn_renvoie_un_score_par_ligne_en_csv(model_dir):
    """Le format que `inference._parse_scores` et la data capture attendent."""
    corps, content_type = serve_entry.output_fn(numpy.array([0.0023, 0.9183]), "text/csv")

    assert content_type == "text/csv"
    assert corps.splitlines() == ["0.002300", "0.918300"]
