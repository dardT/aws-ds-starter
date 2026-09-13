"""Ramène le compte AWS à l'état attendu en fin de J1.

À lancer au matin du J2, pour un binôme qui a décroché la veille ou dont l'endpoint a été
supprimé le soir. Compter une quinzaine de minutes, dont l'essentiel est l'attente de
SageMaker : le lancer en arrière-plan pendant la masterclass du matin, pas devant la salle.

    make restore-day1

Le script est INCRÉMENTAL : il sonde l'état réel avant chaque étape et ne rejoue que ce
qui manque. Un binôme dont il ne reste que l'endpoint à recréer ne réentraîne pas.

Il n'exécute rien qu'il aurait réécrit lui-même : chaque étape appelle la fonction de
`qc.cli` que l'apprenant aurait lancée à la main. Deux implémentations de la même chose
finiraient par diverger, et c'est la version de rattrapage qui divergerait en silence.

Ce script restaure l'état AWS. Il ne touche pas au code : pour cela, c'est le tag git
`j1-fin` du dépôt starter.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from qc.config import ConfigError  # noqa: E402

try:
    from qc.config import DATA_DIR, config  # noqa: E402
except ConfigError as exc:
    print(f"Configuration incomplète : {exc}", file=sys.stderr)
    raise SystemExit(1) from None

from qc import cli, storage  # noqa: E402

FICHIERS = ("train.csv", "test.csv", "sample.csv", "timeline.csv")


# --------------------------------------------------------------------- sondes d'état
#
# Chaque sonde répond à « cette étape est-elle déjà faite ? » en interrogeant AWS, pas
# `.qc-state.json`. Un fichier d'état peut avoir été copié d'une autre machine, ou
# décrire un endpoint supprimé depuis.


def donnees_locales_pretes() -> bool:
    return all((DATA_DIR / f).is_file() for f in FICHIERS)


def donnees_deposees() -> bool:
    objets = config.client("s3").list_objects_v2(
        Bucket=config.s3_bucket, Prefix=f"{storage.PREFIX}/"
    )
    cles = {o["Key"] for o in objets.get("Contents", [])}
    return all(any(k.endswith(f) for k in cles) for f in FICHIERS)


def modele_entraine() -> bool:
    """Un job réussi ET son artefact encore présent dans S3.

    Vérifier le job seul ne suffit pas : SageMaker conserve les métadonnées d'un job
    pendant des mois, même après destruction du bucket. `restore` sautait alors
    l'entraînement, et `deploy` échouait sur « Could not find model data at s3://… » —
    message qui parle du modèle, jamais du bucket. Constaté le 28/07/2026 après un
    socle-destroy suivi d'un restore.
    """
    sm = config.client("sagemaker")
    jobs = sm.list_training_jobs(
        NameContains=config.resource("train"), StatusEquals="Completed", MaxResults=1
    )["TrainingJobSummaries"]
    if not jobs:
        return False

    artefact = sm.describe_training_job(TrainingJobName=jobs[0]["TrainingJobName"])[
        "ModelArtifacts"
    ]["S3ModelArtifacts"]
    _, _, reste = artefact.partition("s3://")
    bucket, _, cle = reste.partition("/")
    try:
        config.client("s3").head_object(Bucket=bucket, Key=cle)
    except Exception:  # noqa: BLE001
        return False
    return True


def statut_endpoint() -> str:
    try:
        return config.client("sagemaker").describe_endpoint(
            EndpointName=config.endpoint_name
        )["EndpointStatus"]
    except Exception:  # noqa: BLE001
        return "ABSENT"


# ------------------------------------------------------------------------ exécution


def executer(nom: str) -> None:
    """Lance une étape via `qc.cli`, exactement comme le ferait l'apprenant."""
    depart = time.monotonic()
    print(f"\n─── {nom} ───")
    resume = cli.BY_NAME[nom].run()
    cli._write_state(nom, resume)
    print(f"─── {nom} terminé en {int(time.monotonic() - depart)} s")


def etape(nom: str, deja_faite: bool, raison: str, forcer: bool) -> None:
    if deja_faite and not forcer:
        print(f"  ✓ {nom:<8} {raison}")
        return
    executer(nom)


def main(argv: list[str]) -> int:
    forcer = "--force" in argv

    print(f"\n  Restauration du J1 — groupe {config.team_id} — {config.region}")
    if forcer:
        print("  --force : toutes les étapes sont rejouées.")
    print()

    etape("load", donnees_locales_pretes(), "données déjà présentes dans data/", forcer)
    etape("upload", donnees_deposees(), "fichiers déjà dans S3", forcer)
    etape("train", modele_entraine(), "un job réussi existe déjà", forcer)

    statut = statut_endpoint()
    if statut == "InService" and not forcer:
        print(f"  ✓ deploy   {config.endpoint_name} déjà en service")
    elif statut in ("Creating", "Updating") and not forcer:
        # Un `restore` lancé deux fois, ou pendant un déploiement manuel. Recréer
        # échouerait ; il n'y a qu'à attendre.
        print(f"  … deploy   {config.endpoint_name} en cours ({statut}), on attend")
        from qc import inference

        inference.wait(config.endpoint_name)
    else:
        executer("deploy")

    # `invoke` est toujours rejouée, contrairement aux autres étapes. Deux secondes, et
    # c'est la seule preuve que l'endpoint qu'on vient de recréer répond vraiment.
    #
    # La sonde évidente — « capture/ contient-elle des lignes ? » — donne une fausse
    # réponse ici : elle voit la capture de la veille et saute l'étape, laissant le
    # binôme avec un endpoint jamais interrogé. Constaté au premier essai réel.
    executer("invoke")

    print("\n  État du J1 rétabli.")
    print("  Vérifier : make check-day1")
    print("  L'endpoint est FACTURÉ — `uv run qc teardown` en fin de journée.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
