# Inland forest to coastal boat: preliminary relay sizing

Prepared November 7, 2026. Assumption study for search and rescue; not a terrain survey, spectrum authorization or validated communications plan. The missing person carries no technology. Source observations originate on the search bird. The boat is a gateway with an independent onward connection.

## Working example

Use an illustrative 60 km straight-line separation from an inland search area to the coastal boat. Taiwan is about 144 km at its widest, but that fact does not identify the most remote forest location or the distance to a particular accessible shore. The chosen 60 km is a scenario, not a measured route. Boat position, search coordinates and a digital elevation model are needed for actual sizing.

Official geography: https://www.roc-taiwan.org/uploads/sites/89/2024/05/2023-2024_Taiwan-at-a-Glance.pdf

## Count links first, then intermediate repeaters

For source bird and boat as fixed endpoints:

    links = ceil(path_distance / usable_hop_length)
    intermediate_repeaters = links - 1

| Assumed tested usable hop | Links across 60 km | Intermediate repeaters | Total nodes including bird and boat |
|---|---:|---:|---:|
| 5 km | 12 | 11 | 13 |
| 10 km | 6 | 5 | 7 |
| 15 km | 4 | 3 | 5 |
| 20 km | 3 | 2 | 4 |

These are arithmetic scenarios, not range predictions for the existing hardware. At 10–15 km verified hops, the example needs 3–5 intermediate relay nodes simultaneously. One or two replacement units would make an illustrative carried inventory of 4–7 relay units, plus the search bird. Endurance and the deployment schedule may require more replacements. There is no defensible fixed answer from radio brand or island width alone.

The chain is search bird → intermediate relays → boat gateway → coordinator. The search bird is an endpoint, not an intermediate repeater. Any local relay needed to climb out of the forest must be counted in the actual topology, rather than automatically added twice to this arithmetic.

## Terrain decides the real topology

A ridge can block a nominally short link. Height above canopy is not height above sea level. A good link requires an elevation profile, antenna heights, obstruction/Fresnel assessment and a complete link budget. Diffraction may permit some obstructed communication, but it must be modeled and tested rather than assumed. ITU-R P.526 explicitly addresses terrain diffraction; P.530 provides terrestrial line-of-sight design methods.

Sources:
- https://www.itu.int/rec/R-REC-P.526-16-202511-I
- https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.530-19-202509-I%21%21PDF-E.pdf

The first connection from a bird inside a forest to infrastructure above the canopy may be more difficult than a longer unobstructed link. A boat's low antenna position and changing orientation also matter. Do not multiply the browser simulator's current single-hop range into an island-wide network: it does not model mountain profiles, multihop routing, simultaneous traffic, antenna pattern or interference.

## Flight energy may be the limiting factor

At assumed 12 m/s groundspeed, 60 km requires 83.3 minutes one way. The full-size reference's illustrative 80–120 W cruise range requires about 111–167 Wh for that transit alone. The previously considered 120 Wh pack with 96 Wh usable does not cover it, even before searching, climbing, wind or returning. The 12 m/s value is airspeed in the flight analysis; using it as groundspeed here explicitly assumes no wind.

A relay network does not by itself solve getting the search aircraft to the inland search area. Launch/recovery arrangements and propulsion/battery sizing need separate closure. Long-duration airborne relays also need much more energy than perching relays. A perched relay needs a mechanically achievable perch with a verified radio path; not every branch is suitable.

If replacement units must fly from the same boat, deployment distance and energy have to be checked for each unit. Reserve units cannot be counted as immediate redundancy until they can reach and assume the role.

## Data and reliability

Start with short position, uncertainty and evidence summaries rather than continuous video. For scale, 100 bytes once per minute is 13.3 bit/s of application data, before framing, error correction, retries and routing overhead. Live video is a different network requirement. Brief data transfer reduces bandwidth and energy demand but does not establish that transmissions are undetectable.

End-to-end delivery must be measured across the entire chain. As a simplified illustration, six independent links each delivering one attempt with probability 0.95 give 0.95^6 = 73.5% end-to-end success; at 0.99 each the product is 94.1%. Real links and weather are correlated. Retries, scheduling and buffering consume time and energy. Required latency and delivery probability should be explicit.

An unobtrusive physical design, appropriate encryption and low data volume are reasonable product requirements. No proposed radio or bird silhouette guarantees secrecy. This study does not select transmission settings or routes for avoiding detection.

## Alternative when real-time delivery is unnecessary

A store-and-carry bird can retain an observation until it reaches an established gateway. This reduces dependence on a continuously functioning chain, at the cost of delivery latency and aircraft energy. Satellite onward service is a separate alternative requiring suitable equipment and sky access; it must not be assumed available from beneath dense foliage.

## Next evidence

1. Pick a representative inland coordinate and boat position; label the distance and terrain profile.
2. Specify whether summaries, still images or video are required, and the allowed reporting delay.
3. Establish hardware, antennas, payload mass, power and spectrum constraints.
4. Test the canopy exit, obstructed terrain paths and boat endpoint; derive usable hops from those results.
5. Close deployment, perch/airborne dwell, replacement and recovery energy before quoting fleet count.

For the current 60 km example, use **3–5 intermediate relays only as a 10–15 km-per-hop planning case**, and keep the broader 2–11 scenario range visible. Actual count is unresolved until topology and tested links are available.
