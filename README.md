# KZ Probe Watch

How healthy is Kazakhstan's RIPE Atlas measurement network, and where are the gaps?

RIPE Atlas is a global network of small measurement devices ("probes") hosted by volunteers. This project looks at every probe ever registered in Kazakhstan and asks:

- How many are still connected, and how many have been abandoned?
- Which regional centres have no working probe nearby?
- Which networks (ASNs) host probes, and which ones lose them most often?
- How long do probes last before they are abandoned?
- How many connected probes have working IPv6?

The page is available in English, Kazakh and Russian.

## First findings (23 Nov 2026)

- 253 probes have ever been registered in Kazakhstan: 106 are connected, 108 are abandoned.
- Kazakhtelecom (AS9198) has hosted the most probes (77) and lost 44 of them.
- KazRENA (AS41419), the national research and education network, has lost all of its probes.
- About half of the connected probes have working IPv6.

These are observations, not conclusions. Finding out why probes are abandoned is the next step.

## How it works

`kz_probe_watch.py` uses only the Python standard library. It:

1. downloads all probes with country code `KZ` from the [RIPE Atlas API](https://atlas.ripe.net/docs/apis/),
2. looks up network names with the [RIPEstat Data API](https://stat.ripe.net/docs/data-api/),
3. computes the statistics and writes `site/index.html` (a Leaflet map plus tables).

```
python3 kz_probe_watch.py
open site/index.html
```

## Notes

- Probe locations are approximate: RIPE Atlas publishes rounded coordinates, so probes in the same city overlap on the map.
- "Lost" means abandoned or written off.
- City coordinates used for the coverage check are approximate.

## Next steps

- Run the script daily to record when and where probes go offline.
- Measure IPv6 deployment across Kazakh networks beyond probe hosts.
- Work with hosts and universities to reconnect abandoned probes.

Built by Aruzhan Baiseit.
