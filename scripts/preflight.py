#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3>=1.35"]
# ///
"""
Preflight — 15 contrôles avant toute création de ressource.

Principe non négociable (§12) : aucune ressource n'est créée et aucun lab n'est
exécuté tant que ce script n'est pas vert.

Sortie : un tableau récapitulatif en console, un rapport `preflight-report.md`
joignable tel quel à une demande d'accès, et un code de sortie 0 si tous les
contrôles sont PASS ou SKIP, 1 dès qu'un seul est FAIL.

Tous les contrôles sont exécutés même en cas d'échec, contrairement à une lecture
littérale de §12 : un rapport partiel n'a aucune valeur face à l'IT. Mieux vaut une
liste complète des droits manquants qu'un premier échec isolé.

    uv run scripts/preflight.py               # mode apprenant : lectures seules,
                                              # aucun appel d'écriture IAM
    uv run scripts/preflight.py --formateur   # ajoute la sonde iam:CreateRole,
                                              # requise avant `make socle-apply`

Deux modes depuis la revue du 29/07/2026 (point 5) : la sonde iam:CreateRole est une
API d'écriture privilégiée — trace CloudTrail, alertes sécurité — inadaptée à un poste
apprenant. Elle est réservée à `--formateur` ; le mode par défaut s'en remet à la
simulation IAM (lecture) et aux sondes non privilégiées des contrôles 4 à 10.

Hygiène des secrets : aucune valeur de credential n'est affichée ni écrite. Seuls
l'identifiant de compte et l'ARN de l'appelant apparaissent — de l'identité, pas
du secret.
"""

from __future__ import annotations

import functools
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Le preflight doit tourner sur une machine NEUVE, avant que l'environnement du projet
# n'existe : c'est un script PEP 723 autonome, et le paquet `qc` n'y est donc pas
# installé. D'où cet ajout au chemin d'import — la seule entorse à la disposition src/
# du dépôt, et elle est délibérée : le contrôle n°13 vérifie justement que `uv sync`
# fonctionne, il ne peut pas en dépendre.
sys.path.insert(0, str(ROOT / "src"))

from qc.config import ConfigError  # noqa: E402

# `config` est construit paresseusement (PEP 562) : le nommer ici DÉCLENCHE sa
# construction, donc un .env incomplet fait échouer l'import lui-même. Sans ce filet,
# l'apprenant reçoit une trace d'appels de quinze lignes au tout premier script du J1 —
# exactement le moment où il cherche à savoir quoi renseigner. Même leçon que D17.
try:
    from qc.config import config  # noqa: E402
except ConfigError as exc:
    print(f"Configuration incomplète : {exc}", file=sys.stderr)
    print("Le preflight ne peut rien contrôler tant que .env n'est pas renseigné.", file=sys.stderr)
    raise SystemExit(1) from None

REPORT = ROOT / "preflight-report.md"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# Sortie non bufferisée : certains contrôles durent plusieurs minutes (résolution des
# dépendances, terraform init). Sans flush, une sortie redirigée vers un fichier reste
# vide jusqu'à la fin — l'opérateur croit le script bloqué.
print = functools.partial(print, flush=True)  # noqa: A001


@dataclass
class Result:
    number: int
    name: str
    status: str
    cause: str = ""
    action: str = ""
    #: Le contrôle dépend du socle Terraform. Un FAIL est normal avant `make socle-apply`.
    needs_socle: bool = False
    details: list[str] = field(default_factory=list)


results: list[Result] = []


def record(
    number: int,
    name: str,
    status: str,
    cause: str = "",
    action: str = "",
    *,
    needs_socle: bool = False,
    details: list[str] | None = None,
) -> Result:
    r = Result(number, name, status, cause, action, needs_socle, details or [])
    results.append(r)
    icon = {PASS: "✓", FAIL: "✗", SKIP: "–"}[status]
    suffix = "  (dépend du socle)" if needs_socle and status == FAIL else ""
    print(f"  {icon} {number:>2}. {name}{suffix}")
    if cause:
        print(f"        {cause}")
    for line in r.details:
        print(f"        {line}")
    if action and status == FAIL:
        print(f"        → {action}")
    return r


def _code(exc: Exception) -> str:
    return getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)


