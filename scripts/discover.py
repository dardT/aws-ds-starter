#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3>=1.35"]
# ///
"""
Sonde de découverte du compte AWS — à exécuter AVANT d'écrire quoi que ce soit d'autre.

Ce script ne crée aucune ressource. Il répond aux questions ouvertes du fichier
`entreprise_decisions.md` qui ne peuvent pas être tranchées de mémoire :

  D5   quels types d'instances SageMaker sont disponibles, et quels quotas
  D6   la région utilise-t-elle des profils d'inférence `eu.*`
  D8   SageMaker managed MLflow est-il disponible en eu-west-3
  D14  quels modèles Bedrock sont au catalogue, et lesquels sont réellement ACTIVÉS

Hygiène des secrets : ce script lit `.env` pour la région et les credentials, mais
n'affiche jamais une valeur de credential. Seul l'ARN de l'appelant est affiché,
ce qui est une information d'identité, pas un secret.

    uv run scripts/discover.py

uv résout boto3 dans un environnement jetable à partir de l'en-tête ci-dessus.
Aucune installation globale, aucun besoin de l'AWS CLI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError, EndpointConnectionError
except ImportError:
    sys.exit("Lancer ce script via uv :  uv run scripts/discover.py")


ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "discovery-report.md"

# Familles de modèles à repérer dans le catalogue, dans l'ordre de l'échelle de
# repli décidée en D14.
FALLBACK_LADDER = [
    ("Amazon Nova Lite", ("nova-lite",)),
    ("Amazon Nova Pro", ("nova-pro",)),
    ("Mistral Large", ("mistral.mistral-large", "mistral-large")),
    ("Claude Sonnet", ("claude-sonnet", "claude-3-5-sonnet", "claude-3-7-sonnet")),
]

lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    lines.append(text)


def section(title: str) -> None:
    say()
    say(f"## {title}")
    say()


def load_dotenv(path: Path) -> None:
    """Charge .env dans l'environnement. N'affiche aucune valeur."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if value and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    load_dotenv(ROOT / ".env")
    region = os.environ.get("AWS_REGION", "eu-west-3")

    say("# Rapport de découverte — compte AWS l'entreprise")
    say()
    say(f"Région sondée : **{region}**")
    say()
    say("Ce rapport ne crée aucune ressource. Il sert à trancher les points laissés")
    say("ouverts dans `entreprise_decisions.md` (D5, D6, D8, D14).")

    session = boto3.Session(region_name=region)

    # ---------------------------------------------------------------- identité
    section("1. Identité et région")
    try:
        ident = session.client("sts").get_caller_identity()
    except (NoCredentialsError, ClientError, EndpointConnectionError) as exc:
        say(f"ÉCHEC — impossible de s'authentifier : {exc}")
        say()
        say("Renseigner AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY dans `.env`,")
        say("ou configurer un profil AWS CLI.")
        REPORT.write_text("\n".join(lines) + "\n")
        return 1

    account_id = ident["Account"]
    say(f"- Compte      : `{account_id}`")
    say(f"- Appelant    : `{ident['Arn']}`")
    say(f"- Suffixe bucket dérivé (D7) : `qc-<TEAM_ID>-data-{account_id[-6:]}`")

    # ---------------------------------------------------------------- bedrock
    section("2. Bedrock — catalogue (D14)")
    catalogue: list[dict] = []
    try:
        bedrock = session.client("bedrock")
        catalogue = bedrock.list_foundation_models().get("modelSummaries", [])
        say(f"{len(catalogue)} modèles au catalogue en {region}.")
        say()
        say("| Recherché | Présent au catalogue | modelId |")
        say("| --- | --- | --- |")
        for label, needles in FALLBACK_LADDER:
            hits = [
                m["modelId"]
                for m in catalogue
                if any(n in m["modelId"].lower() for n in needles)
            ]
            mark = "oui" if hits else "**NON**"
            say(f"| {label} | {mark} | {', '.join(hits) if hits else '—'} |")
    except (ClientError, EndpointConnectionError) as exc:
        say(f"ÉCHEC — `bedrock:ListFoundationModels` : {exc}")

    # ------------------------------------------------- profils d'inférence
    section("3. Bedrock — profils d'inférence (D6)")
    say("Rappel : en Europe, les modèles récents s'invoquent souvent via un profil")
    say("d'inférence cross-région préfixé `eu.`, absent de `list_foundation_models`.")
    say()
    profiles: list[dict] = []
    try:
        profiles = session.client("bedrock").list_inference_profiles().get(
            "inferenceProfileSummaries", []
        )
        if not profiles:
            say("Aucun profil d'inférence. La région utilise les modelId bruts.")
        else:
            say(f"{len(profiles)} profils disponibles :")
            say()
            say("| inferenceProfileId | Nom |")
            say("| --- | --- |")
            for p in profiles:
                say(f"| `{p['inferenceProfileId']}` | {p.get('inferenceProfileName', '')} |")
    except (ClientError, EndpointConnectionError) as exc:
        say(f"Non disponible : {exc}")

    # ----------------------------------------- accès réel (le seul qui compte)
    section("4. Bedrock — accès RÉEL par appel Converse (D14)")
    say("Seul contrôle qui prouve l'activation. La présence au catalogue ne vaut pas accès.")
    say()

    candidates: list[str] = []
    for _, needles in FALLBACK_LADDER:
        for p in profiles:
            pid = p["inferenceProfileId"]
            if any(n in pid.lower() for n in needles):
                candidates.append(pid)
        for m in catalogue:
            mid = m["modelId"]
            if any(n in mid.lower() for n in needles) and "ON_DEMAND" in m.get(
                "inferenceTypesSupported", []
            ):
                candidates.append(mid)

    # dédoublonnage en conservant l'ordre de l'échelle de repli
    seen: set[str] = set()
    candidates = [c for c in candidates if not (c in seen or seen.add(c))]

    if not candidates:
        say("Aucun candidat trouvé. Vérifier la page « Model access » de la console.")
    else:
        runtime = session.client("bedrock-runtime")
        say("| Identifiant testé | Appel Converse | Tool use |")
        say("| --- | --- | --- |")
        for model_id in candidates:
            verdict, tools = _probe_converse(runtime, model_id)
            say(f"| `{model_id}` | {verdict} | {tools} |")

    # -------------------------------------------------------------- sagemaker
    section("5. SageMaker — disponibilité et quotas (D5)")
    for instance in ("ml.m5.large",):
        for quota_kind, label in (
            ("training job usage", "entraînement"),
            ("endpoint usage", "endpoint"),
        ):
            value = _quota(session, f"{instance} for {quota_kind}")
            say(f"- Quota `{instance}` ({label}) : **{value}**")
    say()
    say("Rappel D4 : ce quota doit être ≥ au nombre de binômes AVEC MARGE, car tous")
    say("les groupes lancent `make restore-day1` en même temps le matin du J2.")

    # ----------------------------------------------------------------- mlflow
    section("6. SageMaker managed MLflow (D8)")
    try:
        sm = session.client("sagemaker")
        servers = sm.list_mlflow_tracking_servers().get("TrackingServerSummaries", [])
        say(f"Disponible en {region}. {len(servers)} tracking server(s) existant(s).")
        for s in servers:
            say(f"- `{s['TrackingServerName']}` — statut {s.get('TrackingServerStatus')}")
        if not servers:
            say()
            say("Aucun serveur existant : à créer dans le socle (~20 min de provisioning).")
    except AttributeError:
        say("**INDISPONIBLE** — le SDK boto3 installé ne connaît pas cette API.")
        say("Mettre boto3 à jour, puis relancer.")
    except (ClientError, EndpointConnectionError) as exc:
        say(f"**INDISPONIBLE en {region}** : {exc}")
        say()
        say("Conséquence : **D8 est à rouvrir**. Le lab 6 du J3 n'a plus de cible.")
        say("Repli identifié : MLflow local sur la workstation — à re-soumettre au")
        say("formateur, pas à basculer silencieusement.")

    # ------------------------------------------------------- services du socle
    section("7. Autres services du socle")
    for label, client_name, method in (
        ("ECR", "ecr", "describe_repositories"),
        ("ECS", "ecs", "list_clusters"),
        ("ELBv2", "elbv2", "describe_load_balancers"),
        ("CloudWatch Logs", "logs", "describe_log_groups"),
    ):
        try:
            getattr(session.client(client_name), method)()
            say(f"- {label} : accessible")
        except (ClientError, EndpointConnectionError) as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "?")
            say(f"- {label} : **ÉCHEC** ({code})")

    say()
    say("---")
    say()
    say(f"Rapport écrit dans `{REPORT.name}`.")

    REPORT.write_text("\n".join(lines) + "\n")
    return 0


