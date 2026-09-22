"""Tiny-case exhaustive oracle, independent of engine DFS.

Enumerate destination permutations, then access choices and quote products.
Use the original M1 witness evaluator for route constraints and scoring.
"""

from decimal import Decimal
from itertools import permutations, product

from .validation import Dataset, JsonObject, evaluate_witness, validate_request


def exhaustive_oracle(request: JsonObject, data: Dataset, allowed: set[str]) -> dict[tuple, JsonObject]:
    validate_request(request, data)
    offers = [data.offers[i] for i in sorted(allowed)]
    found = {}
    upper = min(len(data.destinations), len(offers) - 1,
                len(request["required_destination_ids"]) + request["max_optional_destinations"])
    for count in range(1, upper + 1):
        for order in permutations(sorted(data.destinations), count):
            access_choices = [[a for a in data.accesses.values() if a["destination_id"] == d] for d in order]
            for accesses in product(*access_choices):
                airports = [a["airport_id"] for a in accesses]
                choices = [[o for o in offers if o["origin_airport_id"] in request["origin_airport_ids"]
                            and o["destination_airport_id"] == airports[0]]]
                choices.extend([o for o in offers if o["origin_airport_id"] == a and o["destination_airport_id"] == b]
                               for a, b in zip(airports, airports[1:]))
                choices.append([o for o in offers if o["origin_airport_id"] == airports[-1]
                                and o["destination_airport_id"] in request["return_airport_ids"]])
                for selected in product(*choices):
                    ids = tuple(o["id"] for o in selected)
                    access_ids = tuple(a["id"] for a in accesses)
                    if len(set(ids)) != len(ids):
                        continue
                    witness = {"id": "oracle", "offer_ids": list(ids), "access_ids": list(access_ids)}
                    checked = evaluate_witness(request, witness, data, allowed)
                    if checked["valid"]:
                        found[(ids, access_ids)] = checked["metrics"]
    return found


def oracle_front(found: dict[tuple, JsonObject]) -> set[tuple]:
    vectors = {key: (m["flight_total_minor"], Decimal(m["burden_points"]), -Decimal(m["experience_points"]))
               for key, m in found.items()}
    surviving = set(vectors)
    for key, v in vectors.items():
        for other, w in vectors.items():
            if other != key and w[0] <= v[0] and w[1] <= v[1] and w[2] <= v[2] and w != v:
                surviving.discard(key)
                break
    return surviving
