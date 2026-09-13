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
    # TODO-D1-27 — à écrire.
    raise NotImplementedError("TODO-D1-27")


def _sourcedir_bytes() -> bytes:
    """Construit l'archive de code (script d'entraînement + de service + le module de
    modélisation) en mémoire, sans rien écrire sur disque."""
    # TODO-D1-28 — à écrire.
    raise NotImplementedError("TODO-D1-28")


def _upload_sourcedir() -> str:
    """Dépose l'archive de code sur S3. Renvoie son URI.

    Un seul objet, réécrit à chaque appel : contrairement à l'artefact d'un job (qui doit
    rester rejouable), le code est celui qui tourne MAINTENANT — pas besoin d'historique.
    """
    # TODO-D1-29 — à écrire.
    raise NotImplementedError("TODO-D1-29")


def submit(train_input: str, validation_input: str) -> Job:
    """Soumet le job et rend la main immédiatement.

    Deux canaux, pas un seul. Le canal `validation` est ce qui produit les métriques
    `validation:*` — sans lui le job s'entraîne sans évaluation, et la baseline de dérive
    du J3 n'a plus rien à quoi se comparer. Le poids du déséquilibre et le seuil de
    décision ne sont PAS passés en hyperparamètre : le script les calcule lui-même depuis
    le canal `train`, exactement comme le notebook de validation.
    """
    # TODO-D1-30 — à écrire.
    raise NotImplementedError("TODO-D1-30")


def wait(job_name: str, poll_seconds: int = 20, on_tick=None) -> Result:
    """Attend la fin du job et renvoie son issue.

    `on_tick(status, secondes)` est appelé à chaque sondage — c'est par là que `cli.py`
    affiche la progression sans que ce module n'imprime quoi que ce soit lui-même.
    """
    # TODO-D1-31 — à écrire.
    raise NotImplementedError("TODO-D1-31")
