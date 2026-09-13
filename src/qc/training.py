"""J1 · SageMaker — job d'entraînement de la régression logistique régularisée.

Ce module ne fait AUCUN affichage : il calcule, il renvoie. L'affichage est dans `qc.cli`.

Tout passe par **boto3**, pas par le SDK `sagemaker` — même choix que pour l'algorithme
intégré XGBoost qu'il remplace (décision D-optim, `docs/01-decisions.md`) : l'API brute
montre à l'apprenant ce qui part réellement sur le réseau, et le SDK v3 installé ici
(`sagemaker>=3`) construit ses jobs par un mécanisme de « drivers » différent du contrat
classique `sagemaker_program`/`sagemaker_submit_directory` que ce module utilise — les
deux ne se mélangent pas, mieux vaut ne dépendre d'aucun.

Le conteneur **SKLearn officiel** de SageMaker (pas d'algorithme intégré pour une
régression logistique à imputation/standardisation personnalisées) exécute un script
qu'on lui fournit — `model_assets/train_entry.py` — packagé avec une copie de
`qc_optimized.secom` dans une archive déposée sur S3 avant la soumission du job.

    from qc import training
    job = training.submit(train_uri, validation_uri)
    result = training.wait(job.name)
    result.model_artifact       # s3://…/model/qc-g01-train-…/output/model.tar.gz
"""

from __future__ import annotations

import importlib.resources
import io
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

from botocore.exceptions import ClientError

from qc.config import config

#: Compte propriétaire des images officielles SKLearn, PAR RÉGION. Comme pour XGBoost
#: (voir l'ancienne valeur ci-dessous), aucune API ne le découvre. Vérifié le 29/07/2026
#: par un `docker pull` réel contre le compte du groupe g01 — le registre générique
#: `749696950732`, celui de l'ancien algorithme intégré Linear Learner en eu-west-3, est
#: MORT (image introuvable, tout le compte refuse `ecr:BatchGetImage`). `659782779980`
#: est le même compte que celui d'XGBoost, et fonctionne pour les DEUX.
SKLEARN_REGISTRY = {"eu-west-3": "659782779980"}

SKLEARN_VERSION = "1.2-1"

#: ml.m5.large suffit très largement : 1175 lignes, 40 variables, une régression
#: logistique. Une instance plus grosse ne raccourcirait pas le job — le temps est
#: dominé par le démarrage du conteneur — mais multiplierait la facture par six binômes.
INSTANCE_TYPE = "ml.m5.large"

#: Garde-fou. Un job qui déraille est tué au bout de 30 minutes plutôt que de tourner
#: jusqu'à la limite par défaut, qui est de 24 heures.
MAX_RUNTIME_SECONDS = 1800

#: Le préfixe où SageMaker écrit l'artefact. Déclaré aussi dans le socle Terraform et
#: dans `config.S3_PREFIXES` — les trois doivent rester alignés.
MODEL_PREFIX = "model"

#: Sous-préfixe du code embarqué (script d'entraînement + de service). Un seul objet,
#: réécrit à chaque `submit`/`deploy` : c'est du code, pas une donnée versionnée.
CODE_KEY = f"{MODEL_PREFIX}/code/sourcedir.tar.gz"

TRAIN_ENTRY_POINT = "train_entry.py"
SERVE_ENTRY_POINT = "serve_entry.py"

#: Motifs lus dans les logs CloudWatch du job pour peupler `Result.metrics` — le
#: mécanisme `MetricDefinitions` de SageMaker, équivalent à ce que l'algorithme intégré
#: XGBoost publiait nativement pour `train:auc`/`validation:auc`.
METRIC_DEFINITIONS = [
    {"Name": f"validation:{nom}", "Regex": rf"validation:{nom} = ([0-9\.]+)"}
    for nom in ("average_precision", "roc_auc", "recall", "precision")
]

#: Fichiers embarqués dans l'archive de code, dans `src/qc/model_assets/`. `secom_model.py`
#: est une COPIE de `qc_optimized/secom.py`, prise à l'exécution : le conteneur ne
#: contient pas ce dépôt, seulement pandas / scikit-learn / joblib.
_MODEL_ASSETS = Path(__file__).parent / "model_assets"


