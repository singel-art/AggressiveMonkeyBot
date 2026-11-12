"""Market Intelligence d'AggressiveMonkeyBot.

Collecte, stockage "point-in-time" et synthese des donnees macroeconomiques.
Ce module ne prend aucune decision de trading et n'est jamais appele dans le
chemin d'execution des ordres : il ecrit des fichiers que l'Expert Advisor relit.
"""

__version__ = "0.1.0"