def _probe_converse(runtime, model_id: str) -> tuple[str, str]:
    """Appel Converse minimal, puis sonde tool use. Retourne (verdict, tool_use)."""
    try:
        runtime.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Dis OK."}]}],
            inferenceConfig={"maxTokens": 10},
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "?")
        return f"**refusé** ({code})", "—"
    except Exception as exc:  # noqa: BLE001 — on veut voir toute erreur ici
        return f"**erreur** ({type(exc).__name__})", "—"

    try:
        runtime.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Quel temps à Paris ?"}]}],
            inferenceConfig={"maxTokens": 64},
            toolConfig={
                "tools": [
                    {
                        "toolSpec": {
                            "name": "get_weather",
                            "description": "Météo d'une ville.",
                            "inputSchema": {
                                "json": {
                                    "type": "object",
                                    "properties": {"city": {"type": "string"}},
                                    "required": ["city"],
                                }
                            },
                        }
                    }
                ]
            },
        )
        return "OK", "OK"
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "?")
        return "OK", f"**non** ({code})"


def _quota(session, quota_name: str) -> str:
    """Cherche un quota SageMaker par nom. Retourne la valeur ou un message."""
    try:
        sq = session.client("service-quotas")
        paginator = sq.get_paginator("list_service_quotas")
        for page in paginator.paginate(ServiceCode="sagemaker"):
            for q in page["Quotas"]:
                if q["QuotaName"].lower() == quota_name.lower():
                    return str(int(q["Value"]))
        return "quota introuvable (nom exact à vérifier en console)"
    except (ClientError, EndpointConnectionError) as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "?")
        return f"non lisible ({code})"


if __name__ == "__main__":
    sys.exit(main())
