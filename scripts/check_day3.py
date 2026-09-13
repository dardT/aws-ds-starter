#!/usr/bin/env python3
"""Vérifie l'état AWS réellement attendu en fin de J3.

Comme `check_day2.py`, ce script tourne dans l'environnement du projet : il a besoin
d'Evidently, de MLflow et du paquet `qc`.

Six contrôles. Le troisième est celui qui compte : un rapport de dérive n'a de valeur que
si la comparaison a porté sur des colonnes communes. Evidently ignore en silence les
colonnes qu'il ne retrouve pas des deux côtés, et une comparaison portant sur rien renvoie
« aucune dérive » — ce qui ressemble à un succès.

    make check-day3
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
    from qc import monitoring

    print(f"\n  Vérification du J3 — groupe {config.team_id} — {config.region}\n")

    s3 = config.client("s3")

    # --- 1. Une baseline existe --------------------------------------------------------
    try:
        reference = monitoring.load_baseline()
        detail = f"{len(reference)} lignes, {reference.shape[1]} colonnes"
        ok = len(reference) > 0
    except Exception as exc:  # noqa: BLE001
        reference, ok, detail = None, False, str(exc).splitlines()[0]
    verifie("Baseline déposée dans S3", ok, detail, "lancer `uv run qc drift`")

    # --- 2. La production a été capturée -----------------------------------------------
    capture = None
    if reference is not None:
        colonnes = [c for c in reference.columns if c != monitoring.SCORE_COLUMN]
        try:
            capture = monitoring.read_capture(colonnes=colonnes)
            ok, detail = capture.lignes > 0, f"{capture.lignes} prédictions capturées"
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, str(exc).splitlines()[0]
        verifie(
            "Production capturée et exploitable",
            ok,
            detail,
            "invoquer l'endpoint, puis attendre une à deux minutes",
        )

    # --- 3. La comparaison porte sur des colonnes communes -----------------------------
    # LE contrôle du J3. Evidently ignore en silence les colonnes absentes d'un côté, et
    # une comparaison portant sur zéro colonne renvoie « aucune dérive ».
    if reference is not None and capture is not None:
        communes = [c for c in reference.columns if c in capture.frame.columns]
        verifie(
            "Colonnes communes entre référence et production",
            len(communes) >= 2,
            f"{len(communes)} colonne(s) comparables",
            "passer les noms de colonnes : monitoring.read_capture(colonnes=…)."
            " Sans eux, les colonnes de la capture sont numérotées et rien ne correspond.",
        )

    # --- 4. Un rapport a été publié ----------------------------------------------------
    rapports = [
        o
        for o in s3.list_objects_v2(
            Bucket=config.s3_bucket, Prefix=f"{monitoring.REPORTS_PREFIX}/"
        ).get("Contents", [])
        if o["Size"] > 0
    ]
    verifie(
        "Rapport de dérive publié",
        bool(rapports),
        f"{len(rapports)} fichier(s) dans reports/",
        "lancer `uv run qc drift`",
    )

    # --- 5. Les alarmes existent -------------------------------------------------------
    alarmes = monitoring.alarm_states()
    verifie(
        "Alarmes CloudWatch en place",
        len(alarmes) >= 3,
        ", ".join(f"{n.rsplit('-', 1)[-1]}={e}" for n, e in sorted(alarmes.items()))
        or "aucune",
        "appliquer la tranche 3 du socle : make socle-apply",
    )

    # --- 6. MLflow enregistre les exécutions -------------------------------------------
    # Une seule exécution ne sert à rien : c'est la comparaison entre plusieurs qui montre
    # la dérive. Le contrôle exige donc au moins deux runs.
    if config.mlflow_tracking_uri:
        try:
            import mlflow

            mlflow.set_tracking_uri(config.mlflow_tracking_uri)
            experience = mlflow.get_experiment_by_name(f"qc-{config.team_id}")
            runs = (
                mlflow.search_runs(experiment_ids=[experience.experiment_id])
                if experience
                else []
            )
            nombre = len(runs)
        except Exception as exc:  # noqa: BLE001
            nombre, detail = 0, str(exc).splitlines()[0]
        else:
            detail = f"{nombre} exécution(s) dans l'expérience qc-{config.team_id}"
        verifie(
            "Au moins deux exécutions dans MLflow",
            nombre >= 2,
            detail,
            "relancer `uv run qc drift` : une exécution isolée ne montre aucune tendance",
        )
    else:
        verifie(
            "Au moins deux exécutions dans MLflow",
            False,
            "MLFLOW_TRACKING_URI est vide dans .env",
            "create_mlflow = true dans le socle, puis"
            " demander au formateur l'extrait `.env` mis à jour du groupe",
        )

    print()
    if echecs:
        print(f"  {len(echecs)} contrôle(s) en échec : {', '.join(echecs)}\n")
        return 1
    print("  J3 complet.\n")
    print("  Fin de formation — tout éteindre :")
    print("    uv run qc teardown")
    print(f"    make team-destroy TEAM_ID={config.team_id}")
    print("    make socle-destroy\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
