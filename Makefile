# Cibles du projet. `make` sans argument affiche cette aide.
#
# Les scripts déclarent leurs dépendances en ligne (PEP 723) : uv les résout dans un
# environnement jetable. Aucune installation globale, aucun besoin de l'AWS CLI.

.DEFAULT_GOAL := help
.PHONY: help discover probe preflight preflight-formateur bootstrap-apply socle-init \
        socle-plan socle-apply socle-destroy backend-hcl  \
        team-init team-plan team-apply team-destroy   \
          check-day1 check-day2 check-day3 \
        restore-day1 restore-day2   destroy sync test ci  lock \
        ide-credentials ide-check

UV := uv run

help: ## Affiche cette aide
	@echo ""
	@echo "  l'entreprise — AWS pour Data Scientists"
	@echo ""
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Les labs passent par la commande qc, pas par make :"
	@echo "    uv run qc          où en est le binôme"
	@echo "    uv run qc <étape>  exécute l'étape"
	@echo ""
	@echo "  Ordre imposé : preflight vert AVANT toute création de ressource (§12)."
	@echo ""

# --- Diagnostic ---------------------------------------------------------------

discover: ## Sonde le compte AWS (ne crée rien) — modèles, quotas, services
	@$(UV) scripts/discover.py

probe: ## Vérifie l'accès modèle Bedrock et son pilotage par Strands
	@$(UV) scripts/probe_model.py

preflight: ## 15 contrôles obligatoires avant toute création de ressource (mode apprenant : aucune écriture IAM)
	@$(UV) scripts/preflight.py

preflight-formateur: ## [formateur] Preflight avec la sonde iam:CreateRole, requise avant socle-apply
	@$(UV) scripts/preflight.py --formateur

# --- Socle, côté formateur ----------------------------------------------------

TF_BOOTSTRAP := infra/terraform/bootstrap
TF_SOCLE     := infra/terraform/socle

bootstrap-apply: ## [formateur] Étape 1 — crée le bucket d'état Terraform (une seule fois)
	@# L'état de ce module est LOCAL et ignoré par git : sur un clone frais, Terraform
	@# croit que le bucket n'existe pas et l'apply échoue sur un bucket déjà pris. Le
	@# bootstrap n'est à jouer qu'une fois dans la vie de la formation ; ensuite c'est
	@# `make backend-hcl` qui régénère le fichier, sans état ni apply.
	@if $(UV) scripts/backend_hcl.py 2>/dev/null; then \
	  echo "Bucket d'état déjà en place — bootstrap déjà appliqué, rien à créer."; \
	  echo "Enchaîner avec : make socle-init"; \
	else \
	  cd $(TF_BOOTSTRAP) && terraform init && terraform apply \
	    && terraform output -raw backend_config > ../socle/backend.hcl \
	    && echo "backend.hcl généré. Enchaîner avec : make socle-init"; \
	fi

backend-hcl: ## [formateur] Régénère socle/backend.hcl depuis le compte courant (clone frais)
	@$(UV) scripts/backend_hcl.py

socle-init: ## [formateur] Étape 2 — initialise le socle sur son backend distant
	@test -f $(TF_SOCLE)/backend.hcl || $(MAKE) --no-print-directory backend-hcl
	@cd $(TF_SOCLE) && terraform init -backend-config=backend.hcl

socle-plan: ## [formateur] Montre ce qui serait créé, sans rien créer
	@cd $(TF_SOCLE) && terraform plan

socle-apply: ## [formateur] Étape 3 — applique le socle
	@# Garde-fou AMI : allumer les huit machines sans AMI dorée, c'est huit bootstraps
	@# complets en parallèle sur la NAT — un quart d'heure perdu, la veille au soir.
	@if grep -qE '^create_workstations *= *true' $(TF_SOCLE)/terraform.tfvars 2>/dev/null \
	  && grep -qE '^workstation_ami_id *= *""' $(TF_SOCLE)/terraform.tfvars 2>/dev/null; then \
	  echo ""; \
	  echo "  ATTENTION : create_workstations = true mais workstation_ami_id est vide."; \
	  echo "  Les machines démarreront en bootstrap lent (~10 min chacune)."; \
	  echo "  Construire l'AMI d'abord : make ami-build  (elle remplit tfvars toute seule)"; \
	  echo ""; \
	fi
	@cd $(TF_SOCLE) && terraform apply


