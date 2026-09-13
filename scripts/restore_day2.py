"""Ramène le compte AWS à l'état attendu en fin de J2.

À lancer au matin du J3, pour un binôme qui a décroché la veille ou dont le service a été
détruit le soir. Compter une dizaine de minutes, dont l'essentiel est la construction et
la poussée de l'image.

    make restore-day2

Comme `restore_day1.py`, le script est INCRÉMENTAL : il sonde l'état réel avant chaque
étape et ne rejoue que ce qui manque. Il commence d'ailleurs par appeler `restore_day1`,
puisque le J2 ne tient debout que si l'endpoint du J1 répond.

Ce qu'il ne fait PAS : appliquer le module `team/`. C'est du Terraform, il appartient au
binôme, et le rejouer à sa place lui retirerait le seul endroit où il voit un `plan`. Le
script s'arrête donc en affichant la commande.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from qc.config import ConfigError  # noqa: E402

try:
    from qc.config import config  # noqa: E402
except ConfigError as exc:
    print(f"Configuration incomplète : {exc}", file=sys.stderr)
    raise SystemExit(1) from None


def image_poussee() -> bool:
    """Une image existe-t-elle dans le dépôt ECR du groupe ?"""
    try:
        images = config.client("ecr").describe_images(
            repositoryName=config.ecr_repo.rsplit("/", 1)[-1]
        )["imageDetails"]
    except Exception:  # noqa: BLE001
        return False
    return bool(images)


def service_actif() -> bool:
    """Le service ECS tourne-t-il avec au moins une tâche ?"""
    try:
        services = config.client("ecs").describe_services(
            cluster=config.ecs_cluster, services=[config.ecs_service]
        )["services"]
    except Exception:  # noqa: BLE001
        return False
    return bool(services) and services[0]["status"] == "ACTIVE" and services[0]["runningCount"] >= 1


def _executer(commande: list[str], etape: str) -> None:
    depart = time.monotonic()
    print(f"\n─── {etape} ───")
    resultat = subprocess.run(commande, cwd=ROOT)
    if resultat.returncode != 0:
        raise SystemExit(f"{etape} a échoué (code {resultat.returncode}).")
    print(f"─── {etape} terminé en {int(time.monotonic() - depart)} s")


def construire_et_pousser() -> None:
    """Authentifie Docker auprès d'ECR, construit pour linux/amd64, pousse.

    `--platform linux/amd64` n'est pas optionnel : sans lui, une construction depuis une
    machine ARM produit une image que Fargate refuse de démarrer, et la tâche reste en
    PENDING avant d'échouer sur un message qui ne parle pas d'architecture.
    """
    import base64

    donnees = config.client("ecr").get_authorization_token()["authorizationData"][0]
    mot_de_passe = base64.b64decode(donnees["authorizationToken"]).decode().split(":", 1)[1]

    connexion = subprocess.run(
        ["docker", "login", "--username", "AWS", "--password-stdin", donnees["proxyEndpoint"]],
        input=mot_de_passe,
        text=True,
        capture_output=True,
        cwd=ROOT,
    )
    if connexion.returncode != 0:
        raise SystemExit(f"Authentification ECR refusée : {connexion.stderr.strip()}")

    _executer(
        [
            "docker", "buildx", "build",
            "--platform", "linux/amd64",
            "--push",
            "-t", f"{config.ecr_repo}:latest",
            ".",
        ],
        "image",
    )


def main(argv: list[str]) -> int:
    forcer = "--force" in argv

    print(f"\n  Restauration du J2 — groupe {config.team_id} — {config.region}\n")

    # Le J2 ne tient debout que si l'endpoint du J1 répond : l'agent l'interroge, et
    # l'application aussi. On délègue plutôt que de recopier les sondes.
    _executer(
        [sys.executable, str(ROOT / "scripts" / "restore_day1.py")] + (["--force"] if forcer else []),
        "J1",
    )

    if image_poussee() and not forcer:
        print("\n  ✓ image    déjà présente dans ECR")
    else:
        construire_et_pousser()

    if service_actif() and not forcer:
        print("  ✓ service  déjà en fonctionnement")
        print("\n  État du J2 rétabli.")
    else:
        # Le module `team/` appartient au binôme. Le rejouer à sa place lui retirerait le
        # seul endroit du parcours où il lit un `terraform plan`.
        print("\n  Service ECS absent. À lancer vous-même :")
        print(f"    make team-init  TEAM_ID={config.team_id}   # une seule fois")
        print(f"    make team-apply TEAM_ID={config.team_id}")

    print("\n  Vérifier : make check-day2")
    print("  L'endpoint ET la tâche sont facturés — les éteindre en fin de journée.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
