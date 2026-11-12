"""World Gold Council (Goldhub) : lecture des classeurs deposes a la main.

Regles du projet (decisions du 12 novembre 2026) :
- aucune requete automatique vers gold.org : ce connecteur ne fait AUCUN appel
  reseau. Les fichiers sont telecharges par l'utilisateur avec son compte, puis
  deposes dans data/inbox/wgc/ ;
- aucune redistribution : les fichiers restent dans data/ (hors depot) et les
  tests n'embarquent aucun extrait reel ;
- une version brute n'est jamais ecrasee : chaque fichier depose est archive
  sous un nom qui contient son empreinte SHA-256.

Classeurs lus (reconnus par leurs feuilles, pas par leur nom de fichier) :

| Jeu           | Feuilles requises                          | Series produites |
|---------------|--------------------------------------------|------------------|
| etf_flows     | 'Holdings by month', 'Demand by month',    | WGC_ETF_HOLDINGS_TONNES, WGC_ETF_HOLDINGS_USD, |
|               | 'Fund flows by month'                      | WGC_ETF_DEMAND_TONNES_SUM, WGC_ETF_FLOWS_USD_MN_SUM |
| cb_changes    | 'Monthly' (avec 'Country', 'Comments')     | WGC_CB_NET_TONNES_SUM, WGC_CB_REPORTING_COUNT |
| gold_premiums | 'Chinese premiums-discounts',              | WGC_PREMIUM_CHINA_USD_OZ, WGC_PREMIUM_INDIA_USD_OZ |
|               | 'Indian premiums-discounts'                | |

Les series suffixees _SUM sont DERIVEES : somme, par nous, des colonnes par fonds
ou des lignes par pays. Elles ne figurent pas telles quelles dans le classeur.

Tout autre fichier (PDF, autre classeur) est archive sans etre lu.

Disponibilite temporelle : ces classeurs sont des instantanes. Ils ne disent pas
quand chaque chiffre a ete publie. La date de publication est donc INCONNUE et
n'est pas inventee : chaque valeur devient visible a l'instant ou nous avons
recu le fichier, et pas avant. Tout l'historique d'un premier fichier est marque
'backfill'. Un historique de versions ne se construit qu'en deposant les
editions successives.
"""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..errors import ParseError
from ..store import Observation, release_kind
from ..timeutil import iso, now_utc
from .base import CheckResult, CollectReport, Connector

ETF_SHEETS = ("Holdings by month", "Demand by month", "Fund flows by month")
ETF_HEADER = ("Date", "Gold, US$/oz", "Ounces", "Tonnes", "Value (USD)")
PREMIUM_SHEETS = {"Chinese premiums-discounts": ("WGC_PREMIUM_CHINA_USD_OZ", "Chinese Premium/Discount (US$/oz)"),
                  "Indian premiums-discounts": ("WGC_PREMIUM_INDIA_USD_OZ", "Indian Premium/Discount (US$/oz)")}

SERIES_INFO = {
    "WGC_ETF_HOLDINGS_TONNES": ("Avoirs des ETF or, total mondial", "tonnes", 75),
    "WGC_ETF_HOLDINGS_USD": ("Valeur des avoirs des ETF or", "US$", 75),
    "WGC_ETF_DEMAND_TONNES_SUM": ("Variation mensuelle des avoirs des ETF or (somme des fonds, derivee)", "tonnes", 75),
    "WGC_ETF_FLOWS_USD_MN_SUM": ("Flux mensuels des ETF or (somme des fonds, derivee)", "millions US$", 75),
    "WGC_CB_NET_TONNES_SUM": ("Achats nets des banques centrales (somme des pays, derivee)", "tonnes", 120),
    "WGC_CB_REPORTING_COUNT": ("Nombre de pays avec une valeur ce mois", "pays", 120),
    "WGC_PREMIUM_CHINA_USD_OZ": ("Prime locale Chine, moyenne mobile 5 jours", "US$/oz", 12),
    "WGC_PREMIUM_INDIA_USD_OZ": ("Prime locale Inde, moyenne mobile 5 jours", "US$/oz", 12),
}


