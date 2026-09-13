# J2 · Image de l'application.
#
# Construction imposée pour linux/amd64 (§11) :
#
#     docker buildx build --platform linux/amd64 -t qc-g01-app .
#
# Sans `--platform`, une construction depuis un Mac Apple Silicon produit une image arm64
# que Fargate refuse de démarrer. La tâche reste en PENDING puis échoue sur
# « image manifest does not contain descriptor matching platform » — message qui ne parle
# ni d'architecture ni de Mac.

FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

# Les dépendances d'abord, le code ensuite : Docker met en cache chaque couche, et le code
# change à chaque construction alors que les dépendances presque jamais. Inversé, chaque
# modification d'une ligne de Python retélécharge 238 paquets.
# README.md est copié parce que pyproject.toml le déclare comme `readme` : sans lui, la
# construction du paquet qc échoue sur « Readme file does not exist », erreur qui ne
# mentionne ni Docker ni le .dockerignore.
COPY pyproject.toml uv.lock README.md ./

# --frozen : installer EXACTEMENT ce que uv.lock décrit, sans le recalculer. Une image qui
# résout ses dépendances à la construction n'est pas reproductible — deux constructions à
# deux semaines d'écart donnent deux images différentes.
# --no-install-project : le paquet qc n'est pas encore copié à ce stade.
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
COPY app/ ./app/

RUN uv sync --frozen --no-dev

# curl sert au contrôle de santé du conteneur déclaré dans la définition de tâche. Sans
# lui, ECS déclare la tâche malsaine et la relance en boucle, ce qui ressemble à un
# plantage de l'application.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 8501

# TEAM_ID est fourni par la définition de tâche ECS, pas figé ici : la même image sert aux
# six binômes. En local, il vient de .env.
ENV TEAM_ID=""

# --server.baseUrlPath : décision D12. Derrière l'ALB le conteneur reçoit `GET /g01/` ;
# sans ce préfixe Streamlit se croit à la racine et génère ses ressources statiques sur
# /static/…, que l'ALB ne route pas. Page blanche, tâche saine, logs muets.
#
# --server.address=0.0.0.0 : par défaut Streamlit n'écoute que sur localhost, donc l'ALB
# ne peut pas le joindre depuis une autre adresse IP.
#
# Origine derrière l'ALB : Streamlit compare l'en-tête Origin du navigateur à sa liste
# d'origines admises — par défaut sa propre adresse interne (0.0.0.0:8501) et localhost.
# Derrière l'ALB, le navigateur voit une adresse externe différente : la comparaison
# échoue, Streamlit renvoie 403 sur la connexion websocket, et l'appli reste bloquée sur
# l'écran de chargement. Vérifié le 28/07/2026 : sans Origin (curl) 101, avec Origin
# (tout navigateur réel) 403.
#
# La bonne réponse N'EST PAS de désactiver CORS et la protection XSRF : c'est déclarer
# l'origine externe. La définition de tâche passe STREAMLIT_BROWSER_SERVER_ADDRESS
# (= browser.serverAddress) avec le DNS de l'ALB, que Streamlit ajoute à ses origines
# admises — protections intactes. Le tunnel local reste couvert : localhost est toujours
# admis.
#
# SANDBOX_RELAX_WEBSOCKET=1 est la DÉROGATION SANDBOX : elle rétablit l'ancienne
# désactivation, pour un environnement jetable où l'adresse externe n'est pas connue
# (tunnel improvisé, proxy d'atelier). Jamais en formation nominale — le contrôle
# websocket de `make check-day2` vérifie le montage nominal.
#
# La forme `sh -c` est nécessaire pour que $TEAM_ID et la dérogation soient évalués à
# l'exécution ; en forme exec, les variables seraient passées littéralement.
CMD ["sh", "-c", "\
     if [ \"$SANDBOX_RELAX_WEBSOCKET\" = \"1\" ]; then \
       RELAX='--server.enableCORS=false --server.enableXsrfProtection=false'; \
     else \
       RELAX=''; \
     fi; \
     exec uv run --frozen streamlit run app/main.py \
     --server.port=8501 \
     --server.address=0.0.0.0 \
     --server.baseUrlPath=${TEAM_ID} \
     --server.headless=true \
     --browser.gatherUsageStats=false $RELAX"]
