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
        return float(self.y.mean())


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
        dest.mkdir(parents=True, exist_ok=True)

        for name, X, y in (
            ("train", self.X_train, self.y_train),
            ("test", self.X_test, self.y_test),
        ):
            frame = pd.concat(
                [y.reset_index(drop=True), X.reset_index(drop=True)], axis=1
            )
            frame.to_csv(dest / f"{name}.csv", index=False, header=False)

        # Échantillon lisible, conservé pour l'étape `invoke` et pour les tests : mêmes
        # colonnes, avec en-tête cette fois, pour pouvoir l'inspecter à l'œil.
        self.X_test.head(20).to_csv(dest / "sample.csv", index=False)

        # Jeu horodaté complet, conservé pour le J3. Il permet une dérive TEMPORELLE
        # réelle — comparer les premières semaines de production aux dernières — plutôt
        # qu'une dérive injectée artificiellement, toujours moins convaincante en démo.
        self.timeline.to_csv(dest / "timeline.csv", index=False)

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
    from ucimlrepo import fetch_ucirepo

    original = fetch_ucirepo(id=UCI_DATASET_ID).data.original
    if original is None:
        raise SystemExit(
            "ucimlrepo n'a renvoyé aucune donnée. Vérifier l'accès à "
            "archive.ics.uci.edu, ou utiliser la copie de secours du bucket de promo."
        )

    sensor_cols = [c for c in original.columns if c.startswith("Attribute")]

    # Normalisation des noms en sensor_000, sensor_001, … Les intitulés UCI ne sont pas
    # garantis stables d'une version à l'autre ; la liste figée dans features.json reste
    # ainsi valable même si l'amont renomme ses colonnes.
    X = original[sensor_cols].copy()
    X.columns = [f"sensor_{i:03d}" for i in range(len(sensor_cols))]
    X = X.apply(pd.to_numeric, errors="coerce")

    # -1 = conforme, 1 = non conforme. On bascule en 0/1 : c'est la convention de toute
    # la chaîne (poids de classe, seuil F2, métriques), et « 1 = défaut » se lit mieux
    # qu'un -1 pour « tout va bien ».
    y = (original["class"] == 1).astype(int).rename("label")

    # L'horodatage n'entre pas dans le modèle, mais on le conserve : il ouvre la porte à
    # un scénario de dérive TEMPORELLE au J3.
    #
    # Le fichier UCI mélange DEUX formats — '19/07/2008 11:55:00' et '1/8/2008 2:02',
    # sans zéro de tête ni secondes. Un format fixe assorti de errors="coerce" convertit
    # silencieusement 604 lignes sur 1567 en NaT ; la dérive temporelle du J3 se
    # calculerait alors sur 62 % du jeu, sans que rien ne le signale.
    #
    # dayfirst=True lève l'ambiguïté de '1/8/2008' : le jeu couvre juillet à octobre 2008,
    # donc le 1er août, jamais le 8 janvier.
    timestamp = pd.to_datetime(
        original["timestamp"], format="mixed", dayfirst=True
    ).rename("timestamp")

    return Dataset(X=X, y=y, timestamp=timestamp)


def select_features(dataset: Dataset) -> list[str]:
    """Sélectionne N_FEATURES colonnes par une règle STRICTEMENT déterministe.

    Trois filtres successifs, puis un classement. Aucun tirage aléatoire : deux
    exécutions sur les mêmes données produisent forcément la même liste.
    """
    X, y = dataset.X, dataset.y

    # 1. Écarter les colonnes trop lacunaires : imputer 40 % d'une colonne revient à
    #    inventer la majorité de l'information.
    kept = X.columns[X.isna().mean() <= MAX_MISSING_RATIO]

    # 2. Écarter les colonnes constantes : un capteur qui ne varie jamais n'apporte
    #    aucune information et fait diviser par zéro à la standardisation.
    variances = X[kept].var(numeric_only=True)
    kept = variances[variances > 0].index

    # 3. Classer par corrélation absolue avec l'étiquette. Critère simple, explicable en
    #    séance, et surtout parfaitement reproductible.
    imputed = X[kept].fillna(X[kept].median())
    correlations = imputed.corrwith(y).abs().fillna(0.0)

    # Départage par nom de colonne en cas d'égalité : sans cela, l'ordre dépendrait de
    # l'implémentation du tri et pourrait varier d'une version de pandas à l'autre.
    ranked = sorted(correlations.items(), key=lambda kv: (-kv[1], kv[0]))
    return [name for name, _ in ranked[:N_FEATURES]]


def frozen_features(dataset: Dataset) -> list[str]:
    """Liste des variables retenues. Le fichier livré fait foi ; sinon on le génère."""
    if FEATURES_FILE.exists():
        features = json.loads(FEATURES_FILE.read_text())["features"]
        missing = [f for f in features if f not in dataset.X.columns]
        if missing:
            raise SystemExit(
                f"{FEATURES_FILE.name} référence {len(missing)} colonnes absentes du jeu "
                f"téléchargé (ex. {missing[:3]}).\n"
                "  Le jeu amont a probablement changé. Supprimer le fichier pour le "
                "régénérer — en sachant que cela invalide les baselines du J3."
            )
        return features

    features = select_features(dataset)
    FEATURES_FILE.write_text(
        json.dumps(
            {
                "_comment": (
                    "Liste FIGÉE des variables. Générée une fois, puis committée. "
                    "La modifier invalide les valeurs SHAP du J2 et la baseline du J3."
                ),
                "n_features": len(features),
                "selection_rule": (
                    f"valeurs manquantes <= {MAX_MISSING_RATIO}, variance > 0, "
                    "puis corrélation absolue avec l'étiquette, égalités départagées "
                    "par nom de colonne"
                ),
                "features": features,
            },
            indent=2,
        )
        + "\n"
    )
    return features


def split(dataset: Dataset, features: list[str]) -> Split:
    """Découpe train/test stratifié et impute sans fuite de données."""
    frame = dataset.X[features].copy()

    X_train, X_test, y_train, y_test = train_test_split(
        frame,
        dataset.y,
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
        # Indispensable ici : sans elle, le test pourrait contenir trop peu de
        # défauts pour rendre toute évaluation du rappel interprétable.
        stratify=dataset.y,
    )

    # Les statistiques de prétraitement constituent une information apprise : la médiane
    # est ajustée sur le train, puis appliquée telle quelle au test. Inclure le test ici
    # ferait fuiter sa distribution dans l'entraînement.
    train_medians = X_train.median()
    X_train = X_train.fillna(train_medians)
    X_test = X_test.fillna(train_medians)

    timeline = pd.concat(
        [
            dataset.timestamp.reset_index(drop=True),
            dataset.y.reset_index(drop=True),
            frame.reset_index(drop=True),
        ],
        axis=1,
    ).sort_values("timestamp")

    return Split(
        X_train=X_train,
        X_test=X_test,
        y_train=y_train,
        y_test=y_test,
        features=features,
        timeline=timeline,
    )