@dataclass
class WgcDataset:
    kind: str
    edition_date: str | None  # date de derniere modification inscrite dans le classeur
    series: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _as_date(value, where: str) -> str:
    """Une date de cellule, ou un texte 'AAAA-MM-JJ' (les deux existent dans les fichiers)."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10]).isoformat()
        except ValueError:
            pass
    raise ParseError(f"{where} : date attendue, trouve {value!r}")


def _number(value) -> float | None:
    """Nombre, ou None si la cellule est vide. Un texte n'est jamais converti en zero."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _rows(ws, **kwargs):
    return ws.iter_rows(values_only=True, **kwargs)


def _parse_etf(wb) -> dict[str, list[tuple[str, float]]]:
    tables = {}
    for name in ETF_SHEETS:
        rows = list(_rows(wb[name]))
        if len(rows) < 7 or tuple(rows[5][:5]) != ETF_HEADER:
            raise ParseError(f"feuille {name!r} : ligne d'en-tete 6 inattendue {rows[5][:5] if len(rows) > 5 else None}")
        if rows[0][0] != "ticker":
            raise ParseError(f"feuille {name!r} : la ligne 1 devrait commencer par 'ticker'")
        fund_cols = [i for i, cell in enumerate(rows[0]) if i >= 5 and cell]
        if not fund_cols:
            raise ParseError(f"feuille {name!r} : aucune colonne de fonds")
        data = [r for r in rows[6:] if isinstance(r[0], (datetime, date))]
        if not data:
            raise ParseError(f"feuille {name!r} : aucune ligne datee")
        tables[name] = (data, fund_cols)
    holdings, _ = tables["Holdings by month"]
    out: dict[str, list[tuple[str, float]]] = {k: [] for k in (
        "WGC_ETF_HOLDINGS_TONNES", "WGC_ETF_HOLDINGS_USD", "WGC_ETF_DEMAND_TONNES_SUM", "WGC_ETF_FLOWS_USD_MN_SUM")}
    for r in holdings:
        day = _as_date(r[0], "Holdings by month")
        for series_id, col in (("WGC_ETF_HOLDINGS_TONNES", 3), ("WGC_ETF_HOLDINGS_USD", 4)):
            value = _number(r[col])
            if value is not None:
                out[series_id].append((day, value))
    for sheet, series_id in (("Demand by month", "WGC_ETF_DEMAND_TONNES_SUM"),
                             ("Fund flows by month", "WGC_ETF_FLOWS_USD_MN_SUM")):
        data, fund_cols = tables[sheet]
        for r in data:
            values = [v for v in (_number(r[i]) for i in fund_cols if i < len(r)) if v is not None]
            if values:  # un mois sans aucun fonds renseigne ne produit pas de zero
                out[series_id].append((_as_date(r[0], sheet), round(sum(values), 6)))
    return out


def _parse_cb_changes(wb) -> dict[str, list[tuple[str, float]]]:
    rows = list(_rows(wb["Monthly"], max_col=2000))
    header = rows[0]
    if header[0] != "Country" or header[1] != "Comments":
        raise ParseError(f"feuille 'Monthly' : en-tete inattendu {header[:2]}")
    months = [(i, _as_date(c, "Monthly, ligne 1")) for i, c in enumerate(header) if i >= 2 and c is not None]
    if not months:
        raise ParseError("feuille 'Monthly' : aucune colonne de mois")
    countries = [r for r in rows[1:] if isinstance(r[0], str) and r[0].strip()]
    if not countries:
        raise ParseError("feuille 'Monthly' : aucun pays")
    total, count = [], []
    for i, month in months:
        values = [v for v in (_number(r[i]) if i < len(r) else None for r in countries) if v is not None]
        if values:
            total.append((month, round(sum(values), 6)))
            count.append((month, float(len(values))))
    return {"WGC_CB_NET_TONNES_SUM": total, "WGC_CB_REPORTING_COUNT": count}


def _parse_premiums(wb) -> dict[str, list[tuple[str, float]]]:
    out = {}
    for sheet, (series_id, title) in PREMIUM_SHEETS.items():
        rows = list(_rows(wb[sheet], max_col=2))
        if len(rows) < 6 or not (isinstance(rows[3][0], str) and rows[3][0].startswith(title)):
            raise ParseError(f"feuille {sheet!r} : titre de serie inattendu en ligne 4")
        points = []
        for n, r in enumerate(rows[5:], start=6):
            if r[0] is None and r[1] is None:
                continue
            value = _number(r[1])
            if value is None:
                continue
            points.append((_as_date(r[0], f"{sheet}, ligne {n}"), value))
        if not points:
            raise ParseError(f"feuille {sheet!r} : aucune valeur")
        out[series_id] = points
    return out


