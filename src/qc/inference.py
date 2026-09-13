"""J1 · SageMaker — déploiement de l'endpoint et invocation.

Ce module ne fait AUCUN affichage : il calcule, il renvoie. L'affichage est dans `qc.cli`.
C'est ce qui permet à l'agent Bedrock du J2 d'appeler `predict()` directement, sans
récupérer des `print` au milieu de sa trace de raisonnement.

Trois objets AWS, souvent confondus, et c'est le cœur du lab :

    Model            décrit QUOI servir — une image de conteneur, un artefact S3
    EndpointConfig   décrit COMMENT le servir — type d'instance, data capture
    Endpoint         l'URL vivante, celle qui coûte de l'argent

    from qc import inference
    endpoint = inference.deploy()
    inference.predict(rows)
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from botocore.exceptions import ClientError

from qc.config import DATA_DIR, config
from qc.training import (
    MODEL_PREFIX,
    SERVE_ENTRY_POINT,
    _upload_sourcedir,
    image_uri,
)

#: Une seule instance : six binômes en déploient chacun une, et l'endpoint est facturé
#: à la seconde tant qu'il tourne. C'est la ressource la plus chère du J1.
INSTANCE_TYPE = "ml.m5.large"
INSTANCE_COUNT = 1

#: 100 % des requêtes capturées. En production on échantillonnerait ; ici le J3 a besoin
#: de TOUT, sinon la comparaison à la baseline porte sur trop peu de lignes pour être
#: parlante en trois heures de lab.
CAPTURE_PERCENTAGE = 100
CAPTURE_PREFIX = "capture"


class InferenceError(RuntimeError):
    """Erreur de déploiement ou d'invocation, formulée pour être lue par un apprenant."""


@dataclass(frozen=True)
class Endpoint:
    """Un endpoint en service."""

    name: str
    model_name: str
    config_name: str
    artifact: str
    capture_uri: str

    def summary(self) -> str:
        return "\n".join(
            [
                f"endpoint  {self.name}",
                f"  modèle    {self.model_name}",
                f"  config    {self.config_name}",
                f"  artefact  {self.artifact}",
                f"  capture   {self.capture_uri}",
            ]
        )


@dataclass(frozen=True)
class Prediction:
    """Une ligne prédite. `score` est une probabilité, pas une classe."""

    index: int
    score: float

    def label(self, threshold: float) -> str:
        """Le seuil n'a PAS de valeur par défaut : 0,50 n'est un choix ni meilleur ni
        plus neutre qu'un autre, et le seuil réellement sélectionné (par F2 sur le
        train) vit dans l'artefact — `decision_threshold()` le lit."""
        return "NON CONFORME" if self.score >= threshold else "conforme"


def latest_artifact() -> str:
    """URI de l'artefact du dernier job d'entraînement RÉUSSI du groupe.

    On interroge SageMaker plutôt que de lister S3 : le préfixe `model/` contient aussi
    les artefacts des essais précédents, et rien dans un nom d'objet ne dit si le job
    correspondant a réussi.
    """
    # TODO-D1-01 — à écrire.
    raise NotImplementedError("TODO-D1-01")


#: Où les fichiers extraits de l'artefact sont mis en cache. Le télécharger à chaque
#: appel coûterait quelques secondes — sensible quand c'est un outil d'agent qui appelle.
MODEL_CACHE_DIR = DATA_DIR / "model"


def artifact_member(filename: str) -> Path:
    """Extrait UN fichier de l'artefact du dernier entraînement réussi, avec cache.

    L'artefact du job contient trois fichiers : `model.joblib` (le pipeline entraîné),
    `threshold.json` (le seuil F2 choisi sur le train) et `metrics.json`. L'endpoint n'en
    a besoin d'aucun — le conteneur les charge lui-même — mais le seuil de décision et
    l'explicabilité (J2) se lisent ici, côté client.
    """
    # TODO-D1-02 — à écrire.
    raise NotImplementedError("TODO-D1-02")


def decision_threshold() -> float:
    """Le seuil de décision sélectionné à l'entraînement (F2, hors-fold, sur le train).

    C'est LE changement de posture par rapport à l'ancien 0,50 implicite : le seuil est
    un livrable de l'entraînement, choisi sur des prédictions de validation, pas une
    constante universelle.
    """
    # TODO-D1-03 — à écrire.
    raise NotImplementedError("TODO-D1-03")


def _create_model(artifact: str) -> str:
    """Crée le Model : l'image d'inférence + l'artefact + le script de service.

    L'image est la même qu'à l'entraînement — le conteneur SKLearn officiel sait faire
    les deux — mais contrairement à un algorithme intégré, il faut lui dire QUOI exécuter
    pour servir : `SAGEMAKER_PROGRAM` nomme le script (model_fn/input_fn/predict_fn/
    output_fn), `SAGEMAKER_SUBMIT_DIRECTORY` pointe l'archive de code sur S3. C'est le
    pendant, en variables d'environnement, des deux hyperparamètres du job d'entraînement.
    """
    # TODO-D1-04 — à écrire.
    raise NotImplementedError("TODO-D1-04")