class TrainingError(RuntimeError):
    """Erreur d'entraînement, formulée pour être lue par un apprenant."""


@dataclass(frozen=True)
class Job:
    """Un job soumis. Le nom est horodaté : AWS refuse de réutiliser celui d'un job
    supprimé, donc un nom fixe ferait échouer la deuxième exécution du binôme."""

    name: str
    train_input: str
    validation_input: str
    output_path: str


@dataclass(frozen=True)
class Result:
    """Issue d'un job terminé."""

    name: str
    status: str
    seconds: int
    model_artifact: str
    metrics: dict[str, float]
    failure: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "Completed"

    def summary(self) -> str:
        if not self.ok:
            return f"{self.name} : {self.status}\n  {self.failure}"
        lines = [f"{self.name} terminé en {self.seconds // 60} min {self.seconds % 60} s"]
        for k, v in sorted(self.metrics.items()):
            lines.append(f"  {k:28s} {v:.4f}")
        lines.append(f"  artefact : {self.model_artifact}")
        return "\n".join(lines)


def image_uri() -> str:
    """URI de l'image SKLearn pour la région du groupe."""
    account = SKLEARN_REGISTRY.get(config.region)
    if account is None:
        raise TrainingError(
            f"Aucun compte d'image SKLearn connu pour {config.region}.\n"
            "  Corriger : compléter SKLEARN_REGISTRY dans qc/training.py, et VÉRIFIER"
            " par un `docker pull` réel avant de faire confiance à une table trouvée en"
            " ligne — voir le commentaire au-dessus de SKLEARN_REGISTRY."
        )
    return (
        f"{account}.dkr.ecr.{config.region}.amazonaws.com/"
        f"sagemaker-scikit-learn:{SKLEARN_VERSION}-cpu-py3"
    )


def _sourcedir_bytes() -> bytes:
    """Construit l'archive de code (script d'entraînement + de service + le module de
    modélisation) en mémoire, sans rien écrire sur disque."""
    secom_source = importlib.resources.files("qc_optimized").joinpath("secom.py").read_bytes()

    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode="w:gz") as archive:
        for nom, contenu in (
            (TRAIN_ENTRY_POINT, (_MODEL_ASSETS / TRAIN_ENTRY_POINT).read_bytes()),
            (SERVE_ENTRY_POINT, (_MODEL_ASSETS / SERVE_ENTRY_POINT).read_bytes()),
            ("secom_model.py", secom_source),
        ):
            info = tarfile.TarInfo(name=nom)
            info.size = len(contenu)
            archive.addfile(info, io.BytesIO(contenu))
    return tampon.getvalue()


def _upload_sourcedir() -> str:
    """Dépose l'archive de code sur S3. Renvoie son URI.

    Un seul objet, réécrit à chaque appel : contrairement à l'artefact d'un job (qui doit
    rester rejouable), le code est celui qui tourne MAINTENANT — pas besoin d'historique.
    """
    uri = f"s3://{config.s3_bucket}/{CODE_KEY}"
    config.client("s3").put_object(
        Bucket=config.s3_bucket,
        Key=CODE_KEY,
        Body=_sourcedir_bytes(),
        ServerSideEncryption="AES256",
        Tagging="&".join(f"{k}={v}" for k, v in config.tags.items()),
    )
    return uri


