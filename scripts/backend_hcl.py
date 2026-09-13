#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3>=1.35"]
# ///
"""
Régénère `infra/terraform/socle/backend.hcl` sans passer par le module bootstrap.

Pourquoi ce script existe : `backend.hcl` est ignoré par git (il porte un identifiant
de compte), et il n'était produit que par `make bootstrap-apply`, dont l'état est
LOCAL. Sur un clone frais — la machine du formateur prestataire, une EC2 d'admin —
l'état local est absent : `bootstrap-apply` tente de recréer le bucket, échoue parce
qu'il existe déjà, et la ligne qui écrit `backend.hcl` n'est jamais atteinte. Tout le
reste se bloque en cascade : `socle-init`, puis `team-init` qui lit le même fichier.

Or les cinq lignes de `backend.hcl` sont entièrement déterministes : le nom du bucket
se dérive du compte appelant, exactement comme dans le module bootstrap et dans
`qc.config`. Aucun état Terraform n'est nécessaire pour les reconstituer.

    make backend-hcl

Le script vérifie que le bucket existe réellement. S'il n'existe pas, c'est que le
bootstrap n'a jamais été appliqué sur ce compte : il renvoie vers `make bootstrap-apply`
au lieu d'écrire un fichier qui pointerait dans le vide.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
CIBLE = ROOT / "infra" / "terraform" / "socle" / "backend.hcl"


def main() -> int:
    region = os.environ.get("AWS_REGION", "eu-west-3")

    try:
        compte = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    except ClientError as exc:
        print(f"Identité AWS indisponible : {exc}", file=sys.stderr)
        print("Vérifier les credentials (aws sts get-caller-identity).", file=sys.stderr)
        return 1

    # Même dérivation que bootstrap/main.tf et qc.config : les six derniers chiffres.
    bucket = f"qc-promo-tfstate-{compte[6:12]}"

    try:
        boto3.client("s3", region_name=region).head_bucket(Bucket=bucket)
    except ClientError:
        print(f"Bucket d'état {bucket} introuvable sur le compte {compte}.", file=sys.stderr)
        print("Le bootstrap n'a jamais tourné ici : make bootstrap-apply", file=sys.stderr)
        return 1

    CIBLE.write_text(
        f'bucket       = "{bucket}"\n'
        'key          = "socle/terraform.tfstate"\n'
        f'region       = "{region}"\n'
        "encrypt      = true\n"
        "use_lockfile = true\n"
    )
    print(f"backend.hcl écrit — bucket {bucket}, région {region}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
