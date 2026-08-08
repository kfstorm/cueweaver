#!/usr/bin/env python3
"""Build a metadata-only en->zh Glossary; intentionally throwaway evidence code."""

import argparse
import json
import os
import re
import urllib.parse
import urllib.request


UA = "CueWeaver metadata-glossary prototype/0.1 (https://github.com/kfstorm/cueweaver)"


def get_json(url, *, params=None, headers=None):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def tmdb(path, key, **params):
    url = "https://api.themoviedb.org/3/" + path
    if key.count(".") == 2:
        return get_json(url, params=params, headers={"Authorization": "Bearer " + key})
    return get_json(url, params={"api_key": key, **params})


def labels(entity):
    en = entity.get("labels", {}).get("en", {}).get("value")
    zh = entity.get("labels", {}).get("zh", {}).get("value")
    if not en or not zh:
        return None
    return en, zh


def wikidata_entities(qid):
    query = """
    SELECT DISTINCT ?entity WHERE {
      { ?entity wdt:P1441 wd:%s }
      UNION { wd:%s wdt:P674 ?entity }
    }
    """ % (qid, qid)
    data = get_json(
        "https://query.wikidata.org/sparql", params={"query": query, "format": "json"}
    )
    return [
        row["entity"]["value"].rsplit("/", 1)[-1] for row in data["results"]["bindings"]
    ]


def wikidata_labels(qids):
    entities = {}
    for offset in range(0, len(qids), 50):
        ids = "|".join(qids[offset : offset + 50])
        data = get_json(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbgetentities",
                "ids": ids,
                "props": "labels|aliases|info",
                "languages": "en|zh",
                "languagefallback": "1",
                "format": "json",
            },
        )
        entities.update(data.get("entities", {}))
    return entities


def wikipedia_langlink(role, series_title):
    search = get_json(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": '"%s" "%s"' % (role, series_title),
            "srlimit": 5,
            "format": "json",
        },
    )
    for hit in search.get("query", {}).get("search", []):
        title = hit["title"]
        role_words = set(re.findall(r"[A-Za-z]{4,}", role.casefold()))
        title_words = set(re.findall(r"[A-Za-z]{4,}", title.casefold()))
        if role.startswith("[") or not role_words.intersection(title_words):
            continue
        page = get_json(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "prop": "langlinks|pageprops",
                "titles": title,
                "lllang": "zh",
                "lllimit": 1,
                "format": "json",
                "formatversion": 2,
            },
        )
        pages = page.get("query", {}).get("pages", [])
        if not pages or "langlinks" not in pages[0]:
            continue
        langlink = pages[0]["langlinks"][0]
        target = (langlink.get("*") or langlink.get("title", "")).strip()
        if not target or any(marker in target for marker in ("列表", "消歧义")):
            continue
        return {
            "source": role,
            "target": target,
            "provider": "wikipedia-langlink",
            "confidence": 0.65,
            "provenance": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title),
        }
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tmdb-id", required=True, help="TMDb TV series id")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--episode", type=int, required=True)
    parser.add_argument("--tmdb-key", default=os.environ.get("TMDB_API_KEY"))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = {
        "input": vars(args) | {"tmdb_key": bool(args.tmdb_key)},
        "terms": [],
        "fallbacks": [],
    }
    if not args.tmdb_key:
        result["fallbacks"].append("TMDB_API_KEY missing")
        result["summary"] = {
            "status": "baseline-only",
            "coverage": 0,
            "precision": None,
        }
        write(result, args.output)
        return
    try:
        series = tmdb(
            "tv/" + args.tmdb_id, args.tmdb_key, append_to_response="external_ids"
        )
        episode = tmdb(
            "tv/%s/season/%s/episode/%s" % (args.tmdb_id, args.season, args.episode),
            args.tmdb_key,
            append_to_response="credits",
        )
    except Exception as error:
        result["fallbacks"].append("TMDb request failed: " + str(error))
        result["summary"] = {
            "status": "baseline-only",
            "coverage": 0,
            "precision": None,
        }
        write(result, args.output)
        return
    cast = episode.get("credits", {}).get("cast", [])
    roles = [
        item.get("character", "").strip()
        for item in cast
        if item.get("character", "").strip()
    ]
    qid = series.get("external_ids", {}).get("wikidata_id")
    resolved = 0
    if qid:
        try:
            qids = wikidata_entities(qid)
            entities = wikidata_labels(qids)
            for entity_id, entity in entities.items():
                pair = labels(entity)
                if pair and any(
                    role.casefold() == pair[0].casefold() for role in roles
                ):
                    result["terms"].append(
                        {
                            "source": pair[0],
                            "target": pair[1],
                            "provider": "wikidata",
                            "confidence": 0.8,
                            "provenance": "https://www.wikidata.org/wiki/" + entity_id,
                        }
                    )
            resolved = sum(
                any(
                    role.casefold() == term["source"].casefold()
                    for term in result["terms"]
                )
                for role in roles
            )
        except Exception as error:
            result["fallbacks"].append("Wikidata request failed: " + str(error))
    else:
        result["fallbacks"].append("TMDb series has no Wikidata id")
    for role in roles:
        if any(
            role.casefold() == term["source"].casefold() for term in result["terms"]
        ):
            continue
        try:
            term = wikipedia_langlink(role, series.get("name", ""))
        except Exception as error:
            result["fallbacks"].append(
                "Wikipedia langlink failed for %r: %s" % (role, error)
            )
            term = None
        if term:
            result["terms"].append(term)
    unique = {
        (term["source"].casefold(), term["target"]): term for term in result["terms"]
    }
    result["terms"] = list(unique.values())
    accepted = sum(
        any(role.casefold() == term["source"].casefold() for term in result["terms"])
        for role in roles
    )
    result["summary"] = {
        "status": "ok",
        "episode_cast_roles": len(roles),
        "wikidata_roles": resolved,
        "accepted_roles": accepted,
        "terms": len(result["terms"]),
        "coverage": accepted / len(roles) if roles else 0,
        "precision": "manual review required",
        "fallbacks": len(result["fallbacks"]),
    }
    write(result, args.output)


def write(result, path):
    with open(path, "w", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