# ============================================================================ 1
def check_identity() -> str | None:
    try:
        ident = config.client("sts").get_caller_identity()
    except Exception as exc:  # noqa: BLE001
        record(
            1,
            "Identité",
            FAIL,
            f"sts:GetCallerIdentity échoue ({_code(exc)}).",
            "Renseigner des credentials dans .env, ou vérifier le profil d'instance.",
        )
        return None
    record(
        1,
        "Identité",
        PASS,
        details=[f"compte {ident['Account']}", f"appelant {ident['Arn']}"],
    )
    return ident["Arn"]


# ============================================================================ 2
def check_region() -> None:
    try:
        regions = {
            r["RegionName"]
            for r in config.client("ec2").describe_regions()["Regions"]
        }
    except Exception as exc:  # noqa: BLE001
        record(
            2,
            "Région",
            SKIP,
            f"Liste des régions non lisible ({_code(exc)}) — droit ec2:DescribeRegions.",
        )
        return
    if config.region in regions:
        record(2, "Région", PASS, details=[f"AWS_REGION = {config.region}, activée"])
    else:
        record(
            2,
            "Région",
            FAIL,
            f"{config.region} ne figure pas dans les régions activées du compte.",
            "Activer la région, ou corriger AWS_REGION dans .env.",
        )


# ============================================================================ 3
REQUIRED_ACTIONS = [
    "s3:PutObject",
    "s3:GetObject",
    "s3:DeleteObject",
    "s3:ListBucket",
    "sagemaker:CreateTrainingJob",
    "sagemaker:CreateModel",
    "sagemaker:CreateEndpoint",
    "sagemaker:InvokeEndpoint",
    "bedrock:InvokeModel",
    "bedrock:Converse",
    "ecr:GetAuthorizationToken",
    "ecr:PutImage",
    "ecs:UpdateService",
    "ecs:DescribeServices",
    "elasticloadbalancing:DescribeTargetGroups",
    "logs:FilterLogEvents",
    "logs:CreateLogGroup",
    "cloudwatch:GetMetricData",
    "cloudwatch:PutMetricAlarm",
]


def _probe_iam_write(raison: str, indices: list[str] | None = None) -> None:
    """Teste RÉELLEMENT iam:CreateRole, sans créer quoi que ce soit. Mode FORMATEUR.

    La sonde envoie un document de confiance volontairement invalide. IAM autorise
    avant de valider, donc :

      - AccessDenied            → le droit manque, et le socle échouera à mi-parcours ;
      - MalformedPolicyDocument → le droit est là, aucun rôle n'a été créé.

    C'est le seul moyen de connaître la réponse sans laisser derrière soi un rôle
    fantôme dans un compte partagé par toute la promotion.

    Revue du 29/07/2026, point 5 : même non mutante, c'est une API d'ÉCRITURE
    privilégiée — trace CloudTrail iam:CreateRole au nom de l'appelant, alertes
    sécurité sur un compte surveillé. Elle n'est donc émise qu'avec `--formateur`,
    le seul profil qui a besoin de iam:CreateRole (`make socle-apply`). Le mode par
    défaut, apprenant, n'émet AUCUN appel d'écriture IAM.
    """
    try:
        config.client("iam").create_role(
            RoleName="qc-preflight-probe", AssumeRolePolicyDocument="{}"
        )
    except Exception as exc:  # noqa: BLE001
        code = _code(exc)
        if code == "MalformedPolicyDocument":
            record(
                3,
                "Permissions",
                PASS,
                f"iam:CreateRole autorisé ({raison}, sonde directe non mutante).",
                details=indices or [],
            )
            return
        if code == "AccessDenied":
            record(
                3,
                "Permissions",
                FAIL,
                "iam:CreateRole REFUSÉ — le socle ne peut pas créer les rôles SageMaker.",
                "Demander à l'IT iam:CreateRole, iam:PutRolePolicy, iam:GetRole et "
                "iam:PassRole, restreints aux ressources nommées qc-*.",
                details=[
                    "Sans ce droit, `make socle-apply` crée les buckets puis échoue",
                    "sur les six rôles. Le J1 s'arrête à l'étape `train`.",
                    *(indices or []),
                ],
            )
            return
        record(3, "Permissions", SKIP, f"Sonde iam:CreateRole non concluante ({code}).")
        return

    # Ne devrait jamais arriver : un document vide n'est pas une politique valide.
    record(3, "Permissions", SKIP, "Sonde iam:CreateRole inattendue — rôle possiblement créé.")


