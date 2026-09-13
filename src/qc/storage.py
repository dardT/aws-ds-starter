"""J1 · S3 — dépôt des jeux de données dans le bucket du binôme.

Ce module ne fait AUCUN affichage : il calcule, il renvoie. Tout ce qui s'imprime
est dans `qc.cli`. C'est ce qui rend `upload()` réutilisable telle quelle par les
tests et, au J2, par du code qui n'a rien à faire de messages sur sa sortie standard.

Notions du lab : bucket, objet, préfixe, chiffrement au repos, et un `AccessDenied`
volontaire pour que le binôme voie de ses yeux ce que le scopage IAM produit.

    from qc import storage
    report = storage.upload(DATA_DIR)
    report.uri("train.csv")      # s3://qc-g01-data-<suffixe>/curated/train.csv
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from botocore.exceptions import ClientError

from qc.config import config

#: Préfixe de destination. `curated` et non `raw` : ces fichiers sont le PRODUIT de
#: l'étape `load` — sélection de variables, découpage stratifié — pas la donnée brute
#: telle qu'elle sort de l'UCI. Le J3 s'appuie sur cette distinction : `baseline` et
#: `capture` doivent pouvoir être comparés à un `curated` stable.
PREFIX = "curated"

#: Fichiers attendus par les étapes suivantes. `train.csv` et `test.csv` sont lus par
#: le job d'entraînement ; `sample.csv` sert à `invoke` ; `timeline.csv` au J3.
EXPECTED = ("train.csv", "test.csv", "sample.csv", "timeline.csv")


class StorageError(RuntimeError):
    """Erreur de dépôt, formulée pour être lue par un apprenant, pas par un opérateur."""


@dataclass(frozen=True)
class Upload:
    """Un objet déposé, avec ce qu'il faut pour le retrouver et le vérifier."""

    key: str
    size: int
    etag: str

    @property
    def filename(self) -> str:
        return self.key.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Report:
    """Résultat d'un dépôt complet."""

    bucket: str
    prefix: str
    uploads: tuple[Upload, ...]

    @property
    def total_bytes(self) -> int:
        # TODO-D1-19 — à écrire.
        raise NotImplementedError("TODO-D1-19")

    def uri(self, filename: str = "") -> str:
        """URI S3 d'un fichier déposé, ou du préfixe si aucun nom n'est donné."""
        # TODO-D1-20 — à écrire.
        raise NotImplementedError("TODO-D1-20")

    @property
    def training_input(self) -> str:
        """URI que l'étape `train` passe à SageMaker comme canal d'entrée.

        SageMaker prend un PRÉFIXE, pas un fichier : il lit tout ce qui s'y trouve.
        D'où le sous-préfixe dédié — pointer `curated/` directement ferait aussi
        avaler `sample.csv` et `timeline.csv` au job, qui échouerait sur un nombre
        de colonnes inattendu.
        """
        # TODO-D1-21 — à écrire.
        raise NotImplementedError("TODO-D1-21")

    def summary(self) -> str:
        lines = [f"{len(self.uploads)} fichiers déposés dans {self.uri()}"]
        for u in self.uploads:
            lines.append(f"  {u.filename:16s} {u.size / 1024:8.1f} Kio")
        lines.append(f"  {'total':16s} {self.total_bytes / 1024:8.1f} Kio")
        return "\n".join(lines)


def _key(filename: str) -> str:
    """Clé S3 d'un fichier.

    `train.csv` et `test.csv` partent chacun dans son propre sous-préfixe parce que
    SageMaker consomme un préfixe entier. Les deux autres restent à plat : ils ne
    sont jamais lus par un job, seulement par nous.
    """
    # TODO-D1-22 — à écrire.
    raise NotImplementedError("TODO-D1-22")


def upload(source: Path, files: tuple[str, ...] = EXPECTED) -> Report:
    """Dépose les fichiers produits par `qc load` dans le bucket du binôme.

    Lève `StorageError` avec un message actionnable si la source est incomplète ou
    si S3 refuse. L'erreur la plus fréquente ici n'est pas technique : c'est d'avoir
    sauté l'étape `load`.
    """
    # TODO-D1-23 — à écrire.
    raise NotImplementedError("TODO-D1-23")


def download(filename: str, destination: Path) -> Path:
    """Récupère un fichier de `curated/` depuis S3 et l'écrit sur le disque local.

    Le conteneur du J2 ne contient AUCUNE donnée : `.dockerignore` exclut `data/`, qui
    est produit à l'exécution par `qc load`. Une image qui embarquerait les données ne
    serait plus la même d'un binôme à l'autre, et pèserait un mégaoctet de plus pour
    rien.

    L'application va donc les chercher dans S3 au démarrage. C'est aussi ce que fait un
    vrai service : le conteneur est sans état, les données vivent ailleurs.
    """
    # TODO-D1-24 — à écrire.
    raise NotImplementedError("TODO-D1-24")


def verify(report: Report) -> list[str]:
    """Relit ce qui a été déposé et renvoie la liste des anomalies.

    Vide si tout va bien. Contrôler après écriture n'est pas de la paranoïa ici :
    un job d'entraînement qui démarre sur un objet tronqué échoue vingt minutes plus
    tard sur un message qui ne parle pas du fichier.
    """
    # TODO-D1-25 — à écrire.
    raise NotImplementedError("TODO-D1-25")


def probe_isolation(other_team: str) -> str:
    """Tente de lire le bucket d'un AUTRE groupe et renvoie ce qui s'est passé.

    Support du moment « AccessDenied volontaire » du lab. Le message compte plus que
    le résultat : un binôme qui a lu la politique IAM sans la voir agir n'en retient
    rien. Ne lève jamais — un refus est ici le comportement ATTENDU, pas une panne.
    """
    # TODO-D1-26 — à écrire.
    raise NotImplementedError("TODO-D1-26")
