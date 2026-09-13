# Jour 3 — Exploiter ce qui tourne · l'essentiel

Ce document résume l'essentiel de la journée : objectif, points durs, incidents, extinction.

## Objectif de la journée

```
baseline → dérive (Evidently) → MLflow → alarmes CloudWatch → incidents → tout éteindre
```

Le J3 n'ajoute presque rien : il exploite ce que les deux premiers jours ont laissé — la
data capture de l'endpoint, les logs, les métriques. Reprise : `make restore-day2`, tag
`j2-fin`.

## Les points durs — là où les binômes bloquent

1. **Evidently 0.7** : les presets sont dans `evidently.presets` — tout le code trouvable
   en ligne cible la 0.4 et ne fonctionne pas (D18). Les colonnes de la baseline et de la
   production doivent porter exactement les mêmes noms.
2. **La mesure modifie ce qu'elle mesure** : la data capture enregistre aussi les appels de
   supervision. La preuve est déterministe en comptant les `inferenceTime` — à montrer, ça
   marque.
3. **La baseline est une référence, pas une moyenne** — figée au moment de l'entraînement,
   pas recalculée sur la production.
4. **MLflow** : `sagemaker-mlflow` est indispensable et absent de la stack du programme
   (D8). Le serveur managé est facturé à l'heure (`create_mlflow` dans le socle).
5. **Alarmes** : le couple période × points d'évaluation fait la différence entre une
   alarme utile et du bruit. Faire passer une alarme en `ALARM` réellement (breaching),
   pas sur le papier.

## Les incidents : les binômes se les injectent eux-mêmes

`uv run qc chaos` casse une ressource du binôme au hasard sans dire laquelle
(endpoint supprimé, `sample.csv` effacé, service ECS à zéro) ; le binôme diagnostique
par les traces, puis `uv run qc heal` répare et **nomme la panne** — c'est
l'auto-correction. Relançable autant de fois que voulu ; pour le challenge final,
trois `chaos` d'affilée. `uv run qc chaos --panne endpoint|sample|service` force une
panne précise (démos).

Deux incidents restent à provoquer à la main si on veut les montrer (droits IAM
requis, hors rôle apprenant) :

| Incident | Provocation | Ce que ça enseigne |
| --- | --- | --- |
| Page blanche | redéployer sans `--server.baseUrlPath` | lire le groupe de cibles et les logs avant d'accuser le code |
| `AccessDenied` en pleine utilisation | droit retiré | remonter du symptôme HTTP à la politique IAM |

## Fin de formation — l'extinction est un exercice

Les apprenants éteignent eux-mêmes ce qu'ils ont créé (endpoint, service ECS, module
team) — c'est le dernier lab. Le formateur vérifie puis détruit le socle :

```bash
make check-day3                 # 6 contrôles de résultat, par binôme
# puis, une fois les endpoints partis :
make socle-destroy
```

## Où le code se trouve

`src/qc/monitoring.py` (baseline, dérive, métriques), `src/qc/chaos.py` (pannes et
réparations), `infra/terraform/team/alarms.tf` (lab alarmes),
`scripts/check_day3.py`.
