"""Tests de `qc.explain`.

Aucun appel à S3 : les tests entraînent un pipeline minuscule sur place. C'est plus
rapide qu'un téléchargement, et surtout ça permet de vérifier la propriété qui fait la
valeur d'une explication — la somme des contributions et du biais redonne la sortie
brute (le logit) du modèle.
"""

from __future__ import annotations

from pathlib import Path

import numpy
import pandas as pd
import pytest

from qc import explain
from qc_optimized import build_logistic_model


@pytest.fixture(scope="module")
def pipeline():
    """Un pipeline jouet où seule la première mesure décide."""
    graine = numpy.random.default_rng(42)
    X = graine.normal(size=(200, 4))
    y = (X[:, 0] > 0).astype(int)
    model = build_logistic_model(positive_weight=1.0)
    model.fit(X, y)
    return model


NOMS = ["sensor_000", "sensor_001", "sensor_002", "sensor_003"]


def test_la_mesure_qui_decide_arrive_en_tete(pipeline):
    """L'explication doit désigner la bonne variable. Sur ce modèle jouet, seule la
    première compte — si elle n'est pas en tête, le tri ou l'indexation est faux."""
    explication = explain.explain_row([2.5, 0.1, -0.3, 0.2], NOMS, model=pipeline)

    assert explication.contributions[0].nom == "sensor_000"


def test_les_contributions_et_le_biais_redonnent_la_sortie_du_modele(pipeline):
    """C'est LA propriété qui distingue une explication d'une intuition : rien n'a été
    oublié en route. Un graphique d'importance globale ne l'a pas.

    La sortie brute est le LOGIT — `decision_function` chez scikit-learn — pas la
    probabilité : les contributions s'additionnent avant la sigmoïde, comme les
    `pred_contribs` de l'ancien XGBoost s'additionnaient en marge.
    """
    mesures = [1.8, -0.4, 0.7, 0.1]
    explication = explain.explain_row(mesures, NOMS, model=pipeline)

    attendu = pipeline.decision_function(numpy.array([mesures]))[0]

    assert explication.total == pytest.approx(float(attendu), abs=1e-9)


def test_l_additivite_tient_sur_une_piece_reelle_du_modele_livre():
    """Revue du 29/07/2026, point 4 : la troncature au top-5 AVANT la construction de
    l'Explication faussait `total` de 32 % sur cette même pièce — et les tests
    passaient, parce que le pipeline jouet n'a que 4 mesures. Cette version-ci exerce
    le vrai artefact (40 mesures) avec le `top_n` d'affichage par défaut.

    Le logit attendu vient de `decision_function` de la seule régression logistique —
    le produit matriciel de scikit-learn, chemin indépendant de la somme par mesure de
    `total`. Le pipeline complet, picklé sous scikit-learn 1.2, ne sait pas rejouer
    `transform()` sous la version installée (voir le commentaire de `explain_row`)."""
    import warnings

    import joblib

    racine = Path(__file__).resolve().parent.parent
    artefact = racine / "data" / "model" / "model.joblib"
    if not artefact.exists():
        pytest.skip(
            "artefact local absent (cache data/model/, peuplé par `uv run qc invoke`) "
            "— test exercé sur les postes, pas en CI"
        )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = joblib.load(artefact)
    echantillon = pd.read_csv(racine / "data" / "sample.csv")
    mesures = echantillon.iloc[0].tolist()

    explication = explain.explain_row(mesures, list(echantillon.columns), model=model)

    ligne = numpy.array(mesures, dtype=float)
    imputee = numpy.where(
        numpy.isnan(ligne), model.named_steps["imputer"].statistics_, ligne
    )
    scaler = model.named_steps["scaler"]
    standardisee = (imputee - scaler.mean_) / scaler.scale_
    logit = model.named_steps["model"].decision_function(standardisee.reshape(1, -1))[0]

    assert len(explication.contributions) == len(echantillon.columns)
    assert explication.total == pytest.approx(float(logit), abs=1e-9)


def test_l_imputation_du_pipeline_est_rejouee_avant_la_decomposition(pipeline):
    """Une pièce avec une mesure manquante doit s'expliquer comme elle se prédit : le
    manquant passe par la médiane du train, pas par un zéro silencieux."""
    mesures = [1.8, float("nan"), 0.7, 0.1]
    explication = explain.explain_row(mesures, NOMS, model=pipeline)

    attendu = pipeline.decision_function(numpy.array([mesures]))[0]

    assert explication.total == pytest.approx(float(attendu), abs=1e-9)


def test_un_pipeline_inattendu_est_signale_pas_devine():
    """Un artefact d'avant la migration (un Booster XGBoost, un pickle quelconque) doit
    produire un message actionnable, pas un AttributeError au milieu du calcul."""
    with pytest.raises(explain.ExplainError) as exc:
        explain.explain_row([1.0], ["sensor_000"], model=object())

    assert "qc train" in str(exc.value)


def test_le_sens_de_la_contribution_est_explicite():
    """Un opérateur lit « vers NON CONFORME », pas un signe."""
    positive = explain.Contribution("sensor_000", 2.0, 0.8)
    negative = explain.Contribution("sensor_001", -1.0, -0.3)

    assert "NON CONFORME" in positive.sens
    assert "conforme" in negative.sens and "NON" not in negative.sens


def test_un_desaccord_entre_mesures_et_noms_est_signale(pipeline):
    with pytest.raises(explain.ExplainError) as exc:
        explain.explain_row([1.0, 2.0], NOMS, model=pipeline)

    assert "même échantillon" in str(exc.value)


def test_le_resume_nomme_les_capteurs_et_leur_poids(pipeline):
    resume = explain.explain_row(
        [2.5, 0.1, -0.3, 0.2], NOMS, indice=13, model=pipeline
    ).summary()

    assert "Pièce 13" in resume
    assert "sensor_000" in resume


def test_le_nombre_de_mesures_retenues_reste_lisible():
    """Au-delà de cinq lignes, un opérateur ne lit plus."""
    assert explain.TOP_N <= 5


def test_le_resume_tronque_mais_l_objet_garde_tout(pipeline):
    """La troncature est une affaire d'AFFICHAGE : summary() retient top_n lignes,
    l'Explication garde ses quatre contributions — c'est ce qui rend `total` exact."""
    explication = explain.explain_row([2.5, 0.1, -0.3, 0.2], NOMS, model=pipeline)

    assert len(explication.contributions) == 4
    resume = explication.summary(top_n=2)
    # 1 ligne de titre + 2 contributions
    assert len(resume.splitlines()) == 3
