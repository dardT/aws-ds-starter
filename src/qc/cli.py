"""Point d'entrée unique de l'application.

    uv run qc            tableau d'avancement — où en est le binôme
    uv run qc load       exécute une étape

Les modules du paquet n'écrivent jamais sur la sortie standard : ils calculent et
renvoient. Tout l'affichage est ici. C'est ce qui rend `qc.secom` testable sans capturer
de sortie, et ce qui permet de réutiliser les mêmes fonctions au J2 et au J3 sans
polluer les traces d'un agent.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Callable

from qc.config import DATA_DIR, ROOT, ConfigError

STATE_FILE = ROOT / ".qc-state.json"


@dataclass(frozen=True)
class Step:
    day: int
    name: str
    help: str
    run: Callable[[], str] | None    # renvoie le résumé affiché dans le tableau


# --------------------------------------------------------------------------- étapes


def _load() -> str:
    from qc import secom

    print("Téléchargement du jeu UCI SECOM…")
    dataset = secom.load()
    print(f"  {len(dataset.X)} pièces, {dataset.X.shape[1]} mesures capteurs")
    print(
        f"  {int(dataset.y.sum())} non conformes ({dataset.fail_rate:.1%})"
        " — fortement déséquilibré"
    )
    print(
        f"  période couverte : {dataset.timestamp.min():%d/%m/%Y}"
        f" → {dataset.timestamp.max():%d/%m/%Y}"
    )

    features = secom.frozen_features(dataset)
    print(f"{len(features)} variables retenues (liste figée, fait foi)")

    result = secom.split(dataset, features)
    result.write_csv(DATA_DIR)
    print(result.summary())
    print(f"\nDonnées prêtes dans {DATA_DIR.relative_to(ROOT)}/")

    return f"données prêtes ({len(result.X_train)}/{len(result.X_test)})"


def _upload() -> str:
    # Import local : nommer `config` au niveau module déclencherait sa construction
    # paresseuse dès le chargement de la CLI, et `qc --help` replanterait sur un .env
    # incomplet. Même raison que l'absence de `from qc import secom` en tête de fichier.
    from qc import storage
    from qc.config import config

    print(f"Dépôt vers {config.s3_bucket}…")
    report = storage.upload(DATA_DIR)
    print(report.summary())

    anomalies = storage.verify(report)
    if anomalies:
        print("\nAnomalies à la relecture :")
        for a in anomalies:
            print(f"  {a}")
        raise SystemExit(1)
    print("\nRelecture : tailles et chiffrement conformes")
    print(f"Canal d'entrée pour l'entraînement : {report.training_input}")

    return f"{len(report.uploads)} fichiers déposés ({report.total_bytes / 1024:.0f} Kio)"


def _train() -> str:
    from qc import storage, training
    from qc.config import config

    report = storage.Report(bucket=config.s3_bucket, prefix=storage.PREFIX, uploads=())
    validation = f"s3://{config.s3_bucket}/{storage.PREFIX}/test/"

    # Ni poids de classe ni seuil à passer : le script d'entraînement embarqué les
    # calcule lui-même sur le canal train — le déséquilibre pour pondérer les défauts,
    # le seuil F2 par validation croisée hors-fold. Voir model_assets/train_entry.py.
    print(f"Image     : {training.image_uri()}")
    print(f"Rôle      : {config.sagemaker_role_arn}")
    print(f"Canal train      : {report.training_input}")
    print(f"Canal validation : {validation}")

    job = training.submit(report.training_input, validation)
    print(f"\nJob soumis : {job.name}")
    print("Environ 4 minutes — l'essentiel est le démarrage du conteneur, pas le calcul.")

    result = training.wait(
        job.name,
        on_tick=lambda status, secs: print(f"  {secs // 60:02d}:{secs % 60:02d}  {status}"),
    )
    print()
    print(result.summary())

    if not result.ok:
        raise SystemExit(1)

    ap = result.metrics.get("validation:average_precision")
    return f"modèle entraîné (AP {ap:.3f})" if ap else "modèle entraîné"


def _deploy() -> str:
    from qc import inference

    artifact = inference.latest_artifact()
    print(f"Artefact  : {artifact}")

    endpoint = inference.deploy(artifact)
    print(endpoint.summary())
    print("\nEnviron 3 minutes — SageMaker provisionne une instance dédiée.")
    print("ATTENTION : à partir de « InService », l'endpoint est FACTURÉ tant qu'il tourne.")

    inference.wait(
        endpoint.name,
        on_tick=lambda status, secs: print(f"  {secs // 60:02d}:{secs % 60:02d}  {status}"),
    )
    print("\nInService — data capture active vers", endpoint.capture_uri)

    return f"endpoint en service ({INSTANCE_HINT})"


def _invoke() -> str:
    from qc import inference

    header, rows = inference.read_sample(DATA_DIR / "sample.csv")
    print(f"{len(rows)} pièces à évaluer, {len(header)} mesures chacune")

    predictions = inference.predict(rows)

    # Le seuil vient de l'artefact d'entraînement : sélectionné par F2 sur des
    # prédictions hors-fold du train, jamais un 0,50 par défaut.
    seuil = inference.decision_threshold()
    suspectes = [p for p in predictions if p.score >= seuil]
    print(f"\nSeuil de décision (F2, choisi à l'entraînement) : {seuil:.2f}")
    print(f"{len(suspectes)} pièce(s) signalée(s) sur {len(predictions)} :\n")
    for p in sorted(predictions, key=lambda p: -p.score)[:5]:
        print(f"  pièce {p.index:>2}   score {p.score:.4f}   {p.label(seuil)}")

    print(
        "\nLe score est une PROBABILITÉ, pas une décision. Le seuil appartient au métier :"
        "\nlaisser passer une pièce défectueuse ne coûte pas ce que coûte d'écarter une"
        "\nbonne pièce — celui-ci privilégie le rappel des défauts (F2), au prix d'un taux"
        "\nde revue d'environ 19 % des pièces."
    )
    return f"{len(suspectes)}/{len(predictions)} pièces signalées (seuil {seuil:.2f})"


def _teardown() -> str:
    from qc import inference

    supprimes = inference.teardown()
    if not supprimes:
        print("Aucun endpoint en service — rien à supprimer.")
        return "aucun endpoint"
    for item in supprimes:
        print(f"  supprimé : {item}")
    print("\nLa facturation de l'endpoint s'arrête ici.")
    return f"{len(supprimes)} ressource(s) supprimée(s)"


def _agent() -> str:
    from qc import agent
    from qc.config import config

    question = (
        "Parmi les pièces 0 à 19, lesquelles dois-je écarter ? "
        "Donne-moi le score de la plus suspecte et situe-la par rapport aux autres."
    )
    print(f"Modèle    : {config.bedrock_model_id}")
    print(f"Question  : {question}\n")

    trace = agent.ask(question)
    print(trace.summary())

    if not trace.a_consulte_le_modele:
        # Une réponse sans appel d'outil est tout aussi crédible à la lecture. Le signaler
        # ici évite qu'un binôme conclue au succès devant un texte inventé.
        print(
            "\nAVERTISSEMENT : l'agent n'a PAS interrogé l'endpoint."
            "\nSa réponse ne s'appuie sur aucune prédiction réelle."
        )
        return "agent sans appel d'outil"

    return f"agent opérationnel ({len(trace.outils_appeles)} outil(s))"


def _serve() -> str:
    """Rappelle la séquence de mise en service. Ne construit ni ne pousse rien.

    Construire une image et l'appliquer par Terraform depuis Python masquerait exactement
    ce que le J2 enseigne : `docker buildx`, `docker push`, `terraform apply`. Les
    commandes sont donc affichées pour être lancées à la main.
    """
    from qc.config import config

    registre = config.ecr_repo.split("/")[0] if "/" in config.ecr_repo else ""
    print("Mise en service — à lancer dans cet ordre :\n")
    print("  1. Authentifier Docker auprès d'ECR")
    print(
        "     aws ecr get-login-password --region "
        f"{config.region} | docker login --username AWS --password-stdin {registre}\n"
    )
    print("  2. Construire pour linux/amd64 — sans --platform, Fargate refuse l'image")
    print(f"     docker buildx build --platform linux/amd64 -t {config.ecr_repo}:latest .\n")
    print("  3. Pousser")
    print(f"     docker push {config.ecr_repo}:latest\n")
    print("  4. Déployer")
    print(f"     make team-apply TEAM_ID={config.team_id}\n")
    print("  5. Vérifier")
    print("     make check-day2")

    return "séquence affichée"


def _drift() -> str:
    from qc import monitoring

    # La baseline est construite UNE FOIS puis relue. Deux raisons, et la seconde n'est
    # pas évidente :
    #
    #   - une référence qui se reconstruit à chaque exécution n'est plus une référence ;
    #   - build_baseline() invoque l'endpoint, donc ses 392 prédictions sont CAPTURÉES.
    #     La reconstruire à chaque fois injecte le jeu de test dans la production, qui
    #     converge alors vers la référence — et toute dérive disparaît. Constaté le
    #     28/07/2026 : 22 colonnes dérivées à la première exécution, 0 à la seconde.
    try:
        reference = monitoring.load_baseline()
        print(f"Référence relue depuis S3 — {len(reference)} lignes")
    except monitoring.MonitoringError:
        print("Construction de la référence — le jeu de TEST passé par l'endpoint…")
        reference = monitoring.build_baseline()
        uri = monitoring.save_baseline(reference)
        print(f"  {len(reference)} lignes, {reference.shape[1] - 1} mesures + score")
        print(f"  déposée : {uri}")
    print()

    print("Lecture de la data capture…")
    colonnes = [c for c in reference.columns if c != monitoring.SCORE_COLUMN]
    # Seule la capture POSTÉRIEURE à la baseline est de la production : les 392
    # invocations qui ont servi à construire la référence ont été capturées elles aussi.
    capture = monitoring.read_capture(colonnes=colonnes, apres=monitoring.baseline_date())
    print(capture.summary())

    print("\nComparaison…")
    drift = monitoring.compare(reference, capture.frame)
    print(drift.summary())

    uri_rapport = monitoring.publish_report(drift.rapport_html)
    print(f"  publié : {uri_rapport}")

    # Une exécution isolée ne montre aucune tendance : c'est la suite des p-values qui
    # fait le sujet du J3.
    try:
        execution = monitoring.log_to_mlflow(drift, capture)
        print(f"  MLflow : exécution {execution}")
    except monitoring.MonitoringError as exc:
        print(f"  MLflow ignoré — {str(exc).splitlines()[0]}")

    if drift.score_a_derive:
        print(
            "\nLa distribution des scores a changé. Deux lectures possibles, et elles"
            "\nn'appellent pas la même action : les pièces contrôlées ont changé, ou le"
            "\nmodèle ne voit plus la même chose. Regarder QUELLES colonnes dérivent."
        )

    return f"{drift.colonnes_derivees}/{drift.colonnes_totales} colonnes dérivées"


def _monitor() -> str:
    from qc import monitoring

    metriques = monitoring.endpoint_metrics()
    print("Endpoint, dernière heure :")
    print(f"  invocations   {metriques.get('invocations', 0):.0f}")
    print(f"  erreurs 4XX   {metriques.get('erreurs_4xx', 0):.0f}")
    print(f"  erreurs 5XX   {metriques.get('erreurs_5xx', 0):.0f}")
    # ModelLatency est en MICROsecondes. Affichée telle quelle, elle fait croire à un
    # incident : 412 000 se lit comme 412 secondes.
    print(f"  latence       {metriques.get('latence_us', 0) / 1000:.0f} ms")

    alarmes = monitoring.alarm_states()
    print("\nAlarmes :")
    for nom, etat in sorted(alarmes.items()):
        marque = {"OK": "✓", "ALARM": "✗"}.get(etat, "·")
        print(f"  {marque} {nom:<34} {etat}")
    if not alarmes:
        print("  aucune — la tranche 3 du socle n'est pas appliquée")

    en_alarme = [n for n, e in alarmes.items() if e == "ALARM"]
    return f"{len(en_alarme)} alarme(s) active(s)" if en_alarme else "tout est au vert"


def _chaos(panne: str | None = None) -> str:
    from qc import chaos

    chaos.inject(panne)
    # Volontairement muet sur la panne choisie : le diagnostic EST l'exercice.
    print(
        "Une panne a été injectée dans VOTRE environnement.\n"
        "\nÀ vous : partez du symptôme (application, agent, alarmes), identifiez le"
        "\ncomposant, prouvez la cause par une trace — §8.4 donne la méthode."
        "\nRéparez ensuite par la commande que votre diagnostic désigne,"
        "\nou `uv run qc heal` pour la réparation automatique (elle nomme la panne :"
        "\nc'est votre correction)."
    )
    return "panne injectée"


def _heal() -> str:
    from qc import chaos

    reparations = chaos.heal()
    if not reparations:
        print("Aucune panne en place — rien à réparer.")
        return "rien à réparer"
    for ligne in reparations:
        print(ligne)
    return f"{len(reparations)} réparation(s)"


#: Rappel affiché après un déploiement réussi.
INSTANCE_HINT = "facturé — `uv run qc teardown` le soir"


STEPS: tuple[Step, ...] = (
    Step(1, "load", "télécharge SECOM, sélectionne les variables, découpe", _load),
    Step(1, "upload", "dépose les CSV dans le bucket du binôme", _upload),
    Step(1, "train", "soumet le job d'entraînement (régression logistique)", _train),
    Step(1, "deploy", "déploie l'endpoint avec data capture", _deploy),
    Step(1, "invoke", "invoque l'endpoint sur un échantillon", _invoke),
    Step(1, "teardown", "supprime l'endpoint — à lancer chaque soir", _teardown),
    Step(2, "agent", "agent Bedrock outillé sur l'endpoint", _agent),
    Step(2, "serve", "conteneurise et publie l'application", _serve),
    Step(3, "drift", "compare la production à la baseline", _drift),
    Step(3, "monitor", "alarmes et tableau de bord", _monitor),
)

BY_NAME = {step.name: step for step in STEPS}

#: Outils du J3, hors fil rouge : ils ne figurent pas dans le tableau d'avancement et
#: n'écrivent pas d'état — casser son environnement n'est pas une étape franchie.
OUTILS: tuple[Step, ...] = (
    Step(3, "chaos", "injecte une panne au hasard dans VOS ressources (§8.4)", _chaos),
    Step(3, "heal", "répare la panne et dit ce que c'était", _heal),
)

OUTIL_PAR_NOM = {step.name: step for step in OUTILS}


# ----------------------------------------------------------------------------- état


def _read_state() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text())


def _write_state(name: str, summary: str) -> None:
    state = _read_state()
    state[name] = summary
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def _board() -> None:
    """Tableau d'avancement — remplace la lecture d'une arborescence numérotée.

    Il dit où on en est, pas seulement ce qui existe : c'est ce qu'un apprenant cherche
    quand il revient de pause, et ce qu'un formateur regarde en passant derrière lui.
    """
    state = _read_state()
    pending = [s for s in STEPS if s.name not in state]
    next_step = pending[0] if pending else None

    print()
    current_day = None
    for step in STEPS:
        label = f"J{step.day}" if step.day != current_day else "  "
        current_day = step.day

        if step.name in state:
            print(f"  {label:<4}✓ {step.name:<10} {state[step.name]}")
        elif step is next_step:
            print(f"  {label:<4}✗ {step.name:<10} ← étape suivante")
        else:
            print(f"  {label:<4}  {step.name:<10} {step.help}")
    print()

    if next_step is None:
        print("  Toutes les étapes sont franchies.\n")
    else:
        print(f"  uv run qc {next_step.name}\n")


# ------------------------------------------------------------------------ programme


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qc",
        description="Contrôle qualité de pièces — formation AWS pour Data Scientists.",
        epilog="Sans argument : affiche le tableau d'avancement.",
    )
    subparsers = parser.add_subparsers(dest="step", metavar="ÉTAPE")
    for step in STEPS:
        subparsers.add_parser(step.name, help=f"[J{step.day}] {step.help}")
    for outil in OUTILS:
        sous = subparsers.add_parser(outil.name, help=f"[J{outil.day}] {outil.help}")
        if outil.name == "chaos":
            sous.add_argument(
                "--panne",
                choices=("endpoint", "sample", "service"),
                default=None,
                # Mode formateur : une panne précise pour une démo ou le challenge.
                # Sans l'option, le tirage est aléatoire et silencieux.
                help=argparse.SUPPRESS,
            )

    args = parser.parse_args(argv)

    if args.step is None:
        _board()
        return 0

    if args.step in OUTIL_PAR_NOM:
        from qc.chaos import ChaosError

        try:
            if args.step == "chaos":
                _chaos(args.panne)
            else:
                _heal()
        except (ChaosError, ConfigError) as exc:
            print(f"\n{exc}", file=sys.stderr)
            return 1
        return 0

    step = BY_NAME[args.step]
    if step.run is None:
        print(f"L'étape « {step.name} » (J{step.day}) n'est pas encore disponible.")
        return 1

    try:
        summary = step.run()
    except ConfigError as exc:
        # Le message porte déjà l'action corrective : pas de trace d'appels, qui
        # noierait l'information utile sous trente lignes sans intérêt pour l'apprenant.
        print(f"\nConfiguration invalide.\n{exc}", file=sys.stderr)
        return 1

    _write_state(step.name, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
