class SourceUnavailable(Exception):
    """La source n'a pas repondu correctement. Aucune donnee ne doit etre inventee."""

    def __init__(self, source: str, url: str, reason: str):
        super().__init__(f"[{source}] indisponible : {reason} ({url})")
        self.source = source
        self.url = url
        self.reason = reason


class ParseError(Exception):
    """La source a repondu mais son format n'est pas celui attendu."""


class SourceRefused(Exception):
    """La source a repondu, mais a refuse la requete (quota, cle invalide...).
    `code` vaut 'quota', 'invalid_key' ou 'refused'. Le message ne contient jamais la cle."""

    def __init__(self, source: str, code: str, message: str):
        super().__init__(f"[{source}] requete refusee ({code}) : {message}")
        self.source = source
        self.code = code
        self.message = message
