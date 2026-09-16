# l'entreprise AWS for DS — décisions d'arbitrage (session du 2026-07-27)

Complément à `docs/00-programme.md`. En cas de contradiction, **ce fichier prime**
(il est postérieur et tranche les zones grises de la §18).

C'est un **journal** : les entrées sont datées, jamais réécrites, et rangées dans l'ordre
où elles ont été prises. L'index ci-dessous sert à naviguer.

## Index

| # | En une ligne | Statut |
| --- | --- | --- |
| D1 | Binômes, `teams` en variable Terraform, TEAM_ID `^g[0-9]{2}$` | actif |
| D2 / D2 bis | Machines de travail EC2, distribution Ubuntu (pas Amazon Linux) | actif |
| D3 | Rattrapage code par les tags du starter (`j1-fin`, `j2-fin`) | actif |
| D4 | Rattrapage AWS | partiellement obsolète → D23 |
| D5 | Région unique eu-west-3, jamais en dur | actif |
| D6 | La présence au catalogue Bedrock ne vaut pas accès : sonde Converse | actif |
| D7 | Nommage déterministe `qc-<TEAM_ID>-…`, suffixe de compte pour S3 | actif |
| D8 | MLflow managé SageMaker + `sagemaker-mlflow` obligatoire | actif |
| D9 | Durcissement réseau (VPC, endpoints, ALB interne) | actif |
| D10 | Vérification et pilotage : `check-dayN`, `restore-dayN`, dashboard | actif |
| D11 | Jeu SECOM figé et déterministe (`features.json`) | actif |
| D12 | ALB par chemin `/gNN/` → Streamlit `--server.baseUrlPath` | actif |
| D13 | Réorganisation du J3 (lab 6 délesté) | actif |
| D14 | Modèle Bedrock : `mistral.mistral-large-2402-v1:0` seul accessible | actif |
| D15 | Strands en `streaming=False`, sinon le tool use casse | actif |
| D16 | Écarts assumés du preflight par rapport à §12 | actif |
| D17 | `qc/config.py` seul lecteur de `.env` | actif |
| D18 | Versions figées ; Evidently 0.7 (presets déplacés) | actif |
| D19 | Une application unique, pas de dossiers day1/2/3 | actif |
| D20 | Horodatages SECOM : 604 lignes récupérées | actif |
| D21 | Isolation entre groupes par profil d'instance (deny hors préfixe) | actif |
| D22 | Un cluster ECS par groupe, créé dans le socle | actif |
| D23 | Corrections de la revue croisée du 28/07 | actif |
| D24 | La suite de tests ne joint jamais AWS | actif |
| D25 | La régression logistique remplace XGBoost (mode script SKLearn) | actif |
| D26 | La capture se filtre par `inferenceTime`, pas par date de fichier | actif |
| D27 | ECS Exec = canal SSM du formateur, exige `ssmmessages` | actif |
| D28 | Passerelle IDE en HTTPS sur `vsc0de.fr`, chemin `/gNN/` via nginx | actif |
| D29 | Passerelle IDE sur le sous-domaine `ide.vsc0de.fr`, pas l'apex | actif |

## D1 — Groupes et TEAM_ID
- Travail en **binômes** (annule et remplace la recommandation trinômes de §4).
- Le nombre de groupes n'est **pas figé** : variable Terraform `teams` (liste de TEAM_ID).
  Le socle génère workstations, règles ALB, rôles IAM et groupes de logs par `for_each`.
- Le preflight calcule le besoin de quota SageMaker comme `len(teams) × marge`,
  jamais un chiffre en dur.
- Format TEAM_ID imposé et validé par regex : `^g[0-9]{2}$` (g01, g02, …).
- **Supprimer** l'exemple `qc-b03-endpoint` de §11 : il contredit le format `g01`.

## D2 — Machines de travail
- EC2 **définies dans `infra/terraform/socle/`**, appliquées par le formateur avant la
  formation. Les apprenants n'ont la main que sur `infra/terraform/team/` : ils ne
  peuvent donc pas détruire leur propre machine avec le `terraform destroy` du J3.
- **Golden AMI** : une workstation de référence est construite et validée, puis figée en
  AMI. Les machines de la promo démarrent depuis cette AMI (Docker + buildx, Python 3.11,
  AWS CLI, git, cache uv chauffé) → pas de `dnf install` devant 15 personnes au J1.
- **Révisé le 28/07/2026 — l'AMI n'embarque PLUS le dépôt starter.** Un clone figé dans
  une image est périmé au premier commit, et le starter est force-pushé à chaque
  régénération : le `git pull` échoue alors sur un historique réécrit. Les binômes
  clonent eux-mêmes au J1 (~1 Mo, instantané) ; seul le CACHE uv (paquets + interpréteur)
  est chauffé dans l'image, via un clone jetable supprimé avant la création de l'AMI.
  S'il reste vrai qu'aucun dépôt de correction ne doit approcher les machines (D2), la
  remarque sur `git checkout j1-fin` demeure : les tags de correction arrivent avec le
  clone que fait le binôme.
- Cible `make workstation-check` : se connecte par SSM à chaque machine et vérifie
  docker, buildx, python, le clone du repo et `sts get-caller-identity`. À exécuter la veille.
- Accès par Session Manager, profil d'instance `qc-<TEAM_ID>-workstation-profile`,
  aucune clé statique distribuée.

## D2 bis — Distribution des workstations : Ubuntu, pas Amazon Linux
§14 prévoyait Amazon Linux 2023. Décision du formateur : **Ubuntu**. Le contenu du dépôt
(scripts Python, Makefile, Terraform) est totalement agnostique — seul le bootstrap de
l'AMI change. Points à traiter, aucun n'est bloquant :

| Élément | Amazon Linux 2023 | Ubuntu |
| --- | --- | --- |
| Utilisateur par défaut | `ec2-user` | `ubuntu` — impacte les chemins du bootstrap et le groupe `docker` |
| Python | 3.9 système | 3.12 système |
| Docker + buildx | `dnf install docker`, buildx à part | dépôt apt Docker officiel : `docker-ce` + `docker-buildx-plugin` |
| Terraform | dépôt HashiCorp yum | dépôt apt HashiCorp |
| Agent SSM | préinstallé | **à vérifier** sur l'AMI Canonical retenue |

- **Le Python système n'a aucune importance** : `uv` télécharge et gère l'interpréteur
  3.11 exigé par §11. C'est ce qui rend l'écart Arch (formateur) / Ubuntu (workstations)
  sans conséquence, et c'est la principale raison de tout passer par `uv`.
- **Piège Docker sur Ubuntu** : le paquet `docker.io` des dépôts Ubuntu ne fournit pas
  `buildx`. Or §11 impose `docker buildx` et la plateforme `linux/amd64`, et le contrôle
  11 du preflight le vérifie. Le bootstrap doit utiliser le dépôt apt officiel de Docker
  et installer `docker-buildx-plugin`, pas `docker.io`.
