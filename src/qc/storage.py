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
        return sum(u.size for u in self.uploads)

    def uri(self, filename: str = "") -> str:
        """URI S3 d'un fichier déposé, ou du préfixe si aucun nom n'est donné."""
        base = f"s3://{self.bucket}/{self.prefix}/"
        return f"{base}{filename}" if filename else base

    @property
    def training_input(self) -> str:
        """URI que l'étape `train` passe à SageMaker comme canal d'entrée.

        SageMaker prend un PRÉFIXE, pas un fichier : il lit tout ce qui s'y trouve.
        D'où le sous-préfixe dédié — pointer `curated/` directement ferait aussi
        avaler `sample.csv` et `timeline.csv` au job, qui échouerait sur un nombre
        de colonnes inattendu.
        """
        return f"s3://{self.bucket}/{self.prefix}/train/"

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
    stem = filename.removesuffix(".csv")
    if stem in ("train", "test"):
        return f"{PREFIX}/{stem}/{filename}"
    return f"{PREFIX}/{filename}"


def upload(source: Path, files: tuple[str, ...] = EXPECTED) -> Report:
    """Dépose les fichiers produits par `qc load` dans le bucket du binôme.

    Lève `StorageError` avec un message actionnable si la source est incomplète ou
    si S3 refuse. L'erreur la plus fréquente ici n'est pas technique : c'est d'avoir
    sauté l'étape `load`.
    """
    missing = [f for f in files if not (source / f).is_file()]
    if missing:
        raise StorageError(
            f"Fichiers absents de {source} : {', '.join(missing)}.\n"
            "  Corriger : lancer `uv run qc load` d'abord."
        )

    s3 = config.client("s3")
    bucket = config.s3_bucket
    uploads: list[Upload] = []

    for filename in files:
        path = source / filename
        key = _key(filename)
        try:
            s3.upload_file(
                str(path),
                bucket,
                key,
                # Les tags obligatoires (§11) ne se propagent pas tout seuls aux objets :
                # `default_tags` de Terraform ne couvre que les ressources qu'il crée.
                # Sans ceci, `make destroy` ne saurait pas que ces objets sont à lui.
                ExtraArgs={
                    "ServerSideEncryption": "AES256",
                    "Tagging": "&".join(f"{k}={v}" for k, v in config.tags.items()),
                },
            )
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("AccessDenied", "AllAccessDisabled"):
                raise StorageError(
                    f"S3 refuse l'écriture de {key} dans {bucket}.\n"
                    "  Deux causes possibles, et une seule est la vôtre :\n"
                    "  - S3_BUCKET dans .env ne désigne pas le bucket de VOTRE groupe ;\n"
                    "  - vos credentials ont expiré.\n"
                    "  Vérifier : `uv run scripts/preflight.py`, contrôle n°4."
                ) from exc
            if code in ("NoSuchBucket", "404"):
                raise StorageError(
                    f"Le bucket {bucket} n'existe pas.\n"
                    "  Corriger : `make env-from-tf TEAM_ID=<votre groupe>` après"
                    " application du socle."
                ) from exc
            raise StorageError(f"S3 a refusé {key} ({code}).") from exc

        head = s3.head_object(Bucket=bucket, Key=key)
        uploads.append(
            Upload(key=key, size=head["ContentLength"], etag=head["ETag"].strip('"'))
        )

    return Report(bucket=bucket, prefix=PREFIX, uploads=tuple(uploads))


def download(filename: str, destination: Path) -> Path:
    """Récupère un fichier de `curated/` depuis S3 et l'écrit sur le disque local.

    Le conteneur du J2 ne contient AUCUNE donnée : `.dockerignore` exclut `data/`, qui
    est produit à l'exécution par `qc load`. Une image qui embarquerait les données ne
    serait plus la même d'un binôme à l'autre, et pèserait un mégaoctet de plus pour
    rien.

    L'application va donc les chercher dans S3 au démarrage. C'est aussi ce que fait un
    vrai service : le conteneur est sans état, les données vivent ailleurs.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        config.client("s3").download_file(config.s3_bucket, _key(filename), str(destination))
    except ClientError as exc:
        raise StorageError(
            f"{filename} introuvable dans s3://{config.s3_bucket}/{PREFIX}/.\n"
            f"  ({exc.response['Error']['Code']})\n"
            "  Corriger : `uv run qc load` puis `uv run qc upload`."
        ) from exc
    return destination


def verify(report: Report) -> list[str]:
    """Relit ce qui a été déposé et renvoie la liste des anomalies.

    Vide si tout va bien. Contrôler après écriture n'est pas de la paranoïa ici :
    un job d'entraînement qui démarre sur un objet tronqué échoue vingt minutes plus
    tard sur un message qui ne parle pas du fichier.
    """
    s3 = config.client("s3")
    anomalies: list[str] = []

    for u in report.uploads:
        try:
            head = s3.head_object(Bucket=report.bucket, Key=u.key)
        except ClientError as exc:
            anomalies.append(f"{u.key} : introuvable ({exc.response['Error']['Code']})")
            continue
        if head["ContentLength"] != u.size:
            anomalies.append(
                f"{u.key} : {head['ContentLength']} octets relus contre {u.size} déposés"
            )
        if head.get("ServerSideEncryption") != "AES256":
            anomalies.append(f"{u.key} : chiffrement au repos absent")

    return anomalies


def probe_isolation(other_team: str) -> str:
    """Tente de lire le bucket d'un AUTRE groupe et renvoie ce qui s'est passé.

    Support du moment « AccessDenied volontaire » du lab. Le message compte plus que
    le résultat : un binôme qui a lu la politique IAM sans la voir agir n'en retient
    rien. Ne lève jamais — un refus est ici le comportement ATTENDU, pas une panne.
    """
    target = f"qc-{other_team}-data-{config.account_suffix}"
    try:
        config.client("s3").list_objects_v2(Bucket=target, MaxKeys=1)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("AccessDenied", "AllAccessDisabled"):
            return (
                f"Refusé sur {target} — c'est le résultat attendu.\n"
                "  La politique IAM du groupe est scopée à son seul bucket."
            )
        return f"{target} : {code}"
    return (
        f"LECTURE RÉUSSIE sur {target}.\n"
        "  Attendu SI vous exécutez ceci sous une identité de formateur — un rôle SSO\n"
        "  d'administration n'est pas contraint par le scopage de préfixe.\n"
        "  Depuis la machine de travail d'un binôme, en revanche, ce résultat signale\n"
        "  que le profil d'instance n'est pas scopé : prévenir le formateur."
    )
