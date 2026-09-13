"""Jeu SECOM — téléchargement, sélection des variables, découpage.

Cas d'usage : contrôle qualité de pièces en production. Classification binaire à partir
des mesures capteurs du procédé — la pièce est-elle conforme, ou non conforme ?

Le jeu brut compte 1567 pièces, 590 mesures capteurs et 104 défauts, soit environ 6,6 %.
Ce déséquilibre est le cœur du problème métier : un modèle qui prédit « conforme » pour
tout obtient 93 % d'exactitude et ne détecte aucun défaut.

Aucune donnée SECOM n'est committée (§13) : le jeu est téléchargé à l'exécution.

REPRODUCTIBILITÉ — la raison d'être de ce module
    La liste des colonnes retenues est figée dans `features.json`, livré avec le paquet,
    et le découpage utilise un seed fixe. Sans cela chaque binôme obtiendrait un modèle
    différent, les valeurs SHAP du J2 et la baseline de dérive du J3 divergeraient, et
    aucune vérification automatique ne serait possible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

FEATURES_FILE = Path(__file__).resolve().parent / "features.json"

# --- Paramètres de l'adaptation pédagogique (§3) ------------------------------------
# Figés volontairement : ce sont eux qui garantissent que tous les binômes travaillent
# sur exactement le même jeu.
UCI_DATASET_ID = 179          # UCI SECOM
N_FEATURES = 40               # « ~40 features » (§3)
MAX_MISSING_RATIO = 0.30      # au-delà, la colonne est écartée
TEST_SIZE = 0.25
RANDOM_STATE = 42             # seed fixe — ne jamais rendre configurable


@dataclass(frozen=True)
class Dataset:
    """Jeu SECOM brut, normalisé mais ni filtré ni découpé."""

    X: pd.DataFrame          # sensor_000 … sensor_589
    y: pd.Series             # 0 = conforme, 1 = non conforme
    timestamp: pd.Series     # horodatage de la pièce

    @property
    def fail_rate(self) -> float:
        # TODO-D1-13 — à écrire.
        raise NotImplementedError("TODO-D1-13")


@dataclass(frozen=True)
class Split:
    """Découpage train/test prêt à écrire, plus la chronologie conservée pour le J3."""

    X_train: pd.DataFrame
    X_test: pd.DataFrame
    y_train: pd.Series
    y_test: pd.Series
    features: list[str]
    timeline: pd.DataFrame

    def write_csv(self, dest: Path) -> None:
        """Écrit les fichiers attendus par les étapes suivantes.

        Format du contrat d'entraînement : PAS d'en-tête, et la cible en PREMIÈRE
        colonne. Hérité du conteneur XGBoost d'origine et conservé tel quel par le
        script SKLearn (`model_assets/train_entry.py`) — un format qui change casserait
        la data capture et la baseline du J3. Un CSV avec en-tête reste l'erreur
        classique : le job échoue sur un message peu parlant.
        """
        # TODO-D1-14 — à écrire.
        raise NotImplementedError("TODO-D1-14")

    def summary(self) -> str:
        lines = []
        for name, X, y in (
            ("train.csv", self.X_train, self.y_train),
            ("test.csv", self.X_test, self.y_test),
        ):
            n_fail = int(y.sum())
            lines.append(
                f"  {name:<10} {len(X):>5} lignes, {n_fail:>3} défauts "
                f"({n_fail / len(y):.1%})"
            )
        return "\n".join(lines)


def load() -> Dataset:
    """Télécharge SECOM depuis le dépôt UCI et normalise les colonnes.

    ATTENTION — le programme (§3) donne ce snippet :

        X = secom.data.features ; y = secom.data.targets

    Il ne fonctionne pas pour SECOM : `features` et `targets` valent tous les deux
    `None`. Le dépôt UCI ne déclare pas les rôles des colonnes pour ce jeu, et
    `ucimlrepo` ne remplit alors que `data.original` :

        data.original   (1567, 592)
          class         -1 = conforme, 1 = non conforme
          timestamp     '19/07/2008 11:55:00'
          Attribute 1 … Attribute 590

    On extrait donc nous-mêmes les trois blocs.
    """
    # TODO-D1-15 — à écrire.
    raise NotImplementedError("TODO-D1-15")


def select_features(dataset: Dataset) -> list[str]:
    """Sélectionne N_FEATURES colonnes par une règle STRICTEMENT déterministe.

    Trois filtres successifs, puis un classement. Aucun tirage aléatoire : deux
    exécutions sur les mêmes données produisent forcément la même liste.
    """
    # TODO-D1-16 — à écrire.
    raise NotImplementedError("TODO-D1-16")


def frozen_features(dataset: Dataset) -> list[str]:
    """Liste des variables retenues. Le fichier livré fait foi ; sinon on le génère."""
    # TODO-D1-17 — à écrire.
    raise NotImplementedError("TODO-D1-17")


def split(dataset: Dataset, features: list[str]) -> Split:
    """Découpe train/test stratifié et impute sans fuite de données."""
    # TODO-D1-18 — à écrire.
    raise NotImplementedError("TODO-D1-18")
