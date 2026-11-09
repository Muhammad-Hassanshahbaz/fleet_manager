"""Demonstration data: 7 vehicles (6 active + 1 deactivated),
~13 refuels each (May-Nov 2026), partial fills, maintenance expenses,
one suspicious fill (possible fuel theft) and one confirmed odometer override.

Run `python seed.py` to wipe and re-create the demo database.
"""
import random
from datetime import date, datetime, timedelta

from db import connect, init_schema

PETROL = {5: 253.6, 6: 258.4, 7: 266.8, 8: 272.2, 9: 268.9, 10: 275.4, 11: 271.1}
DIESEL = {m: p + 9.5 for m, p in PETROL.items()}

VEHICLES = [
    # reg, model, type, fuel, tank, true km/L, km per trip range, driver, notes, active
    ("LEA-1234", "Toyota Corolla Altis 1.6", "Sedan", "Petrol", 50, 12.6, (300, 480), "Imran Ali", "Head office pool car", 1),
    ("KHI-5521", "Honda Civic 1.8", "Sedan", "Petrol", 47, 11.2, (280, 450), "Bilal Khan", "Regional manager", 1),
    ("ISB-7788", "Suzuki Cultus VXL", "Hatchback", "Petrol", 35, 15.8, (260, 420), "Ahmed Raza", "City deliveries", 1),
    ("LHR-4410", "Toyota Hilux Revo 2.8", "Pickup", "Diesel", 80, 9.1, (450, 720), "Usman Tariq", "Site visits", 1),
    ("KHI-9902", "Suzuki Bolan", "Van", "Petrol", 40, 10.4, (250, 400), "Kashif Mehmood", "Staff pick & drop", 1),
    ("LES-3021", "Isuzu NPR 66", "Truck", "Diesel", 100, 6.2, (380, 560), "Rashid Hussain", "Goods transport", 1),
    ("RWP-1188", "Suzuki Mehran VX", "Hatchback", "Petrol", 30, 16.5, (220, 340), "Naveed Akhtar", "Sold Aug 2026", 0),
]
STATIONS = ["PSO Gulberg", "Shell DHA", "Total Parco Clifton", "Attock F-8", "PSO Motorway M-2", "Shell Saddar"]


def seed(conn):
    rnd = random.Random(2026)
    conn.executescript("DELETE FROM refuels; DELETE FROM expenses; DELETE FROM vehicles;"
                       "DELETE FROM sqlite_sequence;")
    for idx, (reg, model, vtype, fuel, tank, econ, trip, driver, notes, active) in enumerate(VEHICLES):
        odo = rnd.randint(18000, 95000)
        when = datetime(2026, 5, 2, 8) + timedelta(days=rnd.randint(0, 6))
        stop = datetime(2026, 8, 20) if not active else datetime(2026, 11, 7)
        records = []
        used_since_full = 0.0
        n = 0
        # first fill (baseline) - tank brimmed
        records.append([when, odo, round(rnd.uniform(0.5, 0.8) * tank, 2), 1])
        while True:
            when += timedelta(days=rnd.randint(11, 15), hours=rnd.randint(-3, 3))
            if when > stop:
                break
            n += 1
            km = rnd.randint(*trip)
            odo += km
            used = km / (econ * rnd.uniform(0.93, 1.07))
            used_since_full += used
            partial = n % 6 == 3
            if partial:
                litres = used_since_full * rnd.uniform(0.45, 0.6)
                used_since_full -= litres
            else:
                litres = used_since_full
                used_since_full = 0.0
            records.append([when, odo, round(litres, 2), 0 if partial else 1])

        cv = idx + 1
        last_odo = records[-1][1]
        conn.execute("""INSERT INTO vehicles (id, reg_no, make_model, vehicle_type, fuel_type, tank_capacity,
                        next_service_km, next_service_date, notes, active) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (cv, reg, model, vtype, fuel, tank,
                      last_odo + [350, 2400, 4100, -120, 1800, 3000, 5000][idx],
                      (date(2026, 11, 9) + timedelta(days=[30, 60, 9, 45, 5, -3, 90][idx])).isoformat(),
                      notes, active))

        for i, (w, o, litres, full) in enumerate(records):
            price = (DIESEL if fuel == "Diesel" else PETROL)[w.month]
            calc_total = round(litres * price, 2)
            receipt = round(calc_total)  # stations round to the rupee
            note = override = None
            if reg == "KHI-5521" and i == 7:          # suspicious fill (possible theft)
                litres = round(litres * 1.6, 2)
                calc_total = round(litres * price, 2)
                receipt = round(calc_total)
                note = "Driver reported jerry-can top-up"
            conn.execute("""INSERT INTO refuels (vehicle_id, filled_at, odometer, litres, price_per_litre,
                            calc_total, receipt_total, full_tank, driver, station, notes, override_note)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (cv, w.strftime("%Y-%m-%d %H:%M"), o, litres, price, calc_total, receipt, full,
                          driver, rnd.choice(STATIONS), note, override))

        # expenses
        start_odo = records[0][1]
        exp = [
            (records[0][0] + timedelta(days=20), "Maintenance / Service", "Oil & filter change",
             {"Petrol": 6500, "Diesel": 11500}[fuel] * (1.8 if vtype == "Truck" else 1), start_odo + 900),
            (records[0][0] + timedelta(days=95), "Maintenance / Service", "Periodic service + air filter",
             {"Petrol": 9800, "Diesel": 16500}[fuel] * (1.8 if vtype == "Truck" else 1), start_odo + 4200),
            (datetime(2026, 7, 1), "Insurance", "Annual comprehensive insurance (instalment)",
             rnd.choice([18000, 22000, 26000, 35000]), None),
            (records[len(records) // 2][0] + timedelta(days=2), "Toll / Parking", "Motorway tolls (monthly)",
             rnd.randint(1500, 4500), None),
        ]
        if vtype in ("Truck", "Pickup"):
            exp.append((datetime(2026, 9, 14), "Tyres", "2 x new tyres", 64000 if vtype == "Truck" else 46000, None))
        if reg == "ISB-7788":
            exp.append((datetime(2026, 8, 3), "Repair", "Brake pads + disc skimming", 12500, None))
        if reg == "LES-3021":
            exp.append((datetime(2026, 10, 11), "Repair", "Clutch plate replacement", 38000, None))
        if reg == "KHI-9902":
            exp.append((datetime(2026, 9, 2), "Registration / Token Tax", "Annual token tax", 4200, None))
        for d, cat, desc, amt, eodo in exp:
            if d.date() <= (date(2026, 8, 20) if not active else date(2026, 11, 8)):
                conn.execute("INSERT INTO expenses (vehicle_id, date, category, description, amount, odometer)"
                             " VALUES (?,?,?,?,?,?)", (cv, d.date().isoformat(), cat, desc, round(amt), eodo))
    conn.commit()


if __name__ == "__main__":
    c = connect()
    init_schema(c)
    seed(c)
    print("Demo data loaded:",
          c.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0], "vehicles,",
          c.execute("SELECT COUNT(*) FROM refuels").fetchone()[0], "refuels,",
          c.execute("SELECT COUNT(*) FROM expenses").fetchone()[0], "expenses")
