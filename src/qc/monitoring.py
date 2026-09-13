"""J3 · Exploitation — dérive, baseline, métriques.

Ce module ne fait AUCUN affichage : il calcule, il renvoie. L'affichage est dans `qc.cli`.

Le J1 a activé la data capture, le J2 a mis l'application en service. Ce module répond à
la question qui reste : **est-ce que ce qui arrive aujourd'hui ressemble à ce sur quoi le
modèle a été validé ?**

    from qc import monitoring
    reference = monitoring.build_baseline()
    courant   = monitoring.read_capture()
    resultat  = monitoring.compare(reference, courant.frame)

Trois pièges tenus par ce module, chacun vérifié contre la bibliothèque installée :

  - Evidently 0.7 : les presets sont dans `evidently.presets`, et la clé du dictionnaire
    de résultat est `metric_name`. Tout le code trouvable en ligne cible la 0.4, où les
    presets étaient dans `evidently.metric_preset` (décision D18) ;
  - la data capture est du JSONL dont l'entrée est une CHAÎNE CSV, pas un objet. Il faut
    la redécouper ;
  - une comparaison n'a de sens qu'entre colonnes identiques. Reference et current
    doivent porter les mêmes noms, dans le même ordre.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from qc.config import DATA_DIR, config

#: Préfixes S3. Alignés sur `config.S3_PREFIXES` et sur le socle Terraform.
CAPTURE_PREFIX = "capture"
BASELINE_PREFIX = "baseline"
REPORTS_PREFIX = "reports"

#: Nom de la colonne de score, ajoutée aux mesures capteurs. La dérive du score est le
#: signal le plus lisible : elle bouge avant que les capteurs ne paraissent anormaux.
SCORE_COLUMN = "score"

#: Seuil de p-value au-delà duquel une colonne est considérée stable. Valeur par défaut
#: d'Evidently, conservée pour que le lab et la documentation en ligne concordent.
P_VALUE_SEUIL = 0.05

#: Part de colonnes dérivées au-delà de laquelle on parle de dérive du jeu entier.
PART_DERIVEE_SEUIL = 0.3


class MonitoringError(RuntimeError):
    """Erreur d'exploitation, formulée pour être lue par un apprenant."""


@dataclass(frozen=True)
class Capture:
    """Ce que l'endpoint a réellement vu et répondu."""

    frame: pd.DataFrame
    requetes: int

    @property
    def lignes(self) -> int:
        # TODO-D3-01 — à écrire.
        raise NotImplementedError("TODO-D3-01")

    def summary(self) -> str:
        return (
            f"{self.lignes} prédictions capturées sur {self.requetes} requête(s)\n"
            f"  score moyen  {self.frame[SCORE_COLUMN].mean():.4f}\n"
            f"  score max    {self.frame[SCORE_COLUMN].max():.4f}"
        )


@dataclass(frozen=True)
class Drift:
    """Résultat d'une comparaison entre une référence et la production."""

    colonnes_derivees: int
    colonnes_totales: int
    part: float
    p_value_score: float
    rapport_html: Path
    details: dict[str, float] = field(default_factory=dict)

    @property
    def score_a_derive(self) -> bool:
        # TODO-D3-02 — à écrire.
        raise NotImplementedError("TODO-D3-02")

    @property
    def jeu_a_derive(self) -> bool:
        # TODO-D3-03 — à écrire.
        raise NotImplementedError("TODO-D3-03")

    def summary(self) -> str:
        lignes = [
            f"{self.colonnes_derivees}/{self.colonnes_totales} colonnes dérivées"
            f" ({self.part:.0%})",
            f"  distribution des scores : p = {self.p_value_score:.4f}"
            f" — {'DÉRIVE' if self.score_a_derive else 'stable'}",
        ]
        if self.details:
            pires = sorted(self.details.items(), key=lambda kv: kv[1])[:5]
            lignes.append("  colonnes les plus dérivées :")
            lignes += [f"    {nom:<16} p = {p:.4f}" for nom, p in pires]
        lignes.append(f"  rapport : {self.rapport_html}")
        return "\n".join(lignes)