socle-destroy: ## [formateur] Détruit le socle en fin de formation
	@cd $(TF_SOCLE) && terraform destroy
	@echo "Le bucket d'état survit volontairement (prevent_destroy)."
	@echo "Le supprimer à la main une fois la formation clôturée."

# --- Passerelle IDE navigateur (code-server), côté formateur ------------------

ide-credentials: ## [formateur] Tableau TEAM_ID -> URL -> mot de passe code-server, pour la distribution au lancement
	@$(UV) scripts/ide_credentials.py

ide-check: ## [formateur] Vérifie que /healthz répond pour la passerelle IDE de chaque équipe
	@$(UV) scripts/ide_check.py

ssh-tunnel: ## Ouvre un tunnel SSM vers la machine de l'équipe, sans AWS CLI (Windows)
	@$(UV) scripts/ssm_ssh_tunnel.py $(ARGS)

# --- Module team, côté apprenant ----------------------------------------------

TF_TEAM := infra/terraform/team

# Le bucket d'état vient du `.env` apprenant. Sur le dépôt solution, le repli sur
# backend.hcl reste pratique pour le formateur ; ce fichier est volontairement absent du
# starter, car il dépend du compte AWS de formation.
TF_STATE_BUCKET ?= $(shell sed -n 's/^bucket *= *"\(.*\)"/\1/p' $(TF_SOCLE)/backend.hcl 2>/dev/null)

TEAM_TF_VARS = -var="team_id=$(TEAM_ID)" -var="owner_email=$(OWNER_EMAIL)" -var="state_bucket=$(TF_STATE_BUCKET)"

team-init: ## Initialise le module du binôme — usage : make team-init TEAM_ID=g01
	@test -n "$(TEAM_ID)" || (echo "Usage : make team-init TEAM_ID=g01"; exit 1)
	@test -n "$(TF_STATE_BUCKET)" \
	  || (echo "Bucket d'état introuvable — renseigner TF_STATE_BUCKET dans .env, ou (formateur) : make backend-hcl"; exit 1)
	@cd $(TF_TEAM) && terraform init \
	  -backend-config="bucket=$(TF_STATE_BUCKET)" \
	  -backend-config="key=team/$(TEAM_ID)/terraform.tfstate" \
	  -backend-config="region=$${AWS_REGION:-eu-west-3}" \
	  -backend-config="encrypt=true" \
	  -backend-config="use_lockfile=true"

team-plan: ## Montre ce que le binôme créerait — make team-plan TEAM_ID=g01
	@test -n "$(TEAM_ID)" || (echo "Usage : make team-plan TEAM_ID=g01"; exit 1)
	@test -n "$(OWNER_EMAIL)" || (echo "OWNER_EMAIL absent — renseigner .env puis : set -a; . ./.env; set +a"; exit 1)
	@test -n "$(TF_STATE_BUCKET)" || (echo "TF_STATE_BUCKET absent — demander l'extrait .env du groupe au formateur puis recharger .env"; exit 1)
	@cd $(TF_TEAM) && terraform plan $(TEAM_TF_VARS)

team-apply: ## Déploie l'application du binôme — make team-apply TEAM_ID=g01
	@test -n "$(TEAM_ID)" || (echo "Usage : make team-apply TEAM_ID=g01"; exit 1)
	@test -n "$(OWNER_EMAIL)" || (echo "OWNER_EMAIL absent — renseigner .env puis : set -a; . ./.env; set +a"; exit 1)
	@test -n "$(TF_STATE_BUCKET)" || (echo "TF_STATE_BUCKET absent — demander l'extrait .env du groupe au formateur puis recharger .env"; exit 1)
	@cd $(TF_TEAM) && terraform apply $(TEAM_TF_VARS)

