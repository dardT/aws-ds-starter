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
    sm = config.client("sagemaker")

    # Pagination obligatoire : l'API applique NameContains APRÈS le découpage en pages.
    # Une page peut donc revenir VIDE avec un NextToken alors que des jobs du groupe
    # existent plus loin — constaté le 06/08/2026 dès qu'un job `qc-gNN-exo-train-…`
    # (exercice du J1) devient le job le plus récent du compte.
    criteres = dict(
        NameContains=config.resource("train"),
        StatusEquals="Completed",
        SortBy="CreationTime",
        SortOrder="Descending",
    )
    dernier = None
    while True:
        page = sm.list_training_jobs(**criteres)
        if page["TrainingJobSummaries"]:
            dernier = page["TrainingJobSummaries"][0]
            break
        jeton = page.get("NextToken")
        if not jeton:
            break
        criteres["NextToken"] = jeton

    if dernier is None:
        raise InferenceError(
            "Aucun job d'entraînement réussi pour ce groupe.\n"
            "  Corriger : lancer `uv run qc train` d'abord."
        )

    described = sm.describe_training_job(TrainingJobName=dernier["TrainingJobName"])
    return described["ModelArtifacts"]["S3ModelArtifacts"]


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
    import tarfile
    import tempfile

    cache = MODEL_CACHE_DIR / filename
    if cache.is_file():
        return cache

    uri = latest_artifact()
    _, _, reste = uri.partition("s3://")
    bucket, _, cle = reste.partition("/")

    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar.gz") as archive:
        try:
            config.client("s3").download_file(bucket, cle, archive.name)
        except Exception as exc:  # noqa: BLE001
            raise InferenceError(
                f"Artefact du modèle introuvable : {uri}\n"
                "  Corriger : `uv run qc train` d'abord."
            ) from exc
        with tarfile.open(archive.name) as tar:
            # Tout extraire d'un coup : les trois fichiers viennent toujours ensemble,
            # et le prochain appel pour un autre membre sera un accès disque.
            for membre in tar.getmembers():
                if membre.isfile():
                    contenu = tar.extractfile(membre)
                    (MODEL_CACHE_DIR / Path(membre.name).name).write_bytes(contenu.read())

    if not cache.is_file():
        raise InferenceError(
            f"L'artefact {uri} ne contient pas {filename}.\n"
            "  L'entraînement vient-il d'un job antérieur à la migration vers la"
            " régression logistique ? Relancer `uv run qc train`."
        )
    return cache


def decision_threshold() -> float:
    """Le seuil de décision sélectionné à l'entraînement (F2, hors-fold, sur le train).

    C'est LE changement de posture par rapport à l'ancien 0,50 implicite : le seuil est
    un livrable de l'entraînement, choisi sur des prédictions de validation, pas une
    constante universelle.
    """
    import json

    return float(json.loads(artifact_member("threshold.json").read_text())["threshold"])


def _create_model(artifact: str) -> str:
    """Crée le Model : l'image d'inférence + l'artefact + le script de service.

    L'image est la même qu'à l'entraînement — le conteneur SKLearn officiel sait faire
    les deux — mais contrairement à un algorithme intégré, il faut lui dire QUOI exécuter
    pour servir : `SAGEMAKER_PROGRAM` nomme le script (model_fn/input_fn/predict_fn/
    output_fn), `SAGEMAKER_SUBMIT_DIRECTORY` pointe l'archive de code sur S3. C'est le
    pendant, en variables d'environnement, des deux hyperparamètres du job d'entraînement.
    """
    name = config.timestamped_name("model")
    try:
        config.client("sagemaker").create_model(
            ModelName=name,
            PrimaryContainer={
                "Image": image_uri(),
                "ModelDataUrl": artifact,
                "Environment": {
                    "SAGEMAKER_PROGRAM": SERVE_ENTRY_POINT,
                    "SAGEMAKER_SUBMIT_DIRECTORY": _upload_sourcedir(),
                    "SAGEMAKER_REGION": config.region,
                },
            },
            ExecutionRoleArn=config.sagemaker_role_arn,
            Tags=config.tags_list,
        )
    except ClientError as exc:
        raise InferenceError(
            f"Création du modèle refusée ({exc.response['Error']['Code']}) :"
            f" {exc.response['Error']['Message']}"
        ) from exc
    return name


