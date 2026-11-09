"""All calculated fields and aggregations.

FUEL ECONOMY METHOD (full-tank-to-full-tank)
--------------------------------------------
1. Records of one vehicle are sorted by date/time.
2. A record marked "full tank" is a *baseline*: the tank is known to be full.
3. When the next full-tank record arrives, the fuel burned since the baseline
   equals ALL litres added after the baseline up to and including this fill
   (partial top-ups in between are included).
4. km/L         = (odometer_now - odometer_baseline) / litres_added_in_between
   cost per km  = money_paid_in_between / (odometer_now - odometer_baseline)
   L/100 km     = 100 / km/L
5. The very first record of a vehicle only sets the baseline - it has no
   previous odometer reading, so NO economy figure is shown for it.
6. Partial fills show distance but their economy is deferred to the next
   full tank. Records with an odometer problem (confirmed by the user) break
   the chain and start a new baseline so a bad reading cannot pollute results.
"""
from collections import defaultdict
from statistics import median

ANOMALY_THRESHOLD = 0.75   # interval economy < 75% of vehicle median => flag


def vehicle_timeline(rows):
    """rows: refuel records of ONE vehicle. Returns list of dicts with
    calculated fields added, in chronological order."""
    rows = sorted(rows, key=lambda r: (r["filled_at"], r["id"]))
    out = []
    prev = None
    base = None          # last full-tank record (baseline)
    acc_l = acc_c = 0.0  # litres / money accumulated since baseline

    for r in rows:
        d = dict(r)
        d.update(distance=None, economy=None, cost_per_km=None, l_per_100=None,
                 interval_km=None, interval_litres=None, interval_cost=None,
                 valid=False, anomaly=False)

        if prev is None:
            d["status"] = "First record - baseline only"
            if r["full_tank"]:
                base = r
        else:
            dist = r["odometer"] - prev["odometer"]
            if dist <= 0:
                d["status"] = "Odometer issue - excluded"
                base = r if r["full_tank"] else None
                acc_l = acc_c = 0.0
            else:
                d["distance"] = dist
                acc_l += r["litres"]
                acc_c += r["receipt_total"]
                if not r["full_tank"]:
                    d["status"] = "Partial fill - economy at next full tank"
                elif base is None:
                    d["status"] = "First full tank - baseline only"
                else:
                    span = r["odometer"] - base["odometer"]
                    d.update(interval_km=span, interval_litres=acc_l,
                             interval_cost=acc_c, valid=True,
                             economy=span / acc_l,
                             cost_per_km=acc_c / span,
                             l_per_100=100 * acc_l / span,
                             status="Valid interval")
                if r["full_tank"]:
                    base = r
                    acc_l = acc_c = 0.0
        prev = r
        out.append(d)

    # Bonus: anomaly detection (possible fuel theft / leak / wrong entry)
    econ = [d["economy"] for d in out if d["valid"]]
    if len(econ) >= 4:
        med = median(econ)
        for d in out:
            if d["valid"] and d["economy"] < ANOMALY_THRESHOLD * med:
                d["anomaly"] = True
                d["status"] = "Valid interval - ANOMALY"
                d["anomaly_reason"] = (f"{d['economy']:.2f} km/L vs usual "
                                       f"{med:.2f} km/L ({d['economy']/med:.0%})")
    return out


def all_timelines(refuel_rows):
    by_vehicle = defaultdict(list)
    for r in refuel_rows:
        by_vehicle[r["vehicle_id"]].append(r)
    result = []
    for rows in by_vehicle.values():
        result.extend(vehicle_timeline(rows))
    return result


def in_range(date_str, start, end):
    day = date_str[:10]
    return (not start or day >= start) and (not end or day <= end)


