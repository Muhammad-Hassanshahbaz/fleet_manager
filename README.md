# EF101-P03 – Fleet Fuel and Expense Manager

A web application, built in Python, that turns raw refuelling and odometer records for a small fleet into cost and efficiency information for managers.

**Stack:** Python 3.10+ · Flask (web framework) · SQLite (database) · Bootstrap 5 (layout) · Chart.js (charts) · openpyxl (Excel export)

---

## 1. How to run

```bash
# 1. Install the two dependencies
pip install -r requirements.txt

# 2. Start the app
python app.py

# 3. Open in a browser
http://127.0.0.1:5000
```

On first start the app creates `instance/fleet.db` and fills it with demonstration data. To reset the demo data at any time:

```bash
python seed.py
```

> The page styling and charts load from a CDN, so the browser needs internet access. The Python side works offline.

Windows users can double-click `run_windows.bat`.

---

## 2. Platform justification

| Need | Why Python + Flask web app |
|---|---|
| Several users (fleet manager, accounts, drivers) | A web app runs in any browser on PC or phone. Nothing to install on each device. |
| Reliable records | SQLite is a real relational database (tables, keys, indexes) in a single file. No server setup needed. |
| Calculations and aggregation | Python is clear and readable for the economy, cost/km and anomaly logic (`calculations.py`). |
| Charts and dashboard | Chart.js gives interactive charts with hover tooltips in the browser. |
| Export | The Python standard library writes CSV. openpyxl writes a formatted Excel workbook with native charts. |
| Course scope | Small codebase (~1,000 lines), easy to explain and demo. It can move to a cloud database or add logins later. |

Alternatives considered: a **spreadsheet** is quick but weak at validation and multi-user entry. An **Android app** would need a separate backend for shared data. **Desktop (Tkinter)** works on only one machine.

---

## 3. Requirement checklist

| # | Requirement | Where / how |
|---|---|---|
| 1 | Add, edit, deactivate vehicles | **Vehicles** page: Add, ✏️ Edit, ⏸ Deactivate / ▶ Reactivate. Deactivated vehicles keep their history. |
| 2 | Registration/ID, make/model, type, fuel type, optional notes | Vehicle form (plus tank capacity and next service). Registration must be unique. |
| 3 | Refuel: date/time, odometer, litres, price/unit, total paid | **Refuelling → Add refuel** |
| 4 | Auto-calculate total cost, allow receipt verification | Form shows *litres × price* live and compares it to the receipt amount. A mismatch over 2% is held for confirmation. |
| 5 | Reject impossible/suspicious records unless confirmed/corrected | `validate_refuel()` in `app.py`. Impossible values are always rejected. Suspicious values (odometer lower than before, more litres than the tank, odd price, receipt mismatch, huge distance) need a ticked confirmation **and** a reason, which is saved as an audit note. Records can be edited to correct them. |
| 6 | Distance between consecutive refuels | `distance` calculated field (shaded column) |
| 7 | Documented fuel economy method; first record not shown as valid | Full-tank-to-full-tank method. See `calculations.py` and the in-app **Method** page. The first record is labelled *"First record – baseline only"* with no km/L. |
| 8 | Fuel cost per km for valid intervals | `cost_per_km` on each valid interval, plus per vehicle and fleet totals |
| 9 | Maintenance/other expenses in separate module | **Expenses** page (separate `expenses` table) with 8 categories and a category chart |
| 10 | Date filtering and vehicle filtering | Filter bar on Dashboard, Refuelling, Expenses, Reports. Exports respect the filter. |
| 11 | Dashboard: total km, fuel consumed, fuel spend, overall cost/km, most and least economical, highest consumer, monthly fuel cost trend | **Dashboard**: 6 KPI cards, 3 highlight cards, trend chart |
| 12 | ≥1 comparison chart across vehicles + ≥1 trend chart | Comparison: km/L by vehicle, cost/km by vehicle, total spend by vehicle. Trend: monthly fuel cost + other expenses + fleet km/L. |
| 13 | ≥5 vehicles, ~10 refuels each | Demo data: 7 vehicles (6 active, 1 sold), 97 refuels (9–15 each), 33 expenses, May–Nov 2026 |
| 14 | Export to CSV/Excel or downloadable report | **Reports & Export**: Excel workbook (6 sheets with charts), refuels CSV, expenses CSV, printable report (save as PDF) |

