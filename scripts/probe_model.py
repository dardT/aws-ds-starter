#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3>=1.35", "strands-agents>=0.1"]
# ///
"""
Deux vérifications ciblées avant de calibrer `app/prompts.py` et d'écrire l'agent.

1. Mistral Pixtral Large 25.02 est-il activé ?
   La sonde initiale l'a silencieusement ignoré : le motif de recherche cherchait
   « mistral-large », qui n'est pas une sous-chaîne de « pixtral-large ». S'il est
   activé, on obtient un modèle de 2025 au lieu de 2024 pour le même niveau d'accès.

2. Le SDK Strands pilote-t-il réellement un modèle non-Anthropic ?
   La sonde a prouvé que l'API Converse fait du tool use avec Mistral. Elle n'a PAS
   prouvé que le provider Bedrock de Strands sait en exploiter le format d'appel
   d'outil. C'est la seule dépendance dure de §11 : si ça ne marche pas, le choix du
   framework se rouvre, et avec lui toute la progression du J2.

    uv run scripts/probe_model.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent

CANDIDATES = [
    "eu.mistral.pixtral-large-2502-v1:0",
    "mistral.mistral-large-2402-v1:0",
]


def load_dotenv(path: Path) -> None:
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


WEATHER_TOOL = {
    "tools": [
        {
            "toolSpec": {
                "name": "get_endpoint_metrics",
                "description": "Récupère la latence moyenne d'un endpoint SageMaker.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "endpoint_name": {"type": "string"},
                        },
                        "required": ["endpoint_name"],
                    }
                },
            }
        }
    ]
}


def probe_converse(runtime, model_id: str) -> str:
    """Vérifie l'appel simple puis le tool use. Retourne un verdict lisible."""
    try:
        runtime.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Dis OK."}]}],
            inferenceConfig={"maxTokens": 10},
        )
    except ClientError as exc:
        return f"refusé ({exc.response['Error']['Code']})"

    try:
        resp = runtime.converse(
            modelId=model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"text": "Quelle est la latence de l'endpoint qc-g01-endpoint ?"}
                    ],
                }
            ],
            inferenceConfig={"maxTokens": 256},
            toolConfig=WEATHER_TOOL,
        )
    except ClientError as exc:
        return f"appel OK, tool use refusé ({exc.response['Error']['Code']})"

    stop = resp.get("stopReason")
    used = any(
        "toolUse" in block for block in resp["output"]["message"].get("content", [])
    )
    if used or stop == "tool_use":
        return "OK — appel et tool use fonctionnels"
    return f"appel OK, mais l'outil n'a pas été invoqué (stopReason={stop})"


def probe_strands(model_id: str, region: str, *, streaming: bool = True) -> str:
    """Vérifie que Strands pilote ce modèle de bout en bout, outil compris."""
    try:
        from strands import Agent, tool
        from strands.models import BedrockModel
    except ImportError as exc:
        return f"SDK Strands non importable : {exc}"

    called: list[str] = []

    @tool
    def get_endpoint_metrics(endpoint_name: str) -> str:
        """Récupère la latence moyenne d'un endpoint SageMaker.

        Args:
            endpoint_name: Nom de l'endpoint à interroger.
        """
        called.append(endpoint_name)
        return "Latence moyenne sur 5 minutes : 412 ms. Taux d'erreur 5xx : 0 %."

    try:
        agent = Agent(
            model=BedrockModel(
                model_id=model_id, region_name=region, streaming=streaming
            ),
            tools=[get_endpoint_metrics],
            system_prompt=(
                "Tu es un assistant de diagnostic. Pour toute question portant sur les "
                "métriques d'un endpoint, tu DOIS appeler l'outil get_endpoint_metrics "
                "et citer la valeur exacte qu'il retourne."
            ),
        )
        result = agent("Quelle est la latence de l'endpoint qc-g01-endpoint ?")
    except Exception as exc:  # noqa: BLE001 — on veut voir toute erreur ici
        return f"ÉCHEC — {type(exc).__name__}: {exc}"

    if not called:
        return "l'agent a répondu mais N'A PAS appelé l'outil"
    return f"OK — outil appelé avec {called!r}\n     réponse : {str(result).strip()[:200]}"


def main() -> int:
    load_dotenv(ROOT / ".env")
    region = os.environ.get("AWS_REGION", "eu-west-3")
    session = boto3.Session(region_name=region)
    runtime = session.client("bedrock-runtime")

    print("=" * 72)
    print("1. Accès Converse et tool use, par modèle")
    print("=" * 72)
    working: list[str] = []
    for model_id in CANDIDATES:
        verdict = probe_converse(runtime, model_id)
        print(f"  {model_id:<42} {verdict}")
        if verdict.startswith("OK"):
            working.append(model_id)

    if not working:
        print("\nAucun modèle exploitable. Arrêt.")
        return 1

    target = working[0]
    print()
    print("=" * 72)
    print(f"2. Le SDK Strands pilote-t-il {target} ?")
    print("=" * 72)
    print(f"  streaming=True  (défaut Strands) : {probe_strands(target, region)}")
    print()
    print(
        "  streaming=False                  : "
        f"{probe_strands(target, region, streaming=False)}"
    )
    print()
    print(f"Modèle retenu si les deux tests passent : {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