def _create_endpoint_config(model_name: str) -> tuple[str, str]:
    """Crée l'EndpointConfig avec la data capture. Renvoie (nom, URI de capture).

    La data capture n'est PAS une option de l'endpoint : elle se déclare ici, dans la
    configuration. On ne peut donc pas l'activer après coup sans créer une nouvelle
    configuration et mettre l'endpoint à jour — d'où l'insistance du lab à ne pas
    l'oublier, le J3 entier en dépend.
    """
    name = config.timestamped_name("endpoint-config")
    capture_uri = f"s3://{config.s3_bucket}/{CAPTURE_PREFIX}/"
    try:
        config.client("sagemaker").create_endpoint_config(
            EndpointConfigName=name,
            ProductionVariants=[
                {
                    "VariantName": "AllTraffic",
                    "ModelName": model_name,
                    "InitialInstanceCount": INSTANCE_COUNT,
                    "InstanceType": INSTANCE_TYPE,
                    "InitialVariantWeight": 1.0,
                }
            ],
            DataCaptureConfig={
                "EnableCapture": True,
                "InitialSamplingPercentage": CAPTURE_PERCENTAGE,
                "DestinationS3Uri": capture_uri,
                # Les DEUX. Capturer seulement l'entrée priverait le J3 de la
                # distribution des scores, qui est la dérive la plus visible.
                "CaptureOptions": [
                    {"CaptureMode": "Input"},
                    {"CaptureMode": "Output"},
                ],
                "CaptureContentTypeHeader": {"CsvContentTypes": ["text/csv"]},
            },
            Tags=config.tags_list,
        )
    except ClientError as exc:
        raise InferenceError(
            f"Création de la configuration refusée ({exc.response['Error']['Code']}) :"
            f" {exc.response['Error']['Message']}"
        ) from exc
    return name, capture_uri


def deploy(artifact: str | None = None) -> Endpoint:
    """Crée Model, EndpointConfig et Endpoint. Rend la main sans attendre `InService`.

    Le nom de l'endpoint est FIXE — `qc-<TEAM_ID>-endpoint` — contrairement au modèle et
    à la configuration, qui sont horodatés. C'est délibéré : le groupe de logs précréé par
    le socle porte ce nom, et le J2 comme le J3 doivent pouvoir le retrouver sans le
    chercher. Si l'endpoint existe déjà, il est MIS À JOUR au lieu d'être recréé.
    """
    artifact = artifact or latest_artifact()
    model_name = _create_model(artifact)
    config_name, capture_uri = _create_endpoint_config(model_name)

    sm = config.client("sagemaker")
    name = config.endpoint_name
    try:
        sm.create_endpoint(
            EndpointName=name, EndpointConfigName=config_name, Tags=config.tags_list
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ValidationException" and "already exist" in (
            exc.response["Error"]["Message"]
        ):
            # Deuxième passage : on bascule l'endpoint existant sur la nouvelle
            # configuration. SageMaker le fait sans coupure de service.
            sm.update_endpoint(EndpointName=name, EndpointConfigName=config_name)
        else:
            raise InferenceError(
                f"Création de l'endpoint refusée ({exc.response['Error']['Code']}) :"
                f" {exc.response['Error']['Message']}"
            ) from exc

    return Endpoint(
        name=name,
        model_name=model_name,
        config_name=config_name,
        artifact=artifact,
        capture_uri=capture_uri,
    )