def _create_endpoint_config(model_name: str) -> tuple[str, str]:
    """Crée l'EndpointConfig avec la data capture. Renvoie (nom, URI de capture).

    La data capture n'est PAS une option de l'endpoint : elle se déclare ici, dans la
    configuration. On ne peut donc pas l'activer après coup sans créer une nouvelle
    configuration et mettre l'endpoint à jour — d'où l'insistance du lab à ne pas
    l'oublier, le J3 entier en dépend.
    """
    # TODO-D1-05 — à écrire.
    raise NotImplementedError("TODO-D1-05")


def deploy(artifact: str | None = None) -> Endpoint:
    """Crée Model, EndpointConfig et Endpoint. Rend la main sans attendre `InService`.

    Le nom de l'endpoint est FIXE — `qc-<TEAM_ID>-endpoint` — contrairement au modèle et
    à la configuration, qui sont horodatés. C'est délibéré : le groupe de logs précréé par
    le socle porte ce nom, et le J2 comme le J3 doivent pouvoir le retrouver sans le
    chercher. Si l'endpoint existe déjà, il est MIS À JOUR au lieu d'être recréé.
    """
    # TODO-D1-06 — à écrire.
    raise NotImplementedError("TODO-D1-06")


def wait(endpoint_name: str, poll_seconds: int = 20, on_tick=None) -> str:
    """Attend que l'endpoint soit `InService`. Renvoie son statut final.

    Lève `InferenceError` sur `Failed` : un endpoint en échec ne se répare pas, il se
    supprime et se recrée. Le message d'AWS est renvoyé tel quel — il nomme
    généralement la vraie cause, souvent un artefact illisible par le rôle.
    """
    # TODO-D1-07 — à écrire.
    raise NotImplementedError("TODO-D1-07")


def predict(rows: list[list[float]], endpoint_name: str | None = None) -> list[Prediction]:
    """Invoque l'endpoint sur des lignes de mesures et renvoie des probabilités.

    Le corps est du CSV SANS en-tête et SANS colonne cible — le conteneur attend
    exactement les mêmes colonnes, dans le même ordre, que celles vues à
    l'entraînement. Une colonne en trop ou en moins produit un 400 dont le message ne
    dit pas laquelle.

    Le conteneur renvoie une PROBABILITÉ (`predict_proba`, voir `serve_entry.py`), pas
    une classe. Le seuil appartient au métier : en contrôle qualité, laisser passer une
    pièce défectueuse ne coûte pas la même chose que d'en écarter une bonne — celui
    choisi à l'entraînement se lit par `decision_threshold()`.
    """
    # TODO-D1-08 — à écrire.
    raise NotImplementedError("TODO-D1-08")


def _parse_scores(body: str) -> list[Prediction]:
    """Découpe la réponse du conteneur — un score par ligne (`serve_entry.py`).

    Le découpage tolère aussi les virgules : c'était le séparateur que l'ancien
    conteneur XGBoost employait pour une ligne unique, et cette souplesse ne coûte
    rien tant que les scores restent des nombres nus.
    """
    # TODO-D1-09 — à écrire.
    raise NotImplementedError("TODO-D1-09")


def local_sample() -> Path:
    """Chemin de `sample.csv`, téléchargé depuis S3 s'il n'est pas là.

    UN SEUL endroit décide où trouver l'échantillon. C'est nécessaire parce que deux
    chemins de code en ont besoin — l'application et les outils de l'agent — et qu'ils ne
    s'exécutent pas dans le même contexte :

      - en local, le fichier existe, produit par `qc load` ;
      - dans le conteneur du J2, `data/` n'existe pas : `.dockerignore` l'exclut.

    Sans ce point unique, l'onglet « Assistant » ne fonctionnait que si l'onglet
    « Pièces » avait été ouvert d'abord — celui-ci téléchargeait le fichier au passage.
    Une dépendance invisible entre deux onglets, et une panne qui n'arrive qu'à certains
    binômes.
    """
    # TODO-D1-10 — à écrire.
    raise NotImplementedError("TODO-D1-10")


def read_sample(path: Path) -> tuple[list[str], list[list[float]]]:
    """Lit `sample.csv` — AVEC en-tête, contrairement à ce qu'attend l'endpoint.

    L'en-tête est là pour que l'apprenant puisse ouvrir le fichier et comprendre ce
    qu'il envoie ; c'est cette fonction qui le retire avant l'invocation.
    """
    # TODO-D1-11 — à écrire.
    raise NotImplementedError("TODO-D1-11")


def teardown(endpoint_name: str | None = None) -> list[str]:
    """Supprime l'endpoint et sa configuration. Renvoie ce qui a été supprimé.

    L'endpoint est la seule ressource du J1 facturée en continu. Cette fonction existe
    pour qu'on n'ait jamais à ouvrir la console pour l'éteindre le soir.

    Le Model n'est PAS supprimé : il ne coûte rien, et le garder permet de redéployer
    sans réentraîner.
    """
    # TODO-D1-12 — à écrire.
    raise NotImplementedError("TODO-D1-12")
