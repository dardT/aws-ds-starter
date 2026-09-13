"""Casser pour apprendre à diagnostiquer — l'exercice du J3 (§8.4).

`inject()` choisit une panne parmi celles réalisables avec les droits de la machine du
binôme, la déclenche sur SES ressources, et ne dit pas laquelle : le diagnostic est
l'exercice. `heal()` constate ce qui est cassé, répare, et annonce ce qu'il a réparé —
c'est l'auto-correction du binôme.

Tout est borné au groupe de la machine : les trois pannes ne touchent que l'endpoint,
le bucket et le service ECS du binôme, exactement ce que le rôle d'instance autorise.
Aucune panne ne passe par IAM — le rôle apprenant n'a pas ces droits, et c'est voulu.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable

from botocore.exceptions import ClientError

from qc.config import DATA_DIR, ROOT, config

#: La panne en cours, notée pour que `heal` sache quoi vérifier en premier. Jamais
#: committé (voir .gitignore) : c'est un état local, comme .qc-state.json.
MARKER = ROOT / ".qc-chaos.json"

SAMPLE_KEY = "curated/sample.csv"


class ChaosError(RuntimeError):
    """Erreur avec message actionnable, affichée sans trace d'appels."""


@dataclass(frozen=True)
class Panne:
    code: str
    #: Affiché par `heal` UNIQUEMENT — `inject` reste muet sur ce qu'il a cassé.
    resume: str
    disponible: Callable[[], bool]
    injecter: Callable[[], None]
    constatee: Callable[[], bool]
    reparer: Callable[[], str]


# ------------------------------------------------------------------ panne : endpoint


def _endpoint_en_service() -> bool:
    sm = config.client("sagemaker")
    try:
        etat = sm.describe_endpoint(EndpointName=config.endpoint_name)["EndpointStatus"]
    except ClientError:
        return False
    return etat == "InService"


def _casser_endpoint() -> None:
    # L'EndpointConfig et le Model survivent : la réparation est un redéploiement,
    # pas un réentraînement — le même principe que le teardown du soir.
    config.client("sagemaker").delete_endpoint(EndpointName=config.endpoint_name)


def _endpoint_absent() -> bool:
    return not _endpoint_en_service()


def _reparer_endpoint() -> str:
    from qc import inference

    endpoint = inference.deploy(inference.latest_artifact())
    return (
        f"endpoint {endpoint.name} redéployé — compter ~3 minutes avant InService"
        " (`uv run qc invoke` pour vérifier)"
    )


# -------------------------------------------------------------- panne : échantillon


def _sample_present() -> bool:
    s3 = config.client("s3")
    try:
        s3.head_object(Bucket=config.s3_bucket, Key=SAMPLE_KEY)
    except ClientError:
        return False
    return True


def _casser_sample() -> None:
    # Le bucket est versionné : l'objet est masqué par un marqueur de suppression,
    # pas détruit. C'est le symptôme « page d'erreur, code 200, cible saine ».
    config.client("s3").delete_object(Bucket=config.s3_bucket, Key=SAMPLE_KEY)


def _sample_absent() -> bool:
    return not _sample_present()


def _reparer_sample() -> str:
    from qc import storage

    if not (DATA_DIR / "sample.csv").is_file():
        raise ChaosError(
            "data/sample.csv absent en local : `uv run qc load` le régénère,"
            " puis relancer `uv run qc heal`."
        )
    storage.upload(DATA_DIR, files=("sample.csv",))
    return f"{SAMPLE_KEY} redéposé (chiffré, tagué) dans {config.s3_bucket}"


# ------------------------------------------------------------------ panne : service


def _service_actif() -> bool:
    ecs = config.client("ecs")
    try:
        services = ecs.describe_services(
            cluster=config.ecs_cluster, services=[config.ecs_service]
        )["services"]
    except ClientError:
        return False
    return bool(services) and services[0].get("status") == "ACTIVE" and (
        services[0].get("desiredCount", 0) >= 1
    )


def _casser_service() -> None:
    config.client("ecs").update_service(
        cluster=config.ecs_cluster, service=config.ecs_service, desiredCount=0
    )