def check_permissions(caller_arn: str | None, formateur: bool = False) -> None:
    if not caller_arn:
        record(3, "Permissions", SKIP, "Identité inconnue, contrôle impossible.")
        return

    # simulate-principal-policy veut l'ARN du RÔLE, pas celui de la session assumée.
    principal = caller_arn
    if ":assumed-role/" in caller_arn:
        account = caller_arn.split(":")[4]
        role = caller_arn.split("/")[1]
        principal = f"arn:aws:iam::{account}:role/{role}"

    # Le verdict repose sur la SONDE RÉELLE, jamais sur la simulation.
    #
    # Motif, constaté le 27/07/2026 sur deux jeux de permissions successifs : appelée
    # sans ARN de ressource, simulate_principal_policy renvoie « refusé » pour toute
    # action couverte par une politique scopée à des ressources précises — c'est-à-dire
    # la quasi-totalité d'entre elles. Le rapport annonçait s3:PutObject refusé pendant
    # que le contrôle n°4, dans la même exécution, écrivait puis supprimait un objet
    # pour de vrai. Un rapport qui se contredit lui-même ne vaut rien face à l'IT.
    #
    # La simulation reste utile comme indice, jamais comme verdict.
    indices: list[str] = []
    denied: list[str] | None = None
    try:
        resp = config.client("iam").simulate_principal_policy(
            PolicySourceArn=principal, ActionNames=REQUIRED_ACTIONS
        )
        denied = [
            r["EvalActionName"]
            for r in resp["EvaluationResults"]
            if r["EvalDecision"] != "allowed"
        ]
        if denied:
            indices = [
                f"simulation : {len(denied)}/{len(REQUIRED_ACTIONS)} actions données pour refusées,",
                "à confirmer par les contrôles 4 à 10 — la simulation ignore le scopage",
                "par ressource et produit des faux refus.",
            ]
        else:
            indices = [f"simulation : {len(REQUIRED_ACTIONS)} actions autorisées"]
        raison = "simulation disponible"
    except Exception as exc:  # noqa: BLE001
        # Fréquent sur un rôle SSO. Ce cas produisait autrefois un SKIP, et ce SKIP a
        # laissé passer un rôle dépourvu de iam:CreateRole : le blocage n'est apparu
        # qu'au milieu du `terraform apply` du socle, six échecs à la suite.
        raison = f"simulation indisponible ({_code(exc)})"

    if formateur:
        _probe_iam_write(raison, indices)
        return

    # Mode APPRENANT (défaut) : lectures seules, AUCUN appel d'écriture IAM — la sonde
    # create_role est réservée à `--formateur` (voir _probe_iam_write). Un apprenant n'a
    # de toute façon pas besoin de iam:CreateRole : seul `make socle-apply` crée des
    # rôles, et c'est un geste formateur. Ses droits réels sont exercés par les
    # contrôles 4 à 10, par sondes non privilégiées.
    pied = [
        "Mode apprenant : aucune sonde d'écriture IAM n'est émise.",
        "Contrôle complet des droits du socle : `uv run scripts/preflight.py --formateur`.",
    ]
    if denied == []:
        record(
            3,
            "Permissions",
            PASS,
            f"{raison} — les {len(REQUIRED_ACTIONS)} actions du parcours sont autorisées.",
            details=pied,
        )
    else:
        # Refus simulés (faux négatifs fréquents) ou simulation indisponible : le
        # verdict appartient aux sondes réelles des contrôles suivants, pas à un FAIL
        # ici — un rapport qui se contredit ne vaut rien face à l'IT.
        record(3, "Permissions", SKIP, f"{raison}.", details=[*indices, *pied])


