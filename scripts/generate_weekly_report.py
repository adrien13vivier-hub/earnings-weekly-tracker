#!/usr/bin/env python3
"""
Earnings Weekly Tracker — Script de génération automatique
Récupère les données via Finnhub API et génère les fichiers .md et .csv
pour la semaine courante dans le dossier weeks/
"""

import os
import csv
import json
import datetime
import requests
from dateutil.relativedelta import relativedelta

# ─── Configuration ────────────────────────────────────────────────────────────
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
BASE_URL = "https://finnhub.io/api/v1"

# ─── Helpers ──────────────────────────────────────────────────────────────────
def get_week_range():
    """Retourne (date_debut, date_fin, annee, num_semaine) pour la semaine courante."""
    override = os.environ.get("WEEK_OVERRIDE", "").strip()
    if override:
        # Format attendu: YYYY-WXX (ex: 2026-W25)
        year, week = int(override[:4]), int(override[6:])
        monday = datetime.datetime.strptime(f"{year}-W{week:02d}-1", "%Y-W%W-%w").date()
    else:
        today = datetime.date.today()
        # Si on est lundi, utiliser la semaine courante, sinon la semaine prochaine
        days_until_monday = (7 - today.weekday()) % 7
        monday = today + datetime.timedelta(days=days_until_monday) if days_until_monday > 0 else today
    friday = monday + datetime.timedelta(days=4)
    iso = monday.isocalendar()
    return monday, friday, iso[0], iso[1]


def finnhub_get(endpoint, params):
    """Appel générique à l'API Finnhub."""
    if not FINNHUB_API_KEY:
        return None
    params["token"] = FINNHUB_API_KEY
    try:
        r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"⚠️  Finnhub error ({endpoint}): {e}")
        return None


def get_earnings_calendar(date_from, date_to):
    """Récupère le calendrier des earnings de la semaine."""
    data = finnhub_get("calendar/earnings", {
        "from": date_from.strftime("%Y-%m-%d"),
        "to":   date_to.strftime("%Y-%m-%d")
    })
    if data and "earningsCalendar" in data:
        return data["earningsCalendar"]
    return []


def get_recommendation(symbol):
    """Récupère la tendance de recommandation analystes."""
    data = finnhub_get("stock/recommendation", {"symbol": symbol})
    if data and len(data) > 0:
        latest = data[0]
        total = (latest.get("strongBuy", 0) + latest.get("buy", 0) +
                 latest.get("hold", 0) + latest.get("sell", 0) + latest.get("strongSell", 0))
        if total == 0:
            return "N/A", "N/A"
        buy_pct = (latest.get("strongBuy", 0) + latest.get("buy", 0)) / total * 100
        if buy_pct >= 60:
            sentiment = "Achat"
        elif buy_pct >= 40:
            sentiment = "Neutre"
        else:
            sentiment = "Vente"
        return sentiment, f"{buy_pct:.0f}% Buy ({total} analystes)"
    return "N/A", "N/A"


def get_quote(symbol):
    """Récupère le prix actuel d'une action."""
    data = finnhub_get("quote", {"symbol": symbol})
    if data and "c" in data and data["c"] > 0:
        return data["c"]
    return None


def score_company(entry, sentiment, buy_pct_str):
    """Score simple /100 basé sur les données disponibles."""
    score = 50  # base
    eps_est = entry.get("epsEstimate")
    eps_act = entry.get("epsActual")   # peut être None si pas encore publié
    rev_est = entry.get("revenueEstimate")

    # EPS estimate positif
    if eps_est and eps_est > 0:
        score += 10
    elif eps_est and eps_est < 0:
        score -= 10

    # Surprise EPS historique (si epsActual dispo du trimestre précédent)
    if eps_act and eps_est and eps_est != 0:
        surprise = (eps_act - eps_est) / abs(eps_est) * 100
        if surprise > 10:
            score += 15
        elif surprise > 0:
            score += 5
        elif surprise < -10:
            score -= 15
        else:
            score -= 5

    # Sentiment analystes
    if sentiment == "Achat":
        score += 15
    elif sentiment == "Neutre":
        score += 5
    elif sentiment == "Vente":
        score -= 10

    return max(0, min(100, score))