- **Agent SSM** : à confirmer sur l'AMI Ubuntu retenue. C'est le seul accès aux machines
  (D2), donc son absence bloquerait tout le monde au matin du J1. Le `make
  workstation-check` doit le vérifier explicitement la veille.
- **Fichier `.python-version`** ajouté à la racine du dépôt (valeur `3.11`), et
  `make lock` compile désormais avec `--python-version 3.11`. Sans cela, le lock dépendrait
  du Python de la machine qui l'a compilé — Arch en 3.13 chez le formateur, Ubuntu en 3.12
  sur les workstations — et ne serait plus reproductible.

## D3 — Reprise en J2 / J3, couche CODE
- Les tags `j1-fin`, `j2-fin` sont **présents dès le départ sur `main`** du repo
  starter (choix assumé : la solution est accessible dès la première minute).
- Atténuation : les checks portent sur l'**état AWS réel** (endpoint `InService` sous le
  préfixe du groupe, objets présents dans le bucket, data capture qui écrit), pas sur le
  contenu des fichiers. Copier le code sans l'exécuter ne fait pas passer le check.
- Conséquence : le critère « aucune solution résiduelle dans l'historique git » de §13
  (instance C) est **annulé**.

## D4 — [PARTIELLEMENT OBSOLÈTE, voir D23]  Reprise en J2 / J3, couche AWS
- `make restore-day1` rejoue **l'intégralité** du J1 : upload S3 → training job → déploiement
  d'endpoint. ~15 min de mur. Idem `make restore-day2`.
- Lancé en **arrière-plan** avec suivi, pendant la masterclass 1 du J2, pour ne pas manger
  la matinée.
- Conséquence quota : N binômes lançant le restore à 9h = N training jobs simultanés.
  Le contrôle n°5 du preflight (quota d'instances d'entraînement ≥ nombre de groupes)
  devient bloquant à ce moment précis, pas seulement en théorie.

