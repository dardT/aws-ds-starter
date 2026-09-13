"""
Configuration unique du projet — source de vérité de tous les noms de ressources.

Règle non négociable (§12) : **aucun script ne lit `os.environ` directement**.
Tout passe par cet objet. Les tests non plus ne codent aucun nom en dur (décision D7) :
deux noms ne sont pas connaissables à l'avance — le bucket, qui porte un suffixe de
compte pour l'unicité mondiale, et les jobs d'entraînement, qui portent un horodatage
pour l'unicité dans le temps.

Usage :

    from qc.config import config

    print(config.endpoint_name)              # qc-g01-endpoint
    print(config.s3_uri("capture"))          # s3://qc-g01-data-<suffixe>/capture/
    job = config.timestamped_name("train")   # qc-g01-train-20260727T143012Z

Hygiène des secrets : cet objet peut contenir des credentials lus depuis `.env`.
`__repr__` est volontairement redéfini pour ne jamais les exposer.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path

# Racine du dépôt : ce fichier est à <racine>/src/qc/config.py
ROOT = Path(__file__).resolve().parents[2]

# Sorties des labs : jamais committées, régénérées à l'exécution (§13).
DATA_DIR = ROOT / "data"

# Format de TEAM_ID imposé (décision D1). Un format libre rendrait les checks
# non déterministes et casserait l'isolation par préfixe du compte partagé.
TEAM_ID_PATTERN = re.compile(r"^g[0-9]{2}$")

# Préfixes S3 créés à vide par le socle (§11).
S3_PREFIXES = ("raw", "curated", "model", "capture", "baseline", "reports")

# Noms de clés considérés comme secrets : jamais affichés, jamais écrits.
_SECRET_KEYS = frozenset(
    {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}
)


class ConfigError(RuntimeError):
    """Configuration invalide. Le message porte l'action corrective."""


