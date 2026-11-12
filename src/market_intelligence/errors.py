class SourceUnavailable(Exception):
    """La source n'a pas repondu correctement. Aucune donnee ne doit etre inventee."""

    def __init__(self, source: str, url: str, reason: str):
        super().__init__(f"[{source}] indisponible : {reason} ({url})")
        self.source = source
        self.url = url
        self.reason = reason


class ParseError(Exception):
    """La source a repondu mais son format n'est pas celui attendu."""
