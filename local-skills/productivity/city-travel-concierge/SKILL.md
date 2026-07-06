---
name: city-travel-concierge
description: "City travel concierge: addresses, routes, weather, events, nearby places, parking candidates, and map links."
version: 0.5.0
author: Hermes local
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [travel, city, maps, routes, weather, events, parking, cafes, dadata, geoapify, kudago, overpass]
    category: productivity
    requires_toolsets: [terminal]
    related_skills: [maps, travel-search-ru]
---

# City Travel Concierge

Use this skill for ordinary natural-language city travel requests: how long to
drive, where to stop, nearby places, parking, weather, events, and cafes.

## Mandatory execution rules

- Use the helper scripts before answering. Never estimate routes, coordinates,
  parking, ratings, or review counts from memory.
- Never read, print, inspect, or verify the credential store. Helper scripts
  load provider credentials internally and return safe provider status only.
- To check configuration, run the relevant helper and inspect its exit status,
  `provider_status`, result counts, and non-secret error codes. If credential
  access is denied, do not retry direct reads; use the helper or report the
  exact blocker.
- Geoapify route time does not include live traffic. State this and include a
  Yandex Maps link for final checking before departure.
- OpenStreetMap identifies parking candidates only. Never call a place legally
  confirmed or definitely free from OSM data alone.
- For current ratings, reviews, opening hours, tariffs, traffic, and road rules,
  use `web` or `browser` and preserve direct source links.
- Russian street addresses without a house number are approximate starts; say so
  explicitly and ask a clarifying question only when the alternatives materially
  change the route.
- Failure of one provider must not erase successful sections.

## Helpers

```bash
BASE="${HERMES_HOME:-$HOME/.hermes}/skills/productivity/city-travel-concierge/scripts"
CTC="python3 $BASE/city_travel_concierge.py"
CTC2="python3 $BASE/city_travel_itinerary.py"
CTC3="python3 $BASE/city_travel_discovery.py"
CTCP="python3 $BASE/city_travel_parking.py"
CTCT="python3 $BASE/city_travel_trip.py"
CTCS="python3 $BASE/travel_place_save.py"
CTCSF="python3 $BASE/travel_place_save_from_file.py"
```

## One-call route and parking

```bash
$CTCT --start "Королёв, ул. Лесная" --destination "парк Останкино" --parking-radius 1200 --parking-limit 5
```

For route, ETA, map-link, and parking requests, call `city_travel_trip.py`
first. If it returns `answer_ready=true`, a Yandex route link, and parking
candidates, answer immediately from that JSON. Do not run additional web
searches for confidence.


## Save a place from a link, reel, post, or forwarded description

When the user explicitly asks to save a place for a future trip:

1. Inspect the supplied source with `web` or `browser` before asking the user
   to provide facts already present in it.
2. Extract the place name, location, and useful description.
3. Verify changing facts such as prices, schedule, and opening hours from a
   current official or otherwise clearly attributed source. Keep source claims
   separate from verified facts and never invent missing values.
4. Use the `file` tool to write the structured result as UTF-8 JSON to
   `/home/hermes/.hermes/tmp/travel-place-save.json`. Allowed keys are:
   `name`, `location`, `description`, `category`, `map_url`, `entity_key`,
   `price`, `schedule`, `hours`, `source_url`, `verified_source_url`,
   `verified_fields`, `checked_at`, `infrastructure`, `parking`,
   `nearby_food`, `important`, and `relations`.
5. Include `map_url` whenever a stable map object is found. The helper derives
   `entity_key` from a Yandex organisation ID, Google Place ID, coordinates, or
   a normalized name/location fallback. Different publications about the same
   place must update one card and merge sources instead of creating duplicates.
6. Run only this fixed ASCII terminal command. Do not place user text or
   Cyrillic values directly in a shell command:

```bash
/usr/bin/python3 /home/hermes/.hermes/skills/productivity/city-travel-concierge/scripts/travel_place_save_from_file.py
```

Report success only when the helper returns `saved=true` or
`already_exists=true` and `readback_count>0`. If it returns `updated=true`, say
that the existing place card was updated. Never edit the protected `Мои заметки`
block. If the helper fails, return the exact `status` and `message`; do not fall
back to the generic memory tool and do not search the skill source code. If the
source is inaccessible, make one alternative search from visible metadata. Ask
one targeted question only when the place remains genuinely ambiguous.

