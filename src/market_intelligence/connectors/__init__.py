from .bls import BlsConnector
from .cftc import CftcConnector
from .fed import FedConnector
from .fred import FredConnector
from .mt5_calendar import Mt5CalendarConnector
from .wgc import WgcConnector

# Ordre de collecte. Chaque connecteur est independant : la panne de l'un
# n'empeche pas les autres de tourner.
CONNECTORS = {
    # La Fed d'abord (heures de reference), puis le calendrier MT5 (heures reelles et
    # jours feries), dont FRED se sert ensuite pour dater son historique.
    "fed": FedConnector,
    "mt5_calendar": Mt5CalendarConnector,
    "fred": FredConnector,
    "bls": BlsConnector,
    "cftc": CftcConnector,
    "wgc": WgcConnector,
}