def _load_dotenv(path: Path) -> None:
    """Charge `.env` dans l'environnement sans écraser ce qui existe déjà.

    Les variables déjà présentes gagnent : cela permet de surcharger ponctuellement
    sans éditer le fichier, et c'est le comportement attendu sur les workstations où
    le profil d'instance fournit les credentials.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if value and key not in os.environ:
            os.environ[key] = value


class Config:
    """Configuration résolue. Instancier via `load_config()`, pas directement."""

    def __init__(self, env: dict[str, str]) -> None:
        self._env = env

        # --- Obligatoires ----------------------------------------------------
        self.region: str = self._required("AWS_REGION")
        self.team_id: str = self._required("TEAM_ID")
        self.project: str = env.get("PROJECT") or "aws-ds"
        self.owner_email: str = self._required("OWNER_EMAIL")

        if not TEAM_ID_PATTERN.match(self.team_id):
            raise ConfigError(
                f"TEAM_ID vaut « {self.team_id} », format invalide.\n"
                f"  Attendu : deux chiffres précédés de « g » — g01, g02, …\n"
                f"  Corriger TEAM_ID dans .env."
            )

        # --- SageMaker -------------------------------------------------------
        self.sagemaker_role_arn: str = env.get("SAGEMAKER_ROLE_ARN", "")
        self.train_instance: str = env.get("SAGEMAKER_TRAIN_INSTANCE") or "ml.m5.large"
        self.endpoint_instance: str = (
            env.get("SAGEMAKER_ENDPOINT_INSTANCE") or "ml.m5.large"
        )

        # --- Bedrock ---------------------------------------------------------
        # Peut être un modelId brut OU un profil d'inférence `eu.*` / `global.*`.
        # Les deux formes coexistent en Europe (décision D6).
        self.bedrock_model_id: str = env.get("BEDROCK_MODEL_ID", "")
        self.bedrock_max_tokens: int = int(env.get("BEDROCK_MAX_TOKENS") or 2048)

        # --- Exposition ------------------------------------------------------
        self.alb_dns_name: str = env.get("ALB_DNS_NAME", "")
        # Mode dégradé retenu : ALB interne en HTTP. ACM traité en démonstration.
        self.acm_cert_arn: str = env.get("ACM_CERT_ARN", "")

        # --- MLflow ----------------------------------------------------------
        self.mlflow_tracking_uri: str = env.get("MLFLOW_TRACKING_URI", "")

        # --- Divers ----------------------------------------------------------
        self.log_level: str = env.get("LOG_LEVEL") or "INFO"
        # Nombre de binômes de la promotion. Sert au contrôle de quota du preflight :
        # en compte partagé, un quota calculé pour un seul groupe fait échouer la
        # formation entière au déploiement simultané (§12, contrôle n°5).
        self.teams_count: int = int(env.get("TEAMS_COUNT") or 6)

    # ------------------------------------------------------------------ helpers

    def _required(self, key: str) -> str:
        value = self._env.get(key, "").strip()
        if not value:
            raise ConfigError(
                f"{key} est obligatoire et n'est pas renseigné.\n"
                f"  Corriger : ajouter {key}=… dans .env "
                f"(voir .env.example pour la liste des clés)."
            )
        return value

    def _derived(self, key: str, default: str) -> str:
        """Valeur du .env si renseignée, sinon la valeur dérivée du TEAM_ID."""
        return self._env.get(key, "").strip() or default

    # ------------------------------------------------------- identité du compte

    @cached_property
    def account_id(self) -> str:
        """Identifiant du compte AWS. Appel STS différé au premier accès."""
        import boto3

        return boto3.Session(region_name=self.region).client("sts").get_caller_identity()[
            "Account"
        ]

    @cached_property
    def account_suffix(self) -> str:
        """Suffixe pour l'unicité mondiale du nom de bucket (§14)."""
        return self.account_id[-6:]

    # ------------------------------------------------------ noms des ressources

    def resource(self, component: str) -> str:
        """Nom d'une ressource du groupe : `qc-<TEAM_ID>-<composant>`.

        C'est LA convention de nommage du compte partagé (§14). Elle porte à la fois
        l'isolation entre groupes et les conditions des politiques IAM.
        """
        return f"qc-{self.team_id}-{component}"

    def timestamped_name(self, component: str) -> str:
        """Nom horodaté, pour les objets non recréables sous le même nom.

        Concerne les jobs d'entraînement, les jobs de traitement et les
        configurations d'endpoint : AWS refuse de réutiliser leur nom après
        suppression. L'horodatage est généré par le code, jamais par l'apprenant (§14).
        """
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{self.resource(component)}-{stamp}"

    @cached_property
    def s3_bucket(self) -> str:
        # Unicité mondiale obligatoire : d'où le suffixe de compte.
        return self._derived(
            "S3_BUCKET", f"qc-{self.team_id}-data-{self.account_suffix}"
        )

    @property
    def endpoint_name(self) -> str:
        return self._derived("SAGEMAKER_ENDPOINT_NAME", self.resource("endpoint"))

    @property
    def ecr_repo(self) -> str:
        return self._derived("ECR_REPO", self.resource("app"))

    @property
    def ecs_cluster(self) -> str:
        return self._derived("ECS_CLUSTER", self.resource("cluster"))

    @property
    def ecs_service(self) -> str:
        return self._derived("ECS_SERVICE", self.resource("app"))

    @property
    def target_group(self) -> str:
        return self.resource("tg")

    @property
    def mlflow_experiment(self) -> str:
        """Une expérience MLflow par groupe (décision D8)."""
        return f"qc-{self.team_id}"

    # ------------------------------------------------------------------ chemins

    def s3_uri(self, prefix: str = "") -> str:
        """URI S3 d'un préfixe du bucket du groupe."""
        if prefix and prefix not in S3_PREFIXES:
            raise ConfigError(
                f"Préfixe S3 « {prefix} » inconnu.\n"
                f"  Préfixes autorisés : {', '.join(S3_PREFIXES)}."
            )
        return f"s3://{self.s3_bucket}/{prefix}/" if prefix else f"s3://{self.s3_bucket}/"

    @property
    def log_group_ecs(self) -> str:
        return f"/ecs/{self.resource('app')}"

    @property
    def log_group_endpoint(self) -> str:
        return f"/aws/sagemaker/Endpoints/{self.endpoint_name}"

    @property
    def streamlit_base_url_path(self) -> str:
        """Préfixe de chemin sous lequel l'application est publiée (décision D12).

        L'ALB partagé route sur /<TEAM_ID>/ sans réécrire le chemin. Sans ce
        paramètre, Streamlit génère ses ressources statiques et son websocket à la
        racine, l'ALB ne sait pas les router, et la page reste blanche alors que la
        tâche ECS est saine.
        """
        return self.team_id

    @property
    def app_url(self) -> str:
        return f"http://{self.alb_dns_name}/{self.team_id}/"

    @property
    def health_check_path(self) -> str:
        """Chemin de santé natif de Streamlit, préfixé comme le reste (décision D12)."""
        return f"/{self.team_id}/_stcore/health"

    # --------------------------------------------------------------------- tags

    @property
    def tags(self) -> dict[str, str]:
        """Tags obligatoires sur toute ressource (§11).

        Ils ne sont pas décoratifs : `make destroy` s'appuie dessus pour ne
        supprimer que ce qui a été créé pendant les labs.
        """
        return {
            "Project": self.project,
            "Team": self.team_id,
            "Owner": self.owner_email,
        }

    @property
    def tags_list(self) -> list[dict[str, str]]:
        """Mêmes tags, au format attendu par la plupart des API boto3."""
        return [{"Key": k, "Value": v} for k, v in self.tags.items()]

    # -------------------------------------------------------------------- boto3

    def session(self):
        """Session boto3 câblée sur AWS_REGION.

        Sur les workstations, aucun credential n'est fourni ici : la chaîne de
        résolution boto3 par défaut prend le relais via le profil d'instance.
        """
        import boto3

        return boto3.Session(region_name=self.region)

    def client(self, service: str):
        return self.session().client(service)

    # ------------------------------------------------------------------- divers

    def __repr__(self) -> str:
        # Redéfini pour ne jamais exposer un credential dans une trace ou un log.
        return (
            f"Config(region={self.region!r}, team_id={self.team_id!r}, "
            f"project={self.project!r}, bedrock_model_id={self.bedrock_model_id!r})"
        )


def load_config(dotenv: Path | None = None) -> Config:
    """Charge `.env` puis construit la configuration.

    Lève `ConfigError` avec une action corrective si une clé obligatoire manque.
    """
    _load_dotenv(dotenv or (ROOT / ".env"))
    env = {k: v for k, v in os.environ.items() if k not in _SECRET_KEYS}
    return Config(env)


_config: Config | None = None


def __getattr__(name: str):
    """Construction PARESSEUSE de l'instance unique (PEP 562).

    `from qc.config import config` la construit au premier import ; importer seulement
    `ROOT`, `DATA_DIR` ou `ConfigError` ne la construit pas.

    La distinction n'est pas cosmétique. Avec une instance construite au chargement du
    module, `uv run qc` et `uv run qc --help` échouaient sur une trace d'appels de
    quinze lignes dès que `.env` était incomplet — c'est-à-dire exactement à l'instant
    où l'apprenant cherche à savoir quoi remplir. Le tableau d'avancement et l'aide
    doivent rester lisibles sur une machine non configurée.
    """
    if name == "config":
        global _config
        if _config is None:
            _config = load_config()
        return _config
    raise AttributeError(f"module {__name__!r} n'expose pas {name!r}")
