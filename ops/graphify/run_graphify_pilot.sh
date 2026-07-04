#!/bin/sh
set -eu
/usr/bin/python3 /srv/hermes-memory/bin/build_graphify_pilot.py
/home/hermes/.hermes/tools/graphify-0.9.5/venv/bin/graphify cluster-only /srv/hermes-memory/indexes/graphify/personal-anton-pilot/output --graph /srv/hermes-memory/indexes/graphify/personal-anton-pilot/output/graphify-out/graph.json --no-label