team-destroy: ## Supprime les ressources du binôme, jamais le socle
	@test -n "$(TEAM_ID)" || (echo "Usage : make team-destroy TEAM_ID=g01"; exit 1)
	@test -n "$(OWNER_EMAIL)" || (echo "OWNER_EMAIL absent — renseigner .env puis : set -a; . ./.env; set +a"; exit 1)
	@test -n "$(TF_STATE_BUCKET)" || (echo "TF_STATE_BUCKET absent — demander l'extrait .env du groupe au formateur puis recharger .env"; exit 1)
	@cd $(TF_TEAM) && terraform destroy $(TEAM_TF_VARS)





# --- Vérification, côté apprenant ---------------------------------------------

check-day1: ## Vérifie l'état AWS attendu en fin de J1
	@$(UV) scripts/check_day1.py

check-day2: ## Vérifie l'état AWS attendu en fin de J2
	@$(UV) scripts/check_day2.py

check-day3: ## Vérifie l'état AWS attendu en fin de J3
	@$(UV) scripts/check_day3.py

env-from-tf: ## Renseigne le .env depuis les sorties Terraform (jamais à la main)
	@test -n "$(TEAM_ID)" || (echo "Usage : make env-from-tf TEAM_ID=g01"; exit 1)
	@cd $(TF_SOCLE) && terraform output -json env_fragment \
	  | jq -r '.["$(TEAM_ID)"]'
	@echo ""
	@echo "Reporter ces lignes dans .env — ou rediriger cette sortie."

# --- Reprise ------------------------------------------------------------------

restore-day1: ## Rejoue ce qui manque du J1 : S3, entraînement, endpoint (~15 min)
	@$(UV) scripts/restore_day1.py $(ARGS)

restore-day2: ## Rejoue ce qui manque du J2 : J1, image, push ECR (~10 min)
	@$(UV) scripts/restore_day2.py $(ARGS)

# --- Divers -------------------------------------------------------------------

sync: ## Installe l'environnement depuis uv.lock
	@uv sync --frozen

test: ## Lance la suite de tests
	@$(UV) --frozen pytest tests/

ci: ## Tout ce que la CI vérifie : fmt, validate (bootstrap/socle/team), lint IAM, tests
	@terraform fmt -check -recursive infra/terraform
	@# TF_DATA_DIR jetable : un `terraform init` local a pu configurer le backend S3
	@# dans .terraform/, et sa relecture exigerait des credentials — hors sujet ici.
	@for module in bootstrap socle team; do \
		donnees=$$(mktemp -d); \
		echo "terraform validate — $$module"; \
		(cd infra/terraform/$$module \
			&& TF_DATA_DIR=$$donnees terraform init -backend=false -input=false > /dev/null \
			&& TF_DATA_DIR=$$donnees terraform validate) || exit 1; \
		rm -rf $$donnees; \
	done
	@$(UV) scripts/lint_iam.py
	@$(UV) --frozen pytest tests/

lock: ## Fige les dépendances (uv.lock) et régénère requirements.txt
	@uv lock
	@# requirements.txt est un ARTEFACT GÉNÉRÉ, conservé pour la traçabilité demandée
	@# par §13. La source de vérité est pyproject.toml.
	@uv export --no-hashes --no-dev --format requirements-txt -o requirements.txt -q
	@echo "Vérification des imports…"
	@$(UV) --frozen python -c \
	  "import boto3, strands, evidently, mlflow, sagemaker_mlflow, streamlit"
	@echo "OK — $$(grep -c '^[a-zA-Z0-9]' requirements.txt) paquets figés."

destroy: ## Supprime ce que les labs facturent — endpoint puis service ECS
	@test -n "$(TEAM_ID)" || (echo "Usage : make destroy TEAM_ID=g01"; exit 1)
	@$(UV) qc teardown
	@cd $(TF_TEAM) && terraform destroy -var="team_id=$(TEAM_ID)"
	@echo ""
	@echo "L'image ECR et l'artefact S3 restent : ils ne coûtent presque rien et"
	@echo "permettent de redéployer sans reconstruire. make socle-destroy les emporte."
