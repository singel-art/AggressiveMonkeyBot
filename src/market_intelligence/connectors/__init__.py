from .bls import BlsConnector
from .cftc import CftcConnector
from .fed import FedConnector
from .fred import FredConnector
from .mt5_calendar import Mt5CalendarConnector
from .wgc import WgcConnector

# Ordre de collecte. Chaque connecteur est independant : la panne de l'un
# n'empeche pas les autres de tourner.
CONNECTORS = {
    "fred": FredConnector,
    "fed": FedConnector,
    "bls": BlsConnector,
    "cftc": CftcConnector,
    "wgc": WgcConnector,
    "mt5_calendar": Mt5CalendarConnector,
}