# ============================================================================ 4
def check_s3() -> None:
    s3 = config.client("s3")
    bucket = config.s3_bucket
    key = f"preflight/{config.team_id}-probe.txt"

    try:
        s3.head_bucket(Bucket=bucket)
    except Exception as exc:  # noqa: BLE001
        record(
            4,
            "S3",
            FAIL,
            f"Bucket {bucket} inaccessible ({_code(exc)}).",
            "Appliquer le socle Terraform, qui crée le bucket du groupe.",
            needs_socle=True,
        )
        return

    details = []
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=b"preflight")
        s3.delete_object(Bucket=bucket, Key=key)
        details.append("écriture puis suppression : OK")
    except Exception as exc:  # noqa: BLE001
        record(
            4,
            "S3",
            FAIL,
            f"Écriture refusée dans {bucket} ({_code(exc)}).",
            "Vérifier la politique du rôle du groupe sur ce bucket.",
        )
        return

    try:
        s3.get_bucket_encryption(Bucket=bucket)
        details.append("chiffrement au repos : activé")
    except Exception:  # noqa: BLE001
        details.append("chiffrement au repos : ABSENT")

    try:
        pab = s3.get_public_access_block(Bucket=bucket)["PublicAccessBlockConfiguration"]
        details.append(
            "blocage des accès publics : "
            + ("total" if all(pab.values()) else "INCOMPLET")
        )
    except Exception:  # noqa: BLE001
        details.append("blocage des accès publics : NON CONFIGURÉ")

    bad = [d for d in details if "ABSENT" in d or "INCOMPLET" in d or "NON" in d]
    record(
        4,
        "S3",
        FAIL if bad else PASS,
        f"{bucket}",
        "Corriger le socle Terraform : chiffrement et blocage public sont exigés (§14).",
        details=details,
    )


# ============================================================================ 5
def check_sagemaker_quotas() -> None:
    needed = config.teams_count
    details = []
    ok = True
    try:
        sq = config.client("service-quotas")
        paginator = sq.get_paginator("list_service_quotas")
        quotas = {
            q["QuotaName"].lower(): q["Value"]
            for page in paginator.paginate(ServiceCode="sagemaker")
            for q in page["Quotas"]
        }
    except Exception as exc:  # noqa: BLE001
        record(
            5,
            "Quotas SageMaker",
            SKIP,
            f"Service Quotas non lisible ({_code(exc)}).",
            details=[f"À vérifier en console : {needed} groupes simultanés attendus."],
        )
        return

    for instance, kind, label in (
        (config.train_instance, "training job usage", "entraînement"),
        (config.endpoint_instance, "endpoint usage", "endpoint"),
    ):
        value = quotas.get(f"{instance} for {kind}".lower())
        if value is None:
            details.append(f"{instance} ({label}) : quota introuvable")
            continue
        got = int(value)
        verdict = "OK" if got >= needed else "INSUFFISANT"
        details.append(f"{instance} ({label}) : {got} pour {needed} groupes — {verdict}")
        ok &= got >= needed

    record(
        5,
        "Quotas SageMaker",
        PASS if ok else FAIL,
        f"{needed} binômes déclarés (TEAMS_COUNT).",
        "Demander une augmentation de quota : tous les groupes déploient en même temps.",
        details=details,
    )


# ============================================================================ 6
def check_sagemaker_role() -> None:
    arn = config.sagemaker_role_arn
    if not arn:
        record(
            6,
            "Rôle SageMaker",
            FAIL,
            "SAGEMAKER_ROLE_ARN non renseigné.",
            "Demander au formateur l'extrait `.env` de votre groupe.",
            needs_socle=True,
        )
        return
    try:
        role = config.client("iam").get_role(RoleName=arn.split("/")[-1])["Role"]
    except Exception as exc:  # noqa: BLE001
        record(
            6,
            "Rôle SageMaker",
            FAIL,
            f"Rôle introuvable ou illisible ({_code(exc)}).",
            "Vérifier SAGEMAKER_ROLE_ARN et le droit iam:GetRole.",
            needs_socle=True,
        )
        return

    doc = str(role.get("AssumeRolePolicyDocument", ""))
    trusted = "sagemaker.amazonaws.com" in doc
    record(
        6,
        "Rôle SageMaker",
        PASS if trusted else FAIL,
        f"{role['RoleName']}",
        "La politique de confiance doit autoriser sagemaker.amazonaws.com.",
        details=["confiance sagemaker.amazonaws.com : " + ("OK" if trusted else "ABSENTE")],
    )


