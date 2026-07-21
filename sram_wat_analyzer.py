#!/usr/bin/env python3
"""6T SRAM / WAT compact-model correlation and sensitivity analyzer.

This is an engineering exploration model.  It calibrates simple square-law MOS
devices from WAT Vt and Ids and is intentionally independent of a PDK/ngspice.
Use a foundry BSIM deck for sign-off numbers.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import sys
import webbrowser
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Iterable


@dataclass(frozen=True)
class WatPoint:
    corner: str = "TT"
    pu_vt: float = 0.38       # |Vtp|, V
    pu_ids: float = 45.0      # uA, WAT on-current
    pg_vt: float = 0.37       # V
    pg_ids: float = 80.0      # uA
    pd_vt: float = 0.36       # V
    pd_ids: float = 120.0     # uA


@dataclass(frozen=True)
class MosWat:
    """WAT parameters owned by one physical MOS in the bitcell."""
    vt: float
    ids: float


@dataclass(frozen=True)
class SixTWatCell:
    """Object-oriented WAT description of all six physical bitcell devices."""
    corner: str
    pu1: MosWat
    pu2: MosWat
    pg1: MosWat
    pg2: MosWat
    pd1: MosWat
    pd2: MosWat

    def side(self, index: int) -> WatPoint:
        if index not in (1, 2):
            raise ValueError("side must be 1 or 2")
        pu, pg, pd = (getattr(self, f"{name}{index}") for name in ("pu", "pg", "pd"))
        return WatPoint(self.corner, pu.vt, pu.ids, pg.vt, pg.ids, pd.vt, pd.ids)

    def representative(self) -> WatPoint:
        def avg(kind: str, attr: str) -> float:
            return (getattr(getattr(self, kind+"1"), attr) + getattr(getattr(self, kind+"2"), attr)) / 2
        return WatPoint(self.corner, avg("pu","vt"), avg("pu","ids"), avg("pg","vt"),
                        avg("pg","ids"), avg("pd","vt"), avg("pd","ids"))

    def replace_mos(self, name: str, **changes: float) -> "SixTWatCell":
        return replace(self, **{name: replace(getattr(self, name), **changes)})


@dataclass(frozen=True)
class Config:
    # Fixed generic 28 nm low-power core assumptions.
    wat_vdd: float = 0.90
    nominal_vdd: float = 0.90
    vt_step: float = 0.030
    ids_step_pct: float = 10.0
    vmin_start: float = 0.25
    vmin_stop: float = 1.05
    vmin_step: float = 0.01
    read_snm_limit: float = 0.030
    grid_points: int = 301


@dataclass(frozen=True)
class Tech28nm:
    """Fixed geometry used by the generic 28 nm 6T architecture model."""
    node_nm: int = 28
    topology: str = "6T: 2×PU PMOS + 2×PG NMOS + 2×PD NMOS"
    channel_length_nm: float = 28.0
    pu_width_nm: float = 70.0
    pg_width_nm: float = 100.0
    pd_width_nm: float = 140.0
    nominal_temperature_c: float = 25.0


TECH_28NM = Tech28nm()


def _positive(value: str | float, label: str) -> float:
    x = abs(float(value))
    if not math.isfinite(x) or x <= 0:
        raise ValueError(f"{label} must be a positive finite number")
    return x


def read_wat_csv(path: str | os.PathLike[str]) -> list[WatPoint]:
    required = {"corner", "pu_vt", "pu_ids", "pg_vt", "pg_ids", "pd_vt", "pd_ids"}
    rows: list[WatPoint] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError("WAT CSV missing columns: " + ", ".join(sorted(missing)))
        for line, row in enumerate(reader, 2):
            try:
                rows.append(WatPoint(
                    corner=(row["corner"] or f"row_{line}").strip(),
                    pu_vt=_positive(row["pu_vt"], "pu_vt"),
                    pu_ids=_positive(row["pu_ids"], "pu_ids"),
                    pg_vt=_positive(row["pg_vt"], "pg_vt"),
                    pg_ids=_positive(row["pg_ids"], "pg_ids"),
                    pd_vt=_positive(row["pd_vt"], "pd_vt"),
                    pd_ids=_positive(row["pd_ids"], "pd_ids"),
                ))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"WAT CSV line {line}: {exc}") from exc
    if not rows:
        raise ValueError("WAT CSV has no data rows")
    return rows


class Device:
    """Square-law device calibrated so Idsat(wat_vdd) equals WAT Ids."""

    def __init__(self, vt: float, ids_ua: float, wat_vdd: float):
        self.vt = abs(vt)
        self.ids = ids_ua
        overdrive = max(wat_vdd - self.vt, 0.05)
        self.beta = 2.0 * ids_ua / (overdrive * overdrive)  # uA/V^2

    def current(self, vgs: float, vds: float) -> float:
        vov = vgs - self.vt
        if vov <= 0 or vds <= 0:
            return 0.0
        if vds < vov:
            return self.beta * (vov * vds - 0.5 * vds * vds)
        return 0.5 * self.beta * vov * vov


class Sram6T:
    def __init__(self, wat: WatPoint, cfg: Config):
        self.wat, self.cfg = wat, cfg
        self.pu = Device(wat.pu_vt, wat.pu_ids, cfg.wat_vdd)
        self.pg = Device(wat.pg_vt, wat.pg_ids, cfg.wat_vdd)
        self.pd = Device(wat.pd_vt, wat.pd_ids, cfg.wat_vdd)

    def _balance(self, vin: float, vout: float, vdd: float, mode: str) -> float:
        # positive means the node is charged upward
        up = self.pu.current(vdd - vin, vdd - vout)
        down = self.pd.current(vin, vout)
        if mode == "read":
            up += self.pg.current(vdd - vout, vdd - vout)  # WL=BL=VDD
        return up - down

    def transfer(self, vin: float, vdd: float, mode: str = "hold") -> float:
        lo, hi = 0.0, vdd
        f_lo = self._balance(vin, lo, vdd, mode)
        f_hi = self._balance(vin, hi, vdd, mode)
        if f_lo <= 0:
            return 0.0
        if f_hi >= 0:
            return vdd
        for _ in range(48):
            mid = (lo + hi) / 2
            if self._balance(vin, mid, vdd, mode) > 0:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    def vtc(self, vdd: float, mode: str = "hold", points: int | None = None) -> list[tuple[float, float]]:
        n = points or self.cfg.grid_points
        return [(vdd * i / (n - 1), self.transfer(vdd * i / (n - 1), vdd, mode)) for i in range(n)]

    def trip_point(self, vdd: float, mode: str) -> float:
        lo, hi = 0.0, vdd
        for _ in range(50):
            mid = (lo + hi) / 2
            if self.transfer(mid, vdd, mode) > mid:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    def snm(self, vdd: float, mode: str = "read") -> float:
        """Maximum-square SNM for a symmetric hold/read butterfly (V)."""
        if vdd <= 0:
            return 0.0
        m = self.trip_point(vdd, mode)

        def fits(side: float) -> bool:
            if m - side < 0 or m + side > vdd:
                return False
            # Square touches the metastable point and must remain inside both
            # monotonic VTC boundaries of the upper butterfly lobe.
            return (self.transfer(m - side, vdd, mode) >= m + side and
                    self.transfer(m + side, vdd, mode) <= m - side)

        lo, hi = 0.0, min(m, vdd - m)
        for _ in range(42):
            mid = (lo + hi) / 2
            if fits(mid):
                lo = mid
            else:
                hi = mid
        return lo

    @staticmethod
    def _access_current(pg: Device, node: float, bitline: float, wl: float) -> float:
        # Signed current entering node through a symmetric NMOS access device.
        if bitline >= node:
            return pg.current(wl - node, bitline - node)
        return -pg.current(wl - bitline, node - bitline)

    def operate(self, vdd: float, operation: str) -> tuple[float, float]:
        """Damped DC relaxation of the coupled 6T cell; returns (Q, QB)."""
        if operation == "read":
            q, qb, bl, blb = 0.0, vdd, vdd, vdd
        elif operation == "write":
            q, qb, bl, blb = vdd, 0.0, 0.0, vdd  # write Q=0
        else:
            raise ValueError(operation)
        max_i = max(self.wat.pu_ids, self.wat.pg_ids, self.wat.pd_ids, 1.0)
        gain = 0.025 * max(vdd, 0.15) / max_i
        for _ in range(5000):
            iq = (self.pu.current(vdd - qb, vdd - q) - self.pd.current(qb, q) +
                  self._access_current(self.pg, q, bl, vdd))
            iqb = (self.pu.current(vdd - q, vdd - qb) - self.pd.current(q, qb) +
                   self._access_current(self.pg, qb, blb, vdd))
            nq = min(vdd, max(0.0, q + gain * iq))
            nqb = min(vdd, max(0.0, qb + gain * iqb))
            if max(abs(nq - q), abs(nqb - qb)) < max(1e-10, vdd * 1e-8):
                q, qb = nq, nqb
                break
            q, qb = nq, nqb
        return q, qb

    def read_vmin(self) -> float | None:
        for vdd in frange(self.cfg.vmin_start, self.cfg.vmin_stop, self.cfg.vmin_step):
            q, qb = self.operate(vdd, "read")
            if q < 0.35 * vdd and qb > 0.65 * vdd and self.snm(vdd, "read") >= self.cfg.read_snm_limit:
                return vdd
        return None

    def write_vmin(self) -> float | None:
        for vdd in frange(self.cfg.vmin_start, self.cfg.vmin_stop, self.cfg.vmin_step):
            q, qb = self.operate(vdd, "write")
            if q < 0.20 * vdd and qb > 0.80 * vdd:
                return vdd
        return None


class WtZeroBitVminTest:
    """Object-oriented WT 0-bit Vmin flow for one mismatched 6T bitcell."""

    TEST_NAMES = ("Scan4N", "Select_Write", "Select_Read")

    def __init__(self, cell: SixTWatCell, cfg: Config):
        self.cell = cell
        self.cfg = cfg
        self.sides = [Sram6T(cell.side(i), cfg) for i in (1, 2)]

    def _write_pass(self, model: Sram6T, vdd: float) -> bool:
        q, qb = model.operate(vdd, "write")
        return q < .20*vdd and qb > .80*vdd

    def _read_pass(self, model: Sram6T, vdd: float) -> bool:
        q, qb = model.operate(vdd, "read")
        return (q < .35*vdd and qb > .65*vdd and
                model.snm(vdd, "read") >= self.cfg.read_snm_limit)

    def evaluate(self, test_name: str, vdd: float) -> dict:
        # Side 1 and side 2 represent the two logical data polarities in the
        # cross-coupled cell. A 0-bit pass requires every required phase pass.
        write = [self._write_pass(model, vdd) for model in self.sides]
        read = [self._read_pass(model, vdd) for model in self.sides]
        if test_name == "Select_Write":
            phases = {"Write-0": write[0], "Write-1": write[1]}
        elif test_name == "Select_Read":
            phases = {"Read-0": read[0], "Read-1": read[1]}
        elif test_name == "Scan4N":
            phases = {
                "↑ Write-0": write[0],
                "↑ Read-0 / Write-1": read[0] and write[1],
                "↓ Read-1 / Write-0": read[1] and write[0],
                "↓ Read-0": read[0],
            }
        else:
            raise ValueError(f"unknown WT test: {test_name}")
        failed = [name for name, passed in phases.items() if not passed]
        return {"pass": not failed, "failed_phase_count": len(failed),
                "failed_phases": failed, "phases": phases}

    def vmin(self, test_name: str) -> dict:
        last_failure: dict | None = None
        for vdd in frange(self.cfg.vmin_start, self.cfg.vmin_stop, self.cfg.vmin_step):
            status = self.evaluate(test_name, vdd)
            if status["pass"]:
                return {"test": test_name, "vmin_v": vdd, "zero_bit_pass": True,
                        "failed_phase_count": 0, "phases_at_vmin": status["phases"]}
            last_failure = status
        return {"test": test_name, "vmin_v": None, "zero_bit_pass": False,
                "failed_phase_count": None if last_failure is None else last_failure["failed_phase_count"],
                "phases_at_vmin": {}}

    def run(self) -> list[dict]:
        return [self.vmin(name) for name in self.TEST_NAMES]


def frange(start: float, stop: float, step: float) -> Iterable[float]:
    count = int(math.floor((stop - start) / step + 1e-9))
    for i in range(count + 1):
        yield round(start + i * step, 10)


def variants(wat: WatPoint, cfg: Config, device: str) -> list[tuple[str, WatPoint]]:
    vt_name, ids_name = f"{device}_vt", f"{device}_ids"
    base_vt, base_ids = getattr(wat, vt_name), getattr(wat, ids_name)
    frac = cfg.ids_step_pct / 100.0
    return [
        ("Baseline", wat),
        (f"Vt -{cfg.vt_step*1000:.0f}mV", replace(wat, **{vt_name: max(0.01, base_vt - cfg.vt_step)})),
        (f"Vt +{cfg.vt_step*1000:.0f}mV", replace(wat, **{vt_name: base_vt + cfg.vt_step})),
        (f"Ids -{cfg.ids_step_pct:g}%", replace(wat, **{ids_name: base_ids * (1-frac)})),
        (f"Ids +{cfg.ids_step_pct:g}%", replace(wat, **{ids_name: base_ids * (1+frac)})),
    ]


def metric(model: Sram6T, cfg: Config) -> dict[str, float | None]:
    return {
        "hold_snm_mv": 1000 * model.snm(cfg.nominal_vdd, "hold"),
        "read_snm_mv": 1000 * model.snm(cfg.nominal_vdd, "read"),
        "read_vmin_v": model.read_vmin(),
        "write_vmin_v": model.write_vmin(),
    }


def cell_metric(cell: SixTWatCell, cfg: Config) -> dict[str, float | None]:
    """Conservative half-cell mismatch metric: lower SNM and higher Vmin win."""
    sides = [metric(Sram6T(cell.side(i), cfg), cfg) for i in (1, 2)]
    def worst_high(key: str) -> float | None:
        vals = [s[key] for s in sides]
        return None if any(v is None for v in vals) else max(vals)  # type: ignore[arg-type]
    return {
        "hold_snm_mv": min(s["hold_snm_mv"] for s in sides),
        "read_snm_mv": min(s["read_snm_mv"] for s in sides),
        "read_vmin_v": worst_high("read_vmin_v"),
        "write_vmin_v": worst_high("write_vmin_v"),
    }


def analyze(wat: WatPoint, cfg: Config) -> dict:
    groups = {}
    for dev in ("pu", "pg", "pd"):
        items = []
        for label, point in variants(wat, cfg, dev):
            model = Sram6T(point, cfg)
            items.append({"label": label, "wat": asdict(point), "metrics": metric(model, cfg),
                          "read_vtc": model.vtc(cfg.nominal_vdd, "read", 161)})
        groups[dev.upper()] = items
    return {"technology": asdict(TECH_28NM), "wat": asdict(wat), "config": asdict(cfg), "groups": groups}


def analyze_six_mos(cell: SixTWatCell, cfg: Config) -> dict:
    """Full report plus per-physical-MOS sensitivity for an OO six-device cell."""
    result = analyze(cell.representative(), cfg)
    result["cell"] = {
        "corner": cell.corner,
        "mos": {name.upper(): asdict(getattr(cell, name)) for name in ("pu1","pu2","pg1","pg2","pd1","pd2")},
        "method": "two half-cell compact models; report uses lower SNM and higher Vmin",
    }
    baseline = cell_metric(cell, cfg)
    sensitivity = {}
    for name in ("pu1","pu2","pg1","pg2","pd1","pd2"):
        mos = getattr(cell, name); items = []
        scenarios = [
            ("Baseline", cell),
            (f"Vt -{cfg.vt_step*1000:.0f}mV", cell.replace_mos(name, vt=max(.01, mos.vt-cfg.vt_step))),
            (f"Vt +{cfg.vt_step*1000:.0f}mV", cell.replace_mos(name, vt=mos.vt+cfg.vt_step)),
            (f"Ids -{cfg.ids_step_pct:g}%", cell.replace_mos(name, ids=mos.ids*(1-cfg.ids_step_pct/100))),
            (f"Ids +{cfg.ids_step_pct:g}%", cell.replace_mos(name, ids=mos.ids*(1+cfg.ids_step_pct/100))),
        ]
        for label, variant in scenarios:
            items.append({"label": label, "metrics": cell_metric(variant, cfg)})
        sensitivity[name.upper()] = items
    result["cell"]["baseline_metrics"] = baseline
    result["mos_sensitivity"] = sensitivity
    result["wt_test_0bit"] = WtZeroBitVminTest(cell, cfg).run()
    return result


def validate_config(cfg: Config) -> None:
    positive = ("wat_vdd", "nominal_vdd", "vt_step", "ids_step_pct", "vmin_step", "read_snm_limit")
    for name in positive:
        value = getattr(cfg, name)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be greater than zero")
    if cfg.ids_step_pct >= 100:
        raise ValueError("ids_step_pct must be below 100")
    if cfg.vmin_start <= 0 or cfg.vmin_stop < cfg.vmin_start:
        raise ValueError("Vmin range must satisfy 0 < start <= stop")


COLORS = ["#111827", "#2563eb", "#dc2626", "#7c3aed", "#059669"]


def _fmt(value: float | None, digits: int = 3) -> str:
    return "N/A" if value is None else f"{value:.{digits}f}"


def butterfly_svg(items: list[dict], vdd: float, width: int = 640, height: int = 430) -> str:
    left, top, right, bottom = 62, 25, 18, 55
    pw, ph = width-left-right, height-top-bottom
    def xy(x: float, y: float) -> tuple[float, float]:
        return left + x/vdd*pw, top + (1-y/vdd)*ph
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Read butterfly curve">',
             '<rect width="100%" height="100%" fill="white"/>']
    for i in range(6):
        val = vdd*i/5; x, y = xy(val, val)
        parts.append(f'<path d="M {x:.1f} {top} V {top+ph} M {left} {y:.1f} H {left+pw}" stroke="#e5e7eb" stroke-width="1"/>')
        parts.append(f'<text x="{x:.1f}" y="{top+ph+20}" text-anchor="middle" font-size="11">{val:.2f}</text>')
        parts.append(f'<text x="{left-9}" y="{y+4:.1f}" text-anchor="end" font-size="11">{val:.2f}</text>')
    for idx, item in enumerate(items):
        pts = item["read_vtc"]
        p1 = " ".join(f"{xy(x,y)[0]:.1f},{xy(x,y)[1]:.1f}" for x,y in pts)
        p2 = " ".join(f"{xy(y,x)[0]:.1f},{xy(y,x)[1]:.1f}" for x,y in pts)
        c = COLORS[idx]
        parts.append(f'<polyline points="{p1}" fill="none" stroke="{c}" stroke-width="1.7"/>')
        parts.append(f'<polyline points="{p2}" fill="none" stroke="{c}" stroke-width="1.7" opacity=".82"/>')
        lx = left+10+(idx%2)*235; ly = top+17+(idx//2)*20
        parts.append(f'<path d="M {lx} {ly-4} h 22" stroke="{c}" stroke-width="3"/><text x="{lx+28}" y="{ly}" font-size="11">{html.escape(item["label"])}</text>')
    parts += [f'<text x="{left+pw/2}" y="{height-8}" text-anchor="middle" font-size="13">Q (V)</text>',
              f'<text x="15" y="{top+ph/2}" transform="rotate(-90 15 {top+ph/2})" text-anchor="middle" font-size="13">QB (V)</text>', '</svg>']
    return "".join(parts)


def bar_svg(items: list[dict], key: str, title: str, unit: str, width: int = 640, height: int = 260) -> str:
    vals = [x["metrics"][key] for x in items]
    finite = [v for v in vals if v is not None]
    ymax = max(finite or [1.0]) * 1.18 or 1.0
    left, top, bottom = 55, 35, 55
    pw, ph = width-left-15, height-top-bottom
    barw = pw/len(items)*0.58
    p = [f'<svg viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/>',
         f'<text x="{width/2}" y="18" text-anchor="middle" font-size="14" font-weight="600">{html.escape(title)}</text>',
         f'<path d="M {left} {top} V {top+ph} H {left+pw}" fill="none" stroke="#374151"/>']
    for i, (item, val) in enumerate(zip(items, vals)):
        x = left + (i+.5)*pw/len(items); h = 0 if val is None else val/ymax*ph
        p.append(f'<rect x="{x-barw/2:.1f}" y="{top+ph-h:.1f}" width="{barw:.1f}" height="{h:.1f}" fill="{COLORS[i]}" opacity=".86"/>')
        p.append(f'<text x="{x:.1f}" y="{top+ph-h-5:.1f}" text-anchor="middle" font-size="11">{_fmt(val)}</text>')
        p.append(f'<text x="{x:.1f}" y="{top+ph+17}" transform="rotate(18 {x:.1f} {top+ph+17})" text-anchor="start" font-size="10">{html.escape(item["label"])}</text>')
    p.append(f'<text x="13" y="{top+ph/2}" transform="rotate(-90 13 {top+ph/2})" text-anchor="middle" font-size="11">{unit}</text></svg>')
    return "".join(p)


def architecture_svg(width: int = 760, height: int = 315) -> str:
    """Self-contained schematic-style view of the fixed 28 nm 6T topology."""
    return f'''<svg viewBox="0 0 {width} {height}" role="img" aria-label="28 nm 6T SRAM architecture">
    <rect width="100%" height="100%" fill="white"/>
    <text x="380" y="24" text-anchor="middle" font-size="16" font-weight="700">Generic 28 nm 6T SRAM bitcell</text>
    <path d="M120 55 V260 M640 55 V260 M120 75 H235 M525 75 H640 M120 240 H235 M525 240 H640" stroke="#334155" stroke-width="2" fill="none"/>
    <text x="105" y="48" font-size="12">BL</text><text x="635" y="48" font-size="12">BLB</text>
    <path d="M235 64 h55 v22 h-55 z M470 64 h55 v22 h-55 z" fill="#fee2e2" stroke="#dc2626"/>
    <path d="M235 229 h55 v22 h-55 z M470 229 h55 v22 h-55 z" fill="#dbeafe" stroke="#2563eb"/>
    <path d="M145 147 h55 v22 h-55 z M560 147 h55 v22 h-55 z" fill="#dcfce7" stroke="#059669"/>
    <text x="262" y="80" text-anchor="middle" font-size="11">PU1 PMOS</text><text x="497" y="80" text-anchor="middle" font-size="11">PU2 PMOS</text>
    <text x="262" y="245" text-anchor="middle" font-size="11">PD1 NMOS</text><text x="497" y="245" text-anchor="middle" font-size="11">PD2 NMOS</text>
    <text x="172" y="163" text-anchor="middle" font-size="11">PG1</text><text x="587" y="163" text-anchor="middle" font-size="11">PG2</text>
    <path d="M290 75 H380 V145 M470 75 H380 M290 240 H380 V170 M470 240 H380 M200 158 H340 M420 158 H560" stroke="#334155" stroke-width="2" fill="none"/>
    <circle cx="340" cy="158" r="6" fill="#111827"/><circle cx="420" cy="158" r="6" fill="#111827"/>
    <text x="330" y="144" font-size="13" font-weight="700">Q</text><text x="427" y="144" font-size="13" font-weight="700">QB</text>
    <path d="M340 158 C340 112 430 112 470 92 M420 158 C420 204 330 204 290 222" stroke="#7c3aed" stroke-width="1.8" fill="none" stroke-dasharray="5 4"/>
    <path d="M105 285 H655" stroke="#059669" stroke-width="2"/><text x="380" y="303" text-anchor="middle" font-size="12">WL controls PG1 / PG2</text>
    <text x="380" y="275" text-anchor="middle" font-size="11" fill="#475569">L=28 nm · WPU=70 nm · WPG=100 nm · WPD=140 nm</text>
    </svg>'''


def write_outputs(result: dict, out_dir: str | os.PathLike[str]) -> Path:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    rows = []
    for dev, items in result["groups"].items():
        for item in items:
            rows.append({"device": dev, "scenario": item["label"], **item["wat"], **item["metrics"]})
    with open(out/"sram_wat_results.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    with open(out/"sram_wat_results.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    if "mos_sensitivity" in result:
        mos_rows_csv = []
        for name, items in result["mos_sensitivity"].items():
            source = result["cell"]["mos"][name]
            for item in items:
                mos_rows_csv.append({"mos": name, "scenario": item["label"], "input_vt_v": source["vt"],
                                     "input_ids_ua": source["ids"], **item["metrics"]})
        with open(out/"sram_mos_results.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer=csv.DictWriter(f,fieldnames=list(mos_rows_csv[0])); writer.writeheader(); writer.writerows(mos_rows_csv)
    if "wt_test_0bit" in result:
        wt_rows=[]
        for item in result["wt_test_0bit"]:
            wt_rows.append({"test":item["test"],"vmin_v":item["vmin_v"],
                            "zero_bit_pass":item["zero_bit_pass"],
                            "failed_phase_count":item["failed_phase_count"],
                            "phases_at_vmin":"; ".join(k for k,v in item["phases_at_vmin"].items() if v)})
        with open(out/"wt_test_0bit_vmin.csv","w",newline="",encoding="utf-8-sig") as f:
            writer=csv.DictWriter(f,fieldnames=list(wt_rows[0])); writer.writeheader(); writer.writerows(wt_rows)

    cfg = result["config"]; wat = result["wat"]; tech = result["technology"]
    sections = []
    for dev, items in result["groups"].items():
        baseline = items[0]["metrics"]
        trs = "".join("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in
            [x["label"], f'{x["wat"][dev.lower()+"_vt"]:.3f}', f'{x["wat"][dev.lower()+"_ids"]:.2f}',
             f'{x["metrics"]["hold_snm_mv"]:.2f}', f'{x["metrics"]["read_snm_mv"]:.2f}',
             f'{x["metrics"]["read_snm_mv"]-baseline["read_snm_mv"]:+.2f}',
             _fmt(x["metrics"]["read_vmin_v"],2),
             "N/A" if x["metrics"]["read_vmin_v"] is None or baseline["read_vmin_v"] is None else f'{x["metrics"]["read_vmin_v"]-baseline["read_vmin_v"]:+.2f}',
             _fmt(x["metrics"]["write_vmin_v"],2),
             "N/A" if x["metrics"]["write_vmin_v"] is None or baseline["write_vmin_v"] is None else f'{x["metrics"]["write_vmin_v"]-baseline["write_vmin_v"]:+.2f}']) + "</tr>" for x in items)
        sections.append(f'''<section><h2>{dev} sensitivity</h2>
        <div class="grid"><div>{butterfly_svg(items,cfg["nominal_vdd"])}</div>
        <div>{bar_svg(items,"read_snm_mv",dev+" Read SNM","mV")}</div>
        <div>{bar_svg(items,"read_vmin_v",dev+" Read Vmin","V")}</div>
        <div>{bar_svg(items,"write_vmin_v",dev+" Write Vmin","V")}</div></div>
        <table><thead><tr><th>Scenario</th><th>Vt (V)</th><th>Ids (uA)</th><th>Hold SNM (mV)</th><th>Read SNM (mV)</th><th>ΔRSNM</th><th>Read Vmin (V)</th><th>ΔRVmin</th><th>Write Vmin (V)</th><th>ΔWVmin</th></tr></thead><tbody>{trs}</tbody></table></section>''')
    ratio = f'Cell ratio PD/PG={wat["pd_ids"]/wat["pg_ids"]:.3f}; Pull-up ratio PG/PU={wat["pg_ids"]/wat["pu_ids"]:.3f}'
    wat_rows = "".join(f'<tr><td>{d}</td><td>{wat[d.lower()+"_vt"]:.3f}</td><td>{wat[d.lower()+"_ids"]:.2f}</td><td>{wat[d.lower()+"_ids"]/sum(wat[x+"_ids"] for x in ("pu","pg","pd")):.3f}</td></tr>' for d in ("PU","PG","PD"))
    individual_section = ""
    if "cell" in result:
        mos_rows = "".join(f'<tr><td>{name}</td><td>{values["vt"]:.3f}</td><td>{values["ids"]:.2f}</td></tr>' for name,values in result["cell"]["mos"].items())
        sensitivity_blocks = []
        for name, items in result["mos_sensitivity"].items():
            base = items[0]["metrics"]
            body = "".join("<tr>"+"".join(f"<td>{html.escape(str(v))}</td>" for v in [
                x["label"], f'{x["metrics"]["read_snm_mv"]:.2f}', f'{x["metrics"]["read_snm_mv"]-base["read_snm_mv"]:+.2f}',
                _fmt(x["metrics"]["read_vmin_v"],2), _fmt(x["metrics"]["write_vmin_v"],2)])+"</tr>" for x in items)
            sensitivity_blocks.append(f'<div><h3>{name}</h3><table><thead><tr><th>Scenario</th><th>Read SNM (mV)</th><th>ΔRSNM</th><th>Read Vmin</th><th>Write Vmin</th></tr></thead><tbody>{body}</tbody></table></div>')
        individual_section = f'''<section><h2>Six physical MOS objects</h2>
        <p>每顆 MOS 各自保存 Vt/Ids。Mismatch 採兩側 half-cell 分析，整體結果取較低 SNM 與較高 Vmin。</p>
        <table><thead><tr><th>MOS object</th><th>Vt (V)</th><th>Ids (µA)</th></tr></thead><tbody>{mos_rows}</tbody></table>
        <div class="grid">{''.join(sensitivity_blocks)}</div></section>'''
    wt_section = ""
    if "wt_test_0bit" in result:
        wt_defs={
            "Scan4N":"W0 → R0/W1 → R1/W0 → R0；全部 phase 通過",
            "Select_Write":"Write-0 與 Write-1 都達到 20%/80% rail",
            "Select_Read":"Read-0 與 Read-1 都保持 35%/65% rail 且符合 SNM 下限",
        }
        wt_rows="".join(f'<tr><td>{x["test"]}</td><td>{_fmt(x["vmin_v"],2)}</td><td>{"PASS" if x["zero_bit_pass"] else "OUT OF RANGE"}</td><td>{html.escape(wt_defs[x["test"]])}</td></tr>' for x in result["wt_test_0bit"])
        wt_section=f'''<section><h2>WT Test 0-Bit Vmin</h2>
        <p>單一 6T bitcell 的 0-Bit 定義：兩個資料方向與該測項所有 phase 均通過；掃描範圍 {cfg["vmin_start"]:.2f}–{cfg["vmin_stop"]:.2f} V，step={cfg["vmin_step"]:.3f} V。</p>
        <table><thead><tr><th>WT mode</th><th>0-Bit Vmin (V)</th><th>Status</th><th>Pass condition</th></tr></thead><tbody>{wt_rows}</tbody></table></section>'''
    doc = f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>6T SRAM × WAT 分析</title>
    <style>
    :root{{font:100%/1.5 system-ui,-apple-system,"Segoe UI","Microsoft JhengHei",sans-serif;color:#1d1d1f;background:#f5f5f7;font-optical-sizing:auto}}
    *{{box-sizing:border-box}} body{{margin:0;padding:clamp(1.25rem,4vw,3rem);background:radial-gradient(circle at 85% 0,#e8f2ff 0,transparent 28rem),#f5f5f7}}
    main{{max-width:1380px;margin:auto}} h1{{font-size:clamp(2rem,4vw,3.5rem);line-height:1.05;letter-spacing:-.035em;margin:.5rem 0 .35rem}} h2{{letter-spacing:-.018em;margin-top:0}} h3{{letter-spacing:-.01em}}
    .note,.summary,section{{background:rgba(255,255,255,.82);backdrop-filter:blur(22px) saturate(160%);border:1px solid rgba(255,255,255,.75);border-radius:1.25rem;box-shadow:0 1px 2px #0000000a,0 12px 36px #0000000d;padding:clamp(1rem,2.4vw,1.65rem);margin:1rem 0}}
    .note{{border-left:4px solid #007aff}} .summary{{color:#3a3a3c}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,30rem),1fr));gap:1rem}}
    svg{{width:100%;height:auto;border:1px solid #e5e5ea;border-radius:1rem;overflow:hidden}} table{{border-collapse:separate;border-spacing:0;width:100%;margin-top:.8rem;font-size:.82rem;overflow:hidden}}
    th,td{{padding:.65rem .55rem;border-bottom:1px solid #e5e5ea;text-align:right;font-variant-numeric:tabular-nums}} th{{color:#6e6e73;font-size:.74rem;font-weight:650;letter-spacing:.015em}} th:first-child,td:first-child{{text-align:left}} tbody tr:last-child td{{border-bottom:0}} code{{background:#f2f2f7;padding:.18rem .38rem;border-radius:.35rem}}
    @media(prefers-reduced-transparency:reduce){{.note,.summary,section{{background:#fff;backdrop-filter:none;border-color:#d2d2d7}}}}
    @media(prefers-contrast:more){{.note,.summary,section{{background:#fff;border:2px solid #1d1d1f}}}}
    </style></head><body><main>
    <h1>28 nm 6T SRAM × WAT PU/PG/PD 分析</h1><p>Corner: <b>{html.escape(wat["corner"])}</b> · SRAM VDD={cfg["nominal_vdd"]:.3f} V · {ratio}</p>
    <div class="note"><b>28 nm 固定模型：</b>{html.escape(tech["topology"])}；L={tech["channel_length_nm"]:g} nm，WPU/WPG/WPD={tech["pu_width_nm"]:g}/{tech["pg_width_nm"]:g}/{tech["pd_width_nm"]:g} nm，T={tech["nominal_temperature_c"]:g} °C。這是 generic 28 nm 推演模型；沒有 foundry PDK，因此不宣稱對應特定晶圓廠或取代量產 sign-off。</div>
    <section>{architecture_svg()}</section>
    {wt_section}
    {individual_section}
    <div class="summary"><b>判定：</b>Read Vmin 是 read SNM ≥ {cfg["read_snm_limit"]*1000:.0f} mV 且儲存節點保持 35%/65% rail 的最低 VDD；Write Vmin 是寫 0 後 Q&lt;20% VDD、QB&gt;80% VDD 的最低 VDD。掃描 {cfg["vmin_start"]:.2f}–{cfg["vmin_stop"]:.2f} V，step={cfg["vmin_step"]:.3f} V。Vt ±{cfg["vt_step"]*1000:.0f} mV、Ids ±{cfg["ids_step_pct"]:g}% 每次只改一項。<br><b>判讀方向：</b>ΔRSNM 為正代表穩定度改善；ΔRVmin/ΔWVmin 為負代表可在更低電壓操作、屬改善。</div>
    <section><h2>WAT Vt / Ids comparison</h2><table><thead><tr><th>Device</th><th>Vt (V)</th><th>Ids (uA)</th><th>Normalized Ids</th></tr></thead><tbody>{wat_rows}</tbody></table><p>{ratio}</p></section>
    {''.join(sections)}
    <p>Raw data: <code>sram_wat_results.csv</code>, <code>sram_wat_results.json</code></p></main></body></html>'''
    report = out/"sram_wat_report.html"; report.write_text(doc, encoding="utf-8")
    return report


def run_analysis(csv_path: str, out_dir: str, cfg: Config, corner: str | None = None) -> list[Path]:
    validate_config(cfg)
    points = read_wat_csv(csv_path)
    if corner:
        points = [p for p in points if p.corner.lower() == corner.lower()]
        if not points:
            raise ValueError(f"corner not found: {corner}")
    reports = []
    multi = len(points) > 1
    for p in points:
        target = Path(out_dir)/p.corner if multi else Path(out_dir)
        reports.append(write_outputs(analyze(p, cfg), target))
    return reports


def _launch_legacy_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk(); root.title("28 nm 6T SRAM × WAT Analyzer"); root.geometry("820x720"); root.minsize(760, 680)
    values = {"out": tk.StringVar(value=str(Path.cwd()/"output")), "corner": tk.StringVar(value="TT")}
    defaults={"pu":("0.38","45"),"pg":("0.37","80"),"pd":("0.36","120")}
    wat_values={}
    for dev in ("pu1","pu2","pg1","pg2","pd1","pd2"):
        vt,ids=defaults[dev[:2]]
        wat_values[f"{dev}_vt"]=tk.StringVar(value=vt); wat_values[f"{dev}_ids"]=tk.StringVar(value=ids)
    numeric = {k: tk.StringVar(value=str(v)) for k,v in asdict(Config()).items() if k != "grid_points"}
    frame = ttk.Frame(root, padding=18); frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="28 nm 6T SRAM × WAT PU / PG / PD", font=("Segoe UI", 17, "bold")).grid(row=0,column=0,columnspan=4,sticky="w")
    ttk.Label(frame, text="固定 28 nm 6T 架構；手動輸入 WAT，Python 產生 SNM 與 R/W Vmin（不使用 SPICE）",
              foreground="#475569").grid(row=1,column=0,columnspan=4,sticky="w",pady=(3,14))

    wat_box = ttk.LabelFrame(frame, text="WAT 手動輸入", padding=12)
    wat_box.grid(row=2,column=0,columnspan=4,sticky="ew",pady=5)
    ttk.Label(wat_box,text="Corner / Lot ID").grid(row=0,column=0,sticky="w",padx=(0,8))
    ttk.Entry(wat_box,textvariable=values["corner"],width=16).grid(row=0,column=1,sticky="w")
    ttk.Label(wat_box,text="Q side",font=("Segoe UI",10,"bold")).grid(row=1,column=0,columnspan=3,pady=(10,3))
    ttk.Label(wat_box,text="QB side",font=("Segoe UI",10,"bold")).grid(row=1,column=3,columnspan=3,pady=(10,3))
    # Cards are placed like the physical cell: PU top, PG center, PD bottom.
    for row,(kind,desc) in enumerate((("pu","PMOS"),("pg","access"),("pd","NMOS")),2):
        for side,col in (("1",0),("2",3)):
            name=kind+side
            card=ttk.LabelFrame(wat_box,text=name.upper(),padding=5); card.grid(row=row,column=col,columnspan=3,padx=6,pady=4,sticky="ew")
            ttk.Label(card,text="Vt (V)").grid(row=0,column=0); ttk.Entry(card,textvariable=wat_values[f"{name}_vt"],width=9).grid(row=0,column=1,padx=(3,9))
            ttk.Label(card,text="Ids (µA)").grid(row=0,column=2); ttk.Entry(card,textvariable=wat_values[f"{name}_ids"],width=9).grid(row=0,column=3,padx=3)
        ttk.Label(wat_box,text={"pu":"VDD — pull-up","pg":"BL/BLB — WL access","pd":"pull-down — GND"}[kind],foreground="#64748b").grid(row=row,column=6,sticky="w")
    ttk.Label(wat_box,text="Q  ↔  cross-coupled  ↔  QB　　PU Vt 可輸入負值，計算使用 |Vtp|。",foreground="#7c3aed").grid(row=5,column=0,columnspan=7,pady=(8,0))

    out_box = ttk.Frame(frame); out_box.grid(row=3,column=0,columnspan=4,sticky="ew",pady=8)
    ttk.Label(out_box,text="報表輸出目錄").grid(row=0,column=0,sticky="w")
    ttk.Entry(out_box,textvariable=values["out"],width=70).grid(row=0,column=1,sticky="ew",padx=8)
    def pick_out():
        value=filedialog.askdirectory()
        if value: values["out"].set(value)
    ttk.Button(out_box,text="瀏覽",command=pick_out).grid(row=0,column=2)
    out_box.columnconfigure(1,weight=1)

    cfg_box = ttk.LabelFrame(frame,text="分析條件",padding=12)
    cfg_box.grid(row=4,column=0,columnspan=4,sticky="ew",pady=5)
    labels = [("wat_vdd","WAT Ids 測試 VDD (V)"),("nominal_vdd","SRAM 分析 VDD (V)"),("vt_step","Vt 調整量 (V)"),("ids_step_pct","Ids 調整量 (%)"),("vmin_start","Vmin 起點 (V)"),("vmin_stop","Vmin 終點 (V)"),("vmin_step","Vmin 步階 (V)"),("read_snm_limit","Read SNM 下限 (V)")]
    for i,(key,label) in enumerate(labels):
        r=i//2; c=(i%2)*2
        ttk.Label(cfg_box,text=label).grid(row=r,column=c,sticky="w",pady=6,padx=(0,8))
        ttk.Entry(cfg_box,textvariable=numeric[key],width=14).grid(row=r,column=c+1,sticky="w",padx=(0,28))
    status = tk.StringVar(value="待命")
    def execute():
        try:
            kwargs={k:float(v.get()) for k,v in numeric.items()}
            cfg=Config(**kwargs)
            validate_config(cfg)
            mos={}
            for name in ("pu1","pu2","pg1","pg2","pd1","pd2"):
                mos[name]=MosWat(_positive(wat_values[f"{name}_vt"].get(),f"{name}_vt"),
                                 _positive(wat_values[f"{name}_ids"].get(),f"{name}_ids"))
            point=SixTWatCell(values["corner"].get().strip() or "Manual",**mos)
            status.set("分析中…"); root.update_idletasks()
            report=write_outputs(analyze_six_mos(point,cfg),values["out"].get())
            status.set(f"完成：{point.corner}；{report}")
            webbrowser.open(report.resolve().as_uri())
        except Exception as exc:
            status.set("失敗"); messagebox.showerror("分析失敗",str(exc))
    ttk.Button(frame,text="產生並開啟分析報表",command=execute).grid(row=5,column=0,columnspan=4,pady=(18,8),ipadx=24,ipady=6)
    ttk.Label(frame,textvariable=status,wraplength=740).grid(row=6,column=0,columnspan=4)
    ttk.Label(frame,text="輸出：SNM、R/W Vmin、WT 0-Bit Scan4N / Select_Write / Select_Read、CSV、JSON。",
              foreground="#475569").grid(row=7,column=0,columnspan=4,pady=12)
    frame.columnconfigure(1,weight=1); frame.columnconfigure(3,weight=1); root.mainloop()


def launch_gui() -> None:
    """Apple-inspired desktop UI with direct manipulation of the 6T diagram."""
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    BG, CARD, TEXT, SECONDARY = "#F5F5F7", "#FFFFFF", "#1D1D1F", "#6E6E73"
    BLUE, BLUE_DARK, BORDER, GREEN, RED = "#007AFF", "#0062CC", "#D2D2D7", "#34C759", "#FF3B30"
    root = tk.Tk()
    root.title("28 nm 6T SRAM — WAT Studio")
    root.geometry("1120x780")
    root.minsize(1040, 720)
    root.configure(bg=BG)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Root.TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD, relief="flat")
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Variable Display", 24, "bold"))
    style.configure("Subtitle.TLabel", background=BG, foreground=SECONDARY, font=("Segoe UI Variable Text", 10))
    style.configure("Section.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI Variable Text", 13, "bold"))
    style.configure("Body.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI Variable Text", 10))
    style.configure("Meta.TLabel", background=CARD, foreground=SECONDARY, font=("Segoe UI Variable Text", 9))
    style.configure("Apple.TEntry", fieldbackground="#F2F2F7", foreground=TEXT, bordercolor="#E5E5EA",
                    lightcolor="#E5E5EA", darkcolor="#E5E5EA", padding=(8, 6))
    style.map("Apple.TEntry", bordercolor=[("focus", BLUE)])
    style.configure("Accent.TButton", background=BLUE, foreground="white", borderwidth=0,
                    font=("Segoe UI Variable Text", 11, "bold"), padding=(18, 11))
    style.map("Accent.TButton", background=[("pressed", BLUE_DARK), ("active", "#1689FF"), ("disabled", "#A7CFFF")])
    style.configure("Quiet.TButton", background="#E9E9ED", foreground=TEXT, borderwidth=0, padding=(10, 7))
    style.map("Quiet.TButton", background=[("pressed", "#D8D8DC"), ("active", "#E2E2E7")])
    style.configure("Apple.Horizontal.TProgressbar", background=BLUE, troughcolor="#E5E5EA", borderwidth=0)

    values = {"out": tk.StringVar(value=str(Path.cwd()/"output")), "corner": tk.StringVar(value="TT")}
    defaults = {"pu": ("0.38", "45"), "pg": ("0.37", "80"), "pd": ("0.36", "120")}
    wat_values: dict[str, tk.StringVar] = {}
    for dev in ("pu1", "pu2", "pg1", "pg2", "pd1", "pd2"):
        vt, ids = defaults[dev[:2]]
        wat_values[f"{dev}_vt"] = tk.StringVar(value=vt)
        wat_values[f"{dev}_ids"] = tk.StringVar(value=ids)
    numeric = {k: tk.StringVar(value=str(v)) for k, v in asdict(Config()).items() if k != "grid_points"}

    shell = ttk.Frame(root, style="Root.TFrame", padding=(28, 22, 28, 24)); shell.pack(fill="both", expand=True)
    header = ttk.Frame(shell, style="Root.TFrame"); header.pack(fill="x", pady=(0, 18))
    ttk.Label(header, text="28 nm 6T SRAM", style="Title.TLabel").pack(side="left")
    badge = tk.Label(header, text="  WAT STUDIO  ", bg="#E5F1FF", fg=BLUE,
                     font=("Segoe UI Variable Text", 9, "bold"), padx=7, pady=4)
    badge.pack(side="left", padx=12, pady=(7, 0))
    ttk.Label(header, text="Object-oriented bitcell analysis · No SPICE", style="Subtitle.TLabel").pack(side="right", pady=(10, 0))

    content = ttk.Frame(shell, style="Root.TFrame"); content.pack(fill="both", expand=True)
    content.columnconfigure(0, weight=7); content.columnconfigure(1, weight=4); content.rowconfigure(0, weight=1)
    left = ttk.Frame(content, style="Card.TFrame", padding=18); left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
    right = ttk.Frame(content, style="Card.TFrame", padding=18); right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

    ttk.Label(left, text="Bitcell WAT", style="Section.TLabel").pack(anchor="w")
    ttk.Label(left, text="Enter Vt and Ids beside each physical MOS.", style="Meta.TLabel").pack(anchor="w", pady=(2, 8))
    top_fields = ttk.Frame(left, style="Card.TFrame"); top_fields.pack(fill="x", pady=(0, 4))
    ttk.Label(top_fields, text="Corner / Lot", style="Body.TLabel").pack(side="left")
    ttk.Entry(top_fields, textvariable=values["corner"], width=14, style="Apple.TEntry").pack(side="left", padx=10)
    ttk.Label(top_fields, text="Generic 28 nm · 0.9 V · 25 °C", style="Meta.TLabel").pack(side="right")

    schematic = tk.Canvas(left, bg=CARD, highlightthickness=0, height=465)
    schematic.pack(fill="both", expand=True)
    # Circuit skeleton: hierarchy is conveyed through restrained color and spatial mapping.
    schematic.create_text(323, 18, text="VDD", fill=SECONDARY, font=("Segoe UI Variable Text", 9, "bold"))
    schematic.create_line(170, 35, 476, 35, fill=BORDER, width=2)
    schematic.create_line(170, 420, 476, 420, fill=BORDER, width=2)
    schematic.create_text(323, 446, text="GND", fill=SECONDARY, font=("Segoe UI Variable Text", 9, "bold"))
    schematic.create_line(60, 220, 180, 220, fill=GREEN, width=3)
    schematic.create_line(466, 220, 586, 220, fill=GREEN, width=3)
    schematic.create_text(34, 220, text="BL", fill=SECONDARY, font=("Segoe UI Variable Text", 9, "bold"))
    schematic.create_text(612, 220, text="BLB", fill=SECONDARY, font=("Segoe UI Variable Text", 9, "bold"))
    schematic.create_line(260, 160, 386, 285, fill="#AF52DE", width=2, dash=(5, 4))
    schematic.create_line(386, 160, 260, 285, fill="#AF52DE", width=2, dash=(5, 4))
    schematic.create_oval(250, 210, 270, 230, fill=TEXT, outline="")
    schematic.create_oval(376, 210, 396, 230, fill=TEXT, outline="")
    schematic.create_text(260, 198, text="Q", fill=TEXT, font=("Segoe UI Variable Text", 11, "bold"))
    schematic.create_text(386, 198, text="QB", fill=TEXT, font=("Segoe UI Variable Text", 11, "bold"))

    def mos_panel(name: str, accent: str) -> ttk.Frame:
        panel = tk.Frame(schematic, bg="#FAFAFC", highlightbackground=BORDER, highlightthickness=1, padx=9, pady=7)
        tk.Label(panel, text=name.upper(), bg="#FAFAFC", fg=accent,
                 font=("Segoe UI Variable Text", 9, "bold")).grid(row=0, column=0, columnspan=4, sticky="w")
        tk.Label(panel, text="Vt", bg="#FAFAFC", fg=SECONDARY, font=("Segoe UI Variable Text", 8)).grid(row=1, column=0)
        ttk.Entry(panel, textvariable=wat_values[f"{name}_vt"], width=7, style="Apple.TEntry").grid(row=1, column=1, padx=(3, 7), pady=(4, 0))
        tk.Label(panel, text="Ids", bg="#FAFAFC", fg=SECONDARY, font=("Segoe UI Variable Text", 8)).grid(row=1, column=2)
        ttk.Entry(panel, textvariable=wat_values[f"{name}_ids"], width=7, style="Apple.TEntry").grid(row=1, column=3, padx=(3, 0), pady=(4, 0))
        return panel

    positions = {"pu1": (150, 52), "pu2": (370, 52), "pg1": (18, 177), "pg2": (502, 177),
                 "pd1": (150, 310), "pd2": (370, 310)}
    for name, (x, y) in positions.items():
        accent = RED if name.startswith("pu") else GREEN if name.startswith("pg") else BLUE
        schematic.create_window(x, y, anchor="nw", window=mos_panel(name, accent))
    schematic.create_text(323, 390, text="Vt in V · Ids in µA", fill=SECONDARY, font=("Segoe UI Variable Text", 8))

    ttk.Label(right, text="Analysis", style="Section.TLabel").pack(anchor="w")
    ttk.Label(right, text="WT 0-Bit · SNM · Read / Write Vmin", style="Meta.TLabel").pack(anchor="w", pady=(2, 14))
    config_grid = ttk.Frame(right, style="Card.TFrame"); config_grid.pack(fill="x")
    labels = [("nominal_vdd", "SRAM VDD", "V"), ("wat_vdd", "WAT VDD", "V"),
              ("vt_step", "Vt variation", "V"), ("ids_step_pct", "Ids variation", "%"),
              ("vmin_start", "Vmin start", "V"), ("vmin_stop", "Vmin stop", "V"),
              ("vmin_step", "Vmin step", "V"), ("read_snm_limit", "Read SNM limit", "V")]
    for row, (key, label, unit) in enumerate(labels):
        ttk.Label(config_grid, text=label, style="Body.TLabel").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(config_grid, textvariable=numeric[key], width=10, style="Apple.TEntry").grid(row=row, column=1, sticky="e", padx=(12, 5))
        ttk.Label(config_grid, text=unit, style="Meta.TLabel").grid(row=row, column=2, sticky="w")
    config_grid.columnconfigure(0, weight=1)

    ttk.Separator(right).pack(fill="x", pady=16)
    ttk.Label(right, text="Report destination", style="Body.TLabel").pack(anchor="w")
    out_row = ttk.Frame(right, style="Card.TFrame"); out_row.pack(fill="x", pady=(6, 14))
    ttk.Entry(out_row, textvariable=values["out"], style="Apple.TEntry").pack(side="left", fill="x", expand=True)
    def pick_out() -> None:
        selected = filedialog.askdirectory()
        if selected: values["out"].set(selected)
    ttk.Button(out_row, text="Choose…", style="Quiet.TButton", command=pick_out).pack(side="left", padx=(7, 0))

    status = tk.StringVar(value="Ready to analyze")
    status_label = tk.Label(right, textvariable=status, bg=CARD, fg=SECONDARY,
                            font=("Segoe UI Variable Text", 9), anchor="w", justify="left", wraplength=330)
    status_label.pack(fill="x", pady=(0, 7))
    progress = ttk.Progressbar(right, mode="indeterminate", style="Apple.Horizontal.TProgressbar")
    progress.pack(fill="x", pady=(0, 12))
    result_queue: queue.Queue = queue.Queue()

    def collect_inputs() -> tuple[SixTWatCell, Config]:
        cfg = Config(**{k: float(v.get()) for k, v in numeric.items()})
        validate_config(cfg)
        mos = {}
        for name in ("pu1", "pu2", "pg1", "pg2", "pd1", "pd2"):
            mos[name] = MosWat(_positive(wat_values[f"{name}_vt"].get(), f"{name}_vt"),
                               _positive(wat_values[f"{name}_ids"].get(), f"{name}_ids"))
        return SixTWatCell(values["corner"].get().strip() or "Manual", **mos), cfg

    def worker(cell: SixTWatCell, cfg: Config, out_path: str) -> None:
        try:
            report = write_outputs(analyze_six_mos(cell, cfg), out_path)
            result_queue.put((True, cell, report))
        except Exception as exc:
            result_queue.put((False, None, exc))

    def poll_result() -> None:
        try: ok, cell, payload = result_queue.get_nowait()
        except queue.Empty:
            root.after(80, poll_result); return
        progress.stop(); analyze_button.state(["!disabled"])
        if ok:
            status.set(f"Complete · {cell.corner} · Report opened")
            status_label.configure(fg=GREEN)
            webbrowser.open(Path(payload).resolve().as_uri())
        else:
            status.set("Analysis could not be completed")
            status_label.configure(fg=RED)
            messagebox.showerror("Analysis error", str(payload))

    def execute() -> None:
        try: cell, cfg = collect_inputs()
        except Exception as exc:
            status.set("Check the highlighted input values")
            status_label.configure(fg=RED)
            messagebox.showerror("Invalid input", str(exc)); return
        status.set("Analyzing the six-device cell…")
        status_label.configure(fg=BLUE)
        analyze_button.state(["disabled"]); progress.start(10)
        threading.Thread(target=worker, args=(cell, cfg, values["out"].get()), daemon=True).start()
        root.after(80, poll_result)

    analyze_button = ttk.Button(right, text="Analyze & Open Report", style="Accent.TButton", command=execute)
    analyze_button.pack(fill="x", side="bottom")
    ttk.Label(right, text="Scan4N · Select_Write · Select_Read", style="Meta.TLabel").pack(side="bottom", pady=(0, 9))
    root.mainloop()


def parse_args(argv: list[str]) -> argparse.Namespace:
    p=argparse.ArgumentParser(description="6T SRAM / WAT PU-PG-PD sensitivity analyzer")
    p.add_argument("--input",help="WAT CSV; omit to open GUI")
    p.add_argument("--output",default="output",help="output directory")
    p.add_argument("--corner",help="analyze only this corner")
    p.add_argument("--vdd",type=float,default=.80,help="nominal SRAM VDD")
    p.add_argument("--wat-vdd",type=float,default=1.20,help="WAT Ids test voltage")
    p.add_argument("--vt-step",type=float,default=.030)
    p.add_argument("--ids-step-pct",type=float,default=10.0)
    p.add_argument("--vmin-start",type=float,default=.25)
    p.add_argument("--vmin-stop",type=float,default=1.05)
    p.add_argument("--vmin-step",type=float,default=.01)
    p.add_argument("--read-snm-limit",type=float,default=.030)
    return p.parse_args(argv)


def main(argv: list[str] | None=None) -> int:
    args=parse_args(sys.argv[1:] if argv is None else argv)
    if not args.input:
        launch_gui(); return 0
    cfg=Config(wat_vdd=args.wat_vdd,nominal_vdd=args.vdd,vt_step=args.vt_step,
               ids_step_pct=args.ids_step_pct,vmin_start=args.vmin_start,vmin_stop=args.vmin_stop,
               vmin_step=args.vmin_step,read_snm_limit=args.read_snm_limit)
    try:
        reports=run_analysis(args.input,args.output,cfg,args.corner)
        for report in reports: print(report.resolve())
        return 0
    except Exception as exc:
        print(f"error: {exc}",file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
