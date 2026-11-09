"""Fleet Fuel & Expense Manager - EF101-P03
Run:  python app.py   then open http://127.0.0.1:5000
"""
import csv
import io
import os
from datetime import date, datetime, timedelta

from flask import (Flask, Response, flash, redirect, render_template, request,
                   send_file, url_for)

import calculations as calc
from db import DB_PATH, close_db, connect, get_db, init_schema

CURRENCY = "Rs"
VEHICLE_TYPES = ["Sedan", "Hatchback", "SUV", "Pickup", "Van", "Truck", "Motorcycle", "Bus"]
FUEL_TYPES = ["Petrol", "Diesel", "CNG", "Hybrid", "Electric"]
EXPENSE_CATEGORIES = ["Maintenance / Service", "Repair", "Tyres", "Insurance",
                      "Registration / Token Tax", "Toll / Parking", "Cleaning", "Other"]
PRICE_RANGE = (100, 600)   # Rs per litre outside this range => "suspicious"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ef101-p03-dev-key")
app.teardown_appcontext(close_db)


# ----------------------------------------------------------------- helpers
@app.template_filter("money")
def money(v):
    return "-" if v is None else f"{CURRENCY} {v:,.0f}"


@app.template_filter("num")
def num(v, digits=0):
    return "-" if v is None else f"{v:,.{digits}f}"


@app.context_processor
def inject_globals():
    return dict(CURRENCY=CURRENCY, today=date.today().isoformat())


