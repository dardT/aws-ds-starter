"""J2 · Explicabilité — quelles mesures pèsent dans un score.

Ce module ne fait AUCUN affichage : il calcule, il renvoie.

Le J1 donne un score, le J2 donne une explication. Un opérateur qui écarte une pièce doit
pouvoir dire pourquoi, et « le modèle a dit 0,9183 » ne suffit ni devant un client ni
devant un auditeur.

    from qc import explain
    explication = explain.explain_row(mesures, noms)
    explication.summary()

Pour une régression logistique, les contributions linéaires au logit sont EXACTES et se
calculent sans bibliothèque dédiée : le logit est `intercept + Σ coef_i · z_i`, où `z_i`
est la mesure imputée puis standardisée. La contribution linéaire de la mesure i est donc
`coef_i · z_i`, et le biais est l'intercept. `shap.LinearExplainer` donnerait les mêmes
nombres à une translation près (il centre sur la moyenne du jeu de référence) ; ici la
référence est la moyenne du train, déjà encodée dans la standardisation — z = 0 est
littéralement « la pièce moyenne du train ».

Propriété à connaître avant de lire un résultat : les contributions et le biais
s'additionnent pour donner le logit — la sortie brute du modèle. C'est ce qui permet de
dire « ces trois capteurs expliquent 80 % de l'écart », et c'est ce qui distingue une
explication d'une intuition. Pour que l'égalité tienne, `Explication` porte TOUTES les
contributions (revue du 29/07/2026, point 4 : tronquer avant la somme la rendait fausse
d'un tiers sur une pièce réelle) ; seul l'affichage — `summary()` — retient les `top_n`
plus fortes. La somme vit dans l'espace du logit, pas dans celui de la probabilité.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Nombre de mesures affichées par défaut dans `summary()`. Au-delà, un opérateur ne
#: lit plus. L'objet `Explication`, lui, garde toujours TOUTES les contributions.
TOP_N = 5


class ExplainError(RuntimeError):
    """Erreur d'explicabilité, formulée pour être lue par un apprenant."""


@dataclass(frozen=True)
class Contribution:
    """Contribution linéaire d'une mesure au logit d'une pièce."""

    nom: str
    valeur: float
    poids: float

    @property
    def sens(self) -> str:
        return "vers NON CONFORME" if self.poids > 0 else "vers conforme"


@dataclass(frozen=True)
class Explication:
    """Décomposition COMPLÈTE du logit d'une pièce.

    `contributions` porte toutes les mesures, triées par poids absolu décroissant —
    jamais tronquées : c'est la condition de l'additivité portée par `total`. La
    troncature est une affaire d'affichage, elle vit dans `summary()`.
    """

    indice: int
    contributions: tuple[Contribution, ...]
    biais: float

    @property
    def total(self) -> float:
        """Somme des contributions linéaires et du biais — le logit du modèle.

        Cette égalité est la garantie de l'explication : elle dit que rien n'a été
        oublié. Un graphique d'importance globale, lui, ne s'additionne pas et ne se
        rapporte à aucune pièce en particulier.
        """
        return self.biais + sum(c.poids for c in self.contributions)

    def summary(self, top_n: int = TOP_N) -> str:
        """Les `top_n` contributions les plus fortes — l'affichage, pas le calcul."""
        lignes = [f"Pièce {self.indice} — mesures les plus déterminantes :"]
        for c in self.contributions[:top_n]:
            lignes.append(
                f"  {c.nom:<14} = {c.valeur:>12.4f}   {c.poids:+.4f}  {c.sens}"
            )
        return "\n".join(lignes)


def load_model():
    """Charge le pipeline scikit-learn (imputation, standardisation, logistique) depuis
    l'artefact du dernier entraînement réussi.

    Le conteneur du J2 ne contient aucun modèle : il sert des prédictions par l'endpoint.
    Pour EXPLIQUER, il faut le modèle lui-même — l'endpoint ne renvoie qu'un score, il ne
    décompose rien. C'est une différence de fond entre prédire et expliquer.
    """
    import warnings

    import joblib

    from qc import inference

    # L'artefact est picklé par le scikit-learn 1.2 du conteneur, relu ici par une
    # version plus récente : sklearn émet un InconsistentVersionWarning à chaque
    # chargement. Il est sans objet dans ce module — `explain_row` ne lit que des
    # attributs ajustés (statistics_, mean_, scale_, coef_), jamais de méthode — et le
    # laisser passer noierait la trace de l'agent et la sortie du CLI.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*InconsistentVersion.*")
        warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")
        return joblib.load(inference.artifact_member("model.joblib"))


def explain_row(
    mesures: list[float], noms: list[str], indice: int = 0, model=None
) -> Explication:
    """Décompose le logit d'une pièce en contributions linéaires par mesure — toutes.

    La décomposition rejoue les trois étages du pipeline : imputation (remplace un
    éventuel manquant par la médiane du train), standardisation (centre-réduit avec les
    statistiques du train), puis produit par les coefficients. Une pièce parfaitement
    moyenne a toutes ses contributions à zéro — il ne reste que le biais.
    """
    import numpy

    if len(mesures) != len(noms):
        raise ExplainError(
            f"{len(mesures)} mesures pour {len(noms)} noms de colonnes.\n"
            "  Les deux doivent venir du même échantillon."
        )

    model = model if model is not None else load_model()
    try:
        imputer = model.named_steps["imputer"]
        scaler = model.named_steps["scaler"]
        logistic = model.named_steps["model"]
    except (AttributeError, KeyError) as exc:
        raise ExplainError(
            "L'artefact chargé n'est pas le pipeline attendu"
            " (imputer → scaler → model).\n"
            "  L'entraînement vient-il d'un job antérieur à la migration ?"
            " Relancer `uv run qc train`."
        ) from exc

    # Les trois étages sont rejoués à partir des PARAMÈTRES ajustés (statistics_,
    # mean_, scale_, coef_), jamais en appelant transform() : l'artefact est picklé par
    # le scikit-learn 1.2 du conteneur, et les méthodes d'une version plus récente
    # attendent des attributs internes que ce pickle n'a pas — constaté le 29/07/2026,
    # `AttributeError: '_fill_dtype'` sous 1.9. Les attributs ajustés, eux, font partie
    # du contrat public et traversent les versions.
    ligne = numpy.array(mesures, dtype=float)
    imputee = numpy.where(numpy.isnan(ligne), imputer.statistics_, ligne)
    standardisee = (imputee - scaler.mean_) / scaler.scale_
    poids = logistic.coef_[0] * standardisee
    biais = float(logistic.intercept_[0])

    # Tri par poids absolu décroissant, SANS troncature : couper ici casserait
    # l'additivité de `total` (revue du 29/07/2026, point 4 — 32 % d'écart sur la
    # première pièce de l'échantillon). Le top-5 est l'affaire de `summary()`.
    ordre = sorted(range(len(poids)), key=lambda i: abs(poids[i]), reverse=True)

    return Explication(
        indice=indice,
        contributions=tuple(
            Contribution(nom=noms[i], valeur=float(mesures[i]), poids=float(poids[i]))
            for i in ordre
        ),
        biais=biais,
    )
