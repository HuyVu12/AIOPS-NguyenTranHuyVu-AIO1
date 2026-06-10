import json
import os
import pandas as pd
import networkx as nx
from datetime import datetime
from collections import defaultdict

SEVERITY_RANK = {'crit': 2, 'warn': 1, 'info': 0}

def fingerprint(a):
    return "{}|{}|{}".format(a['service'], a['metric'], a['severity'])

def parse_ts(t):
    return datetime.fromisoformat(t.replace('Z', '+00:00'))

class Deduper:
    def __init__(self):
        self.store = {}

    def push(self, alert):
        fp = fingerprint(alert)
        if fp not in self.store:
            self.store[fp] = {
                'count': 1,
                'first_seen': alert['ts'],
                'last_seen': alert['ts'],
                'alerts': [alert['id']],
            }
        else:
            c = self.store[fp]
            c['count'] += 1
            c['last_seen'] = alert['ts']
            c['alerts'].append(alert['id'])
        return fp


def session_groups(alerts, gap_sec=120):
    sa = sorted(alerts, key=lambda a: a['ts'])
    groups = [[sa[0]]]
    for a in sa[1:]:
        gap = (parse_ts(a['ts']) - parse_ts(groups[-1][-1]['ts'])).total_seconds()
        if gap <= gap_sec:
            groups[-1].append(a)
        else:
            groups.append([a])
    return groups

def topology_group(alerts, graph, max_hop=2):
    und = graph.to_undirected()
    by_svc = defaultdict(list)
    for a in alerts:
        by_svc[a['service']].append(a)

    sl = list(by_svc.keys())
    parent = {s: s for s in sl}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, s1 in enumerate(sl):
        for s2 in sl[i + 1:]:
            try:
                if nx.shortest_path_length(und, s1, s2) <= max_hop:
                    parent[find(s1)] = find(s2)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                pass

    grps = defaultdict(list)
    for s in sl:
        grps[find(s)].extend(by_svc[s])
    return list(grps.values())


def correlate(alerts, graph, gap_sec=120, max_hop=2):
    d = Deduper()
    seen = set()
    deduped = []
    for a in alerts:
        fp = d.push(a)
        if fp not in seen:
            seen.add(fp)
            deduped.append(a)
    print('[1] Dedupe: {} -> {}'.format(len(alerts), len(deduped)))

    sessions = session_groups(deduped, gap_sec)
    print('[2] Sessions: {} (gap_sec={})'.format(len(sessions), gap_sec))

    clusters = []
    for si, sess in enumerate(sessions):
        for gi, grp in enumerate(topology_group(sess, graph, max_hop)):
            ts = sorted(a['ts'] for a in grp)
            clusters.append({
                'cluster_id':   'c-{:03d}-{:03d}'.format(si, gi),
                'alert_count':  len(grp),
                'services':     sorted({a['service'] for a in grp}),
                'time_range':   [ts[0], ts[-1]],
                'max_severity': max(
                    (a['severity'] for a in grp),
                    key=lambda s: SEVERITY_RANK.get(s, 0)
                ),
                'fingerprints': sorted({fingerprint(a) for a in grp}),
            })

    n_in = len(alerts)
    n_out = len(clusters)
    ratio = round(1 - n_out / n_in, 4)
    print('[3] Clusters: {}, reduction_ratio={}'.format(n_out, ratio))
    return {
        'input_alerts':    n_in,
        'output_clusters': n_out,
        'reduction_ratio': ratio,
        'clusters':        clusters,
    }