def read_workbook(path: Path) -> WgcDataset | None:
    """Lit un classeur WGC reconnu. Renvoie None si ce n'est pas un classeur pris
    en charge ; leve ParseError si le classeur est reconnu mais que sa mise en page
    n'est pas celle attendue (rien n'est alors importe)."""
    if path.suffix.lower() != ".xlsx":
        return None
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - depend de l'installation
        raise ParseError("lecture des classeurs WGC : installer openpyxl (pip install -r requirements.txt)") from exc
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise ParseError(f"classeur illisible : {type(exc).__name__}") from exc
    try:
        sheets = set(wb.sheetnames)
        modified = wb.properties.modified
        edition = modified.date().isoformat() if isinstance(modified, datetime) else None
        if set(ETF_SHEETS) <= sheets:
            kind, series = "etf_flows", _parse_etf(wb)
        elif set(PREMIUM_SHEETS) <= sheets:
            kind, series = "gold_premiums", _parse_premiums(wb)
        elif "Monthly" in sheets and "Annual" in sheets:
            kind, series = "cb_changes", _parse_cb_changes(wb)
        else:
            return None
    finally:
        wb.close()
    dataset = WgcDataset(kind, edition, series)
    for series_id, points in series.items():
        dates = [d for d, _ in points]
        if len(set(dates)) != len(dates):
            raise ParseError(f"{series_id} : dates en double dans le classeur")
        dataset.notes.append(f"{series_id} : {len(points)} valeurs, {min(dates)} -> {max(dates)}")
    return dataset


class WgcConnector(Connector):
    name = "wgc"

    def _inbox(self) -> Path:
        return self.settings.inbox_dir / "wgc"

    def check(self) -> CheckResult:
        """Controle local uniquement : aucune requete vers gold.org."""
        files = [p for p in self._inbox().iterdir() if p.is_file()] if self._inbox().exists() else []
        stored = self.store.db.execute(
            "SELECT COUNT(DISTINCT series_id) FROM observations WHERE source='wgc'").fetchone()[0]
        return CheckResult("wgc", True, f"depot manuel, sans acces reseau : {len(files)} fichier(s) en attente, "
                           f"{stored} serie(s) deja importee(s)")

    def collect(self, since: date) -> CollectReport:
        """Archive chaque fichier depose dans data/inbox/wgc/, puis lit ceux qui sont reconnus."""
        report = CollectReport("wgc")
        inbox = self._inbox()
        inbox.mkdir(parents=True, exist_ok=True)
        files = sorted(p for p in inbox.iterdir() if p.is_file())
        if not files:
            report.notes.append(f"aucun fichier dans {inbox} : rien a archiver")
            return report
        for path in files:
            try:
                dataset = read_workbook(path)
            except ParseError as exc:
                report.errors.append(f"{path.name}: {exc} ; fichier laisse en place, rien n'est importe")
                continue
            now = now_utc()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            target = self.settings.raw_dir / "wgc" / now.strftime("%Y/%m/%d") / f"{digest[:12]}_{path.name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), target)
            fetch_id = self.store.log_fetch(source="wgc", url=f"depot manuel : {path.name}", fetched_at=iso(now),
                                            status=200, sha256=digest, raw_path=str(target), error=None)
            if dataset is None:
                report.notes.append(f"archive sans lecture : {path.name}")
                continue
            seen = iso(now)
            # La date d'edition inscrite dans le classeur date la version. La disponibilite,
            # elle, est l'instant de reception : la date de publication reelle est inconnue.
            vintage = dataset.edition_date or seen[:10]
            rows = []
            for series_id, points in dataset.series.items():
                known = self.store.latest_values(series_id)
                first_load = not known
                for obs_date, value in points:
                    if obs_date < since.isoformat() or known.get(obs_date) == value:
                        continue
                    rows.append(Observation(series_id, obs_date, vintage, value, seen, "observed", "wgc",
                                            fetch_id, release_kind=release_kind(known, obs_date, first_load),
                                            time_quality="observed"))
            added = self.store.add_observations(rows)
            report.rows_added += added
            report.notes.append(f"lu : {path.name} ({dataset.kind}, edition du {dataset.edition_date or 'inconnue'}, "
                                f"+{added} lignes)")
            report.notes.extend(f"  {n}" for n in dataset.notes)
        return report