# ------------------------------------------------------------------- data capture


def _inference_time(objet: dict):
    """Horodatage de l'inférence d'un enregistrement de capture, ou None.

    C'est `eventMetadata.inferenceTime`, le moment où l'endpoint a RÉPONDU — pas le
    moment où le fichier de capture est apparu dans S3, qui arrive une à deux minutes
    plus tard. La distinction est ce qui permet d'écarter proprement les invocations de
    la construction de la baseline (voir `read_capture`).
    """
    # TODO-D3-04 — à écrire.
    raise NotImplementedError("TODO-D3-04")


def _parse_capture(contenu: str, apres=None) -> tuple[list[list[float]], list[float], int]:
    """Découpe un fichier de capture JSONL. Renvoie (mesures, scores, requêtes retenues).

    Le format n'est pas celui qu'on attend. Chaque ligne du fichier est un objet JSON,
    mais `endpointInput.data` et `endpointOutput.data` sont des CHAÎNES : le CSV envoyé
    et les scores renvoyés, tels quels. Une requête portant vingt pièces produit donc UNE
    ligne de fichier contenant vingt lignes CSV et vingt scores séparés par des retours à
    la ligne — le même séparateur qu'au J1.

    `apres` écarte les enregistrements dont l'INFÉRENCE est antérieure à cette date. Un
    enregistrement sans horodatage lisible est conservé : mieux vaut une ligne de trop
    qu'une production silencieusement ignorée.
    """
    # TODO-D3-05 — à écrire.
    raise NotImplementedError("TODO-D3-05")


def baseline_date():
    """Date de dépôt de la baseline, ou None si elle n'existe pas."""
    # TODO-D3-06 — à écrire.
    raise NotImplementedError("TODO-D3-06")


def read_capture(
    colonnes: list[str] | None = None, max_objets: int = 200, apres=None
) -> Capture:
    """Lit la data capture du groupe et la met en tableau.

    `colonnes` donne les noms des mesures. Sans elle, les colonnes sont numérotées, et la
    comparaison à la baseline échouera faute de noms communs.

    `apres` écarte les prédictions dont l'inférence est antérieure à cette date. C'est
    indispensable, et ce n'est pas évident : construire la baseline invoque l'endpoint
    sur les 392 lignes de test, et la data capture les enregistre comme n'importe quelle
    autre requête. Sans ce filtre, la référence se retrouve DANS la production qu'on lui
    compare, et la comparaison conclut invariablement à l'absence de dérive.

    Le filtre porte sur l'horodatage de CHAQUE enregistrement (`inferenceTime`), pas sur
    la date de dépôt du fichier : la capture s'écrit une à deux minutes après
    l'inférence, si bien que le fichier des invocations de la baseline atterrit APRÈS
    `baseline.csv`. Mesuré le 29/07/2026 avec un filtre par date de fichier : les 392
    lignes de la baseline comptées comme production, p = 1,0000 partout. Et le 28/07 :
    80 prédictions volontairement décalées, noyées dans ces 392 lignes, donnaient
    0 colonne dérivée sur 41.
    """
    # TODO-D3-07 — à écrire.
    raise NotImplementedError("TODO-D3-07")


# ----------------------------------------------------------------------- baseline


def build_baseline(source: Path | None = None) -> pd.DataFrame:
    """Construit la référence : le jeu de test, passé par l'endpoint.

    La référence n'est pas le jeu d'entraînement. Ce qu'on veut comparer, c'est ce que le
    modèle produisait sur des données qu'il n'avait pas vues, au moment où on l'a validé.
    Prendre le jeu d'entraînement donnerait une distribution de scores optimiste : même
    régularisée, la logistique choisit ses coefficients pour CES pièces-là, et son seuil
    F2 a été sélectionné sur des prédictions issues de ce même train.
    """
    # TODO-D3-08 — à écrire.
    raise NotImplementedError("TODO-D3-08")


def save_baseline(reference: pd.DataFrame) -> str:
    """Dépose la référence dans S3 et renvoie son URI.

    Le versioning du bucket protège cette référence : comparer une production à une
    baseline suppose que la baseline n'a pas été remplacée entre-temps.
    """
    # TODO-D3-09 — à écrire.
    raise NotImplementedError("TODO-D3-09")


