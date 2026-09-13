#!/usr/bin/env python3
"""Vérifie l'état AWS réellement attendu en fin de J2.

Contrairement à `check_day1.py`, ce script tourne dans l'ENVIRONNEMENT DU PROJET et non
dans un environnement jetable : son septième contrôle interroge l'agent, donc il a besoin
de Strands et du paquet `qc`. D'où l'absence d'en-tête PEP 723.

Neuf contrôles de RÉSULTAT, dans l'ordre où le trafic les traverse : image, service,
tâche, cible, ALB, application, données, websocket, agent. Un échec en position N rend
inutile de regarder les suivants, et l'ordre indique donc où chercher.

Le contrôle qui compte est le sixième. Un service ECS « stable » avec une tâche
« RUNNING » et une cible « healthy » peut parfaitement servir une page blanche : c'est le
mode d'échec de D12, et il ne se voit qu'en demandant la page.

    make check-day2

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
    import requests

    print(f"\n  Vérification du J2 — groupe {config.team_id} — {config.region}\n")

    s3 = config.client("s3")
    ecr = config.client("ecr")
    ecs = config.client("ecs")
    elb = config.client("elbv2")

    # Les noms viennent de `qc.config`, jamais reconstruits ici (décisions D7 et D10) :
    # `.env` peut les surcharger, et un check qui les code en dur contrôlerait autre chose
    # que ce qui a été déployé.
    depot = config.ecr_repo.rsplit("/", 1)[-1]
    service = config.ecs_service
    cluster = config.ecs_cluster

    # --- 1. Une image a été poussée ---------------------------------------------------
    try:
        images = ecr.describe_images(repositoryName=depot)["imageDetails"]
    except Exception:  # noqa: BLE001
        images = []
    verifie(
        "Image présente dans ECR",
        bool(images),
        f"{len(images)} image(s) dans {depot}" if images else "",
        "construire et pousser — `uv run qc serve` affiche la séquence",
    )

    # --- 2. Le service ECS existe et a atteint sa cible -------------------------------
    # `describe_services` LÈVE quand le cluster n'existe pas, au lieu de renvoyer une
    # liste vide. Sans ce filet, le script s'arrête sur une trace avant d'avoir affiché
    # les contrôles suivants — et le binôme ne voit pas que rien n'est déployé.
    try:
        services = ecs.describe_services(cluster=cluster, services=[service])["services"]
    except Exception:  # noqa: BLE001
        services = []
    actif = services[0] if services and services[0]["status"] == "ACTIVE" else None
    verifie(
        "Service ECS actif",
        actif is not None,
        f"{actif['runningCount']}/{actif['desiredCount']} tâche(s)"
        if actif
        else f"ni le cluster {cluster} ni le service {service}",
        f"make socle-apply puis make team-apply TEAM_ID={config.team_id}",
    )

    # --- 3. Une tâche tourne vraiment --------------------------------------------------
    # `runningCount` peut valoir 0 pendant plusieurs minutes sans que rien ne le signale :
    # ECS relance en boucle une tâche qui s'arrête au démarrage. La cause est presque
    # toujours l'image (mauvaise architecture) ou le rôle d'exécution.
    if actif:
        verifie(
            "Tâche en cours d'exécution",
            actif["runningCount"] >= 1,
            f"desired={actif['desiredCount']} running={actif['runningCount']}"
            f" pending={actif['pendingCount']}",
            "regarder les logs : /ecs/qc-<TEAM_ID>-app, et l'onglet Events du service."
            " Une image arm64 reste en PENDING puis échoue.",
        )

    # --- 4. La cible est saine pour l'ALB ---------------------------------------------
    groupes = [
        g
        for g in elb.describe_target_groups()["TargetGroups"]
        if g["TargetGroupName"] == config.resource("tg")
    ]
    sante = []
    if groupes:
        sante = elb.describe_target_health(TargetGroupArn=groupes[0]["TargetGroupArn"])[
            "TargetHealthDescriptions"
        ]
    saines = [t for t in sante if t["TargetHealth"]["State"] == "healthy"]
    verifie(
        "Cible saine dans le groupe",
        bool(saines),
        f"{len(saines)}/{len(sante)} cible(s) saine(s)"
        + (f" — {sante[0]['TargetHealth'].get('Reason', '')}" if sante and not saines else ""),
        "le health check doit viser /<TEAM_ID>/_stcore/health, PAS /_stcore/health :"
        " sans le préfixe l'ALB reçoit 404 et retire la cible (D12)",
    )

    # --- 5. La règle de routage existe -------------------------------------------------
    regles = []
    if groupes:
        ecouteurs = [
            e
            for lb in elb.describe_load_balancers()["LoadBalancers"]
            if lb["LoadBalancerName"] == "qc-promo-alb"
            for e in elb.describe_listeners(LoadBalancerArn=lb["LoadBalancerArn"])[
                "Listeners"
            ]
        ]
        for ecouteur in ecouteurs:
            for regle in elb.describe_rules(ListenerArn=ecouteur["ListenerArn"])["Rules"]:
                valeurs = [
                    v
                    for c in regle.get("Conditions", [])
                    for v in c.get("Values", [])
                ]
                if any(config.team_id in v for v in valeurs):
                    regles.append(regle)
    verifie(
        "Règle de routage sur l'ALB",
        bool(regles),
        f"priorité {regles[0]['Priority']}" if regles else "",
        f"make team-apply TEAM_ID={config.team_id}",
    )

    # --- 6. L'application répond VRAIMENT ----------------------------------------------
    # Le contrôle qui compte. Tout ce qui précède peut être vert devant une page blanche :
    # c'est le mode d'échec de D12, et il ne se voit qu'en demandant la page.
    #
    # Ce contrôle suppose d'être lancé depuis le VPC — l'ALB est interne. Depuis le poste
    # du formateur, il expirera : c'est attendu, pas un échec de l'application.
    if config.alb_dns_name:
        url = f"http://{config.alb_dns_name}/{config.team_id}/_stcore/health"
        try:
            reponse = requests.get(url, timeout=10)
            code, detail = reponse.status_code, f"{url} → {reponse.status_code}"
        except Exception as exc:  # noqa: BLE001
            code, detail = 0, f"{type(exc).__name__} — l'ALB est interne, lancer ce contrôle depuis une machine de travail"
        verifie(
            "Application joignable derrière l'ALB",
            code == 200,
            detail,
            "404 : le préfixe /<TEAM_ID>/ n'est pas routé, ou Streamlit a démarré sans"
            " --server.baseUrlPath. 502/504 : la tâche ne répond pas sur le port 8501.",
        )
    else:
        verifie(
            "Application joignable derrière l'ALB",
            False,
            "ALB_DNS_NAME est vide dans .env",
            "demander au formateur l'extrait `.env` mis à jour du groupe",
        )

    # --- 6 bis. L'application peut lire ses données ------------------------------------
    # Le conteneur est sans état : il ne contient aucune donnée et va les chercher dans
    # S3. Si l'objet manque, l'application démarre, sert une page d'erreur avec un code
    # 200, et le contrôle précédent reste vert.
    objets = s3.list_objects_v2(
        Bucket=config.s3_bucket, Prefix="curated/sample.csv"
    ).get("Contents", [])
    verifie(
        "Données de l'application disponibles dans S3",
        bool(objets),
        f"curated/sample.csv — {objets[0]['Size']} octets" if objets else "absent",
        "`uv run qc upload` — le conteneur ne contient aucune donnée, il les lit dans S3",
    )

    # --- 6 ter. Le websocket accepte un VRAI navigateur --------------------------------
    # Le contrôle 6 passe au vert devant une application dont le websocket refuse tout
    # navigateur : le health check est un simple GET, que la protection d'origine ne
    # regarde pas. Ce handshake envoie l'Origin qu'enverrait un navigateur pointé sur
    # l'ALB — curl sans Origin obtiendrait 101 même quand tout navigateur réel reçoit
    # 403 (docs/jour-2.md, point dur n°3 ; contrôle J2, même si l'utilitaire vit dans
    # qc.monitoring).
    if config.alb_dns_name:
        from qc import monitoring

        code_ws = monitoring.websocket_handshake_status(
            config.alb_dns_name,
            f"/{config.team_id}/_stcore/stream",
            f"http://{config.alb_dns_name}",
        )
        verifie(
            "Websocket ouvert à un navigateur réel",
            code_ws == 101,
            f"handshake avec Origin → {code_ws or 'connexion impossible'}",
            "403 : l'origine du navigateur est refusée — vérifier que la définition de"
            " tâche passe STREAMLIT_BROWSER_SERVER_ADDRESS=<DNS de l'ALB>, ou (sandbox"
            " uniquement) SANDBOX_RELAX_WEBSOCKET=1",
        )

    # --- 7. L'agent Bedrock répond et appelle ses outils --------------------------------
    try:
        from qc import agent

        trace = agent.ask("Quel est le score de la pièce 0 ?")
        ok_agent, detail = trace.a_consulte_le_modele, f"outils : {', '.join(trace.outils_appeles) or 'aucun'}"
    except Exception as exc:  # noqa: BLE001
        ok_agent, detail = False, f"{type(exc).__name__} — {exc}"
    verifie(
        "Agent opérationnel et outillé",
        ok_agent,
        detail,
        "un agent qui répond sans appeler d'outil invente son score."
        " Vérifier streaming=False (D15) et le prompt système.",
    )

    print()
    if echecs:
        print(f"  {len(echecs)} contrôle(s) en échec : {', '.join(echecs)}\n")
        return 1
    print("  J2 complet.\n")
    print("  Ne pas oublier en fin de journée :")
    print("    uv run qc teardown                       endpoint SageMaker")
    print(f"    make team-destroy TEAM_ID={config.team_id}            service ECS\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
