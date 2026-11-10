#!/usr/bin/env python3
# ROOM watcher: kijkt op roommatch.nl naar nieuwe kamers en stuurt via Gmail
# een mail als er een nieuwe kamer is die past.
#
# Pas hieronder de stad en de maximale huur per persoon aan. Het Gmail-adres en
# app-wachtwoord staan als secrets in GitHub (GMAIL_ADDRESS, GMAIL_APP_PASSWORD).

import json
import os
import re
import smtplib
import time
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path

CITY = "Delft"
MAX_PER_PERSON = 650

API_URL = "https://www.roommatch.nl/portal/object/frontend/getallobjects/format/json"
DETAIL_URL = "https://www.roommatch.nl/aanbod/studentenwoningen/details/{}"
SEEN_FILE = Path(__file__).with_name("seen.json")


def pick(obj, *paths):
    # Geef de eerste niet-lege waarde terug voor een lijst mogelijke veldnamen ("a.b" = genest).
    for path in paths:
        value = obj
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        if value not in (None, "", []):
            return value
    return None


def to_float(value):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def fetch_listings():
    req = urllib.request.Request(
        API_URL,
        headers={"User-Agent": "Mozilla/5.0 (room-watcher)", "Accept": "application/json"},
    )
    # ROOMmatch is soms even traag of onbereikbaar: tot 3 keer proberen
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
            break
        except (OSError, TimeoutError) as e:
            if attempt == 2:
                raise
            print(f"Poging {attempt + 1} mislukt ({e}), opnieuw proberen...")
            time.sleep(10)
    return data.get("result", data) if isinstance(data, dict) else data


def city_of(obj):
    return str(pick(obj, "city.name", "gemeenteGeoLocatieNaam", "city", "plaats") or "")


def rent_of(obj):
    return to_float(pick(obj, "totalRent", "totaleHuur", "grossRent", "netRent", "nettohuur"))


def persons_of(obj):
    # Aantal personen voor deze woning. Valt terug op 1 als het niet te bepalen is.
    n = to_float(pick(obj, "maxPersons", "maximumAantalPersonen", "aantalPersonen",
                      "maxHouseholdSize", "huishoudgrootteMax", "maximumHouseholdSize"))
    if n and n >= 1:
        return int(n)
    # Anders zoeken in type/omschrijving, bv. "2-persoons" of "voor 3 personen"
    text = " ".join(str(pick(obj, p) or "") for p in
                    ("dwellingType.localizedName", "dwellingType.name", "objectType",
                     "description", "descriptionMarkdown", "omschrijving"))
    m = re.search(r"(\d+)\s*-?\s*(?:personen|persoons|persons|people)", text, re.I)
    return int(m.group(1)) if m else 1


def listing_id(obj):
    return str(pick(obj, "id", "urlKey", "objectId"))


def in_city(obj):
    return city_of(obj).strip().lower() == CITY.lower()


def matches(obj):
    rent = rent_of(obj)
    return in_city(obj) and rent is not None and rent / persons_of(obj) <= MAX_PER_PERSON


def describe(obj):
    rent = rent_of(obj)
    persons = persons_of(obj)
    address = " ".join(str(pick(obj, p) or "") for p in ("street", "houseNumber", "houseNumberAddition")).strip()
    kind = pick(obj, "dwellingType.localizedName", "dwellingType.name", "objectType") or "Woning"
    area = pick(obj, "areaDwelling", "oppervlakte")
    closing = pick(obj, "closingDate", "sluitingsdatum")
    url_key = pick(obj, "urlKey")
    url = DETAIL_URL.format(url_key) if url_key else "https://www.roommatch.nl/aanbod/studentenwoningen"
    lines = [
        f"{kind} - {address}, {city_of(obj)}",
        f"Huur totaal: €{rent:.2f}  |  Personen: {persons}  |  Per persoon: €{rent / persons:.2f}",
    ]
    if area:
        lines.append(f"Oppervlakte: {area} m²")
    if closing:
        lines.append(f"Reageren kan tot: {closing}")
    lines.append(url)
    return "\n".join(lines)


def send_mail(subject, body):
    user = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    recipients = [a.strip() for a in (os.environ.get("MAIL_TO") or user).split(",") if a.strip()]
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.sendmail(user, recipients, msg.as_string())


def load_seen():
    try:
        return set(json.loads(SEEN_FILE.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def main():
    listings = fetch_listings()
    seen = load_seen()
    new = [o for o in listings if matches(o) and listing_id(o) not in seen]

    # Overzicht in de log, zodat je kunt controleren of huur en personen goed gelezen worden
    for o in listings:
        if in_city(o):
            print(f"  {pick(o, 'street')} {pick(o, 'houseNumber')}: huur {rent_of(o)}, "
                  f"personen {persons_of(o)}, past {'JA' if matches(o) else 'nee'}")

    if new:
        body = f"{len(new)} nieuwe woning(en) op ROOM in {CITY} (max €{MAX_PER_PERSON} p.p.):\n\n"
        body += "\n\n".join(describe(o) for o in new)
        send_mail(f"ROOM: {len(new)} nieuwe woning(en) in {CITY}", body)
        print(body)
    else:
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {len(listings)} advertenties, niks nieuws.")

    seen.update(listing_id(o) for o in new)
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=0))


if __name__ == "__main__":
    main()