def format_mois_fr(date):
    mois = ["janvier", "février", "mars", "avril", "mai", "juin",
            "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    return mois[date.month - 1]


def generate_md(companies, monday, friday, year, week_num):
    """Génère le contenu Markdown du rapport."""
    month_fr = format_mois_fr(monday)
    lines = [
        f"# 📊 TOP 10 Earnings Plays — Semaine W{week_num:02d} ({monday.day}–{friday.day} {month_fr} {year})",
        "",
        f"> **Généré le :** {datetime.date.today().strftime('%d %B %Y')}  ",
        "> **Univers :** Actions US, Europe & Grandes Capitalisations Internationales  ",
        "> **Source données :** Finnhub API  ",
        "",
        "---",
        "",
        "## 🗓️ Calendrier de la semaine",
        "",
        "| Jour | Société | Ticker | Heure | EPS Estimé | CA Estimé |",
        "|------|---------|--------|-------|-----------|----------|",
    ]

    jours_fr = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
    for c in companies:
        d = c["date"]
        day_name = jours_fr[d.weekday()] if d.weekday() < 5 else d.strftime("%d/%m")
        day_str = f"{day_name} {d.day} {format_mois_fr(d)}"
        eps = f"${c['eps_est']:.2f}" if c["eps_est"] else "N/A"
        rev = f"${c['rev_est']/1e9:.2f} Mds" if c["rev_est"] else "N/A"
        lines.append(f"| {day_str} | {c['name']} | {c['ticker']} | {c['hour']} | {eps} | {rev} |")

    lines += [
        "",
        "*BMO = Before Market Open | AMC = After Market Close*",
        "",
        "---",
        "",
        "## 🏆 Classement TOP 10",
        "",
        "| Rang | Nom | Ticker | Score | Sentiment | EPS Est. |",
        "|------|-----|--------|-------|-----------|----------|",
    ]

    for i, c in enumerate(companies, 1):
        eps = f"${c['eps_est']:.2f}" if c["eps_est"] else "N/A"
        lines.append(f"| {i} | {c['name']} | {c['ticker']} | **{c['score']}/100** | {c['sentiment']} | {eps} |")

    lines += [
        "",
        "---",
        "",
        "## 📋 Fiches détaillées",
        "",
    ]

    for i, c in enumerate(companies, 1):
        eps = f"${c['eps_est']:.2f}" if c["eps_est"] else "N/A"
        rev = f"${c['rev_est']/1e9:.2f} Mds" if c["rev_est"] else "N/A"
        price = f"~${c['price']:.2f}" if c["price"] else "N/A"
        lines += [
            f"### {i}. {c['name']} ({c['ticker']}) — Score : {c['score']}/100",
            "| Champ | Détail |",
            "|---|---|",
            f"| **Date résultats** | {c['date'].strftime('%d/%m/%Y')} — {c['hour']} |",
            f"| **Prix actuel** | {price} |",
            f"| **EPS consensus** | {eps} |",
            f"| **CA consensus** | {rev} |",
            f"| **Sentiment analystes** | {c['sentiment_detail']} |",
            "",
            "---",
            "",
        ]

    lines += [
        "> ⚠️ *Analyse générée automatiquement via Finnhub API. Fournie à titre informatif uniquement.*",
        "> *Ne constitue pas un conseil en investissement.*",
        "",
    ]
    return "\n".join(lines)


def generate_csv(companies, monday, friday, week_num):
    """Génère le contenu CSV du rapport."""
    rows = []
    headers = ["Rang", "Nom", "Ticker", "Date Résultats", "Heure",
               "Prix Actuel", "Score /100", "Sentiment", "EPS Consensus",
               "CA Consensus (Mds$)"]
    for i, c in enumerate(companies, 1):
        rows.append([
            i,
            c["name"],
            c["ticker"],
            c["date"].strftime("%d/%m/%Y"),
            c["hour"],
            f"{c['price']:.2f}" if c["price"] else "",
            c["score"],
            c["sentiment"],
            f"{c['eps_est']:.2f}" if c["eps_est"] else "",
            f"{c['rev_est']/1e9:.2f}" if c["rev_est"] else "",
        ])
    output = []
    output.append(",".join(headers))
    for row in rows:
        output.append(",".join(str(x) for x in row))
    return "\n".join(output) + "\n"


def update_readme(year, week_num, top_pick, top_score, monday, friday):
    """Met à jour la ligne historique dans le README."""
    readme_path = "README.md"
    try:
        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()
        month_fr = format_mois_fr(monday)
        new_row = f"| W{week_num:02d} | {monday.day}–{friday.day} {month_fr} {year} | {top_pick} | {top_score}/100 |"
        # Insérer avant la ligne de fermeture du tableau historique
        if "| W" in content:
            # Ajouter la nouvelle semaine après la dernière entrée
            lines = content.split("\n")
            insert_idx = None
            for idx, line in enumerate(lines):
                if line.startswith("| W") or (line.startswith("| W") and "mai" in line):
                    insert_idx = idx
            if insert_idx:
                lines.insert(insert_idx + 1, new_row)
                content = "\n".join(lines)
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✅ README mis à jour avec W{week_num:02d}")
    except Exception as e:
        print(f"⚠️  Erreur mise à jour README: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    if not FINNHUB_API_KEY:
        print("❌ ERREUR : Variable FINNHUB_API_KEY manquante !")
        print("   → Ajoutez-la dans : Settings > Secrets and variables > Actions > New repository secret")
        raise SystemExit(1)

    monday, friday, year, week_num = get_week_range()
    print(f"📅 Génération du rapport W{week_num:02d} — {monday} au {friday}")

    # 1. Récupérer le calendrier earnings de la semaine
    calendar = get_earnings_calendar(monday, friday)
    print(f"📋 {len(calendar)} earnings trouvés via Finnhub")

    if not calendar:
        print("⚠️  Aucun earning trouvé pour cette semaine. Vérifiez la clé API ou les dates.")
        raise SystemExit(0)

    # 2. Enrichir chaque entrée avec données analystes et prix
    enriched = []
    for entry in calendar[:30]:  # limiter pour éviter le rate-limit
        symbol = entry.get("symbol", "")
        if not symbol:
            continue
        name = entry.get("company", symbol)
        date_str = entry.get("date", "")
        try:
            date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            continue

        hour_raw = entry.get("hour", "")
        if hour_raw == "amc":
            hour = "AMC"
        elif hour_raw == "bmo":
            hour = "BMO"
        else:
            hour = hour_raw or "N/A"

        eps_est = entry.get("epsEstimate")
        rev_est = entry.get("revenueEstimate")
        sentiment, sentiment_detail = get_recommendation(symbol)
        price = get_quote(symbol)
        score = score_company(entry, sentiment, sentiment_detail)

        enriched.append({
            "ticker":           symbol,
            "name":             name,
            "date":             date,
            "hour":             hour,
            "eps_est":          eps_est,
            "rev_est":          rev_est,
            "price":            price,
            "sentiment":        sentiment,
            "sentiment_detail": sentiment_detail,
            "score":            score,
        })

    if not enriched:
        print("⚠️  Aucune donnée enrichie disponible.")
        raise SystemExit(0)

    # 3. Trier par score et prendre le TOP 10
    enriched.sort(key=lambda x: x["score"], reverse=True)
    top10 = enriched[:10]

    # 4. Générer les fichiers
    month_fr = format_mois_fr(monday)
    month_short = month_fr[:3]
    file_base = f"weeks/{year}-W{week_num:02d}_{monday.day:02d}-{friday.day:02d}-{month_short}"

    os.makedirs("weeks", exist_ok=True)

    md_content  = generate_md(top10, monday, friday, year, week_num)
    csv_content = generate_csv(top10, monday, friday, week_num)

    with open(f"{file_base}.md",  "w", encoding="utf-8") as f:
        f.write(md_content)
    with open(f"{file_base}.csv", "w", encoding="utf-8") as f:
        f.write(csv_content)

    print(f"✅ Fichiers générés : {file_base}.md et {file_base}.csv")

    # 5. Mettre à jour le README
    top_pick  = top10[0]["ticker"] + " (" + top10[0]["name"] + ")"
    top_score = top10[0]["score"]
    update_readme(year, week_num, top_pick, top_score, monday, friday)


if __name__ == "__main__":
    main()
