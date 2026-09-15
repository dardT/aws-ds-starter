#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3>=1.35"]
# ///
"""Ouvre un tunnel SSM vers la machine du groupe, sans AWS CLI ni binaire à installer.

Le chemin normal du SSH par Session Manager (voir `docs/REMOTE_DEV.md`) demande deux
installations : l'AWS CLI et le `session-manager-plugin`. Sur les postes Windows
d'entreprise, ces deux installations sont souvent interdites — l'apprenant a une clé
d'accès IAM et le droit d'installer des paquets Python, rien de plus. Sans repli, il
perd les trois jours de labs sur un problème de poste.

Ce script est ce repli. Il ne fait que deux choses :

  1. retrouver, par boto3, l'identifiant d'instance de la machine de SON groupe ;
  2. passer la main à `uvx pyssm-client port-forward`, qui ouvre un port TCP local
     tunnelé vers le port 22 de l'instance via le document `AWS-StartPortForwardingSession`.

`pyssm-client` est un paquet PyPI tiers, en Python pur, bâti sur boto3 : `uvx` le résout
dans un environnement jetable au moment de l'appel, avec son propre interpréteur. Rien
n'est installé globalement, et l'AWS CLI n'est jamais requise.

Conséquence sur le modèle de connexion : contrairement au `ProxyCommand`, ce tunnel est
un processus qui VIT. On le lance dans un terminal, on le laisse tourner, et on se
connecte depuis un autre terminal sur `localhost:<port local>`.

    make ssh-tunnel
    # puis, dans un autre terminal :
    ssh -p 2222 ubuntu@localhost

Limite connue, à vérifier avec le formateur si le tunnel ne s'ouvre jamais :
`pyssm-client` n'implémente pas le chiffrement KMS des sessions. Si les préférences
Session Manager du compte imposent une clé KMS, ce chemin reste muet — il faut alors
revenir au couple AWS CLI + session-manager-plugin.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from qc.config import ConfigError  # noqa: E402

try:
    from qc.config import config  # noqa: E402
except ConfigError as exc:
    print(f"Configuration incomplète : {exc}", file=sys.stderr)
    raise SystemExit(1) from None

# Port local par défaut. Fixe et documenté : le bloc `Host` du `~/.ssh/config` le code
# en dur, il ne doit donc pas changer d'une exécution à l'autre.
PORT_LOCAL = 2222


def instance_du_groupe() -> str | None:
    """Identifiant de la machine de travail du groupe courant, ou None.

    Même filtrage que `workstation_check.py`, restreint au seul TEAM_ID de l'appelant :
    ici on cherche une machine, pas la promotion entière.
    """
    reponse = config.client("ec2").describe_instances(
        Filters=[
            {"Name": "tag:Project", "Values": [config.project]},
            {"Name": "instance-state-name", "Values": ["running"]},
            {"Name": "tag:Name", "Values": ["qc-*-workstation"]},
        ]
    )
    for reservation in reponse["Reservations"]:
        for instance in reservation["Instances"]:
            etiquettes = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
            if etiquettes.get("Team") == config.team_id:
                return instance["InstanceId"]
    return None


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyseur.add_argument(
        "--local-port",
        type=int,
        default=PORT_LOCAL,
        help=f"port TCP local à ouvrir (défaut : {PORT_LOCAL})",
    )
    options = analyseur.parse_args()

    # uvx absent = uv absent : c'est le seul prérequis du poste, autant le dire ici
    # plutôt que de laisser échouer l'exec sur un FileNotFoundError nu.
    if shutil.which("uvx") is None:
        print(
            "\n  uvx introuvable — uv n'est pas installé (ou pas dans le PATH).\n"
            "  Installer uv, par exemple :  pip install uv\n",
            file=sys.stderr,
        )
        return 1

    instance = instance_du_groupe()
    if instance is None:
        print(
            f"\n  Aucune machine en fonctionnement pour le groupe {config.team_id}.\n"
            "  Les machines ne sont allumées que pour la formation : voir le formateur.\n",
            file=sys.stderr,
        )
        return 1

    # Affiché AVANT l'exec : le processus courant est remplacé juste après, rien de ce
    # qui suivrait cet appel ne serait exécuté.
    print(f"\n  Machine {config.team_id} : {instance}  —  {config.region}")
    print(f"  Port local ouvert : {options.local_port}")
    print("\n  Laisser ce terminal ouvert (Ctrl-C ferme le tunnel).")
    print("  Depuis un AUTRE terminal :")
    print(f"    ssh -p {options.local_port} ubuntu@localhost\n")

    commande = [
        "uvx",
        "--from",
        "pyssm-client",
        "pyssm",
        "port-forward",
        "--target",
        instance,
        "--remote-port",
        "22",
        "--local-port",
        str(options.local_port),
        "--region",
        config.region,
    ]
    # execvp et non subprocess : le tunnel devient CE processus, donc Ctrl-C et les
    # signaux du terminal l'atteignent directement, sans intermédiaire à réémettre.
    os.execvp(commande[0], commande)


if __name__ == "__main__":
    raise SystemExit(main())