# ============================================================================ 7
def check_bedrock() -> None:
    """Contrôle corrigé par rapport à §12 (décision D6).

    La spec d'origine exige que BEDROCK_MODEL_ID figure dans `list_foundation_models`.
    C'est faux en Europe : les modèles récents s'invoquent via un profil d'inférence
    préfixé `eu.` ou `global.`, qui n'apparaît que dans `list_inference_profiles`.
    Et surtout, la présence au catalogue ne vaut pas accès — seul l'appel réel le prouve.
    """
    model_id = config.bedrock_model_id
    if not model_id:
        record(
            7,
            "Bedrock",
            FAIL,
            "BEDROCK_MODEL_ID non renseigné.",
            "Lancer `uv run scripts/discover.py` pour trouver un modèle activé.",
        )
        return

    details = []
    try:
        bedrock = config.client("bedrock")
        catalogue = {
            m["modelId"] for m in bedrock.list_foundation_models()["modelSummaries"]
        }
        profiles = {
            p["inferenceProfileId"]
            for p in bedrock.list_inference_profiles()["inferenceProfileSummaries"]
        }
        where = (
            "catalogue"
            if model_id in catalogue
            else "profils d'inférence"
            if model_id in profiles
            else None
        )
        details.append(
            f"référencé dans : {where}" if where else "NON référencé (catalogue ni profils)"
        )
    except Exception as exc:  # noqa: BLE001
        details.append(f"listing indisponible ({_code(exc)})")

    runtime = config.client("bedrock-runtime")

    try:
        runtime.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "OK"}]}],
            inferenceConfig={"maxTokens": 5},
        )
    except Exception as exc:  # noqa: BLE001
        record(
            7,
            "Bedrock",
            FAIL,
            f"Appel Converse refusé sur {model_id} ({_code(exc)}).",
            "Activer le modèle (console Bedrock → Model access) ou vérifier la "
            "politique IAM du rôle. Voir décision D14.",
            details=details,
        )
        return

    details.append("appel Converse simple : OK")

    # Le tool use est la SEULE contrainte dure de l'agent du J2, et un modèle peut
    # parfaitement accepter Converse tout en refusant le tool use (décision D15 : c'est
    # exactement le cas de Mistral en mode streaming). Un preflight qui s'arrête à
    # l'appel simple passerait au vert sur un modèle qui casse le lab de l'après-midi.
    try:
        runtime.converse(
            modelId=model_id,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": "Latence de l'endpoint qc-g01-endpoint ?"}],
                }
            ],
            inferenceConfig={"maxTokens": 128},
            toolConfig={
                "tools": [
                    {
                        "toolSpec": {
                            "name": "get_endpoint_metrics",
                            "description": "Latence moyenne d'un endpoint SageMaker.",
                            "inputSchema": {
                                "json": {
                                    "type": "object",
                                    "properties": {"endpoint_name": {"type": "string"}},
                                    "required": ["endpoint_name"],
                                }
                            },
                        }
                    }
                ]
            },
        )
    except Exception as exc:  # noqa: BLE001
        details.append(f"tool use : REFUSÉ ({_code(exc)})")
        record(
            7,
            "Bedrock",
            FAIL,
            f"{model_id} accepte Converse mais refuse le tool use.",
            "Choisir un modèle supportant le tool use — l'agent du J2 en dépend. "
            "Lancer `make probe` pour tester les candidats et le pilotage par Strands.",
            details=details,
        )
        return

    details.append("tool use : OK")
    details.append(
        "rappel D15 : Strands doit être instancié avec streaming=False sur ce modèle"
    )
    record(7, "Bedrock", PASS, model_id, details=details)


# ============================================================================ 8
def check_ecr() -> None:
    try:
        ecr = config.client("ecr")
        ecr.get_authorization_token()
    except Exception as exc:  # noqa: BLE001
        record(
            8,
            "ECR",
            FAIL,
            f"get_authorization_token échoue ({_code(exc)}).",
            "Ajouter ecr:GetAuthorizationToken au rôle du groupe.",
        )
        return
    try:
        ecr.describe_repositories(repositoryNames=[config.ecr_repo])
        detail = f"dépôt {config.ecr_repo} : existe"
    except Exception:  # noqa: BLE001
        detail = f"dépôt {config.ecr_repo} : absent (créé par le socle)"
    record(8, "ECR", PASS, details=[detail, "jeton d'authentification : OK"])


