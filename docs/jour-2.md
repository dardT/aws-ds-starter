# Jour 2 — De l'endpoint à l'application en service · l'essentiel

Ce document résume l'essentiel de la journée : objectif, points durs, commandes de contrôle.

## Objectif de la journée

```
agent Bedrock outillé → image Docker → push ECR → module Terraform team → app derrière l'ALB
```

Matin : un agent Strands sur Mistral Large qui appelle quatre outils (dont l'endpoint du
J1). Après-midi : l'application Streamlit conteneurisée, poussée dans ECR, déployée sur
ECS, servie par l'ALB partagé sur `/gNN/*`.

Reprise du J1 : `make restore-day1` pour l'état AWS, tag `j1-fin` pour le code.

## Les points durs — là où les binômes bloquent

1. **Modèle Bedrock** : `mistral.mistral-large-2402-v1:0` uniquement — Nova et Anthropic
   sont au catalogue mais renvoient `AccessDenied` (D14). La présence au catalogue ne vaut
   pas accès (D6).
2. **Strands** : `BedrockModel(..., streaming=False)` obligatoire, sinon le tool use casse
   sur ce modèle (D15).
3. **Le préfixe d'URL — la panne du J2** : Streamlit doit démarrer avec
   `--server.baseUrlPath=<TEAM_ID>`, sinon page blanche derrière l'ALB avec une tâche ECS
   parfaitement saine (D12). Côté client, `browser.serverAddress` règle le websocket.
4. **Image Docker** : construire en `linux/amd64` (buildx sur Mac ARM) — vérifiable avant
   push avec `docker inspect --format '{{.Os}}/{{.Architecture}}'`. L'ordre des
   instructions du Dockerfile détermine le temps de reconstruction (dépendances avant code).
5. **Deux rôles ECS, la distinction de la journée** : `ecs-exec` démarre la tâche (tire
   l'image, écrit les logs), `ecs-task` la fait travailler (appelle l'endpoint, Bedrock).
6. **Deux modules, deux états** : le socle (formateur) et `team/` (binôme, état séparé sous
   `team/<TEAM_ID>/`). Le module team ne crée que le service ECS et son attachement au
   groupe de cibles — l'ALB et son routage sont précréés par le socle.

## Commandes de contrôle

```bash
make check-day2                 # 8 contrôles de résultat, par binôme
make restore-day2               # rattrapage incrémental du matin J3
```

Rattrapage code : `git checkout -b jour-3 j2-fin`.

Diagnostic quand l'app ne répond pas, dans l'ordre : la tâche ECS tourne-t-elle → le
health check est-il vert dans le groupe de cibles → les logs `/ecs/qc-gNN-app` → le
préfixe d'URL (point 3 ci-dessus).

## Où le code se trouve

`src/qc/agent.py` (agent et outils), `app/main.py` (Streamlit), `Dockerfile`,
`infra/terraform/team/` (module binôme).