def _service_a_zero() -> bool:
    ecs = config.client("ecs")
    try:
        services = ecs.describe_services(
            cluster=config.ecs_cluster, services=[config.ecs_service]
        )["services"]
    except ClientError:
        return False
    return bool(services) and services[0].get("status") == "ACTIVE" and (
        services[0].get("desiredCount", 0) == 0
    )


def _reparer_service() -> str:
    config.client("ecs").update_service(
        cluster=config.ecs_cluster, service=config.ecs_service, desiredCount=1
    )
    return (
        f"service {config.ecs_service} relancé (desiredCount 1) — la cible redevient"
        " saine en 2-3 minutes"
    )


# ------------------------------------------------------------------------- registre

PANNES: tuple[Panne, ...] = (
    Panne(
        "endpoint",
        "endpoint supprimé — l'outil predire_conformite de l'agent échouait",
        _endpoint_en_service,
        _casser_endpoint,
        _endpoint_absent,
        _reparer_endpoint,
    ),
    Panne(
        "sample",
        "curated/sample.csv effacé — l'application servait sa page d'erreur (code 200)",
        _sample_present,
        _casser_sample,
        _sample_absent,
        _reparer_sample,
    ),
    Panne(
        "service",
        "service ECS à zéro — 503 derrière l'ALB, alarme service-sans-tache",
        _service_actif,
        _casser_service,
        _service_a_zero,
        _reparer_service,
    ),
)

PAR_CODE = {p.code: p for p in PANNES}


def _write_marker(code: str) -> None:
    MARKER.write_text(
        json.dumps({"panne": code, "quand": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        + "\n"
    )


def inject(code: str | None = None, choisir=None) -> str:
    """Déclenche une panne et n'annonce PAS laquelle.

    `code` force une panne précise (mode formateur, démos). `choisir` est le tirage,
    injectable dans les tests ; par défaut `random.choice`.
    """
    if code is not None:
        if code not in PAR_CODE:
            raise ChaosError(f"panne inconnue : {code} (choix : {', '.join(PAR_CODE)})")
        candidates = [PAR_CODE[code]]
        if not candidates[0].disponible():
            raise ChaosError(
                f"panne « {code} » impossible : la ressource visée n'est pas en service."
            )
    else:
        candidates = [p for p in PANNES if p.disponible()]

    if not candidates:
        raise ChaosError(
            "rien à casser : aucune ressource du binôme n'est en service.\n"
            "L'exercice suppose l'environnement du J2 en route — au minimum l'endpoint"
            " (`uv run qc deploy`), idéalement le service (`make team-apply`)."
        )

    if choisir is None:
        import random

        choisir = random.choice
    panne = choisir(candidates)
    panne.injecter()
    _write_marker(panne.code)
    return panne.code  # renvoyé pour les tests et le formateur ; la CLI ne l'affiche pas


def heal() -> list[str]:
    """Répare ce qui est cassé, et dit ce que c'était.

    Cas limite qui justifie le marqueur : un endpoint absent n'est pas forcément une
    panne — c'est aussi l'état normal après le `teardown` du soir. Le redéployer
    d'office relancerait la facturation. On ne le recrée donc que si le marqueur
    atteste que `chaos` l'a supprimé ; les deux autres pannes sont sans ambiguïté et
    se réparent marqueur ou pas.
    """
    marque: str | None = None
    if MARKER.exists():
        marque = json.loads(MARKER.read_text()).get("panne")

    reparations: list[str] = []
    for panne in PANNES:
        if not panne.constatee():
            continue
        if panne.code == "endpoint" and marque != "endpoint":
            reparations.append(
                "endpoint absent, mais rien n'indique que `chaos` l'a supprimé"
                " (teardown du soir ?) — redéployez vous-même si besoin :"
                " `uv run qc deploy`"
            )
            continue
        resultat = panne.reparer()
        reparations.append(f"panne réparée : {panne.resume}\n  → {resultat}")
    if MARKER.exists():
        MARKER.unlink()
    return reparations
