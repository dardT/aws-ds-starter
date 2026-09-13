#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3>=1.35"]
# ///
"""Vérifie l'état AWS réellement attendu en fin de J1.

Contrairement au preflight, qui contrôle des DROITS avant de créer quoi que ce soit,
celui-ci contrôle des RÉSULTATS : les objets sont-ils là, l'endpoint répond-il, la data
capture écrit-elle vraiment ?

La distinction compte pour le J3 : un endpoint en service dont la capture est inactive
paraît parfaitement sain, et le problème n'apparaît que deux jours plus tard, quand il
n'y a rien à comparer à la baseline.

    make check-day1

Sortie 0 si tout est vert, 1 dès qu'un contrôle échoue.
"""

from __future__ import annotations

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

echecs: list[str] = []


def verifie(titre: str, ok: bool, detail: str = "", correction: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {titre}")
    if detail:
        print(f"      {detail}")
    if not ok:
        echecs.append(titre)
        if correction:
            print(f"      → {correction}")


def main() -> int:
    print(f"\n  Vérification du J1 — groupe {config.team_id} — {config.region}\n")

    s3 = config.client("s3")
    sm = config.client("sagemaker")

    # --- 1. Les données sont dans S3, aux bons préfixes ------------------------------
    attendus = {
        "curated/train/train.csv",
        "curated/test/test.csv",
        "curated/sample.csv",
        "curated/timeline.csv",
    }
    presents = {
        o["Key"]
        for o in s3.list_objects_v2(Bucket=config.s3_bucket, Prefix="curated/").get(
            "Contents", []
        )
    }
    manquants = attendus - presents
    verifie(
        "Données déposées dans S3",
        not manquants,
        f"{len(attendus - manquants)}/{len(attendus)} fichiers dans {config.s3_bucket}",
        f"manquants : {', '.join(sorted(manquants))} — lancer `uv run qc upload`",
    )

    # --- 2. Un job d'entraînement a réussi -------------------------------------------
    jobs = sm.list_training_jobs(
        NameContains=config.resource("train"),
        StatusEquals="Completed",
        SortBy="CreationTime",
        SortOrder="Descending",
        MaxResults=1,
    )["TrainingJobSummaries"]
    verifie(
        "Job d'entraînement réussi",
        bool(jobs),
        jobs[0]["TrainingJobName"] if jobs else "",
        "lancer `uv run qc train`",
    )

    # --- 3. Le modèle a été évalué sur un jeu de validation ---------------------------
    # Les métriques `validation:*` viennent des `MetricDefinitions` du job : des motifs
    # lus dans les logs du script embarqué. Une liste vide signale un canal `validation`
    # manquant ou un job antérieur à la migration vers la régression logistique — dans
    # les deux cas le J3 n'aurait pas de référence.
    if jobs:
        detail = sm.describe_training_job(TrainingJobName=jobs[0]["TrainingJobName"])
        metriques = {
            m["MetricName"]: m["Value"] for m in detail.get("FinalMetricDataList", [])
        }
        ap = metriques.get("validation:average_precision")
        verifie(
            "Métrique de validation présente",
            ap is not None,
            f"validation:average_precision = {ap:.4f}" if ap is not None else "",
            "le job doit déclarer DEUX canaux, `train` et `validation`, et venir de"
            " `uv run qc train` (version régression logistique)",
        )

    # --- 4. L'endpoint est en service -------------------------------------------------
    try:
        endpoint = sm.describe_endpoint(EndpointName=config.endpoint_name)
        statut = endpoint["EndpointStatus"]
    except Exception:  # noqa: BLE001
        endpoint, statut = None, "ABSENT"
    verifie(
        "Endpoint en service",
        statut == "InService",
        f"{config.endpoint_name} : {statut}",
        "lancer `uv run qc deploy`",
    )

    # --- 5. La data capture est ACTIVÉE dans la configuration -------------------------
    capture_active = False
    if endpoint:
        cfg = sm.describe_endpoint_config(
            EndpointConfigName=endpoint["EndpointConfigName"]
        )
        capture = cfg.get("DataCaptureConfig", {})
        capture_active = capture.get("EnableCapture", False)
        verifie(
            "Data capture activée",
            capture_active,
            f"{capture.get('InitialSamplingPercentage', 0)} % vers"
            f" {capture.get('DestinationS3Uri', '—')}",
            "la capture se déclare dans l'EndpointConfig, pas sur l'endpoint :"
            " redéployer",
        )

    # --- 6. La data capture a réellement ÉCRIT ----------------------------------------
    # Le contrôle qui compte. Une configuration correcte ne garantit rien tant que
    # l'endpoint n'a pas été invoqué au moins une fois.
    if capture_active:
        objets = [
            o
            for o in s3.list_objects_v2(
                Bucket=config.s3_bucket, Prefix="capture/"
            ).get("Contents", [])
            if o["Size"] > 0
        ]
        verifie(
            "Capture écrite dans S3",
            bool(objets),
            f"{len(objets)} fichier(s) dans capture/",
            "invoquer l'endpoint au moins une fois : `uv run qc invoke`"
            " (comptez une à deux minutes avant l'écriture)",
        )

    print()
    if echecs:
        print(f"  {len(echecs)} contrôle(s) en échec : {', '.join(echecs)}\n")
        return 1
    print("  J1 complet.\n")
    print("  Ne pas oublier `uv run qc teardown` en fin de journée :")
    print("  l'endpoint est facturé tant qu'il tourne.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