def load_baseline() -> pd.DataFrame:
    """Relit la référence déposée dans S3."""
    # TODO-D3-10 — à écrire.
    raise NotImplementedError("TODO-D3-10")


# -------------------------------------------------------------------------- dérive


def compare(reference: pd.DataFrame, courant: pd.DataFrame, sortie: Path | None = None) -> Drift:
    """Compare la production à la référence et écrit un rapport HTML.

    Les deux tableaux doivent porter LES MÊMES colonnes. Evidently ne compare que les
    colonnes communes et ignore les autres en silence : une comparaison portant sur zéro
    colonne commune renvoie « aucune dérive », ce qui ressemble à un succès.
    """
    # TODO-D3-11 — à écrire.
    raise NotImplementedError("TODO-D3-11")


def _lire_resultat(resultat: dict, colonnes: int, rapport: Path) -> Drift:
    """Extrait les chiffres du dictionnaire produit par Evidently.

    La clé est `metric_name` en 0.7. Les exemples en ligne lisent `metric_id`, hérité de
    la 0.4 : la lecture renvoie alors None partout, et le rapport paraît vide alors qu'il
    est complet (décision D18).
    """
    # TODO-D3-12 — à écrire.
    raise NotImplementedError("TODO-D3-12")


def publish_report(rapport: Path) -> str:
    """Dépose le rapport HTML dans S3 et renvoie son URI."""
    # TODO-D3-13 — à écrire.
    raise NotImplementedError("TODO-D3-13")


# ------------------------------------------------------------------------ MLflow


def log_to_mlflow(drift: Drift, capture: Capture) -> str:
    """Enregistre la comparaison dans MLflow. Renvoie l'identifiant de l'exécution.

    Une expérience par groupe, `qc-<TEAM_ID>`. L'intérêt du J3 n'est pas d'enregistrer une
    exécution mais de pouvoir en comparer plusieurs : c'est la courbe de la p-value sur
    trois exécutions qui montre la dérive, pas un chiffre isolé.
    """
    # TODO-D3-14 — à écrire.
    raise NotImplementedError("TODO-D3-14")


# -------------------------------------------------------------------- CloudWatch


def endpoint_metrics(minutes: int = 60) -> dict[str, float]:
    """Métriques de l'endpoint sur la dernière heure.

    `ModelLatency` est en MICROsecondes. C'est la source d'erreur classique : lue en
    millisecondes, une latence de 400 ms paraît être de 400 000 ms, et on croit à un
    incident.
    """
    # TODO-D3-15 — à écrire.
    raise NotImplementedError("TODO-D3-15")


def alarm_states() -> dict[str, str]:
    """État des alarmes du groupe. `ALARM`, `OK` ou `INSUFFICIENT_DATA`.

    `INSUFFICIENT_DATA` n'est pas une panne : une alarme sans données récentes est dans
    cet état, ce qui est normal sur un endpoint qu'on vient d'allumer.
    """
    # TODO-D3-16 — à écrire.
    raise NotImplementedError("TODO-D3-16")


def websocket_handshake_status(
    hote: str, chemin: str, origine: str, port: int = 80, timeout: float = 10.0
) -> int:
    """Code HTTP du handshake websocket, avec un VRAI en-tête Origin. 0 si injoignable.

    La leçon du J3 §6.1 : un `curl` sans Origin obtient 101 même quand tout navigateur
    réel reçoit 403 — Streamlit ne vérifie l'origine que si elle est fournie. Ce
    handshake envoie donc l'Origin qu'enverrait un navigateur pointé sur l'ALB, et c'est
    le seul contrôle scriptable qui exerce le chemin websocket comme un utilisateur.

    101 : l'application accepte le navigateur. 403 : protection XSRF/CORS mal
    configurée — vérifier STREAMLIT_BROWSER_SERVER_ADDRESS dans la définition de tâche.
    """
    # TODO-D3-17 — à écrire.
    raise NotImplementedError("TODO-D3-17")