## D5 — Région
- **eu-west-3 (Paris)**, résidence France.
- À vérifier dans la console avant tout engagement :
  - liste des modèles Bedrock activables depuis eu-west-3 (délai de demande d'accès) ;
  - quotas des types d'instances SageMaker retenus × nombre de groupes.
- Dimensionnement retenu (régression logistique — et déjà valable pour l'ancien
  XGBoost —, ~40 features, 1567 lignes → CPU suffit) :
  `SAGEMAKER_TRAIN_INSTANCE=ml.m5.large`, `SAGEMAKER_ENDPOINT_INSTANCE=ml.m5.large`.
  Pas de GPU.

## D6 — Correction du preflight Bedrock (§12, contrôle n°7)
- La spec actuelle est **fausse en Europe** : elle exige que `BEDROCK_MODEL_ID` figure dans
  `list_foundation_models`. En région UE, les modèles Claude récents s'invoquent via un
  **profil d'inférence cross-région** préfixé `eu.`, qui apparaît dans
  `list_inference_profiles`, pas dans `list_foundation_models`.
- Correction : le contrôle accepte les deux formes, interroge `list_inference_profiles`
  quand l'ID commence par `eu.`, et conserve l'**appel `Converse` réel** comme critère
  décisif (seul contrôle qui prouve l'accès effectif).

## D7 — Nommage déterministe vs noms dérivés
- Les tests **ne codent jamais un nom en dur**. Ils importent `src/qc/config.py`, qui est
  l'unique source de vérité des noms de ressources.
- Raison : deux noms ne sont pas connaissables à l'écriture des tests —
  le bucket (`qc-<TEAM_ID>-data-<suffixe compte>`, unicité mondiale) et les jobs
  d'entraînement / configurations d'endpoint (horodatage UTC, unicité dans le temps).

## D8 — MLflow
- **SageMaker managed MLflow** : un tracking server géré, unique pour la promo, créé
  dans le socle bien avant la formation (~20 min de provisioning, facturé à l'heure).
- Une expérience par groupe, nommée `qc-<TEAM_ID>`. Droits `sagemaker-mlflow:*` ajoutés
  au rôle `qc-<TEAM_ID>-lab`. `MLFLOW_TRACKING_URI` = ARN du tracking server.
- **Ajout obligatoire à `requirements.txt` (absent de la stack §11)** : le paquet
  `sagemaker-mlflow`. Sans ce plugin, le client MLflow ne parle pas au serveur géré et
  le lab fil rouge 6 du J3 échoue immédiatement.
- Le contrôle n°14 du preflight devient **bloquant** : le SKIP prévu en §12 est annulé,
  puisque le J3 en dépend.
- À confirmer en console : disponibilité du MLflow géré en eu-west-3.
**Vérifié le 28/07/2026 — risque levé.** `list_mlflow_tracking_servers` répond en
eu-west-3 : le MLflow managé y est disponible. D8 n'a pas à être rouvert, et le repli sur
un MLflow local sur la workstation n'est plus nécessaire. Écrit en tranche 3
(`infra/terraform/socle/mlflow.tf`), derrière `create_mlflow = false` — le serveur est
facturé à l'heure dès sa création.

- **Risque non couvert, contrairement à D14.** Le choix du modèle Bedrock a une échelle de
  repli et se change en une variable ; MLflow géré n'en a pas. S'il n'est pas disponible en
  eu-west-3, le lab 6 du J3 n'a plus de cible et D8 doit être **rouvert** — ce n'est pas un
  changement de variable, ça déplace de l'infrastructure entre le socle et la workstation.
  Repli identifié le cas échéant : MLflow local sur la workstation (option écartée lors de
  l'arbitrage initial, à re-soumettre plutôt qu'à basculer silencieusement).

## D9 — Durcissement réseau
- `enable_vpc_endpoints = false`. Les tâches sortent par la NAT.
- Motif : à `true` tel que spécifié en §14, la sortie internet est supprimée — ce qui
  casse le téléchargement du dataset SECOM (archive.ics.uci.edu), `pip install` et
  `git clone`. La formation ne démarrerait pas.
- Les endpoints d'interface (ECR api/dkr, SageMaker runtime, Bedrock runtime,
  CloudWatch Logs, STS) restent **écrits et commentés** dans le Terraform, non appliqués,
  et servent de **démonstration commentée en J2** — même traitement que ACM/HTTPS.
- L'endpoint S3 de type gateway reste créé (gratuit).

## D10 — Vérification et pilotage
- Côté apprenant : `make check-day1` / `check-day2` / `check-day3`, sortie en tableau
  PASS/FAIL par critère avec cause et action corrective — même format que `make preflight`,
  pour que le vocabulaire d'erreur soit appris une seule fois.
- Les checks portent sur l'**état AWS réel** sous le préfixe du groupe, pas sur le contenu
  des fichiers (cf. D3). Ils lisent les noms depuis `src/qc/config.py` (cf. D7).
- Chaque exécution écrit son résultat dans `s3://<bucket promo>/checks/<TEAM_ID>/`.
- Côté formateur : `make dashboard` agrège l'état de tous les groupes en une commande.
  Objectif : identifier un binôme bloqué sans faire le tour des écrans.
- `pytest tests/` reste en place (§13) mais devient le harnais sous-jacent, pas
  l'interface apprenant.

## D11 — Déterminisme du jeu de données
- Le dataset SECOM reste **téléchargé à l'exécution** via `ucimlrepo` (§13 respecté,
  aucune donnée committée).
- Mais la **liste des ~40 colonnes retenues est figée** dans un fichier versionné du
  starter, et le split train/test utilise un **seed fixe**. Tous les binômes obtiennent
  donc le même jeu, le même modèle (l'entraînement de la régression logistique est
  déterministe, `random_state=42`), les mêmes contributions au J2 et la même baseline de
  drift au J3 — condition nécessaire à des checks déterministes.
- Un échantillon anonymisé de 50 lignes est committé pour les tests (déjà toléré §14).
- **Risque résiduel** : dépendance à la disponibilité d'`archive.ics.uci.edu` le matin du
  J1. Atténuation retenue : le dataset brut est pré-mis en cache dans la golden AMI, et
  une copie de secours est déposée dans le bucket commun de la promo. Le code tente la
  source, puis bascule sur le cache.

## D12 — Exposition de l'application et routage ALB
- **Un seul ALB pour toute la promo**, créé dans `infra/terraform/socle/` avec son
  écouteur :80 (action par défaut : 404 « groupe inconnu »).
- ~~Le module `infra/terraform/team/` greffe sa propre `aws_lb_listener_rule` et son
  `aws_lb_target_group`.~~ **Amendé le 30/07/2026 (revue technique, point 2)** : les
  écritures `elasticloadbalancing` exigées par cette greffe portaient sur l'écouteur
  partagé (`resources = ["*"]`), donc un binôme pouvait supprimer la règle d'un autre.
  La règle `/<TEAM_ID>/*` et le groupe de cibles `qc-<TEAM_ID>-tg` sont désormais
  **précréés par le socle** (`socle/alb_teams.tf`) ; le module `team/` lit l'ARN du
  groupe de cibles dans les sorties du socle et y enregistre son service ECS. Le rôle
  workstation n'a plus que `elasticloadbalancing:Describe*`. Le parallélisme des apply
  est conservé.
- La **priorité de règle** doit être unique par groupe : la dériver du TEAM_ID
  (ex. `g01` → 10, `g02` → 20) — calculée dans le socle désormais.
- **Correction d'un conflit de §14** : l'ALB ne réécrit pas le chemin, le conteneur reçoit
  donc `GET /g01/`. Streamlit en configuration par défaut se croit à la racine et génère
  ses ressources statiques et son websocket sur `/static/...` et `/_stcore/stream`, que
  l'ALB ne sait pas router → page blanche, tâche saine, logs muets.
- Correction retenue : l'application est démarrée avec `--server.baseUrlPath=<TEAM_ID>`,
  injecté depuis `src/qc/config.py`. Le health check du groupe de cibles vise alors
  `/<TEAM_ID>/_stcore/health`.
- Ce mode d'échec est conservé comme **matériel pédagogique** : c'est exactement le type
  d'incident que le J3 apprend à diagnostiquer.

## D13 — Réorganisation du J3 (surcharge du lab 6)
- Constat : le lab 6 de §8 tient 2 h 10 et contient six tâches techniques **plus** les
  démonstrations orales. À N binômes × ~7 min, les orales seules consomment la moitié
  du créneau. Le programme tel qu'écrit ne tient pas.
- Décision : le **bloc Terraform sort du lab 6** et devient un lab court et autonome
  placé juste après la masterclass 2 (modifier `desired_count` / `cpu` / `memory` /
  rétention des logs, lire le `plan`, appliquer, vérifier l'effet).
- Le lab 6 se recentre sur : injecter une dérive · comparer référence et production avec
  Evidently · tracer dans MLflow · exposer `check_data_drift()` à l'agent.
- Le challenge final et les démonstrations orales conservent leur temps.
- Rien n'est retiré du programme : seul l'ordre change. §8 est à réécrire en conséquence.

## RÉSULTATS DE LA SONDE (2026-07-27, compte <identifiant de compte>, eu-west-3)
> **Instantané du 27/07** — le rapport complet est `docs/02-decouverte-compte-2026-07-27.md`.

- **Identité** : rôle SSO `AWSReservedSSO_MachineLearningSandboxAccess`.
  Suffixe de bucket dérivé : `qc-<TEAM_ID>-data-<suffixe>`.
- **D5 — RÉSOLU, favorablement.** Quotas `ml.m5.large` : **30** en entraînement,
  **16** en endpoint. Largement suffisant pour la promo. Aucune demande
  d'augmentation nécessaire. Le contrôle n°5 du preflight reste écrit, mais il ne
  sera pas bloquant.
- **D6 — CONFIRMÉ.** eu-west-3 expose bien 34 profils d'inférence, préfixés `eu.` et
  `global.`. La correction du contrôle n°7 du preflight est donc nécessaire, comme
  anticipé.
- **D8 — CONFIRMÉ.** SageMaker managed MLflow est disponible en eu-west-3. Aucun
  tracking server existant : à créer dans le socle. Le repli « MLflow local » n'a pas
  à être rouvert.
- **D14 — INVALIDÉ. Un seul modèle est réellement activé : `mistral.mistral-large-2402-v1:0`**
  (appel Converse OK, tool use OK). Nova Lite, Nova Pro et **tous** les modèles
  Anthropic renvoient `AccessDeniedException`, alors qu'ils sont au catalogue.
  Cela valide rétrospectivement la décision D6 : la présence au catalogue ne vaut
  pas accès, seul l'appel Converse réel le prouve.
- ECR, ECS, ELBv2 et CloudWatch Logs : accessibles.

## D15 — Strands doit tourner en mode NON streaming (vérifié)
- Constat, obtenu par `scripts/probe_model.py` : le provider Bedrock de Strands appelle
  `ConverseStream` par défaut. Or Mistral Large ne supporte pas le tool use en streaming.
  Résultat brut, sans configuration :
  `ValidationException: This model doesn't support tool use in streaming mode`.
- Correction : instancier `BedrockModel(model_id=..., region_name=..., streaming=False)`.
  Vérifié de bout en bout — l'agent appelle l'outil et cite la valeur retournée.
- **Conséquence sur le starter** : ce paramètre doit être **pré-rempli et commenté** dans
  `src/qc/agent.py`, jamais laissé en TODO. Sans lui, chaque binôme se heurte à une
  ValidationException opaque au J2, sur un point qui n'a aucune valeur pédagogique.
- Exploitation pédagogique possible en masterclass J2 : illustre que « le modèle supporte
  le tool use » et « le modèle supporte le tool use dans ce mode d'appel » sont deux
  affirmations distinctes.
- Pixtral Large 25.02 (`eu.mistral.pixtral-large-2502-v1:0`), non testé par la première
  sonde, est lui aussi refusé. Mistral Large 2402 reste le seul modèle utilisable.

## D14 — Modèle Bedrock
- Contrainte : l'accès aux modèles Anthropic dans le compte n'est pas garanti. Le choix
  se porte donc sur ce qui est **assurément activable**, pas sur le plus capable.
- **DÉCISION FINALE, fondée sur la sonde : `mistral.mistral-large-2402-v1:0`.**
  C'est le seul modèle réellement appelable dans le compte (Converse OK, tool use OK).
  L'hypothèse initiale Amazon Nova Lite est abandonnée : Nova est au catalogue mais
  renvoie `AccessDeniedException`, comme tous les modèles Anthropic.
- Noter que c'est un **modelId brut**, pas un profil d'inférence `eu.*` — le preflight
  doit donc bien accepter les deux formes (cf. D6).
- Bénéfice secondaire : argument de souveraineté européenne, appréciable chez l'entreprise.
- **Action ouverte pour le formateur** : déterminer en console (page « Model access »
  de Bedrock, eu-west-3) si les refus viennent d'une activation manquante ou d'une
  restriction IAM sur le rôle SSO `MachineLearningSandboxAccess`. Le fait que Nova —
  modèle maison AWS, normalement le plus simple d'accès — soit lui aussi refusé penche
  fortement vers la restriction IAM, donc vers une décision de l'IT plutôt qu'une case
  à cocher.
- Si un meilleur modèle est débloqué avant la formation : bascule en changeant
  `BEDROCK_MODEL_ID`, plus une revérification du gabarit de `src/qc/agent.py` (SYSTEM_PROMPT).
- **Le choix n'est pas structurant** : tout passe par l'API Converse et le provider Bedrock
  de Strands. Changer de modèle = changer `BEDROCK_MODEL_ID`, aucune ligne applicative.
- Seule contrainte réelle : le modèle doit supporter le **tool use** via Converse.
- Conséquence sur `src/qc/agent.py` (SYSTEM_PROMPT) : le gabarit est écrit **contraint et explicite**, pour
  tenir même sur un modèle léger. §7 demande d'obliger le LLM à citer les valeurs des
  outils et de tester les cas ambigus — un modèle léger y résiste moins bien, ce qui reste
  exploitable pédagogiquement mais impose un prompt plus verrouillé.
- Le preflight (contrôle n°7) valide l'accès par un **appel Converse réel**, jamais par la
  simple présence au catalogue (cf. D6).
- À vérifier en console avant de figer : page « Model access » de Bedrock en eu-west-3
  (l'API n'expose pas de façon fiable ce qui est *activé*, seulement ce qui est au
  catalogue).

## Informations en attente (chemin critique) — [OBSOLÈTE : tranché depuis]
- **Dates de la formation** et **effectif exact**. Conditionnent le planning des trois
  items à long délai : demande d'accès aux modèles Bedrock, augmentation des quotas
  SageMaker, construction et validation de la golden AMI.
- Liste des services AWS interdits par les politiques de l'entreprise (§18, non tranché).
- Durée retenue pour les démonstrations finales (§18, non tranché).

## D16 — Écarts assumés du preflight par rapport à §12
Décisions prises en écrivant `scripts/preflight.py`.

- **Tous les contrôles sont exécutés, même après un échec.** §12 demande de s'arrêter net
  au premier FAIL. Un rapport partiel n'a aucune valeur face à l'IT : mieux vaut la liste
  complète des droits manquants qu'un premier échec isolé. Le code de sortie reste 1 dès
  qu'un contrôle échoue, conformément à §12.
- **Les contrôles dépendant du socle sont marqués comme tels.** Avant `make socle-apply`,
  les contrôles 4, 6, 9 et 14 échouent normalement. Sans ce marquage, un formateur lisant
  un preflight rouge avant d'avoir construit le socle croirait à un problème.
- **Contrôle 3 (permissions) dégradé en SKIP sur rôle SSO.** `iam:SimulatePrincipalPolicy`
  échoue fréquemment sur un rôle assumé. Le script convertit l'ARN de session en ARN de
  rôle, et bascule en SKIP si l'appel reste refusé — les contrôles suivants testent les
  droits réellement utilisés, ce qui est plus fiable qu'une simulation.
- **Contrôle 9 : un `ACM_CERT_ARN` vide est un PASS, pas un manque.** C'est la valeur
  normale du mode dégradé HTTP retenu en §14. Le traiter comme une erreur produirait un
  faux négatif permanent.
- **Contrôle 13 délégué à `uv`.** La résolution de `requirements.txt` et les imports sont
  vérifiés dans un environnement jetable (`uv run --with-requirements`), ce qui valide au
  passage que le fichier se résout réellement — l'intention réelle du contrôle.
- **Contrôle 14 (MLflow) rendu bloquant**, cf. D8.
- **Contrôle 7 (Bedrock) corrigé deux fois.** D'abord cf. D6 : il accepte modelId brut et
  profil d'inférence, et fait foi sur l'appel Converse réel plutôt que sur la présence au
  catalogue. Ensuite, il teste explicitement le **tool use** avec un `toolConfig`, pas
  seulement l'appel simple — un modèle peut accepter Converse et refuser le tool use
  (c'est précisément le cas de Mistral en streaming, cf. D15). Sans cette seconde sonde,
  un changement de `BEDROCK_MODEL_ID` passerait au vert le matin et casserait l'agent
  l'après-midi du J2. Le message d'échec renvoie vers `make probe`, qui teste en plus le
  pilotage par Strands.
- **Contrat de sortie vérifié** : 5 FAIL → code de sortie 1.

## D17 — `src/qc/config.py`
- Objet `config` unique, importé partout. Aucun script ne lit `os.environ` (§12).
- Construction **paresseuse** (PEP 562 `__getattr__`) : importer `ROOT`, `DATA_DIR` ou
  `ConfigError` ne construit pas la configuration. Avec une instance bâtie au chargement
  du module, `uv run qc` et `uv run qc --help` échouaient sur une trace d'appels dès que
  `.env` était incomplet — précisément au moment où l'apprenant cherche quoi remplir.
- Validation à la construction avec message d'erreur portant l'**action corrective**,
  pas seulement la cause.
- `__repr__` redéfini pour ne jamais exposer un credential dans une trace ou un log ;
  les clés secrètes sont écartées du dictionnaire d'environnement dès le chargement.
- Les appels réseau sont **différés** : `account_id` et `account_suffix` sont des
  `cached_property`, donc importer la configuration ne déclenche aucun appel AWS.
- Helpers portant les décisions : `timestamped_name()` (D-unicité temporelle),
  `streamlit_base_url_path` et `health_check_path` (D12), `mlflow_experiment` (D8),
  `tags` / `tags_list` (support de `make destroy`).
- `s3_uri()` rejette tout préfixe hors de la liste `raw/ curated/ capture/ baseline/
  reports/` — évite qu'un binôme écrive à côté et fasse échouer un check pour une faute
  de frappe.

## D18 — Versions figées (`uv.lock`, 2026-07-27)
- `make lock` exécuté : **238 paquets figés**, installation et imports vérifiés.
- Versions clés : `evidently 0.7.21` · `mlflow 3.14.0` · `sagemaker-mlflow 0.5.0` ·
  `strands-agents 1.50.1` · `scikit-learn 1.9.0` · `streamlit 1.60.0` · `boto3 1.43.56`.
- MAJ 29/07/2026 (D25) : **170 paquets figés**. Le SDK `sagemaker`, `torch` (qu'il
  traînait via sagemaker-serve), `xgboost` et `shap` sortent des dépendances
  d'exécution — rien ne les importe depuis la migration. `xgboost` et `shap` restent
  disponibles pour les notebooks d'exploration via le groupe `notebooks`.
- Le contrôle 13 du preflight pointe désormais sur `uv.lock` s'il existe, et
  avertit s'il est absent. Vérifier `requirements.txt` validerait une résolution que
  personne n'installera.
- **Piège Evidently 0.7 à documenter dans le `HINTS.md` du J3.** Les presets ont changé
  de module : ils sont dans `evidently.presets`, et `evidently.metric_preset` n'existe
  plus (`ModuleNotFoundError`). `DataDriftPreset` et `ClassificationPreset` existent
  toujours, sous le nouveau chemin. Conséquence : **tout le code Evidently trouvable en
  ligne cible la 0.4 et ne fonctionnera pas.** Un binôme bloqué qui cherche de l'aide
  tombera sur des exemples faux. À dire explicitement plutôt qu'à laisser découvrir.
- L'API `Report` a également évolué entre 0.4 et 0.7 : écrire le code du J3 contre la
  version figée, jamais contre un souvenir ou un tutoriel.

## D19 — Une application unique plutôt que des scripts numérotés (déroge à §13)

§13 impose une arborescence `day1/01_load_secom.py`, `day1/02_upload_s3.py`, … Retenu à
la place : **un seul paquet `src/qc/`, que les apprenants font grandir sur trois jours**,
piloté par une commande unique `uv run qc <étape>`.

- **L'exigence de fond de §13 est préservée** : arborescence strictement identique entre
  la solution et le starter, TODO numérotés (`TODO-D1-03`), seuls les corps de fonctions
  diffèrent. Seule la *forme* de l'arborescence change.
- **Un TODO gagne à être dans une fonction typée.** `def split(dataset: Dataset,
  features: list[str]) -> Split:` dit à l'apprenant ce qu'il doit produire et avec quel
  type. Au milieu d'un script de 255 lignes, un TODO ne dit que « écris ici ».
- **Le J2 réutilise le J1 sans copier.** C'était le défaut réel de la structure en
  scripts : pour retrouver la liste des variables ou invoquer l'endpoint, le J2 aurait
  dupliqué le code du J1 — les fichiers `01_*.py` ne sont pas importables, leur nom
  commence par un chiffre. Le défaut aurait explosé au J2, pas au J1.
- **Livrable final** : l'apprenant repart avec une application cohérente, pas quinze
  scripts. C'est ce qu'il peut effectivement reprendre chez l'entreprise.
- **Disposition `src/`** (recommandation officielle du Python Packaging Authority) : le
  paquet n'est importable qu'une fois installé, donc les tests testent ce qui est livré.
- **Ce qui remplace l'ordre visible dans l'arborescence** : `uv run qc` sans argument
  affiche le tableau d'avancement, qui dit *où on en est* et pas seulement ce qui existe.
  Plus utile qu'un `ls`, pour l'apprenant qui revient de pause comme pour le formateur
  qui passe derrière lui.
- **Structure plate assumée** : un module par étape du cycle de vie (`secom`, `storage`,
  `training`, `inference`, `agent`, `monitoring`), aucun sous-paquet. On ne transforme un
  module en dossier que s'il dépasse ~300 lignes ou gagne une seconde implémentation.
  Découper à l'avance, c'est deviner.
- **Séparation calcul / affichage** : les modules ne font aucun `print`, tout l'affichage
  est dans `qc/cli.py`. Sans cela, l'agent du J2 qui appelle `qc.inference.predict()`
  récupérerait des `print` au milieu de sa trace.
- **Seule entorse** : `scripts/preflight.py` reste un script PEP 723 autonome avec un
  `sys.path.insert(ROOT / "src")`. Il doit tourner sur une machine neuve, et son contrôle
  n°13 vérifie justement que `uv sync` fonctionne — il ne peut pas en dépendre.

## D20 — Horodatages SECOM : 604 lignes sur 1567 étaient perdues silencieusement

Le fichier UCI mélange **deux formats** d'horodatage : `19/07/2008 11:55:00` et
`1/8/2008 2:02` (sans zéro de tête ni secondes). Le format fixe `%d/%m/%Y %H:%M:%S`
assorti de `errors="coerce"` convertissait **38 % du jeu en `NaT`, sans un mot**.

- Conséquence si non corrigé : la dérive **temporelle** du J3 — comparer les premières
  semaines de production aux dernières — se serait calculée sur 62 % des données, et le
  tri chronologique aurait placé 604 lignes n'importe où.
- Corrigé par `format="mixed", dayfirst=True`. `dayfirst` lève l'ambiguïté de `1/8/2008` :
  le jeu couvre juillet à octobre 2008, donc le 1er août, jamais le 8 janvier.
- Vérifié : 0 `NaT`, période inchangée (19/07/2008 → 17/10/2008), tri monotone.
- **Leçon à porter dans le code du J3** : `errors="coerce"` transforme une erreur bruyante
  en perte de données silencieuse. Ne l'utiliser que lorsqu'on vérifie ensuite ce qui a
  été écarté.

## D21 — L'isolation entre groupes n'existe pas encore (27/07/2026)

`storage.probe_isolation("g02")`, exécutée depuis le poste formateur, **a lu le bucket
d'un autre groupe**. Vérification faite : ce n'est pas un défaut de la tranche 1, c'est
une fonctionnalité qui n'a simplement pas encore été écrite.

Le socle scope bien une politique par groupe — mais sur `qc-gNN-sagemaker-exec`, le rôle
que **SageMaker** endosse. Rien ne contraint l'identité depuis laquelle **l'apprenant**
agit. Or c'est celle-là qui compte pour l'isolation : un binôme qui se trompe de valeur
dans `S3_BUCKET` écrase aujourd'hui les données d'un autre, sans rencontrer le moindre
refus.

- Le README annonçait cette isolation comme l'un des trois piliers du compte partagé.
  C'était faux. Reformulé en « à venir — tranche 2 » plutôt que corrigé en silence.
- À écrire en tranche 2 : un profil d'instance par groupe, porté par la machine de
  travail, avec une politique conditionnée par le préfixe `qc-<TEAM_ID>-*`.
- Tant que ce n'est pas fait, le lab « AccessDenied volontaire » du J1 **ne peut pas
  fonctionner** : il n'y a rien pour refuser. C'est un prérequis pédagogique, pas
  seulement un durcissement.
- `probe_isolation()` distingue désormais les deux situations dans son message : sous une
  identité de formateur, une lecture réussie est normale ; depuis la machine d'un binôme,
  elle signale un profil d'instance non scopé.

**Écrit en tranche 2 (28/07/2026)** — `infra/terraform/socle/workstations.tf` crée
`qc-<TEAM_ID>-workstation-profile`, dont la politique borne S3, SageMaker, ECR, ECS, les
logs et Bedrock au seul préfixe du groupe. Le refus de lire le bucket d'un autre devient
un refus implicite : rien ne l'autorise. Reste à confirmer par un `probe_isolation()`
lancé **depuis une machine de travail**, une fois la tranche appliquée — c'est le seul
endroit où le profil s'applique. Depuis le poste formateur, la lecture réussira toujours.

Deux limites sont assumées et commentées dans le code :

- les actions que l'API AWS ne permet pas de borner par ressource (lister des jobs,
  obtenir un jeton ECR, lire des métriques) restent ouvertes en **lecture** ;
- `elasticloadbalancing:CreateRule` porte sur l'ARN de l'écouteur, partagé par toute la
  promotion. Un binôme peut donc techniquement supprimer la règle d'un autre. Le risque
  suppose un geste délibéré et se répare en quelques secondes par un `apply` du module
  `team/`.

## D22 — Un cluster ECS par groupe, créé dans le socle (28/07/2026)

Le programme se contredisait : la règle de nommage impose `qc-<TEAM_ID>-cluster`, et le
paragraphe sur la séparation des états parle d'un cluster unique appartenant au socle.

Tranché en faveur du nommage par préfixe : un cluster ECS ne coûte rien, ce n'est qu'un
regroupement logique, alors que le préfixe porte l'isolation et les conditions IAM. Les
clusters sont donc **par groupe**, et **créés dans le socle** pour que le
`terraform destroy` du J3 ne puisse pas les emporter.

Corollaire : `FARGATE` reste le fournisseur de capacité par défaut, `FARGATE_SPOT` est
déclaré mais non utilisé par défaut. Une tâche Spot peut être interrompue avec deux
minutes de préavis, ce qui ferait disparaître l'application d'un binôme au milieu d'une
démonstration.

## Corrections appliquées à `docs/00-programme.md` (27/07/2026) — FAIT

Le document était **splicé** à six endroits : des fins de phrases avaient migré à la fin
d'une autre section. Chaque fragment a été recollé à sa phrase d'origine ; aucun mot n'a
été inventé ni supprimé, seulement déplacé.

| § | Phrase tronquée | Fragment retrouvé en |
| --- | --- | --- |
| 4 | « Des checkpoints récupérables… » | fin de §4 |
| 8 | « Livrable J3 : … et lecture du » | fin du squelette J3 (« socle Terraform. ») |
| 11 | « 1) boucle tool use brute en Converse » | fin de §12, après « Nettoyage » |
| 13 | « le dataset est téléchargé via ucimlrepo, » | fin de §14, après les coûts |
| 14 | « …via default_tags au niveau » | fin du paragraphe quotas (« du provider. ») |
| 14 | « bucket qc-<TEAM_ID>-tfstate créé » | fin du module team (« par une étape d'amorçage. ») |

Deux **erreurs factuelles** de §3 ont également été corrigées, chacune signalée sur place
par un encadré daté plutôt que réécrite en silence — le document est la source de vérité
d'agents de code, une correction invisible se reperdrait :

- **591 → 590 mesures capteurs.** La fiche UCI affiche « 591 features » parce qu'elle
  compte `timestamp` comme une variable, ce qu'il n'est pas.
- **L'extrait de chargement ne s'exécutait pas.** `X = secom.data.features ;
  y = secom.data.targets` lève `AttributeError` : les deux valent `None` pour SECOM. Le
  document indique désormais `data.original` et renvoie vers `src/qc/secom.py`. Le piège
  des deux formats d'horodatage (D20) y est documenté au même endroit.

**Reste volontairement non corrigé** : §13 décrit une arborescence `day1/ day2/ day3/`
que D19 remplace par le paquet `src/qc/`. Ce n'est pas une erreur de rédaction mais un
arbitrage assumé, tracé en D19 — le corriger dans le programme masquerait la dérogation.
Même chose pour l'en-tête (« binômes ») que §4 contredit (« trinômes recommandés ») :
c'est à trancher avec l'effectif réel, pas par une retouche du document.


## D23 — Corrections issues de la revue croisée (28/07/2026)

Trois relectures indépendantes du dépôt : une revue de cohérence code/doc/décisions, un
audit du dépôt starter, une relecture des slides contre leur spécification. Ce qu'elles
ont trouvé, et ce qui a été décidé.

### Corrigé

- **Droits MLflow absents du profil apprenant.** D8 les exige ; la politique de la
  machine de travail n'en contenait aucun. Il en faut DEUX familles :
  `sagemaker:*MlflowTracking*` pour dialoguer avec le serveur, et `sagemaker-mlflow:*`
  pour l'API MLflow, qu'AWS expose sous un service distinct. N'accorder que la première
  donne un `AccessDenied` au premier `log_metric`, sur un message qui ne nomme ni l'une
  ni l'autre. Testé jusqu'ici depuis une identité d'administration, qui masquait le
  manque.
- **`env_fragment` incomplet.** Il ne portait ni `MLFLOW_TRACKING_URI` ni `TEAMS_COUNT`,
  alors que `make env-from-tf` est présenté comme la seule façon de remplir `.env`.
  `TEAMS_COUNT` est désormais dérivé de `length(var.teams)`, ce que D1 demandait : un
  effectif modifié dans Terraform se propage au contrôle de quota du preflight.
- **`make restore-day2` promis par D4 et absent.** Écrit, sur le modèle de
  `restore_day1` : incrémental, et il commence par appeler ce dernier puisque le J2 ne
  tient debout que si l'endpoint du J1 répond. Il n'applique PAS le module `team/` —
  c'est du Terraform, il appartient au binôme, et le rejouer à sa place lui retirerait le
  seul endroit du parcours où il lit un `plan`.
- **`check_day2` reconstruisait les noms de ressources** au lieu de les lire dans
  `qc.config`, contre D7 et D10. Une surcharge dans `.env` faisait alors contrôler autre
  chose que ce qui était déployé.
- **`make destroy` et `make slides`**, tous deux annoncés par le programme, sortaient en
  erreur. `destroy` enchaîne l'extinction de l'endpoint et la destruction du module
  `team/` ; `slides` produit les PDF par `marp-cli`.
- **Tags de reprise.** D3 annonçait `day1-end` / `day2-end` ; les tags réellement posés
  sont `j1-fin` / `j2-fin`. La décision suivait des noms inexistants.
- **Références mortes** dans les décisions : `shared/config.py`, `agent/agent_strands.py`,
  `app/prompts.py`, `requirements.lock`. Les fichiers réels sont `src/qc/config.py`,
  `src/qc/agent.py` et `uv.lock`.

### Décidé, non corrigé

- **D4 est dépassée sur `restore-day1`.** Elle décrit une reprise intégrale d'un quart
  d'heure ; le script est incrémental et ne rejoue que ce qui manque. Le comportement
  actuel est meilleur : la décision est marquée obsolète plutôt que le code aligné
  dessus.
- ~~**Le tableau de bord formateur de D10 reste à écrire.**~~ Écrit le 30/07/2026
  (revue technique, point 6) — sous une autre forme que l'agrégation S3 imaginée par
  D10 : `make dashboard` crée un dashboard CloudWatch par groupe
  (`scripts/dashboard.py` — invocations et 4XX de l'endpoint, ModelLatency en µs,
  RunningTaskCount ECS, état des trois alarmes), le relit en preuve, et
  `make dashboard-destroy` nettoie. La requête Logs Insights compagne est dans
  `scripts/logs_insights_erreurs_app.query`. Les checks continuent d'afficher sans
  déposer sous `checks/<TEAM_ID>/`.
- **`discover.py` et `probe_model.py` lisent `os.environ` directement**, contre la règle
  posée par D17. Ce sont des scripts de diagnostic autonomes, exécutables avant que
  `qc.config` ne soit renseigné — c'est précisément leur rôle. La règle vaut pour le
  paquet `qc`, pas pour eux.

---

## D24 — La suite de tests joignait AWS sans le dire (28/07/2026)

`conftest.py` remplace `config.client` par une fabrique de clients factices : les tests
vérifient le contenu des appels au lieu de les envoyer. Quatorze tests passaient malgré
tout par le réseau.

`config.account_id` ouvre son propre client STS par `boto3.Session`, hors de
`config.client`. Tout test dont le chemin touche `s3_bucket` — dont le nom dérive du
suffixe de compte — appelait donc réellement `GetCallerIdentity`.

Le défaut est invisible tant que les identifiants sont valides. Il apparaît sur
`ExpiredToken`, au moment où le nom de la ressource testée n'a plus rien à voir avec le
problème. C'est la forme classique d'un test qui ment : il passe pour une raison qui n'est
pas celle qu'il annonce.

La fixture `aws` écrit maintenant un identifiant de compte factice directement dans le
`__dict__` de `config` — c'est là que `cached_property` range sa valeur, l'y écrire revient
à la mettre en cache. On passe par le dictionnaire plutôt que par `monkeypatch.setattr`,
qui lit l'ancienne valeur avant d'écrire, et cette lecture déclencherait justement l'appel
STS. Les trois valeurs en cache sont vidées avant et après chaque test, `config` étant un
singleton partagé par toute la session.

Contrôle qui vaut la peine d'être rejoué après toute modification de `conftest.py` :

```bash
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
  AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_CONFIG_FILE=/dev/null \
  uv run --frozen pytest tests/
```

Une suite qui ne prétend pas joindre AWS doit passer sans aucun identifiant.

## D25 — La régression logistique remplace XGBoost (« D-optim », 29/07/2026)

- Le candidat validé du notebook `exploration-modelisation-secom-optim.ipynb` devient
  **le** modèle du fil rouge : imputation médiane → standardisation → régression
  logistique régularisée (`C=0.01`), poids de classe calculé sur le train.
- **Ce qui a tranché** : au seuil sélectionné de la même façon (F2, hors-fold sur le
  train), rappel des défauts **57,7 % contre 7,7 %** pour la baseline XGBoost, average
  precision **0,236 contre 0,180**. Validation temporelle (train passé → test futur) :
  AP 0,308, ROC-AUC 0,759. Confirmé par un job SageMaker réel le 29/07 (2 min 09,
  `validation:average_precision 0.2364`, `validation:recall 0.5769`).
- **Conséquences d'architecture** :
  - plus d'algorithme intégré : **mode script** sur le conteneur SKLearn officiel
    (`sagemaker-scikit-learn:1.2-1`, même compte de registre `659782779980`), archive de
    code `model/code/sourcedir.tar.gz`, contrat `sagemaker_program` /
    `sagemaker_submit_directory` en valeurs JSON-encodées ;
  - les métriques passent par `MetricDefinitions` (regex sur les logs du script) ;
  - le **seuil de décision est un livrable de l'entraînement** (`threshold.json` dans
    l'artefact, 0,58 mesuré) — `Prediction.label()` n'a plus de défaut à 0,50 ;
  - l'explication passe de TreeSHAP aux **contributions linéaires exactes**, calculées
    depuis les paramètres ajustés du pipeline (`statistics_`, `mean_`, `scale_`,
    `coef_`), jamais par `transform()` sur l'objet dépicklé — l'artefact est écrit par
    le scikit-learn 1.2 du conteneur et relu par un 1.9 local (incident du 29/07,
    documenté au J3).
- Le contrat externe ne bouge pas : CSV sans en-tête en entrée, un score par ligne en
  sortie — la data capture et le J3 ne voient pas la différence.

## D26 — La capture se filtre par l'horodatage de l'inférence, pas du fichier (29/07/2026)

- Écarter les invocations de la construction de la baseline en filtrant les fichiers de
  capture par leur `LastModified` ne suffit pas : le fichier atterrit une à deux minutes
  **après** `baseline.csv`, et passe le filtre. Constaté le 29/07 : 423 « prédictions de
  production » lues dont les 392 lignes de la baseline, p = 1,0000 sur toutes les
  colonnes — la référence comparée à elle-même, dérive invisible à jamais.
- Le filtre porte désormais sur `eventMetadata.inferenceTime` de **chaque
  enregistrement** (le moment où l'endpoint a répondu). La date du fichier reste un
  pré-filtre, valable dans un seul sens. Un enregistrement sans horodatage lisible est
  conservé : mieux vaut une ligne de trop qu'une production silencieusement ignorée.
- Vérifié sur la capture réelle : 21 prédictions retenues sur 423, et des p-values enfin
  dispersées (score : p = 0,4759) au lieu d'un 1,0000 uniforme.

## D27 — ECS Exec ouvre le canal SSM du formateur, et exige `ssmmessages` (29/07/2026)

- Sans workstation en marche (elles n'existent que pendant la formation, l'AMI se
  construisant la veille — cf. `infra/ami/README.md`), le formateur n'a aucun
  point d'entrée dans le VPC pour joindre l'ALB interne. La tâche ECS du binôme en est
  un : le service est créé avec `enable_execute_command`, et le tunnel
  `AWS-StartPortForwardingSessionToRemoteHost` se monte sur la cible
  `ecs:<cluster>_<task-id>_<runtime-id>`.
- Le piège, constaté le 29/07 : activer l'Exec côté service ne suffit pas. L'agent
  `ExecuteCommandAgent` affiche `RUNNING` dans `describe-tasks` mais chaque
  `start-session` échoue en `TargetNotConnected` tant que le **rôle de tâche** n'accorde
  pas les quatre actions `ssmmessages:*` (canaux de contrôle et de données). Le statut de
  l'agent dit qu'il a démarré, pas qu'il a pu ouvrir son canal.
- Correctif dans le socle (`OuvrirLeCanalExecSSM`, `socle/iam.tf`), appliqué aux six
  rôles. La campagne navigateur du 29/07 (captures du J2) est passée par ce tunnel.

## D28 — La passerelle IDE passe en HTTPS sur `vsc0de.fr`, routée par chemin (15/09/2026)

- **Revirement assumé sur le « pas de domaine, pas de TLS »** de PLAN.md (section
  Non-goals). Ce choix n'était pas une position de principe : personne ne possédait de
  domaine, et un certificat public ACM ne peut pas être émis pour le nom DNS par défaut
  d'un ALB (AWS ne délègue pas `elb.amazonaws.com`, même raisonnement que le bloc HTTPS
  écrit-mais-non-appliqué de `socle/alb.tf`). Le formateur ayant enregistré `vsc0de.fr`,
  la contrainte tombe : la passerelle n'écoute plus qu'en **HTTPS**, le `:80` ne sert
  que la redirection 301.
- **Chemin littéral, pas de sous-domaine par binôme.** `https://vsc0de.fr/g01/`,
  `/g02/`, … — demande explicite du commanditaire. Un `g01.vsc0de.fr` aurait exigé un
  certificat joker et neuf enregistrements à créer à la main chez le registrar, pour un
  résultat identique côté apprenant. Le routage par chemin aligne en plus la passerelle
  IDE sur la convention déjà en place pour Streamlit (décision D12).
- **Le routage par chemin exige un nginx sur chaque machine.** Une règle
  `path_pattern` d'ALB transmet le chemin **tel quel** à la cible : l'ALB ne sait pas
  retirer un préfixe. Streamlit s'en sort avec `--server.baseUrlPath` (D12) ;
  code-server n'a **aucun équivalent**, et recevoir `/g01/…` lui fait chercher ses
  ressources statiques au mauvais endroit — page blanche. Un nginx minimal écoute donc
  en `:8081`, retire le préfixe grâce à la barre oblique finale de
  `proxy_pass http://127.0.0.1:8080/;`, et relaie vers le code-server local. Cette barre
  oblique **est** tout le mécanisme. Les en-têtes `Upgrade` / `Connection` l'accompagnent
  obligatoirement : sans elles le terminal intégré, qui repose sur des websockets, reste
  noir et se reconnecte en boucle.
- **Conséquences d'architecture** :
  - le port exposé par le groupe de sécurité des machines passe de 8080 à **8081** ;
    code-server ne traverse plus jamais ce groupe, il n'est joignable que par la boucle
    locale de sa propre machine ;
  - la sonde de santé du groupe de cibles devient `/<TEAM_ID>/healthz`, **préfixée**
    puisqu'elle traverse nginx — même piège que celui déjà documenté pour le
    `/_stcore/health` de Streamlit dans `alb_teams.tf` ;
  - les neuf écouteurs « un port par binôme » (10001-10009) laissent place à **un seul**
    écouteur `:443` partagé, action par défaut en 404 explicite, plus une
    `aws_lb_listener_rule` par binôme — priorité et double motif `["/gNN", "/gNN/*"]`
    repris à l'identique de `alb_teams.tf` ;
  - `make ide-credentials` et `make ide-check` ne calculent plus de port : ils lisent le
    domaine dans la nouvelle sortie `ide_domain_name`.
- **Le DNS reste HORS de Terraform, et c'est délibéré.** `vsc0de.fr` est enregistré chez
  Hostinger, pas délégué à Route 53 : aucune `aws_route53_zone` ni `aws_route53_record`
  dans ce socle, et il ne faut pas en ajouter — la zone n'appartient pas à ce compte AWS.
  Deux gestes manuels en découlent :
  1. le CNAME de validation ACM, à lire dans `terraform output
     ide_certificate_validation_records` et à créer dans le panneau DNS de Hostinger
     (hPanel → Domaines → DNS / Zone DNS) ;
  2. un enregistrement **ALIAS / ANAME** à l'apex pointant sur `terraform output
     ide_alb_dns_name`. Ni CNAME (illégal à la racine d'une zone) ni A (un ALB n'a pas
     d'IP fixe) ne conviennent — c'est le piège classique du domaine apex.
- **Le premier `terraform apply` SE FIGE, ce n'est pas une panne.**
  `aws_acm_certificate_validation.ide` interroge ACM en boucle (jusqu'à 45 min) tant que
  le CNAME du point 1 n'existe pas. Marche à suivre : lancer un premier apply, le laisser
  bloquer ou l'interrompre, lire la sortie, créer l'enregistrement, relancer. Les applys
  suivants ne bloquent plus.
- Ce qui ne bouge pas : l'authentification reste le mot de passe code-server par équipe
  (toujours pas de Cognito), et `aws_instance.workstation` n'est toujours touché **nulle
  part** — nginx arrive par le même canal SSM `AWS-RunShellScript` que code-server,
  idempotent, sans redémarrage, jamais par `user_data` (contrainte dure de PLAN.md).

## D29 — La passerelle IDE vit sur le sous-domaine `ide.vsc0de.fr`, pas l'apex (15/09/2026)

- **Précision apportée après coup à D28**, qui envisageait un enregistrement à l'apex de
  `vsc0de.fr` (ALIAS/ANAME, seule option puisqu'un CNAME est illégal à la racine d'une
  zone). Dans la pratique, le formateur a créé un **CNAME classique** pour le
  sous-domaine `ide.vsc0de.fr` pointant vers `terraform output ide_alb_dns_name` — un
  geste DNS standard, supporté partout (y compris chez Hostinger), sans dépendre d'un
  type de record propriétaire au registrar.
- **`variable "ide_domain_name"` vaut donc `"ide.vsc0de.fr"`**, pas `"vsc0de.fr"` —
  mis à jour dans `terraform.tfvars.example`. Le certificat ACM (`aws_acm_certificate.ide`,
  `ide.tf`) suit automatiquement puisqu'il est émis pour `var.ide_domain_name`, quel que
  soit le nom qu'elle contient ; aucun changement de ressource, seulement de valeur.
- **Conséquence documentaire** : les commentaires de `ide.tf`, `outputs.tf` et
  `terraform.tfvars.example` qui décrivaient le piège classique de l'apex (« ni CNAME, ni
  A ») ont été corrigés pour décrire ce qui est réellement en place — un CNAME de
  sous-domaine, plus simple que ce que D28 anticipait. `vsc0de.fr` lui-même reste libre
  pour un usage futur, ce qui n'était qu'un effet de bord bienvenu de ce choix, pas son
  motif premier.
- Aucun changement côté Terraform au-delà de la valeur de la variable : les ressources
  (`aws_acm_certificate.ide`, les écouteurs, les règles par binôme) sont paramétrées par
  `var.ide_domain_name` depuis le départ (D28), donc agnostiques du nom exact.
