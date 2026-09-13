# Jour 1 — Du CSV à l'endpoint SageMaker · l'essentiel

Ce document résume l'essentiel de la journée : objectif, points durs, commandes de contrôle.

## Objectif de la journée

Une chaîne complète, chaque maillon étant une commande du paquet `qc` :

```
load → upload → train → deploy → invoke → (check-day1) → teardown
 local    S3     SageMaker   endpoint    prédictions              extinction
```

Données SECOM, 40 variables figées (`src/qc/features.json` fait foi), régression
logistique en mode script SKLearn. `uv run qc` sans argument affiche l'avancement du binôme.

## Les points durs — là où les binômes bloquent

1. **Le rôle d'exécution SageMaker n'est pas le leur.** C'est le service qui l'endosse au
   démarrage du job. Symptôme classique : le binôme lit son bucket depuis sa machine, mais
   le job échoue en `AccessDenied` — deux identités différentes. C'est LA notion du J1.
2. **Les noms de jobs sont horodatés** (unicité dans le temps) — on ne les code jamais en
   dur. Corollaire vécu : `list_training_jobs` pagine **avant** de filtrer sur
   `NameContains` ; une page vide avec `NextToken` n'est pas « aucun job »
   (`qc/inference.py::latest_artifact`, test de régression dédié).
3. **L'endpoint facture en continu dès `deploy`** (~0,134 $/h en `ml.m5.large`). Le
   `teardown` du soir n'est pas optionnel. La data capture se décide à la création de
   l'`EndpointConfig`, pas après coup.
4. **L'endpoint renvoie une probabilité, pas une décision** — le seuil appartient au
   client, pas au modèle.
5. **Isolation entre groupes** : le lab « AccessDenied volontaire » (sonde sur le bucket
   d'un autre groupe) doit répondre `Refusé` — sinon le profil d'instance de la machine
   n'est pas le bon.

## Commandes de contrôle

```bash
make check-day1                 # 6 contrôles de résultat, par binôme, en fin de journée
make restore-day1               # le lendemain matin : rejoue uniquement ce qui manque
```

Rattrapage code : `git checkout -b jour-2 j1-fin` sur le starter.

## Où le code se trouve

`src/qc/secom.py` (load), `storage.py` (upload, sonde d'isolation), `training.py` (train),
`inference.py` (deploy/invoke/teardown). Les tests de `tests/` sont l'énoncé côté starter :
chaque TODO-D1-xx y correspond à un test qui le nomme.