### Bonus features included
- **Maintenance reminders**: next service by km and/or date. The dashboard lists *due soon* and *overdue* services.
- **Anomaly detection for possible fuel theft**: flags any interval below 75% of the vehicle's median km/L. The demo data includes one on KHI-5521.
- **Driver records**: driver name stored on each refuel.
- **Audit trail**: confirmed suspicious records keep the warning and the user's reason.

---

## 4. Fuel economy method (summary)

1. Sort a vehicle's records by date/time.
2. A **full-tank** fill is a baseline.
3. At the next full-tank fill:
   `km/L = (odometer now − odometer at baseline) ÷ all litres added since the baseline` (partial top-ups included)
   `cost/km = money paid since baseline ÷ km since baseline`
4. The first record has no earlier reading, so it is only a baseline and has no economy figure.
5. Partial fills show distance, but their economy is counted at the next full tank.
6. A confirmed odometer error is excluded and starts a new baseline.

Fleet and per-vehicle figures are **total ÷ total** (weighted averages), not an average of averages.

---

## 5. Concepts to explain (viva notes)

- **Raw data vs information**: raw = odometer, litres, price as entered. Information = "LES-3021 costs Rs 44/km, more than double ISB-7788", which a manager can act on.
- **Calculated fields**: total cost, distance, km/L, L/100km, cost/km. They are computed and not stored, so they never go out of date (shaded columns in the Refuelling table).
- **Database records**: three related tables, `vehicles` 1—∞ `refuels` and `vehicles` 1—∞ `expenses`, linked by `vehicle_id`.
- **Time-series trend**: monthly totals in date order show rising costs and the effect of fuel price changes.
- **Data validation**: checks at entry time (section 3, row 5) protect the quality of every report.
- **Aggregation**: SUM, COUNT and GROUP BY (per vehicle, per month).
- **Average vs total**: the truck is the *highest total consumer* (scale) and also the *least economical* (efficiency). These are two different questions.
- **Manager decisions**: move long routes to efficient vehicles, service or replace poor performers, investigate anomalies, budget from the monthly trend, plan services from reminders.

---

## 6. Project structure

```
fleet_manager/
├── app.py              # Flask routes, validation, exports
├── calculations.py     # economy method, dashboard aggregation, anomalies, reminders
├── db.py               # SQLite schema + connection
├── seed.py             # demonstration data
├── requirements.txt
├── run_windows.bat
├── static/style.css
└── templates/          # Jinja2 HTML pages
    ├── base.html, _filters.html
    ├── dashboard.html, reports.html, method.html
    ├── vehicles.html, vehicle_form.html
    ├── refuels.html, refuel_form.html
    └── expenses.html, expense_form.html
```

## 7. Suggested demo script (5 minutes)

1. **Dashboard**: walk through the KPIs, best/worst vehicle and charts. Filter to *Oct 2026*, then to one vehicle.
2. **Refuelling**: show the shaded calculated columns, the *baseline* first record, a *partial* fill and the red **anomaly** row on KHI-5521.
3. **Add refuel**: enter an odometer *lower* than the last one and show the warning. Tick confirm, add a reason, save, and show it as *Excluded*.
4. **Add refuel**: enter a receipt that doesn't match litres × price and show the warning.
5. **Vehicles**: deactivate a vehicle and show that it disappears from the refuel form but stays in reports.
6. **Expenses**: show the category breakdown.
7. **Reports**: download the Excel workbook and open the charts.
8. **Method**: explain the economy method and the concepts.