def build_dashboard(vehicles, refuel_rows, expense_rows, start=None, end=None,
                    vehicle_id=None):
    """Aggregate everything the dashboard needs.

    History is always computed on the FULL record set (so intervals that
    start before the filter window are still correct); filtering is then
    applied to the record date."""
    timeline = all_timelines(refuel_rows)
    recs = [d for d in timeline
            if in_range(d["filled_at"], start, end)
            and (not vehicle_id or d["vehicle_id"] == vehicle_id)]
    exps = [dict(e) for e in expense_rows
            if in_range(e["date"], start, end)
            and (not vehicle_id or e["vehicle_id"] == vehicle_id)]
    vmap = {v["id"]: dict(v) for v in vehicles}

    # --- per vehicle ---
    per = {}
    for vid, v in vmap.items():
        if vehicle_id and vid != vehicle_id:
            continue
        per[vid] = dict(id=vid, reg_no=v["reg_no"], make_model=v["make_model"],
                        fuel_type=v["fuel_type"], active=v["active"],
                        km=0.0, litres=0.0, fuel_cost=0.0, fills=0,
                        valid_km=0.0, valid_litres=0.0, valid_cost=0.0,
                        maint_cost=0.0, anomalies=0)
    for d in recs:
        p = per.get(d["vehicle_id"])
        if p is None:
            continue
        p["fills"] += 1
        p["litres"] += d["litres"]
        p["fuel_cost"] += d["receipt_total"]
        if d["distance"]:
            p["km"] += d["distance"]
        if d["valid"]:
            p["valid_km"] += d["interval_km"]
            p["valid_litres"] += d["interval_litres"]
            p["valid_cost"] += d["interval_cost"]
        if d["anomaly"]:
            p["anomalies"] += 1
    for e in exps:
        if e["vehicle_id"] in per:
            per[e["vehicle_id"]]["maint_cost"] += e["amount"]
    for p in per.values():
        p["economy"] = p["valid_km"] / p["valid_litres"] if p["valid_litres"] else None
        p["cost_per_km"] = p["valid_cost"] / p["valid_km"] if p["valid_km"] else None
        p["total_cost"] = p["fuel_cost"] + p["maint_cost"]
    vehicles_with_data = [p for p in per.values() if p["fills"]]

    # --- fleet totals ---
    total_km = sum(p["km"] for p in per.values())
    total_litres = sum(p["litres"] for p in per.values())
    total_fuel_cost = sum(p["fuel_cost"] for p in per.values())
    valid_km = sum(p["valid_km"] for p in per.values())
    valid_litres = sum(p["valid_litres"] for p in per.values())
    valid_cost = sum(p["valid_cost"] for p in per.values())
    total_maint = sum(p["maint_cost"] for p in per.values())

    ranked = sorted([p for p in vehicles_with_data if p["economy"]],
                    key=lambda p: p["economy"], reverse=True)

    # --- monthly trend ---
    months = defaultdict(lambda: dict(fuel_cost=0.0, litres=0.0, maint=0.0,
                                      vkm=0.0, vl=0.0))
    for d in recs:
        m = months[d["filled_at"][:7]]
        m["fuel_cost"] += d["receipt_total"]
        m["litres"] += d["litres"]
        if d["valid"]:
            m["vkm"] += d["interval_km"]
            m["vl"] += d["interval_litres"]
    for e in exps:
        months[e["date"][:7]]["maint"] += e["amount"]
    month_keys = sorted(months)
    trend = dict(
        labels=month_keys,
        fuel_cost=[round(months[k]["fuel_cost"], 2) for k in month_keys],
        litres=[round(months[k]["litres"], 2) for k in month_keys],
        maint=[round(months[k]["maint"], 2) for k in month_keys],
        economy=[round(months[k]["vkm"] / months[k]["vl"], 2) if months[k]["vl"] else None
                 for k in month_keys],
    )

    return dict(
        total_km=total_km,
        total_litres=total_litres,
        total_fuel_cost=total_fuel_cost,
        total_maint=total_maint,
        overall_cost_per_km=valid_cost / valid_km if valid_km else None,
        overall_economy=valid_km / valid_litres if valid_litres else None,
        avg_fill_cost=total_fuel_cost / len(recs) if recs else None,
        fills=len(recs),
        most_economical=ranked[0] if ranked else None,
        least_economical=ranked[-1] if ranked else None,
        highest_consumer=max(vehicles_with_data, key=lambda p: p["litres"]) if vehicles_with_data else None,
        per_vehicle=sorted(vehicles_with_data, key=lambda p: p["reg_no"]),
        trend=trend,
        anomalies=[d | {"reg_no": vmap[d["vehicle_id"]]["reg_no"]} for d in recs if d["anomaly"]],
        timeline=recs,
        expenses=exps,
    )


def maintenance_reminders(vehicles, refuel_rows, today, km_window=500, day_window=14):
    """Bonus: service due soon / overdue, by km or by date."""
    from datetime import date
    last_odo = {}
    for r in refuel_rows:
        last_odo[r["vehicle_id"]] = max(last_odo.get(r["vehicle_id"], 0), r["odometer"])
    out = []
    for v in vehicles:
        if not v["active"]:
            continue
        msgs = []
        odo = last_odo.get(v["id"])
        if v["next_service_km"] and odo is not None:
            left = v["next_service_km"] - odo
            if left <= 0:
                msgs.append(f"overdue by {-left:,.0f} km")
            elif left <= km_window:
                msgs.append(f"due in {left:,.0f} km")
        if v["next_service_date"]:
            days = (date.fromisoformat(v["next_service_date"]) - today).days
            if days < 0:
                msgs.append(f"overdue by {-days} days")
            elif days <= day_window:
                msgs.append(f"due in {days} days ({v['next_service_date']})")
        if msgs:
            out.append(dict(reg_no=v["reg_no"], id=v["id"],
                            overdue=any("overdue" in m for m in msgs),
                            message="; ".join(msgs)))
    return out