def submit(train_input: str, validation_input: str) -> Job:
    """Soumet le job et rend la main immédiatement.

    Deux canaux, pas un seul. Le canal `validation` est ce qui produit les métriques
    `validation:*` — sans lui le job s'entraîne sans évaluation, et la baseline de dérive
    du J3 n'a plus rien à quoi se comparer. Le poids du déséquilibre et le seuil de
    décision ne sont PAS passés en hyperparamètre : le script les calcule lui-même depuis
    le canal `train`, exactement comme le notebook de validation.
    """
    name = config.timestamped_name("train")
    output_path = f"s3://{config.s3_bucket}/{MODEL_PREFIX}/"
    sourcedir_uri = _upload_sourcedir()

    def channel(label: str, uri: str) -> dict:
        return {
            "ChannelName": label,
            "ContentType": "text/csv",
            "DataSource": {
                "S3DataSource": {
                    # S3Prefix : SageMaker lit TOUT ce qui se trouve sous l'URI. C'est
                    # pourquoi `storage.py` isole train.csv et test.csv dans leur propre
                    # sous-préfixe.
                    "S3DataType": "S3Prefix",
                    "S3Uri": uri,
                    "S3DataDistributionType": "FullyReplicated",
                }
            },
        }

    try:
        config.client("sagemaker").create_training_job(
            TrainingJobName=name,
            AlgorithmSpecification={
                "TrainingImage": image_uri(),
                "TrainingInputMode": "File",
                "MetricDefinitions": METRIC_DEFINITIONS,
            },
            RoleArn=config.sagemaker_role_arn,
            InputDataConfig=[
                channel("train", train_input),
                channel("validation", validation_input),
            ],
            OutputDataConfig={"S3OutputPath": output_path},
            ResourceConfig={
                "InstanceType": INSTANCE_TYPE,
                "InstanceCount": 1,
                "VolumeSizeInGB": 5,
            },
            StoppingCondition={"MaxRuntimeInSeconds": MAX_RUNTIME_SECONDS},
            # Contrat classique du sagemaker-training-toolkit, DÉJÀ présent dans le
            # conteneur SKLearn officiel : ces deux clés lui disent quel script lancer et
            # où trouver son code. Valeurs JSON-encodées (avec guillemets) : c'est ainsi
            # que le conteneur les distingue d'une valeur numérique — vérifié le
            # 29/07/2026 par un job réel, `HyperParameters` vide sinon silencieusement.
            HyperParameters={
                "sagemaker_program": f'"{TRAIN_ENTRY_POINT}"',
                "sagemaker_submit_directory": f'"{sourcedir_uri}"',
            },
            Tags=config.tags_list,
        )
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        message = exc.response["Error"]["Message"]
        if "ValidationException" in code and "execution role" in message:
            raise TrainingError(
                "SageMaker refuse le rôle d'exécution.\n"
                f"  {config.sagemaker_role_arn}\n"
                "  Vérifier que sa politique de confiance autorise sagemaker.amazonaws.com.\n"
                "  Contrôle n°6 du preflight."
            ) from exc
        if code == "AccessDeniedException":
            raise TrainingError(
                "Vous n'avez pas le droit de soumettre un job d'entraînement, ou pas"
                " celui de passer ce rôle à SageMaker (iam:PassRole).\n"
                "  Vérifier : `uv run scripts/preflight.py`."
            ) from exc
        if code == "ResourceLimitExceeded":
            raise TrainingError(
                f"Quota atteint pour {INSTANCE_TYPE} en entraînement.\n"
                "  Sur un compte partagé, six binômes qui lancent en même temps"
                " saturent le quota par défaut.\n"
                "  Contrôle n°5 du preflight, et demande d'augmentation auprès de l'IT."
            ) from exc
        raise TrainingError(f"Soumission refusée ({code}) : {message}") from exc

    return Job(
        name=name,
        train_input=train_input,
        validation_input=validation_input,
        output_path=output_path,
    )


def wait(job_name: str, poll_seconds: int = 20, on_tick=None) -> Result:
    """Attend la fin du job et renvoie son issue.

    `on_tick(status, secondes)` est appelé à chaque sondage — c'est par là que `cli.py`
    affiche la progression sans que ce module n'imprime quoi que ce soit lui-même.
    """
    sm = config.client("sagemaker")
    started = time.monotonic()

    while True:
        described = sm.describe_training_job(TrainingJobName=job_name)
        status = described["TrainingJobStatus"]
        elapsed = int(time.monotonic() - started)

        if status in ("Completed", "Failed", "Stopped"):
            break
        if on_tick is not None:
            on_tick(described.get("SecondaryStatus", status), elapsed)
        time.sleep(poll_seconds)

    metrics = {
        m["MetricName"]: float(m["Value"])
        for m in described.get("FinalMetricDataList", [])
    }
    return Result(
        name=job_name,
        status=status,
        seconds=described.get("TrainingTimeInSeconds", elapsed),
        model_artifact=described.get("ModelArtifacts", {}).get("S3ModelArtifacts", ""),
        metrics=metrics,
        failure=described.get("FailureReason", ""),
    )