# ============================================================================ 9
def check_ecs_alb_acm() -> None:
    details, ok = [], True

    try:
        clusters = config.client("ecs").describe_clusters(clusters=[config.ecs_cluster])
        found = bool(clusters["clusters"])
        details.append(f"cluster {config.ecs_cluster} : " + ("OK" if found else "ABSENT"))
        ok &= found
    except Exception as exc:  # noqa: BLE001
        details.append(f"cluster : erreur ({_code(exc)})")
        ok = False

    try:
        svc = config.client("ecs").describe_services(
            cluster=config.ecs_cluster, services=[config.ecs_service]
        )
        found = bool(svc["services"])
        details.append(f"service {config.ecs_service} : " + ("OK" if found else "ABSENT"))
        ok &= found
    except Exception as exc:  # noqa: BLE001
        details.append(f"service : erreur ({_code(exc)})")
        ok = False

    # ACM : mode dégradé retenu (§14). Un ARN vide est la valeur NORMALE, pas un manque.
    if not config.acm_cert_arn:
        details.append("ACM : non utilisé — mode dégradé HTTP assumé (§14)")
    else:
        try:
            cert = config.client("acm").describe_certificate(
                CertificateArn=config.acm_cert_arn
            )["Certificate"]
            issued = cert["Status"] == "ISSUED"
            details.append(f"certificat ACM : {cert['Status']}")
            ok &= issued
        except Exception as exc:  # noqa: BLE001
            details.append(f"certificat ACM : erreur ({_code(exc)})")
            ok = False

    if config.alb_dns_name:
        try:
            socket.gethostbyname(config.alb_dns_name)
            details.append(f"{config.alb_dns_name} : résout")
        except OSError:
            details.append(f"{config.alb_dns_name} : NE RÉSOUT PAS")
            ok = False
    else:
        details.append("ALB_DNS_NAME non renseigné")
        ok = False

    record(
        9,
        "ECS / ALB / ACM",
        PASS if ok else FAIL,
        action="Demander au formateur l'extrait `.env` de votre groupe.",
        needs_socle=True,
        details=details,
    )


# ============================================================================ 10
def check_cloudwatch() -> None:
    logs = config.client("logs")
    # Le nom de la sonde vit DANS le préfixe du groupe : le rôle workstation n'autorise
    # les écritures logs que sous /ecs/qc-<TEAM_ID>-*. Un nom hors périmètre (l'ancien
    # /preflight/<TEAM_ID>) faisait échouer ce contrôle sur tout poste apprenant alors
    # que les droits réellement utiles étaient là — constaté le 10/08/2026.
    name = f"/ecs/qc-{config.team_id}-preflight-probe"
    try:
        logs.create_log_group(logGroupName=name)
        logs.delete_log_group(logGroupName=name)
    except Exception as exc:  # noqa: BLE001
        record(
            10,
            "CloudWatch Logs",
            FAIL,
            f"Création/suppression d'un groupe de test refusée ({_code(exc)}).",
            "Ajouter logs:CreateLogGroup et logs:DeleteLogGroup au rôle du groupe.",
        )
        return
    record(10, "CloudWatch Logs", PASS, details=["création puis suppression : OK"])


# ============================================================================ 11
def check_docker() -> None:
    if not shutil.which("docker"):
        record(
            11,
            "Docker",
            FAIL,
            "Binaire docker introuvable.",
            "Installer Docker. Sur les workstations, il est fourni par la golden AMI.",
        )
        return
    details = []
    try:
        subprocess.run(
            ["docker", "info"], capture_output=True, check=True, timeout=30
        )
        details.append("démon : répond")
    except Exception:  # noqa: BLE001
        record(
            11,
            "Docker",
            FAIL,
            "Le démon Docker ne répond pas.",
            "Démarrer le service Docker.",
        )
        return
    try:
        out = subprocess.run(
            ["docker", "buildx", "ls"], capture_output=True, text=True, timeout=30
        ).stdout
        details.append("buildx : disponible")
        amd64 = "linux/amd64" in out
        details.append("plateforme linux/amd64 : " + ("supportée" if amd64 else "ABSENTE"))
        record(11, "Docker", PASS if amd64 else FAIL, details=details,
               action="Activer l'émulation linux/amd64 (§11 impose cette plateforme).")
    except Exception:  # noqa: BLE001
        details.append("buildx : ABSENT")
        record(11, "Docker", FAIL, action="Installer docker buildx.", details=details)


