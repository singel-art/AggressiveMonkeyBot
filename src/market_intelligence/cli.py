"""Ligne de commande du module Market Intelligence.

    python -m market_intelligence audit                 verifie l'acces a chaque source
    python -m market_intelligence collect --since 2015-01-01
    python -m market_intelligence context               ecrit le contexte actuel pour l'EA
    python -m market_intelligence context --asof 2025-12-17T15:00:00Z
    python -m market_intelligence asof UNRATE 2025-12-20
    python -m market_intelligence status
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .config import load_settings
from .connectors import CONNECTORS
from .connectors.base import CheckResult, CollectReport
from .context import build_context, write_context
from .errors import SourceUnavailable
from .http import HttpClient
from .store import Store
from .timeutil import iso, now_utc, parse_iso

# Sources etudiees mais sans connecteur : l'audit en garde la preuve.
EXTRA_CHECKS = {
    "tradingeconomics": ("https://api.tradingeconomics.com/calendar", {"c": "guest:guest", "f": "json"},
                         "API payante : l'acces invite a ete supprime"),
    "cme": ("https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html", None,
            "collecte automatique interdite par les conditions d'utilisation du site"),
}


def _open(args):
    settings = load_settings(args.data_dir)
    store = Store(settings.db_path)
    http = HttpClient(store, settings.raw_dir)
    return settings, store, http


def cmd_audit(args) -> int:
    settings, store, http = _open(args)
    results: list[CheckResult] = []
    for name, cls in CONNECTORS.items():
        results.append(cls(http, store, settings).check())
    for name, (url, params, verdict) in EXTRA_CHECKS.items():
        try:
            http.get(name, url, params=params, ext="txt")
            results.append(CheckResult(name, True, "a repondu, a reexaminer : " + verdict))
        except SourceUnavailable as exc:
            results.append(CheckResult(name, False, f"{exc.reason} ; {verdict}", needs_key=True))
    for r in results:
        print(f"{'OK ' if r.ok else 'KO '} {r.source:<17} {r.detail}")
    report = {"audited_at": iso(now_utc()), "results": [r.__dict__ for r in results]}
    path = settings.data_dir / f"audit_{date.today().isoformat()}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRapport ecrit : {path}")
    return 0


def cmd_collect(args) -> int:
    settings, store, http = _open(args)
    since = date.fromisoformat(args.since)
    names = args.only.split(",") if args.only else list(CONNECTORS)
    failed = False
    for name in names:
        try:
            report = CONNECTORS[name](http, store, settings).collect(since)
        except Exception as exc:  # un connecteur defaillant ne doit pas arreter les autres
            report = CollectReport(name, errors=[f"erreur inattendue {type(exc).__name__}: {exc}"])
        print(f"{'OK ' if report.ok else 'KO '} {name:<13} +{report.rows_added} observations, "
              f"{report.events_upserted} evenements")
        for line in report.errors:
            failed = True
            print(f"     erreur : {line}")
        for line in report.notes:
            print(f"     note   : {line}")
    return 1 if failed else 0


def cmd_context(args) -> int:
    settings, store, _ = _open(args)
    asof = parse_iso(args.asof) if args.asof else None
    ctx = build_context(store, asof, historical=bool(args.asof))
    if args.print_only:
        print(json.dumps(ctx, ensure_ascii=False, indent=2))
        return 0
    paths = write_context(ctx, args.out or settings.context_dir)
    bias = ctx["gold_macro_bias"]
    print(f"Contexte {ctx['mode']} au {ctx['asof']} : statut {ctx['status']}, "
          f"risque d'annonce {ctx['events']['announcement_risk']['level']}, "
          f"biais {bias['label']} (non valide)")
    for kind, path in paths.items():
        print(f"  {kind:<6} {path}")
    return 0


def cmd_asof(args) -> int:
    _, store, _ = _open(args)
    rows = store.asof(args.series, iso(parse_iso(args.when)), limit=args.last)
    if not rows:
        print("Aucune donnee disponible a cette date.")
        return 1
    print("obs_date    valeur        version     disponible_le         base      source")
    for r in rows:
        print(f"{r['obs_date']}  {r['value']:<12g}  {r['vintage_date']}  {r['available_at']}  "
              f"{r['available_basis']:<8}  {r['source']}")
    return 0


def cmd_status(args) -> int:
    _, store, _ = _open(args)
    print("Series :")
    for r in store.series_summary():
        print(f"  {r['series_id']:<24} {r['source']:<8} {r['n_obs']:>5} obs, {r['n_rows']:>6} versions, "
              f"{r['first_obs']} -> {r['last_obs']}")
    print("Evenements :")
    for r in store.event_counts():
        print(f"  {r['source']:<13} {r['kind']:<17} {r['n']:>5}  {r['first_at'][:10]} -> {r['last_at'][:10]}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="market_intelligence", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", help="dossier des donnees (defaut : ./data ou AMB_DATA_DIR)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit").set_defaults(func=cmd_audit)
    p = sub.add_parser("collect")
    p.add_argument("--since", default="2015-01-01")
    p.add_argument("--only", help="liste de connecteurs separes par des virgules")
    p.set_defaults(func=cmd_collect)
    p = sub.add_parser("context")
    p.add_argument("--asof", help="instant UTC ISO ; absent = maintenant")
    p.add_argument("--out", help="dossier de sortie (ex. dossier Common\\Files de MT5)")
    p.add_argument("--print-only", action="store_true")
    p.set_defaults(func=cmd_context)
    p = sub.add_parser("asof")
    p.add_argument("series")
    p.add_argument("when")
    p.add_argument("--last", type=int, default=6)
    p.set_defaults(func=cmd_asof)
    sub.add_parser("status").set_defaults(func=cmd_status)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