def wait(endpoint_name: str, poll_seconds: int = 20, on_tick=None) -> str:
    """Attend que l'endpoint soit `InService`. Renvoie son statut final.

    Lève `InferenceError` sur `Failed` : un endpoint en échec ne se répare pas, il se
    supprime et se recrée. Le message d'AWS est renvoyé tel quel — il nomme
    généralement la vraie cause, souvent un artefact illisible par le rôle.
    """
    import time

    sm = config.client("sagemaker")
    started = time.monotonic()

    while True:
        described = sm.describe_endpoint(EndpointName=endpoint_name)
        status = described["EndpointStatus"]
        if status in ("InService", "Failed"):
            break
        if on_tick is not None:
            on_tick(status, int(time.monotonic() - started))
        time.sleep(poll_seconds)

    if status == "Failed":
        raise InferenceError(
            f"L'endpoint {endpoint_name} a échoué.\n"
            f"  {described.get('FailureReason', 'aucune raison fournie')}\n"
            "  Un endpoint en échec ne se répare pas : `uv run qc teardown` puis"
            " redéployer."
        )
    return status


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
    name = endpoint_name or config.endpoint_name

    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)

    try:
        response = config.client("sagemaker-runtime").invoke_endpoint(
            EndpointName=name, ContentType="text/csv", Body=buffer.getvalue()
        )
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ValidationError":
            raise InferenceError(
                f"L'endpoint {name} n'existe pas ou n'est pas en service.\n"
                "  Corriger : `uv run qc deploy`."
            ) from exc
        if code == "ModelError":
            raise InferenceError(
                "Le conteneur a rejeté la requête.\n"
                f"  {exc.response['Error']['Message']}\n"
                "  Cause la plus fréquente : un nombre de colonnes différent de celui"
                " vu à l'entraînement, ou une colonne cible laissée dans les données."
            ) from exc
        raise InferenceError(f"Invocation refusée ({code}).") from exc

    body = response["Body"].read().decode("utf-8").strip()
    return _parse_scores(body)


def _parse_scores(body: str) -> list[Prediction]:
    """Découpe la réponse du conteneur — un score par ligne (`serve_entry.py`).

    Le découpage tolère aussi les virgules : c'était le séparateur que l'ancien
    conteneur XGBoost employait pour une ligne unique, et cette souplesse ne coûte
    rien tant que les scores restent des nombres nus.
    """
    valeurs = [v for v in body.replace(",", "\n").split("\n") if v.strip()]
    return [Prediction(index=i, score=float(v)) for i, v in enumerate(valeurs)]


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
    chemin = DATA_DIR / "sample.csv"
    if not chemin.is_file():
        from qc import storage

        storage.download("sample.csv", chemin)
    return chemin


def read_sample(path: Path) -> tuple[list[str], list[list[float]]]:
    """Lit `sample.csv` — AVEC en-tête, contrairement à ce qu'attend l'endpoint.

    L'en-tête est là pour que l'apprenant puisse ouvrir le fichier et comprendre ce
    qu'il envoie ; c'est cette fonction qui le retire avant l'invocation.
    """
    if not path.is_file():
        raise InferenceError(
            f"{path} est absent.\n  Corriger : lancer `uv run qc load` d'abord."
        )
    with path.open(newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        rows = [[float(v) for v in row] for row in reader if row]
    return header, rows


def teardown(endpoint_name: str | None = None) -> list[str]:
    """Supprime l'endpoint et sa configuration. Renvoie ce qui a été supprimé.

    L'endpoint est la seule ressource du J1 facturée en continu. Cette fonction existe
    pour qu'on n'ait jamais à ouvrir la console pour l'éteindre le soir.

    Le Model n'est PAS supprimé : il ne coûte rien, et le garder permet de redéployer
    sans réentraîner.
    """
    name = endpoint_name or config.endpoint_name
    sm = config.client("sagemaker")
    supprimes: list[str] = []

    try:
        described = sm.describe_endpoint(EndpointName=name)
    except ClientError:
        return supprimes

    sm.delete_endpoint(EndpointName=name)
    supprimes.append(f"endpoint {name}")

    config_name = described.get("EndpointConfigName")
    if config_name:
        try:
            sm.delete_endpoint_config(EndpointConfigName=config_name)
            supprimes.append(f"configuration {config_name}")
        except ClientError:
            pass

    return supprimes