# ============================================================================ 12
def check_terraform() -> None:
    if not shutil.which("terraform"):
        record(
            12,
            "Terraform",
            FAIL,
            "Binaire terraform introuvable.",
            "Installer Terraform >= 1.6.",
        )
        return
    try:
        out = subprocess.run(
            ["terraform", "version", "-json"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        import json

        version = json.loads(out)["terraform_version"]
    except Exception:  # noqa: BLE001
        record(12, "Terraform", FAIL, "Version illisible.", "Vérifier l'installation.")
        return

    major, minor = (int(x) for x in version.split(".")[:2])
    ok = (major, minor) >= (1, 6)
    details = [f"version {version}"]

    tfdir = ROOT / "infra" / "terraform"
    if not tfdir.exists():
        details.append("infra/terraform/ absent — socle pas encore écrit")
        record(12, "Terraform", PASS if ok else FAIL,
               action="Terraform >= 1.6 requis.", details=details)
        return

    try:
        subprocess.run(
            ["terraform", "init", "-backend=false"],
            cwd=tfdir, capture_output=True, check=True, timeout=180,
        )
        details.append("terraform init : OK")
    except Exception:  # noqa: BLE001
        details.append("terraform init : ÉCHEC")
        ok = False

    record(12, "Terraform", PASS if ok else FAIL,
           action="Terraform >= 1.6 et un init valide sont requis.", details=details)


# ============================================================================ 13
def check_python() -> None:
    details = [f"interpréteur {sys.version_info.major}.{sys.version_info.minor}"]

    lock = ROOT / "uv.lock"
    if not lock.exists():
        record(13, "Python", FAIL, "uv.lock absent.",
               "Lancer `make lock`. Sans lock, deux binômes peuvent installer deux "
               "résolutions différentes.", details=details)
        return
    if not shutil.which("uv"):
        record(13, "Python", SKIP, "uv introuvable, environnement non testé.",
               details=details)
        return

    # `--frozen` échoue si uv.lock ne correspond plus à pyproject.toml : on valide donc
    # à la fois la cohérence du lock ET le fait que tout s'importe ensemble.
    imports = "import boto3, strands, evidently, mlflow, sagemaker_mlflow, streamlit"
    try:
        subprocess.run(
            ["uv", "run", "--frozen", "python", "-c", imports],
            cwd=ROOT, capture_output=True, check=True, timeout=900,
        )
        details.append("uv.lock cohérent avec pyproject.toml, imports OK")
        record(13, "Python", PASS, details=details)
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or b"").decode(errors="replace").strip().splitlines()[-3:]
        details.extend(tail)
        record(13, "Python", FAIL, "Lock désynchronisé ou import en échec.",
               "Lancer `make lock` puis relancer le preflight.", details=details)
    except subprocess.TimeoutExpired:
        record(13, "Python", SKIP, "Installation trop longue (>15 min).", details=details)


# ============================================================================ 14
def check_mlflow() -> None:
    """Contrôle rendu BLOQUANT par la décision D8.

    §12 prévoyait un SKIP si MLFLOW_TRACKING_URI était vide. Ce n'est plus tenable :
    le lab fil rouge 6 du J3 trace ses rapports Evidently dans MLflow. Un preflight
    vert sans MLflow signifierait une demi-journée de lab sans cible.
    """
    uri = config.mlflow_tracking_uri
    if not uri:
        record(
            14,
            "MLflow",
            FAIL,
            "MLFLOW_TRACKING_URI non renseigné.",
            "Demander au formateur l'extrait `.env` mis à jour après création du tracking server.",
            needs_socle=True,
        )
        return
    try:
        sm = config.client("sagemaker")
        name = uri.split("/")[-1]
        info = sm.describe_mlflow_tracking_server(TrackingServerName=name)
        status = info["TrackingServerStatus"]
        ok = status == "Created"
        record(
            14,
            "MLflow",
            PASS if ok else FAIL,
            f"tracking server {name}",
            "Attendre la fin du provisioning, ou recréer le serveur.",
            details=[f"statut : {status}", f"expérience du groupe : {config.mlflow_experiment}"],
        )
    except Exception as exc:  # noqa: BLE001
        record(
            14,
            "MLflow",
            FAIL,
            f"Tracking server injoignable ({_code(exc)}).",
            "Vérifier MLFLOW_TRACKING_URI et le droit sagemaker-mlflow sur le rôle.",
            needs_socle=True,
        )


# ============================================================================ 15
def check_costs() -> None:
    record(
        15,
        "Coûts et tags",
        PASS,
        "Rappel, pas un contrôle bloquant.",
        details=[
            f"endpoint {config.endpoint_instance} : facturé tant qu'il tourne — "
            "l'éteindre chaque soir",
            "service Fargate et passerelle NAT : facturés en continu",
            "tracking server MLflow : facturé à l'heure",
            "tags appliqués : " + ", ".join(f"{k}={v}" for k, v in config.tags.items()),
            "`make destroy` s'appuie sur ces tags — sans eux, rien n'est nettoyé",
        ],
    )


# ============================================================================
def write_report() -> None:
    lines = [
        "# Rapport de preflight",
        "",
        f"- Région : `{config.region}`",
        f"- Groupe : `{config.team_id}`",
        f"- Modèle Bedrock : `{config.bedrock_model_id or '(non renseigné)'}`",
        "",
        "Ce rapport est joignable tel quel à une demande d'accès.",
        "",
        "| # | Contrôle | État | Cause |",
        "| --- | --- | --- | --- |",
    ]
    for r in results:
        lines.append(f"| {r.number} | {r.name} | **{r.status}** | {r.cause or '—'} |")

    failures = [r for r in results if r.status == FAIL]
    if failures:
        lines += ["", "## Échecs et actions correctives", ""]
        for r in failures:
            lines.append(f"### {r.number}. {r.name}")
            lines.append("")
            if r.cause:
                lines.append(f"- Cause : {r.cause}")
            for d in r.details:
                lines.append(f"- {d}")
            if r.action:
                lines.append(f"- **Action** : {r.action}")
            if r.needs_socle:
                lines.append(
                    "- Ce contrôle dépend du socle Terraform. Un échec est **normal** "
                    "tant que `make socle-apply` n'a pas été exécuté."
                )
            lines.append("")

    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parseur = argparse.ArgumentParser(
        description="15 contrôles avant toute création de ressource."
    )
    parseur.add_argument(
        "--formateur",
        action="store_true",
        help="active la sonde d'écriture iam:CreateRole (nécessaire à `make"
        " socle-apply`). Par défaut — mode apprenant — aucun appel d'écriture IAM"
        " n'est émis.",
    )
    options = parseur.parse_args(argv)

    print()
    print("=" * 72)
    mode = "formateur" if options.formateur else "apprenant"
    print(
        f"  Preflight ({mode}) — {config.project} — groupe {config.team_id} — {config.region}"
    )
    print("=" * 72)
    print()

    caller = check_identity()
    check_region()
    check_permissions(caller, formateur=options.formateur)
    check_s3()
    check_sagemaker_quotas()
    check_sagemaker_role()
    check_bedrock()
    check_ecr()
    check_ecs_alb_acm()
    check_cloudwatch()
    check_docker()
    check_terraform()
    check_python()
    check_mlflow()
    check_costs()

    write_report()

    failures = [r for r in results if r.status == FAIL]
    socle_only = [r for r in failures if r.needs_socle]

    print()
    print("=" * 72)
    counts = {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, SKIP)}
    print(f"  {counts[PASS]} PASS · {counts[FAIL]} FAIL · {counts[SKIP]} SKIP")
    if failures:
        print()
        print("  Échecs : " + ", ".join(f"{r.number}. {r.name}" for r in failures))
        if len(socle_only) == len(failures):
            print()
            print("  Tous ces échecs dépendent du socle Terraform, pas encore appliqué.")
            print("  C'est le comportement attendu à ce stade.")
    print(f"  Rapport écrit dans {REPORT.name}")
    print("=" * 72)
    print()

    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfigError as exc:
        print(f"\nConfiguration invalide.\n\n{exc}\n", file=sys.stderr)
        sys.exit(1)
