"""Contrôle qualité de pièces en production — application construite sur trois jours.

Un module par étape du cycle de vie. Les apprenants font grandir ce paquet :

    J1   secom · storage · training · inference     de la donnée à l'endpoint
    J2   agent                                      agent Bedrock outillé
    J3   monitoring                                 dérive et supervision

Les modules ne sont pas importés ici : `qc.config` déclenche la lecture de `.env` et
`qc.secom` tire pandas. Un import paresseux garde `uv run qc --help` instantané.
"""

__version__ = "0.1.0"