## Knowledge navigation

For questions about existing Hermes knowledge, prefer `knowledge_context` over
broad file scanning. It loads `Personal Anton/00-start/INDEX.md`, one relevant
file from `Personal Anton/views/`, and the canonical documents named there.
Do not scan `Archive`, backups, or historical context packs unless the user asks
for history, evidence, rollback, or the canonical files contain a conflict.
If two current documents conflict, report the conflict instead of choosing the
newest file by modification date.

## Addresses and nearby POI

```bash
$CTC dadata-address "Королёв, Лесная улица" --limit 3
$CTC geoapify-geocode "Усадьба Останкино" --limit 3
$CTC poi --lat 55.824 --lon 37.614 --category catering.cafe --radius 1500 --limit 10
```

Use DaData for Russian address/FIAS fields and Geoapify for geocoding and POI.

## Routes and weather

```bash
$CTC2 itinerary \
  --start "55.920,37.820" \
  --stop "55.825,37.615" \
  --destination "55.824,37.611" \
  --departure-at "2026-07-01T10:00:00Z" \
  --priority "children"
```

The itinerary compares direct and via-stop routes, reports added distance and
time, computes ETA for each point, and fetches Open-Meteo weather for arrival.

## Events and nearby infrastructure

```bash
$CTC3 overpass \
  --lat 55.824 --lon 37.614 --radius 1500 \
  --category toilets --category playgrounds --category museums

$CTC3 events \
  --location msk \
  --start "2026-07-01T00:00:00Z" \
  --end "2026-07-07T23:59:59Z" \
  --lat 55.824 --lon 37.614 --radius 5000 \
  --children --limit 10
```

Allowed Overpass categories are `toilets`, `playgrounds`, `parks`, `museums`,
`libraries`, `theatres`, and `parking`. KudaGo results must retain the original
source URL and attribution.

## Parking candidates

```bash
$CTCP --lat 55.824 --lon 37.614 --radius 1800 --limit 10
```

Parking statuses:

- `official`: tariff or free status confirmed by an official current source;
- `user_confirmed`: recently confirmed by the user on site;
- `likely_free`: OSM says `fee=no`, but signs, zone, barrier, and access are not
  verified;
- `unverified`: insufficient or conflicting data.

Do not recommend `access=private`, `customers`, `permit`, `residents`, or other
restricted candidates. Every answer must include the warning to check signs,
markings, barriers, and the paid-parking layer before leaving the car.
For parking follow-ups, reuse the saved trip context instead of geocoding the
short follow-up again. Return a coordinate pin link and, when start coordinates
are available, a direct Yandex route to each parking candidate. A request such
as “route to the second parking option” must use the last displayed option list.
For Moscow paid parking, use the official city open-data source
`data.mos.ru/opendata/623` (`Платные парковки на улично-дорожной сети`) or the
same dataset through an open raw API. Treat it only as official paid-zone
evidence. Never promote OSM `fee=no` to `official`, and prefer official evidence
when OSM conflicts with the official paid zone.

Recent user confirmations may be stored only under
`${HERMES_HOME:-~/.hermes}/state/city-travel-concierge/parking_confirmations.json`.
Do not put movement history, personal data, or confirmations into Git.

## Cafe ranking by reviews

1. Resolve the destination coordinates with this skill.
2. Fetch 6–10 nearby cafe/restaurant candidates through Geoapify.
3. Use `web` or `browser` to verify current Yandex Maps or 2GIS rating, number of
   reviews, opening hours, and child suitability.
4. Return no more than 3 ranked options with rating, review count, distance,
   child-related reason, and direct source link.
5. Do not rank a venue when current review data cannot be verified.
6. If `web` or `browser` is unavailable, return Geoapify candidates without
   ratings and state the degradation. Unknown child suitability is `unknown`.

## Output contract

Common fields include `source`, `checked_at`, `expires_at`, `confidence`,
`verification_status`, `coordinates`, `source_url`, `deep_links`, and
`provider_status`. Routes add ETA, distance, duration, detour, and
`traffic_status`. Parking adds `parking_status`, `parking_evidence`,
`eligible_for_recommendation`, and `parking_warning`.

## Verification

```bash
python3 -m unittest discover -s ${HERMES_HOME:-$HOME/.hermes}/skills/productivity/city-travel-concierge/tests
```
