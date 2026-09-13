"""Garanties de reproductibilité du jeu SECOM.

Ces tests ne valident pas la qualité du modèle : ils valident que **tous les binômes
travaillent sur exactement les mêmes données**. C'est la condition sans laquelle les
valeurs SHAP du J2 et la baseline de dérive du J3 ne sont pas comparables d'un groupe à
l'autre, et sans laquelle aucun `make check-dayN` n'est possible.

Le téléchargement UCI est fait une seule fois pour toute la session (fixture de portée
`session`) : le relancer par test ferait quatre allers-retours réseau pour rien.
"""

from __future__ import annotations

import json
import pandas as pd


import pytest

from qc import secom


@pytest.fixture(scope="session")
def dataset() -> secom.Dataset:
    return secom.load()


@pytest.fixture(scope="session")
def features(dataset: secom.Dataset) -> list[str]:
    return secom.frozen_features(dataset)


def test_le_jeu_a_la_forme_annoncee(dataset: secom.Dataset) -> None:
    """1567 pièces et 590 capteurs — les chiffres cités en séance et dans les slides."""
    assert dataset.X.shape == (1567, 590)
    assert dataset.y.isin({0, 1}).all()
    assert dataset.timestamp.notna().all()


def test_le_desequilibre_est_bien_le_probleme_metier(dataset: secom.Dataset) -> None:
    """~6,6 % de défauts. Si ce taux dérive, tout le fil pédagogique tombe."""
    assert dataset.fail_rate == pytest.approx(0.066, abs=0.001)


def test_la_liste_de_variables_livree_fait_foi(features: list[str]) -> None:
    """Le fichier committé est la référence : il ne doit jamais être régénéré en séance."""
    livree = json.loads(secom.FEATURES_FILE.read_text())["features"]
    assert features == livree
    assert len(features) == secom.N_FEATURES


def test_la_selection_est_deterministe(dataset: secom.Dataset) -> None:
    """Deux appels donnent la même liste — aucun tirage aléatoire dans la règle."""
    assert secom.select_features(dataset) == secom.select_features(dataset)


def test_la_regle_reproduit_la_liste_livree(
    dataset: secom.Dataset, features: list[str]
) -> None:
    """La liste livrée est bien le produit de la règle documentée, pas un choix arbitraire.

    Si ce test tombe, c'est que le jeu amont a changé : `features.json` reste valable
    (il fait foi) mais la règle décrite dans le fichier ne le reproduit plus, et il faut
    corriger la documentation plutôt que le fichier.
    """
    assert secom.select_features(dataset) == features


def test_le_decoupage_est_stratifie_et_reproductible(
    dataset: secom.Dataset, features: list[str]
) -> None:
    a = secom.split(dataset, features)
    b = secom.split(dataset, features)

    assert a.X_train.index.equals(b.X_train.index)
    assert len(a.X_train) == 1175
    assert len(a.X_test) == 392

    # Stratification : sans elle, le test pourrait contenir trop peu de défauts pour
    # qu'un rappel soit interprétable.
    assert a.y_train.mean() == pytest.approx(dataset.fail_rate, abs=0.005)
    assert a.y_test.mean() == pytest.approx(dataset.fail_rate, abs=0.005)


def test_aucune_valeur_manquante_ne_subsiste(
    dataset: secom.Dataset, features: list[str]
) -> None:
    """Le conteneur XGBoost de SageMaker refuse les cellules vides du CSV."""
    result = secom.split(dataset, features)
    assert not result.X_train.isna().any().any()
    assert not result.X_test.isna().any().any()



def test_l_imputation_du_test_utilise_la_mediane_du_train() -> None:
    """Le test ne doit contribuer à aucune statistique de prétraitement."""
    X = pd.DataFrame({"sensor_000": range(20)}, dtype=float)
    y = pd.Series([0] * 10 + [1] * 10, name="label")
    dataset = secom.Dataset(
        X=X,
        y=y,
        timestamp=pd.Series(pd.date_range("2024-01-01", periods=len(X)), name="timestamp"),
    )

    first_split = secom.split(dataset, ["sensor_000"])
    missing_index = first_split.X_test.index[0]
    dataset.X.loc[missing_index, "sensor_000"] = float("nan")

    result = secom.split(dataset, ["sensor_000"])
    expected_median = dataset.X.loc[result.X_train.index, "sensor_000"].median()

    assert result.X_test.loc[missing_index, "sensor_000"] == expected_median

def test_le_csv_respecte_le_format_sagemaker(
    tmp_path, dataset: secom.Dataset, features: list[str]
) -> None:
    """Sans en-tête, cible en PREMIÈRE colonne. Erreur classique et message peu parlant."""
    import pandas as pd

    secom.split(dataset, features).write_csv(tmp_path)

    train = pd.read_csv(tmp_path / "train.csv", header=None)
    assert train.shape == (1175, secom.N_FEATURES + 1)
    assert train[0].isin({0, 1}).all()

    # L'échantillon, lui, garde son en-tête : il est fait pour être lu à l'œil.
    assert pd.read_csv(tmp_path / "sample.csv").columns.tolist() == features
