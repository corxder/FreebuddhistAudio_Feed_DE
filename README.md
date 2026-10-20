# FBA – Deutsche Vorträge als Podcast-Feed

Erzeugt aus freebuddhistaudio.com einen RSS-Feed nur mit deutschsprachigen
Vorträgen (ohne geführte Meditationen, ohne andere Sprachen) für AntennaPod
oder jeden anderen Podcast-Player. Die MP3s werden direkt von FBA gestreamt,
es wird nichts neu gehostet.

## Einrichtung (einmalig, ca. 5 Minuten)

1. Auf GitHub ein neues **öffentliches** Repository anlegen, z.B. `fba-de-feed`.
2. Die Dateien dieses Ordners hochladen – inklusive `.github/workflows/feed.yml`
   (der Ordner muss genau so heißen).
3. Im Repo unter **Actions** → „Feed aktualisieren" → **Run workflow** starten.
   Der erste Lauf dauert einige Minuten, weil alle Kandidaten einmal geprüft werden.
   Danach läuft er täglich automatisch und prüft nur noch neue Uploads.
4. In AntennaPod: **Podcast hinzufügen → RSS-Adresse**:

   `https://raw.githubusercontent.com/<DEIN-GITHUB-NAME>/fba-de-feed/main/feed.xml`

## Wie wird gefiltert?

Kandidaten kommen aus den deutschsprachigen Zentren (Essen, Berlin, Fulda,
Minden, Das Stadtretreat), allen FBA-Themen mit „(deutsch)" und den neuesten
Uploads. Für jeden Kandidaten wird die Detailseite gelesen:

- aufgenommen, wenn FBA `language = de` meldet, das Genre „(deutsch)" trägt,
  eine deutsche Übersetzung vorhanden ist, oder der Vortrag aus einem deutschen
  Zentrum stammt und die Sprache dort nur per Formular-Default auf „en" steht;
- ausgeschlossen bei Titeln wie „Geführte Meditation", „Meditationsanleitung",
  „Metta Bhavana", „Meditationsglocken", den Minden-Retreat-Anleitungen
  („Meditation Metta", „Meditation Karuna" …) sowie dem Genre
  „Chanting und Rituale (deutsch)". Vorträge *über* Meditation
  („Meditation – der Weg der Transformation") bleiben drin.

Stand des ersten Laufs (20.10.2026): 448 Kandidaten geprüft, 324 Vorträge im
Feed, davon 152 einteilig und 172 in Kapitel zerlegt (Median 10 Kapitel).

`excluded.txt` listet alles Aussortierte mit Grund. Falsch einsortierte
Vorträge per Katalognummer in `ALWAYS_INCLUDE` / `ALWAYS_EXCLUDE` oben im
Skript eintragen. Nach Regeländerungen einmal mit `--recheck` laufen lassen
(in `feed.yml` an den Aufruf anhängen, danach wieder entfernen).

Vorträge in mehreren Teilen erscheinen als einzelne Episoden „(1/3)", „(2/3)" …;
mit Sortierung „älteste zuerst" laufen sie in der richtigen Reihenfolge.

## Lokal testen

    python3 fba_de_feed.py --out feed.xml

## Hinweis

FBA arbeitet an einer neuen API (`/api/v2`, geplant Dezember 2026). Wenn die
alte abgeschaltet wird, bricht der Lauf mit „keine Kandidaten" ab und der
bestehende Feed bleibt erhalten – dann muss das Skript angepasst werden.
