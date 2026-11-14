"""Reconstruct routes without merging edges across invocations."""

import json


def observed_paths(trace, targets):
    calls = {}
    routes = {}
    hits = 0
    keys = {(target["file"], target["line"]) for target in targets}
    if not trace.exists():
        return dict(target_hits=0, routes=[], shortest_route=None)
    with trace.open(encoding="utf-8") as handle:
        for line in handle:
            call = json.loads(line)
            calls[call["id"]] = call
            if (call["file"], call["line"]) not in keys:
                continue
            hits += 1
            chain = [call]
            while (not chain[-1]["test_entry"] and not chain[-1]["fixture_entry"]
                   and chain[-1]["parent"] is not None):
                chain.append(calls[chain[-1]["parent"]])
            chain.reverse()
            anchor = ("test" if chain[0]["test_entry"] else
                      "fixture" if chain[0]["fixture_entry"] else "phase")
            frames = [
                {field: frame[field] for field in ("file", "line", "function", "caller_line", "direct_parent")}
                for frame in chain
            ]
            key = json.dumps([anchor, call["phase"], frames], sort_keys=True)
            route = routes.setdefault(key, dict(anchor=anchor, phase=call["phase"],
                                               frames=frames, target_invocations=[]))
            route["target_invocations"].append(call["id"])
    ordered = [routes[key] for key in sorted(routes, key=lambda key: (len(routes[key]["frames"]), key))]
    return dict(target_hits=hits, routes=ordered,
                shortest_route=next((r for r in ordered if r["anchor"] != "phase"), None))