def to_float(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def all_vehicles(active_only=False):
    sql = "SELECT * FROM vehicles"
    if active_only:
        sql += " WHERE active = 1"
    return get_db().execute(sql + " ORDER BY reg_no").fetchall()


def get_filters():
    vid = request.args.get("vehicle_id", type=int)
    start = request.args.get("start") or None
    end = request.args.get("end") or None
    return vid, start, end


# --------------------------------------------------------------- dashboard
@app.route("/")
def dashboard():
    vid, start, end = get_filters()
    db = get_db()
    vehicles = all_vehicles()
    refuels = db.execute("SELECT * FROM refuels").fetchall()
    expenses = db.execute("SELECT * FROM expenses").fetchall()
    data = calc.build_dashboard(vehicles, refuels, expenses, start, end, vid)
    reminders = calc.maintenance_reminders(vehicles, refuels, date.today())
    return render_template("dashboard.html", d=data, vehicles=vehicles,
                           reminders=reminders, f=dict(vehicle_id=vid, start=start, end=end))


# ---------------------------------------------------------------- vehicles
@app.route("/vehicles")
def vehicles():
    show = request.args.get("show", "active")
    db = get_db()
    rows = db.execute("""
        SELECT v.*, COUNT(r.id) AS fills, MAX(r.odometer) AS last_odo,
               MAX(r.filled_at) AS last_fill
        FROM vehicles v LEFT JOIN refuels r ON r.vehicle_id = v.id
        GROUP BY v.id ORDER BY v.active DESC, v.reg_no""").fetchall()
    if show == "active":
        rows = [r for r in rows if r["active"]]
    return render_template("vehicles.html", rows=rows, show=show)


def vehicle_form_data():
    f = request.form
    return dict(
        reg_no=f.get("reg_no", "").strip().upper(),
        make_model=f.get("make_model", "").strip(),
        vehicle_type=f.get("vehicle_type", ""),
        fuel_type=f.get("fuel_type", ""),
        tank_capacity=to_float(f.get("tank_capacity")) if f.get("tank_capacity") else None,
        next_service_km=to_float(f.get("next_service_km")) if f.get("next_service_km") else None,
        next_service_date=f.get("next_service_date") or None,
        notes=f.get("notes", "").strip() or None,
    )


def validate_vehicle(v, vid=None):
    errors = []
    if not v["reg_no"]:
        errors.append("Registration / vehicle ID is required.")
    if not v["make_model"]:
        errors.append("Make / model is required.")
    if v["vehicle_type"] not in VEHICLE_TYPES:
        errors.append("Choose a vehicle type.")
    if v["fuel_type"] not in FUEL_TYPES:
        errors.append("Choose a fuel type.")
    if v["tank_capacity"] is not None and not (1 <= v["tank_capacity"] <= 1000):
        errors.append("Tank capacity must be between 1 and 1000 litres.")
    dup = get_db().execute("SELECT id FROM vehicles WHERE reg_no = ? AND id != ?",
                           (v["reg_no"], vid or 0)).fetchone()
    if dup:
        errors.append(f"A vehicle with registration {v['reg_no']} already exists.")
    return errors


@app.route("/vehicles/new", methods=["GET", "POST"])
@app.route("/vehicles/<int:vid>/edit", methods=["GET", "POST"])
def vehicle_form(vid=None):
    db = get_db()
    existing = db.execute("SELECT * FROM vehicles WHERE id = ?", (vid,)).fetchone() if vid else None
    if vid and not existing:
        flash("Vehicle not found.", "danger")
        return redirect(url_for("vehicles"))
    if request.method == "POST":
        v = vehicle_form_data()
        errors = validate_vehicle(v, vid)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("vehicle_form.html", v=v, vid=vid,
                                   types=VEHICLE_TYPES, fuels=FUEL_TYPES)
        cols = list(v)
        if vid:
            db.execute(f"UPDATE vehicles SET {', '.join(c + '=?' for c in cols)} WHERE id=?",
                       [v[c] for c in cols] + [vid])
            flash(f"Vehicle {v['reg_no']} updated.", "success")
        else:
            db.execute(f"INSERT INTO vehicles ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                       [v[c] for c in cols])
            flash(f"Vehicle {v['reg_no']} added.", "success")
        db.commit()
        return redirect(url_for("vehicles"))
    return render_template("vehicle_form.html", v=dict(existing) if existing else {},
                           vid=vid, types=VEHICLE_TYPES, fuels=FUEL_TYPES)


@app.post("/vehicles/<int:vid>/toggle")
def vehicle_toggle(vid):
    db = get_db()
    v = db.execute("SELECT * FROM vehicles WHERE id = ?", (vid,)).fetchone()
    if v:
        db.execute("UPDATE vehicles SET active = ? WHERE id = ?", (0 if v["active"] else 1, vid))
        db.commit()
        flash(f"Vehicle {v['reg_no']} {'deactivated' if v['active'] else 'reactivated'}. "
              "Its history is kept for reporting.", "info")
    return redirect(url_for("vehicles", show="all"))


# ----------------------------------------------------------------- refuels
@app.route("/refuels")
def refuels():
    vid, start, end = get_filters()
    db = get_db()
    vmap = {v["id"]: v for v in all_vehicles()}
    timeline = calc.all_timelines(db.execute("SELECT * FROM refuels").fetchall())
    rows = [d for d in timeline
            if calc.in_range(d["filled_at"], start, end)
            and (not vid or d["vehicle_id"] == vid)]
    rows.sort(key=lambda d: (d["filled_at"], d["id"]), reverse=True)
    return render_template("refuels.html", rows=rows, vmap=vmap, vehicles=list(vmap.values()),
                           f=dict(vehicle_id=vid, start=start, end=end))


def refuel_form_data():
    f = request.form
    litres = to_float(f.get("litres"))
    price = to_float(f.get("price_per_litre"))
    calc_total = round(litres * price, 2) if litres and price else None
    receipt = to_float(f.get("receipt_total")) if f.get("receipt_total") else calc_total
    return dict(
        vehicle_id=f.get("vehicle_id", type=int),
        filled_at=(f.get("filled_at") or "").replace("T", " ")[:16],
        odometer=to_float(f.get("odometer")),
        litres=litres,
        price_per_litre=price,
        calc_total=calc_total,
        receipt_total=receipt,
        full_tank=1 if f.get("full_tank") else 0,
        driver=f.get("driver", "").strip() or None,
        station=f.get("station", "").strip() or None,
        notes=f.get("notes", "").strip() or None,
    )


def validate_refuel(r, rid=None):
    """Returns (errors, warnings).
    errors   = impossible data -> always rejected
    warnings = suspicious data -> rejected unless the user ticks 'I confirm'"""
    errors, warnings = [], []
    db = get_db()
    v = db.execute("SELECT * FROM vehicles WHERE id = ?", (r["vehicle_id"],)).fetchone()
    if not v:
        errors.append("Select a vehicle.")
    elif not v["active"] and not rid:
        errors.append(f"{v['reg_no']} is deactivated - reactivate it before adding records.")
    try:
        when = datetime.strptime(r["filled_at"], "%Y-%m-%d %H:%M")
        if when > datetime.now() + timedelta(hours=1):
            errors.append("Date/time cannot be in the future.")
    except ValueError:
        errors.append("Enter a valid date and time.")
    for key, label in [("odometer", "Odometer"), ("litres", "Litres"),
                       ("price_per_litre", "Price per litre"), ("receipt_total", "Receipt total")]:
        if r[key] is None:
            errors.append(f"{label} must be a number.")
        elif r[key] < 0 or (key != "odometer" and r[key] == 0):
            errors.append(f"{label} must be greater than zero.")
    if errors:
        return errors, warnings

    prev = db.execute("""SELECT * FROM refuels WHERE vehicle_id=? AND id!=? AND filled_at<=?
                         ORDER BY filled_at DESC, id DESC LIMIT 1""",
                      (r["vehicle_id"], rid or 0, r["filled_at"])).fetchone()
    nxt = db.execute("""SELECT * FROM refuels WHERE vehicle_id=? AND id!=? AND filled_at>?
                        ORDER BY filled_at ASC, id ASC LIMIT 1""",
                     (r["vehicle_id"], rid or 0, r["filled_at"])).fetchone()
    if prev:
        if r["odometer"] < prev["odometer"]:
            warnings.append(f"Odometer {r['odometer']:,.0f} km is LOWER than the previous reading "
                            f"{prev['odometer']:,.0f} km ({prev['filled_at']}).")
        elif r["odometer"] == prev["odometer"]:
            warnings.append(f"Odometer is the same as the previous reading ({prev['filled_at']}) - zero distance.")
        else:
            max_range = (v["tank_capacity"] or 60) * 30
            if r["odometer"] - prev["odometer"] > max_range:
                warnings.append(f"Distance since last fill is {r['odometer'] - prev['odometer']:,.0f} km, "
                                f"more than this vehicle could normally drive on one tank (~{max_range:,.0f} km).")
    if nxt and r["odometer"] > nxt["odometer"]:
        warnings.append(f"Odometer is HIGHER than the next later record "
                        f"{nxt['odometer']:,.0f} km ({nxt['filled_at']}).")
    if v["tank_capacity"] and r["litres"] > v["tank_capacity"] * 1.05:
        warnings.append(f"{r['litres']:.1f} L is more than the tank capacity ({v['tank_capacity']:.0f} L).")
    if not PRICE_RANGE[0] <= r["price_per_litre"] <= PRICE_RANGE[1]:
        warnings.append(f"Price {CURRENCY} {r['price_per_litre']:.2f}/L is outside the normal range "
                        f"{CURRENCY} {PRICE_RANGE[0]}-{PRICE_RANGE[1]}.")
    diff = r["receipt_total"] - r["calc_total"]
    if abs(diff) > max(5, 0.02 * r["calc_total"]):
        warnings.append(f"Receipt amount {CURRENCY} {r['receipt_total']:,.2f} differs from "
                        f"litres x price = {CURRENCY} {r['calc_total']:,.2f} by {CURRENCY} {diff:+,.2f}.")
    return errors, warnings


@app.route("/refuels/new", methods=["GET", "POST"])
@app.route("/refuels/<int:rid>/edit", methods=["GET", "POST"])
def refuel_form(rid=None):
    db = get_db()
    existing = db.execute("SELECT * FROM refuels WHERE id = ?", (rid,)).fetchone() if rid else None
    if rid and not existing:
        flash("Record not found.", "danger")
        return redirect(url_for("refuels"))
    vehicles = all_vehicles()
    warnings = []
    if request.method == "POST":
        r = refuel_form_data()
        errors, warnings = validate_refuel(r, rid)
        confirmed = request.form.get("confirm") == "1"
        reason = request.form.get("override_reason", "").strip()
        if errors:
            for e in errors:
                flash(e, "danger")
        elif warnings and not (confirmed and reason):
            if confirmed and not reason:
                flash("Give a short reason for confirming the suspicious values.", "warning")
            flash("This record looks suspicious. Correct the values, or tick the confirmation box "
                  "and give a reason to save it anyway.", "warning")
        else:
            r["override_note"] = (f"CONFIRMED: {reason} | " + " / ".join(warnings)) if warnings else None
            cols = list(r)
            if rid:
                db.execute(f"UPDATE refuels SET {', '.join(c + '=?' for c in cols)} WHERE id=?",
                           [r[c] for c in cols] + [rid])
                flash("Refuelling record updated.", "success")
            else:
                db.execute(f"INSERT INTO refuels ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           [r[c] for c in cols])
                flash("Refuelling record saved.", "success")
            db.commit()
            return redirect(url_for("refuels", vehicle_id=r["vehicle_id"]))
        form = dict(r, filled_at=r["filled_at"].replace(" ", "T"))
    elif existing:
        form = dict(existing, filled_at=existing["filled_at"].replace(" ", "T"))
    else:
        form = dict.fromkeys(["odometer", "litres", "price_per_litre", "receipt_total",
                              "driver", "station", "notes", "override_note"])
        form.update(filled_at=datetime.now().strftime("%Y-%m-%dT%H:%M"), full_tank=1,
                    vehicle_id=request.args.get("vehicle_id", type=int))
    last = {row["vehicle_id"]: dict(row) for row in db.execute(
        """SELECT vehicle_id, MAX(odometer) AS odo, MAX(filled_at) AS at
           FROM refuels GROUP BY vehicle_id""").fetchall()}
    return render_template("refuel_form.html", r=form, rid=rid, vehicles=vehicles,
                           warnings=warnings, last=last)


@app.post("/refuels/<int:rid>/delete")
def refuel_delete(rid):
    db = get_db()
    db.execute("DELETE FROM refuels WHERE id = ?", (rid,))
    db.commit()
    flash("Refuelling record deleted.", "info")
    return redirect(request.referrer or url_for("refuels"))


# ---------------------------------------------------------------- expenses
@app.route("/expenses")
def expenses():
    vid, start, end = get_filters()
    vmap = {v["id"]: v for v in all_vehicles()}
    rows = [dict(e) for e in get_db().execute(
        "SELECT * FROM expenses ORDER BY date DESC, id DESC").fetchall()
        if calc.in_range(e["date"], start, end) and (not vid or e["vehicle_id"] == vid)]
    by_cat = {}
    for e in rows:
        by_cat[e["category"]] = by_cat.get(e["category"], 0) + e["amount"]
    return render_template("expenses.html", rows=rows, vmap=vmap, vehicles=list(vmap.values()),
                           by_cat=sorted(by_cat.items(), key=lambda x: -x[1]),
                           total=sum(e["amount"] for e in rows),
                           f=dict(vehicle_id=vid, start=start, end=end))


@app.route("/expenses/new", methods=["GET", "POST"])
@app.route("/expenses/<int:eid>/edit", methods=["GET", "POST"])
def expense_form(eid=None):
    db = get_db()
    existing = db.execute("SELECT * FROM expenses WHERE id = ?", (eid,)).fetchone() if eid else None
    if request.method == "POST":
        f = request.form
        e = dict(vehicle_id=f.get("vehicle_id", type=int), date=f.get("date", ""),
                 category=f.get("category", ""), description=f.get("description", "").strip() or None,
                 amount=to_float(f.get("amount")),
                 odometer=to_float(f.get("odometer")) if f.get("odometer") else None)
        errors = []
        if not db.execute("SELECT 1 FROM vehicles WHERE id=?", (e["vehicle_id"],)).fetchone():
            errors.append("Select a vehicle.")
        try:
            if date.fromisoformat(e["date"]) > date.today():
                errors.append("Date cannot be in the future.")
        except ValueError:
            errors.append("Enter a valid date.")
        if e["category"] not in EXPENSE_CATEGORIES:
            errors.append("Choose a category.")
        if e["amount"] is None or e["amount"] <= 0:
            errors.append("Amount must be greater than zero.")
        if errors:
            for msg in errors:
                flash(msg, "danger")
        else:
            cols = list(e)
            if eid:
                db.execute(f"UPDATE expenses SET {', '.join(c + '=?' for c in cols)} WHERE id=?",
                           [e[c] for c in cols] + [eid])
            else:
                db.execute(f"INSERT INTO expenses ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           [e[c] for c in cols])
            db.commit()
            flash("Expense saved.", "success")
            return redirect(url_for("expenses"))
        form = e
    else:
        form = dict(existing) if existing else dict(
            dict.fromkeys(["category", "description", "amount", "odometer"]),
            date=date.today().isoformat(), vehicle_id=request.args.get("vehicle_id", type=int))
    return render_template("expense_form.html", e=form, eid=eid, vehicles=all_vehicles(),
                           categories=EXPENSE_CATEGORIES)


@app.post("/expenses/<int:eid>/delete")
def expense_delete(eid):
    db = get_db()
    db.execute("DELETE FROM expenses WHERE id = ?", (eid,))
    db.commit()
    flash("Expense deleted.", "info")
    return redirect(url_for("expenses"))


# ------------------------------------------------------- reports & export
def filtered_report():
    vid, start, end = get_filters()
    db = get_db()
    vehicles = all_vehicles()
    data = calc.build_dashboard(vehicles, db.execute("SELECT * FROM refuels").fetchall(),
                                db.execute("SELECT * FROM expenses").fetchall(), start, end, vid)
    return data, vehicles, dict(vehicle_id=vid, start=start, end=end)


@app.route("/reports")
def reports():
    data, vehicles, f = filtered_report()
    return render_template("reports.html", d=data, vehicles=vehicles, f=f,
                           generated=datetime.now().strftime("%Y-%m-%d %H:%M"))


REFUEL_HEADERS = ["Record ID", "Vehicle", "Date/time", "Odometer (km)", "Litres", "Price/L",
                  "Calculated total", "Receipt total", "Full tank", "Driver", "Station",
                  "Distance since last (km)", "Interval km", "Interval litres", "km/L",
                  "L/100km", "Cost per km", "Status", "Override note"]


def refuel_rows(data, vmap):
    for d in sorted(data["timeline"], key=lambda d: (vmap[d["vehicle_id"]]["reg_no"], d["filled_at"])):
        yield [d["id"], vmap[d["vehicle_id"]]["reg_no"], d["filled_at"], d["odometer"], d["litres"],
               d["price_per_litre"], d["calc_total"], d["receipt_total"],
               "Yes" if d["full_tank"] else "No", d["driver"] or "", d["station"] or "",
               d["distance"], d["interval_km"], round(d["interval_litres"], 2) if d["interval_litres"] else None,
               round(d["economy"], 2) if d["economy"] else None,
               round(d["l_per_100"], 2) if d["l_per_100"] else None,
               round(d["cost_per_km"], 2) if d["cost_per_km"] else None,
               d["status"], d["override_note"] or ""]


@app.route("/export/refuels.csv")
def export_refuels_csv():
    data, vehicles, _ = filtered_report()
    vmap = {v["id"]: v for v in vehicles}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(REFUEL_HEADERS)
    w.writerows(refuel_rows(data, vmap))
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=refuels.csv"})


@app.route("/export/expenses.csv")
def export_expenses_csv():
    data, vehicles, _ = filtered_report()
    vmap = {v["id"]: v for v in vehicles}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Expense ID", "Vehicle", "Date", "Category", "Description", "Amount", "Odometer"])
    for e in sorted(data["expenses"], key=lambda e: e["date"]):
        w.writerow([e["id"], vmap[e["vehicle_id"]]["reg_no"], e["date"], e["category"],
                    e["description"] or "", e["amount"], e["odometer"] or ""])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=expenses.csv"})


@app.route("/export/report.xlsx")
def export_xlsx():
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    data, vehicles, f = filtered_report()
    vmap = {v["id"]: v for v in vehicles}
    wb = Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="1F4E79")

    def sheet(ws, headers, rows):
        ws.append(headers)
        for c in ws[1]:
            c.font, c.fill = head_font, head_fill
        for r in rows:
            ws.append(list(r))
        for i, h in enumerate(headers, 1):
            ws.column_dimensions[get_column_letter(i)].width = max(12, len(h) + 2)
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = "Summary"
    ws.append(["Fleet Fuel & Expense Report"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append(["Generated", datetime.now().strftime("%Y-%m-%d %H:%M")])
    ws.append(["Filter", f"vehicle={vmap[f['vehicle_id']]['reg_no'] if f['vehicle_id'] else 'All'}, "
                         f"from={f['start'] or 'start'}, to={f['end'] or 'latest'}"])
    ws.append([])
    kpis = [("Total km served", data["total_km"]), ("Total fuel consumed (L)", data["total_litres"]),
            (f"Total fuel expenditure ({CURRENCY})", data["total_fuel_cost"]),
            (f"Overall cost per km ({CURRENCY})", data["overall_cost_per_km"]),
            ("Fleet average km/L", data["overall_economy"]),
            (f"Other expenses ({CURRENCY})", data["total_maint"]),
            ("Most economical vehicle", data["most_economical"]["reg_no"] if data["most_economical"] else "-"),
            ("Least economical vehicle", data["least_economical"]["reg_no"] if data["least_economical"] else "-"),
            ("Highest total fuel consumer", data["highest_consumer"]["reg_no"] if data["highest_consumer"] else "-")]
    for k, v in kpis:
        ws.append([k, round(v, 2) if isinstance(v, float) else v])
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 22

    wv = wb.create_sheet("Per vehicle")
    sheet(wv, ["Vehicle", "Make/model", "Fuel", "Fills", "km", "Litres", "Fuel cost",
               "km/L", "Cost per km", "Other expenses", "Total cost", "Anomalies"],
          [[p["reg_no"], p["make_model"], p["fuel_type"], p["fills"], round(p["km"]),
            round(p["litres"], 1), round(p["fuel_cost"]),
            round(p["economy"], 2) if p["economy"] else None,
            round(p["cost_per_km"], 2) if p["cost_per_km"] else None,
            round(p["maint_cost"]), round(p["total_cost"]), p["anomalies"]] for p in data["per_vehicle"]])
    n = len(data["per_vehicle"])
    if n:
        ch = BarChart()
        ch.title, ch.y_axis.title = "Fuel economy by vehicle", "km/L"
        ch.add_data(Reference(wv, min_col=8, min_row=1, max_row=n + 1), titles_from_data=True)
        ch.set_categories(Reference(wv, min_col=1, min_row=2, max_row=n + 1))
        ch.width, ch.height = 18, 8
        wv.add_chart(ch, f"A{n + 4}")

    wm = wb.create_sheet("Monthly trend")
    t = data["trend"]
    sheet(wm, ["Month", "Fuel cost", "Litres", "Other expenses", "km/L"],
          zip(t["labels"], t["fuel_cost"], t["litres"], t["maint"], t["economy"]))
    m = len(t["labels"])
    if m:
        lc = LineChart()
        lc.title, lc.y_axis.title = "Monthly fuel cost", CURRENCY
        lc.add_data(Reference(wm, min_col=2, min_row=1, max_row=m + 1), titles_from_data=True)
        lc.set_categories(Reference(wm, min_col=1, min_row=2, max_row=m + 1))
        lc.width, lc.height = 18, 8
        wm.add_chart(lc, "G2")

    sheet(wb.create_sheet("Refuels"), REFUEL_HEADERS, refuel_rows(data, vmap))
    sheet(wb.create_sheet("Expenses"),
          ["Expense ID", "Vehicle", "Date", "Category", "Description", "Amount", "Odometer"],
          [[e["id"], vmap[e["vehicle_id"]]["reg_no"], e["date"], e["category"], e["description"],
            e["amount"], e["odometer"]] for e in sorted(data["expenses"], key=lambda e: e["date"])])
    sheet(wb.create_sheet("Vehicles"),
          ["ID", "Registration", "Make/model", "Type", "Fuel", "Tank (L)", "Active", "Notes"],
          [[v["id"], v["reg_no"], v["make_model"], v["vehicle_type"], v["fuel_type"],
            v["tank_capacity"], "Yes" if v["active"] else "No", v["notes"]] for v in vehicles])

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="fleet_report.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/method")
def method():
    return render_template("method.html", threshold=calc.ANOMALY_THRESHOLD, price_range=PRICE_RANGE)


# ------------------------------------------------------------------ start
def ensure_db():
    fresh = not os.path.exists(DB_PATH)
    conn = connect()
    init_schema(conn)
    if fresh or not conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]:
        from seed import seed
        seed(conn)
        print("Database created and filled with demonstration data.")
    conn.close()


ensure_db()

if __name__ == "__main__":
    app.run(debug=True)
