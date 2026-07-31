#!/usr/bin/env python3
"""6T SRAM / WAT compact-model correlation and cell-level SNM analyzer.

This is an engineering exploration model.  It calibrates simple square-law MOS
devices from WAT Vt and Ids and is intentionally independent of a PDK/ngspice.
Use a foundry BSIM deck for sign-off numbers.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
import csv
import html
import json
import math
import os
import re
import statistics as statlib
import sys
import webbrowser
from dataclasses import asdict, dataclass, replace
from datetime import datetime
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
class DatasheetTargets:
    """PU/PG/PD WAT targets used only for measured-WAT comparison."""
    pu: MosWat
    pg: MosWat
    pd: MosWat


@dataclass(frozen=True)
class JudgmentTargets:
    """User-owned design/datasheet limits for model-derived cell metrics."""
    cell_ratio_min: float = 1.20
    pull_up_ratio_min: float = 1.50
    hold_snm_min_mv: float = 300.0
    read_snm_min_mv: float = 200.0
    write_snm_min_mv: float = 100.0
    marginal_band_pct: float = 10.0


@dataclass(frozen=True)
class TargetValidationSettings:
    """User-owned bands/specs used to validate WAT targets against measured WT."""
    vt_tolerance_mv: float = 15.0
    idsat_tolerance_pct: float = 8.0
    scan4n_vmin_max: float = 0.650
    select_write_vmin_max: float = 0.600
    select_read_vmin_max: float = 0.620
    minimum_statistical_n: int = 10


DISPLAY_MOS_NAMES = {
    "pu1": "PUL", "pu2": "PUR",
    "pg1": "PGL", "pg2": "PGR",
    "pd1": "PDL", "pd2": "PDR",
}


def _safe_path_component(value: object, fallback: str) -> str:
    """Return a Windows-safe, readable directory component."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value).strip())
    text = re.sub(r"\s+", "_", text).strip(" ._")
    return (text[:80] or fallback)


def create_run_output_dir(base_dir: str | os.PathLike[str], wafer_id: object,
                          analysis_name: str,
                          timestamp: datetime | None = None) -> Path:
    """Create a unique date/Wafer/time analysis directory and run manifest."""
    instant = timestamp or datetime.now().astimezone()
    wafer = _safe_path_component(wafer_id, "Unknown_Wafer")
    analysis = _safe_path_component(analysis_name, "analysis")
    parent = Path(base_dir) / instant.strftime("%Y-%m-%d") / wafer
    stem = f'{instant.strftime("%H%M%S")}_{analysis}'
    suffix = 1
    while True:
        candidate = parent / (stem if suffix == 1 else f"{stem}_{suffix:02d}")
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            suffix += 1
    manifest = {
        "wafer_id": str(wafer_id).strip() or "Unknown Wafer",
        "analysis": analysis_name,
        "created_local": instant.isoformat(timespec="seconds"),
        "output_directory": str(candidate.resolve()),
    }
    (candidate / "run_info.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return candidate


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
class ExcelMosStatistics:
    """Wafer-site statistics used to collapse repeated WAT rows into one MOS model input."""
    valid_count: int
    total_count: int
    vt_mean: float
    vt_median: float
    vt_stdev: float
    vt_min: float
    vt_max: float
    ids_mean: float
    ids_median: float
    ids_stdev: float
    ids_min: float
    ids_max: float


@dataclass(frozen=True)
class ExcelWatSweepPoint:
    """One 6T WAT sample measured/evaluated at one model supply voltage."""
    lot_wafer: str
    model_vdd_v: float
    cell: SixTWatCell
    statistics: dict[str, ExcelMosStatistics]


@dataclass(frozen=True)
class ThreeTWatCell:
    """Merged object mode: one PU, PG and PD object shared by both cell sides."""
    corner: str
    pu: MosWat
    pg: MosWat
    pd: MosWat

    def to_six_t(self) -> SixTWatCell:
        return SixTWatCell(self.corner, self.pu, self.pu, self.pg, self.pg, self.pd, self.pd)

    def representative(self) -> WatPoint:
        return WatPoint(self.corner, self.pu.vt, self.pu.ids, self.pg.vt,
                        self.pg.ids, self.pd.vt, self.pd.ids)


@dataclass(frozen=True)
class RsnmVccPoint:
    """Grouped PU/PG/PD WAT inputs measured at one operating VDD."""
    vcc_v: float
    pu: MosWat
    pg: MosWat
    pd: MosWat


@dataclass(frozen=True)
class ManualVmin:
    """Measured WT values entered by the user; never generated by the model."""
    scan4n: float
    select_write: float
    select_read: float

    def rows(self) -> list[dict]:
        return [
            {"test": "Scan4N", "vmin_v": self.scan4n, "source": "Manual measured value"},
            {"test": "Select_Write", "vmin_v": self.select_write, "source": "Manual measured value"},
            {"test": "Select_Read", "vmin_v": self.select_read, "source": "Manual measured value"},
        ]


@dataclass(frozen=True)
class Config:
    # Fixed generic 28 nm low-power core assumptions.
    wat_vdd: float = 0.90
    nominal_vdd: float = 0.90
    vt_step: float = 0.030
    ids_step_pct: float = 10.0
    vmin_start: float = 0.40
    vmin_stop: float = 0.90
    vmin_step: float = 0.01
    read_snm_limit: float = 0.030
    grid_points: int = 5001
    technology_node_nm: float = 28.0
    channel_length_nm: float = 28.0
    pu_width_nm: float = 70.0
    pg_width_nm: float = 100.0
    pd_width_nm: float = 140.0
    nominal_temperature_c: float = 25.0
    read_wordline_over_vdd: float = 1.0
    read_bitline_over_vdd: float = 1.0
    write_wordline_over_vdd: float = 1.0
    write_low_bitline_over_vdd: float = 0.0
    write_high_bitline_over_vdd: float = 1.0


@dataclass(frozen=True)
class Tech28nm:
    """Transparent non-foundry defaults for the generic 28 nm SRAM model."""
    node_nm: int = 28
    process_family: str = "generic planar bulk CMOS, LP-like"
    topology: str = "6T: 2×PU PMOS + 2×PG NMOS + 2×PD NMOS"
    channel_length_nm: float = 28.0
    pu_width_nm: float = 70.0
    pg_width_nm: float = 100.0
    pd_width_nm: float = 140.0
    nominal_temperature_c: float = 25.0
    read_wordline_over_vdd: float = 1.0
    read_bitline_over_vdd: float = 1.0
    write_wordline_over_vdd: float = 1.0
    write_low_bitline_over_vdd: float = 0.0
    write_high_bitline_over_vdd: float = 1.0
    hold_wordline_over_vdd: float = 0.0
    beta_policy: str = "WAT-calibrated: 2*Idsat/(WAT_VDD-|Vt|)^2"
    common_vth_policy: str = "mean(|Vt_PU|, Vt_PG, Vt_PD) for analytical Read SNM"


TECH_28NM = Tech28nm()


def tech_from_config(cfg: Config) -> Tech28nm:
    """Resolve editable GUI assumptions, falling back to generic defaults."""
    return replace(
        TECH_28NM,
        node_nm=int(round(cfg.technology_node_nm)),
        channel_length_nm=cfg.channel_length_nm,
        pu_width_nm=cfg.pu_width_nm,
        pg_width_nm=cfg.pg_width_nm,
        pd_width_nm=cfg.pd_width_nm,
        nominal_temperature_c=cfg.nominal_temperature_c,
        read_wordline_over_vdd=cfg.read_wordline_over_vdd,
        read_bitline_over_vdd=cfg.read_bitline_over_vdd,
        write_wordline_over_vdd=cfg.write_wordline_over_vdd,
        write_low_bitline_over_vdd=cfg.write_low_bitline_over_vdd,
        write_high_bitline_over_vdd=cfg.write_high_bitline_over_vdd,
    )


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


def _inverse_vtc(curve: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Return y=f^-1(x) on the original x grid for a decreasing VTC."""
    xs = [point[0] for point in curve]
    ys = [point[1] for point in curve]
    reversed_ys = list(reversed(ys))

    def inverse_y(x_value: float) -> float:
        index = bisect_left(reversed_ys, x_value)
        if index <= 0:
            return xs[-1]
        if index >= len(reversed_ys):
            return xs[0]
        y0, y1 = reversed_ys[index - 1], reversed_ys[index]
        x0, x1 = xs[-index], xs[-index - 1]
        if abs(y1 - y0) < 1e-15:
            return (x0 + x1) / 2.0
        return x0 + (x_value - y0) * (x1 - x0) / (y1 - y0)

    return [(x_value, inverse_y(x_value)) for x_value in xs]


def _fit_butterfly_squares(direct_curve: list[tuple[float, float]],
                            mirrored_curve: list[tuple[float, float]],
                            vdd: float, mode: str) -> dict:
    """Fit independent maximum squares between two VTCs on a shared Vin grid."""
    if len(direct_curve) != len(mirrored_curve) or len(direct_curve) < 21 or vdd <= 0:
        return {"valid": False, "reason": "VDD must be positive and matched curves need >= 21 points",
                "snm_v": None, "snm_mv": None, "squares": []}
    xs = [point[0] for point in direct_curve]
    if any(abs(x - mirrored_curve[index][0]) > 1e-12 for index, x in enumerate(xs)):
        return {"valid": False, "reason": "Direct and mirrored VTC grids do not match",
                "snm_v": None, "snm_mv": None, "squares": []}
    bounds = [(x, min(direct_curve[index][1], mirrored_curve[index][1]),
               max(direct_curve[index][1], mirrored_curve[index][1]))
              for index, x in enumerate(xs)]

    # The central VTC crossing is the metastable boundary between the two eyes.
    differences = [direct_curve[index][1] - mirrored_curve[index][1]
                   for index in range(len(xs))]
    crossing_candidates = []
    for index in range(len(xs) - 1):
        d0, d1 = differences[index], differences[index + 1]
        if d0 == 0:
            crossing_candidates.append(xs[index])
        elif d0 * d1 < 0:
            fraction = abs(d0) / (abs(d0) + abs(d1))
            crossing_candidates.append(xs[index] + fraction * (xs[index + 1] - xs[index]))
    crossing = (min(crossing_candidates, key=lambda value: abs(value - vdd / 2.0))
                if crossing_candidates else
                xs[min(range(len(xs)), key=lambda index: abs(differences[index]))])
    split = min(range(len(xs)), key=lambda index: abs(xs[index] - crossing))

    states = (("upper_left", "Left node=0 / Right node=1"),
              ("lower_right", "Left node=1 / Right node=0"))
    squares = []
    for lobe, ((start, stop), (state_key, state_label)) in enumerate(
            zip(((0, split), (split, len(xs) - 1)), states), 1):
        best_side = 0.0
        best_position = None
        for left_index in range(start, stop + 1):
            minimum_upper = math.inf
            maximum_lower = -math.inf
            for right_index in range(left_index, stop + 1):
                minimum_upper = min(minimum_upper, bounds[right_index][2])
                maximum_lower = max(maximum_lower, bounds[right_index][1])
                side = xs[right_index] - xs[left_index]
                clearance = minimum_upper - maximum_lower
                if side <= clearance + 1e-12:
                    if side > best_side:
                        y0 = maximum_lower + max(0.0, clearance - side) / 2.0
                        best_side = side
                        best_position = (xs[left_index], y0)
                elif right_index > left_index:
                    break
        if best_position is None:
            return {"valid": False, "reason": f"No square found in lobe {lobe}",
                    "snm_v": None, "snm_mv": None, "squares": squares}
        squares.append({"lobe": lobe, "state_key": state_key, "state": state_label,
                        "x_v": best_position[0], "y_v": best_position[1],
                        "side_v": best_side, "side_mv": 1000.0 * best_side})

    upper_v, lower_v = squares[0]["side_v"], squares[1]["side_v"]
    mean_v = (upper_v + lower_v) / 2.0
    delta_v = upper_v - lower_v
    snm_v = min(upper_v, lower_v)
    return {
        "valid": True,
        "reason": "Independent two-eye maximum-square fit",
        "definition": "Cell Read SNM is the smaller of the two state-dependent square sides",
        "mode": mode,
        "grid_points": len(xs),
        "crossing_v": crossing,
        "trip_v": crossing,
        "snm_v": snm_v,
        "snm_mv": 1000.0 * snm_v,
        "snm_upper_left_v": upper_v,
        "snm_upper_left_mv": 1000.0 * upper_v,
        "snm_lower_right_v": lower_v,
        "snm_lower_right_mv": 1000.0 * lower_v,
        "delta_snm_v": delta_v,
        "delta_snm_mv": 1000.0 * delta_v,
        "mismatch_index_pct": (abs(delta_v) / mean_v * 100.0 if mean_v > 0 else None),
        "squares": squares,
    }


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
            wl = self.cfg.read_wordline_over_vdd * vdd
            bitline = self.cfg.read_bitline_over_vdd * vdd
            up += self._access_current(self.pg, vout, bitline, wl)
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

    def transfer_with_bitline(self, vin: float, vdd: float, bitline: float,
                              wordline: float | None = None) -> float:
        """Inverter transfer with WL high and the access device tied to bitline."""
        wl = vdd if wordline is None else wordline
        def balance(vout: float) -> float:
            return (self.pu.current(vdd - vin, vdd - vout) - self.pd.current(vin, vout) +
                    self._access_current(self.pg, vout, bitline, wl))
        lo, hi = 0.0, vdd
        if balance(lo) <= 0:
            return 0.0
        if balance(hi) >= 0:
            return vdd
        for _ in range(48):
            mid = (lo + hi) / 2
            if balance(mid) > 0:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    def write_vtc_pair(self, vdd: float, points: int = 201) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        """Write-condition VTC pair for low BL and high BLB at the configured WL."""
        low, high = [], []
        wl = self.cfg.write_wordline_over_vdd * vdd
        low_bl = self.cfg.write_low_bitline_over_vdd * vdd
        high_bl = self.cfg.write_high_bitline_over_vdd * vdd
        for i in range(points):
            vin = vdd * i / (points - 1)
            low.append((vin, self.transfer_with_bitline(vin, vdd, low_bl, wl)))
            high.append((vin, self.transfer_with_bitline(vin, vdd, high_bl, wl)))
        return low, high

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
        """Legacy metastable-centered SNM proxy (V); retained for traceability."""
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

    def butterfly_squares(self, vdd: float, mode: str = "read",
                          points: int = 1201) -> dict:
        """Fit the two squares of a symmetric cell from one VTC and its inverse."""
        if vdd <= 0 or points < 21:
            return {"valid": False, "reason": "VDD must be positive and points >= 21",
                    "snm_v": None, "snm_mv": None, "squares": []}
        curve = self.vtc(vdd, mode, points)
        return _fit_butterfly_squares(curve, _inverse_vtc(curve), vdd, mode)

    def analytical_read_snm_eq_3_36(self, vdd: float) -> dict:
        """Evaluate the 6T read-SNM expression from Section 3.4.2, Eq. 3.36.

        Reference: *High-Speed CMOS Circuit Technology*, Section 3.4.2,
        "Analytical SNM Expression for a 6T SRAM Cell", printed page 51.

        The source assumes one common threshold voltage for PU/PG/PD and
        long-channel square-law devices.  WAT supplies three distinct values,
        so this implementation explicitly maps them to their arithmetic mean.
        Domain failures are reported instead of forcing a non-physical result.
        """
        q = self.pu.beta / self.pg.beta  # beta_p / beta_a
        r = self.pd.beta / self.pg.beta  # beta_d / beta_a
        vth_eff = (self.wat.pu_vt + self.wat.pg_vt + self.wat.pd_vt) / 3.0
        result = {
            "source": "High-Speed CMOS Circuit Technology, Section 3.4.2, Equation 3.36",
            "mode": "read-accessed 6T SRAM",
            "valid": False,
            "reason": "",
            "snm_v": None,
            "snm_mv": None,
            "vdd_v": vdd,
            "vth_eff_v": vth_eff,
            "vth_mapping": "arithmetic mean of |Vt_PU|, Vt_PG and Vt_PD",
            "q_beta_p_over_beta_a": q,
            "r_beta_d_over_beta_a": r,
            "vs_v": None,
            "vr_v": None,
            "k": None,
            "term_a_v": None,
            "term_b_v": None,
            "assumptions": [
                "PU, PG and PD share one threshold voltage",
                "long-channel square-law MOS model",
                "Q1/Q4 saturated and Q2/Q5 linear as defined by the source",
                "local linearity around the read operating point",
                "short-channel effects are neglected",
            ],
        }

        def invalid(reason: str) -> dict:
            result["reason"] = reason
            return result

        if not all(math.isfinite(x) and x > 0 for x in (vdd, vth_eff, q, r)):
            return invalid("VDD, effective VTH, q and r must be positive finite values")

        vs = vdd - vth_eff
        vr = vs - (r / (r + 1.0)) * vth_eff
        result["vs_v"], result["vr_v"] = vs, vr
        if vs <= 0:
            return invalid("VDD must exceed the effective threshold voltage")
        if abs(vr) < 1e-15:
            return invalid("Vr is zero in the analytical model")

        k_domain = (r + 1.0) - (vs * vs) / (vr * vr)
        result["k_domain"] = k_domain
        if k_domain <= 0:
            return invalid("Analytical square-root domain is non-positive")

        k = (r / (r + 1.0)) * (math.sqrt((r + 1.0) / k_domain) - 1.0)
        result["k"] = k
        if not math.isfinite(k) or abs(k) < 1e-15 or abs(k + 1.0) < 1e-15:
            return invalid("Analytical model produced an invalid k")

        denom_a = 1.0 + r / (k * (r + 1.0))
        root_arg = (r / q) * (1.0 + 2.0 * k + (r / q) * k * k)
        result["root_argument"] = root_arg
        if root_arg < 0 or abs(denom_a) < 1e-15:
            return invalid("Analytical denominator or square-root domain is invalid")
        denom_b = 1.0 + k * r / q + math.sqrt(root_arg)
        if abs(denom_b) < 1e-15:
            return invalid("Analytical second denominator is zero")

        term_a = (vdd - ((2.0 * r + 1.0) / (r + 1.0)) * vth_eff) / denom_a
        term_b = (vdd - 2.0 * vth_eff) / denom_b
        snm_v = vth_eff - (term_a - term_b) / (k + 1.0)
        result["term_a_v"], result["term_b_v"] = term_a, term_b
        if not math.isfinite(snm_v) or snm_v < 0 or snm_v > vdd:
            return invalid("Analytical result is outside the physical range 0 to VDD")

        result.update({"valid": True, "reason": "Within the real-valued analytical domain",
                       "snm_v": snm_v, "snm_mv": 1000.0 * snm_v})
        return result

    def strength_ratios(self) -> dict[str, float]:
        """Return Vt-aware model-beta ratios plus direct WAT-current proxies."""
        return {
            "cell_ratio_beta": self.pd.beta / self.pg.beta,
            "pull_up_ratio_beta": self.pg.beta / self.pu.beta,
            "cell_ratio_ids_proxy": self.wat.pd_ids / self.wat.pg_ids,
            "pull_up_ratio_ids_proxy": self.wat.pg_ids / self.wat.pu_ids,
        }

    def write_snm(self, vdd: float) -> float:
        """Compact-model write bitline-noise margin (V).

        The result is the largest voltage allowed on the nominally-low write
        bitline while PG can still overcome PU at the hold inverter trip point.
        This is a directional WAT-calibrated proxy, not a foundry sign-off WSNM.
        """
        if vdd <= 0:
            return 0.0
        trip = self.trip_point(vdd, "hold")
        wl = self.cfg.write_wordline_over_vdd * vdd
        nominal_low = self.cfg.write_low_bitline_over_vdd * vdd
        pull_up = self.pu.current(vdd, vdd - trip)

        def writable(low_bitline: float) -> bool:
            if low_bitline >= trip:
                return False
            access = self.pg.current(wl - low_bitline, trip - low_bitline)
            return access >= pull_up

        if not writable(nominal_low):
            return 0.0
        lo, hi = nominal_low, trip
        for _ in range(42):
            mid = (lo + hi) / 2
            if writable(mid):
                lo = mid
            else:
                hi = mid
        return lo - nominal_low

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


class AsymmetricSram6T:
    """Cross-coupled Read-SNM model retaining all six independent WAT objects."""

    def __init__(self, cell: SixTWatCell, cfg: Config):
        self.cell = cell
        self.cfg = cfg
        self.left = Sram6T(cell.side(1), cfg)   # PUL / PGL / PDL
        self.right = Sram6T(cell.side(2), cfg)  # PUR / PGR / PDR

    def read_butterfly(self, vdd: float, points: int = 1201) -> dict:
        # Plot coordinates are (left storage node, right storage node).
        # The right inverter directly gives y=f_right(x).  The left inverter
        # gives x=f_left(y), therefore its inverse is the second curve y(x).
        direct_fit = self.right.vtc(vdd, "read", points)
        mirrored_fit = _inverse_vtc(self.left.vtc(vdd, "read", points))
        fitted = _fit_butterfly_squares(direct_fit, mirrored_fit, vdd, "read")
        fitted["coordinate_definition"] = {
            "x": "left storage-node voltage (PUL/PGL/PDL side)",
            "y": "right storage-node voltage (PUR/PGR/PDR side)",
            "direct_vtc": "right inverter: y=f_right(x)",
            "mirrored_vtc": "inverse left inverter: y=f_left^-1(x)",
        }
        direct_plot = self.right.vtc(vdd, "read", 201)
        mirrored_plot = _inverse_vtc(self.left.vtc(vdd, "read", 201))
        return {"read_butterfly": fitted, "read_vtc": direct_plot,
                "read_vtc_mirrored": mirrored_plot}


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


def metric(model: Sram6T, cfg: Config,
           read_butterfly: dict | None = None) -> dict[str, float | None]:
    square_points = max(1201, cfg.grid_points)
    read_butterfly = read_butterfly or model.butterfly_squares(
        cfg.nominal_vdd, "read", square_points)
    analytical = model.analytical_read_snm_eq_3_36(cfg.nominal_vdd)
    return {
        "read_snm_mv": read_butterfly["snm_mv"],
        "write_snm_proxy_mv": 1000 * model.write_snm(cfg.nominal_vdd),
        "read_snm_trip_proxy_mv": 1000 * model.snm(cfg.nominal_vdd, "read"),
        "analytical_read_snm_mv": analytical["snm_mv"],
        **model.strength_ratios(),
    }


def cell_metric(cell: SixTWatCell, cfg: Config) -> dict[str, float | None]:
    """Conservative half-cell mismatch metric: the lower Read SNM wins."""
    sides = [metric(Sram6T(cell.side(i), cfg), cfg) for i in (1, 2)]
    return {
        "read_snm_mv": min(s["read_snm_mv"] for s in sides),
        "write_snm_proxy_mv": min(s["write_snm_proxy_mv"] for s in sides),
        "cell_ratio_beta": min(s["cell_ratio_beta"] for s in sides),
        "pull_up_ratio_beta": min(s["pull_up_ratio_beta"] for s in sides),
        "cell_ratio_ids_proxy": min(s["cell_ratio_ids_proxy"] for s in sides),
        "pull_up_ratio_ids_proxy": min(s["pull_up_ratio_ids_proxy"] for s in sides),
    }


def evaluate_judgment(metrics: dict[str, float | None],
                      targets: JudgmentTargets) -> dict:
    """Evaluate four higher-is-better SRAM metrics against user-owned limits."""
    definitions = (
        ("cell_ratio", "Cell Ratio", "cell_ratio_beta", targets.cell_ratio_min, "ratio",
         "Increase PD strength or reduce PG strength; then re-check write margin."),
        ("pull_up_ratio", "Pull-up Ratio", "pull_up_ratio_beta", targets.pull_up_ratio_min, "ratio",
         "Increase PG strength or reduce PU strength to improve writeability."),
        ("hold_snm", "Hold SNM", "hold_snm_mv", targets.hold_snm_min_mv, "mV",
         "Check PU/PD balance and left-right mismatch; PG is off during hold."),
        ("read_snm", "Read SNM", "read_snm_mv", targets.read_snm_min_mv, "mV",
         "Increase PD relative to PG, then verify the writeability trade-off."),
        ("write_snm", "Write SNM", "write_snm_mv", targets.write_snm_min_mv, "mV",
         "Increase PG strength or reduce PU strength; verify Read SNM trade-off."),
    )
    rows = []
    for key, label, metric_key, target, unit, action in definitions:
        value = metrics.get(metric_key)
        if value is None:
            status, margin, margin_pct = "N/A", None, None
        else:
            margin = value - target
            margin_pct = margin / target * 100 if target else None
            if value >= target:
                status = "PASS"
            elif value >= target * (1 - targets.marginal_band_pct / 100):
                status = "MARGINAL"
            else:
                status = "FAIL"
        displayed_action = ("Meets target; no corrective adjustment is required."
                            if status == "PASS" else action)
        rows.append({"key": key, "label": label, "metric_key": metric_key,
                     "value": value, "target": target, "unit": unit,
                     "margin": margin, "margin_pct": margin_pct,
                     "status": status, "recommended_action": displayed_action})
    statuses = [row["status"] for row in rows]
    overall = "FAIL" if "FAIL" in statuses or "N/A" in statuses else (
        "MARGINAL" if "MARGINAL" in statuses else "PASS")
    return {"overall_status": overall, "marginal_band_pct": targets.marginal_band_pct,
            "targets": asdict(targets), "items": rows,
            "rule": "All five metrics must PASS; values below target but within the marginal band are MARGINAL."}


def analyze(wat: WatPoint, cfg: Config) -> dict:
    model = Sram6T(wat, cfg)
    square_points = max(1201, cfg.grid_points)
    read_butterfly = model.butterfly_squares(cfg.nominal_vdd, "read", square_points)
    write_low_vtc, write_high_vtc = model.write_vtc_pair(cfg.nominal_vdd, 201)
    read_vtc = model.vtc(cfg.nominal_vdd, "read", 201)
    baseline = {"metrics": metric(model, cfg, read_butterfly),
                "analytical_read_snm_eq_3_36": model.analytical_read_snm_eq_3_36(cfg.nominal_vdd),
                "read_butterfly": read_butterfly,
                "read_vtc": read_vtc,
                "read_vtc_mirrored": _inverse_vtc(read_vtc),
                "write_vtc_low": write_low_vtc,
                "write_vtc_high": write_high_vtc,
                "read_trip_v": model.trip_point(cfg.nominal_vdd, "read")}
    report_config = {
        "wat_vdd": cfg.wat_vdd, "nominal_vdd": cfg.nominal_vdd,
        "grid_points": cfg.grid_points,
        "technology_node_nm": cfg.technology_node_nm,
        "channel_length_nm": cfg.channel_length_nm,
        "pu_width_nm": cfg.pu_width_nm, "pg_width_nm": cfg.pg_width_nm,
        "pd_width_nm": cfg.pd_width_nm,
        "nominal_temperature_c": cfg.nominal_temperature_c,
        "read_wordline_over_vdd": cfg.read_wordline_over_vdd,
        "read_bitline_over_vdd": cfg.read_bitline_over_vdd,
        "write_wordline_over_vdd": cfg.write_wordline_over_vdd,
        "write_low_bitline_over_vdd": cfg.write_low_bitline_over_vdd,
        "write_high_bitline_over_vdd": cfg.write_high_bitline_over_vdd,
    }
    return {"technology": asdict(tech_from_config(cfg)), "wat": asdict(wat), "config": report_config,
            "strength_ratios": model.strength_ratios(), "baseline_6t": baseline, "groups": {}}


def _target_comparisons(cell: SixTWatCell | ThreeTWatCell,
                        targets: DatasheetTargets | None) -> list[dict]:
    """Compare each measured object against its matching datasheet device target."""
    if targets is None:
        return []
    if isinstance(cell, ThreeTWatCell):
        measured = ((name.upper(), name, getattr(cell, name)) for name in ("pu", "pg", "pd"))
    else:
        measured = ((DISPLAY_MOS_NAMES[name], name[:2], getattr(cell, name))
                    for name in ("pu1", "pu2", "pg1", "pg2", "pd1", "pd2"))
    rows = []
    for object_name, device, actual in measured:
        target = getattr(targets, device)
        rows.append({
            "object": object_name,
            "device": device.upper(),
            "target_vt_v": target.vt,
            "measured_vt_v": actual.vt,
            "delta_vt_mv": round((actual.vt - target.vt) * 1000, 6),
            "target_isat_ua": target.ids,
            "measured_isat_ua": actual.ids,
            "delta_isat_ua": round(actual.ids - target.ids, 6),
            "delta_isat_pct": round((actual.ids / target.ids - 1) * 100, 6),
        })
    return rows


_MOS_EXCEL_ALIASES = {
    "pul": "pu1", "pu1": "pu1", "m2": "pu1",
    "pur": "pu2", "pu2": "pu2", "m4": "pu2",
    "pgl": "pg1", "pg1": "pg1", "m5": "pg1",
    "pgr": "pg2", "pg2": "pg2", "m6": "pg2",
    "pdl": "pd1", "pd1": "pd1", "m1": "pd1",
    "pdr": "pd2", "pd2": "pd2", "m3": "pd2",
}
_VOLTAGE_UNITS = {"v": 1.0, "mv": 1e-3, "uv": 1e-6, "nv": 1e-9}
_CURRENT_TO_UA = {"a": 1e6, "ma": 1e3, "ua": 1.0, "na": 1e-3, "pa": 1e-6}
GUI_STATE_FILENAME = ".hv28_sram_analysis_state.json"


def _normalized_excel_key(value: object) -> str:
    raw = "" if value is None else str(value).strip().lower()
    raw = raw.replace("μ", "u").replace("µ", "u")
    raw = re.sub(r"\([^)]*\)|\[[^]]*\]", "", raw)
    return re.sub(r"[^a-z0-9]", "", raw)


def _unit_text(value: object, default: str) -> str:
    unit = default if value is None or str(value).strip() == "" else str(value)
    return _normalized_excel_key(unit)


def _excel_number(value: object, unit: object, unit_map: dict[str, float], default_unit: str,
                  label: str) -> float:
    if isinstance(value, str):
        match = re.fullmatch(r"\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([A-Za-zμµ]*)\s*", value)
        if not match:
            raise ValueError(f"{label} must be numeric")
        magnitude, embedded_unit = float(match.group(1)), match.group(2)
        unit = embedded_unit or unit
    else:
        magnitude = float(value)
    if not math.isfinite(magnitude):
        raise ValueError(f"{label} must be finite")
    normalized_unit = _unit_text(unit, default_unit)
    if normalized_unit not in unit_map:
        raise ValueError(f"{label} unit '{unit}' is unsupported; use {', '.join(unit_map)}")
    return magnitude * unit_map[normalized_unit]


def _excel_header_map(headers: list[object]) -> tuple[dict[str, int], dict[str, str]]:
    keys, header_units = {}, {}
    for index, header in enumerate(headers):
        key = _normalized_excel_key(header)
        if key:
            keys[key] = index
            unit_match = re.search(r"[\[(]\s*([^\])]+)\s*[\])]", str(header)) if header else None
            if unit_match:
                header_units[key] = unit_match.group(1)
    return keys, header_units


def _first_header(header_map: dict[str, int], *names: str) -> str | None:
    return next((name for name in names if name in header_map), None)


def _cell(row: list[object], headers: dict[str, int], key: str | None) -> object | None:
    return None if key is None or key not in headers or headers[key] >= len(row) else row[headers[key]]


def _read_wat_excel_rows(headers_raw: list[object], data_rows: list[list[object]],
                         default_model_vdd_v: float) -> list[ExcelWatSweepPoint]:
    """Parse either a long or a wide six-MOS WAT worksheet into SI-normalized values.

    Long form: Lot/Wafer, Model VDD, MOS, Vt, Idsat, plus optional *_Unit columns.
    Wide form: Lot/Wafer, Model VDD, PUL_Vt, PUL_Idsat ... PDR_Vt, PDR_Idsat.
    Blank unit cells default to V for Vt/VDD and uA for Idsat.
    """
    headers, header_units = _excel_header_map(headers_raw)
    lot_key = _first_header(headers, "lotwafer", "lot", "wafer", "waferid", "corner")
    vdd_key = _first_header(headers, "modelvdd", "modelvoltage", "vdd", "supplyvoltage", "testvoltage")
    vdd_unit_key = _first_header(headers, "modelvddunit", "modelvoltageunit", "vddunit", "voltageunit")
    mos_key = _first_header(headers, "mos", "mosname", "device", "transistor")
    vt_key = _first_header(headers, "vt", "vth", "thresholdvoltage")
    ids_key = _first_header(headers, "idsat", "isat", "ids", "ion")
    vt_unit_key = _first_header(headers, "vtunit", "vthunit", "voltageunit")
    ids_unit_key = _first_header(headers, "idsatunit", "isatunit", "idsunit", "ionunit", "currentunit")
    if not lot_key:
        raise ValueError("Excel requires a Lot/Wafer (or Corner) column")

    def vdd_from(row: list[object], row_number: int) -> float:
        raw_vdd = _cell(row, headers, vdd_key)
        if raw_vdd is None or str(raw_vdd).strip() == "":
            return default_model_vdd_v
        unit = _cell(row, headers, vdd_unit_key) or header_units.get(vdd_key or "", "V")
        return _excel_number(raw_vdd, unit, _VOLTAGE_UNITS, "V", f"Excel row {row_number} Model VDD")

    measurements: dict[tuple[str, float], dict[str, list[MosWat]]] = {}
    total_rows: dict[tuple[str, float], dict[str, int]] = {}

    def register(lot: str, vdd: float, mos_name: str, mos: MosWat | None) -> None:
        key = (lot, vdd)
        total_rows.setdefault(key, {})[mos_name] = total_rows.setdefault(key, {}).get(mos_name, 0) + 1
        if mos is not None:
            measurements.setdefault(key, {}).setdefault(mos_name, []).append(mos)

    if mos_key and vt_key and ids_key:  # Long form
        for row_number, row in enumerate(data_rows, 2):
            if not any(value is not None and str(value).strip() for value in row):
                continue
            alias = _normalized_excel_key(_cell(row, headers, mos_key))
            if alias not in _MOS_EXCEL_ALIASES:
                raise ValueError(f"Excel row {row_number}: unknown MOS '{_cell(row, headers, mos_key)}'")
            vdd = vdd_from(row, row_number)
            if vdd <= 0:
                continue  # SNM has no physical definition at zero supply.
            lot = str(_cell(row, headers, lot_key) or f"row_{row_number}").strip()
            mos_name = _MOS_EXCEL_ALIASES[alias]
            raw_vt = _cell(row, headers, vt_key)
            raw_ids = _cell(row, headers, ids_key)
            vt_blank = raw_vt is None or str(raw_vt).strip() == ""
            ids_blank = raw_ids is None or str(raw_ids).strip() == ""
            if vt_blank and ids_blank:
                register(lot, vdd, mos_name, None)
                continue
            if vt_blank != ids_blank:
                raise ValueError(f"Excel row {row_number}: Vt and Idsat must both be filled or both be blank")
            vt_unit = _cell(row, headers, vt_unit_key) or header_units.get(vt_key, "V")
            ids_unit = _cell(row, headers, ids_unit_key) or header_units.get(ids_key, "uA")
            mos = MosWat(
                _positive(_excel_number(raw_vt, vt_unit, _VOLTAGE_UNITS, "V", f"Excel row {row_number} Vt"), "Vt"),
                _positive(_excel_number(raw_ids, ids_unit, _CURRENT_TO_UA, "uA", f"Excel row {row_number} Idsat"), "Idsat"),
            )
            register(lot, vdd, mos_name, mos)
    else:  # Wide form
        for row_number, row in enumerate(data_rows, 2):
            if not any(value is not None and str(value).strip() for value in row):
                continue
            vdd = vdd_from(row, row_number)
            if vdd <= 0:
                continue
            lot = str(_cell(row, headers, lot_key) or f"row_{row_number}").strip()
            for mos_name in ("pu1", "pu2", "pg1", "pg2", "pd1", "pd2"):
                aliases = [alias for alias, mapped in _MOS_EXCEL_ALIASES.items() if mapped == mos_name]
                vt_header = _first_header(headers, *(f"{alias}vt" for alias in aliases),
                                          *(f"{alias}vth" for alias in aliases))
                ids_header = _first_header(headers, *(f"{alias}idsat" for alias in aliases),
                                           *(f"{alias}isat" for alias in aliases),
                                           *(f"{alias}ids" for alias in aliases),
                                           *(f"{alias}ion" for alias in aliases))
                if not vt_header or not ids_header:
                    continue
                raw_vt = _cell(row, headers, vt_header)
                raw_ids = _cell(row, headers, ids_header)
                vt_blank = raw_vt is None or str(raw_vt).strip() == ""
                ids_blank = raw_ids is None or str(raw_ids).strip() == ""
                if vt_blank and ids_blank:
                    register(lot, vdd, mos_name, None)
                    continue
                if vt_blank != ids_blank:
                    raise ValueError(f"Excel row {row_number}: {mos_name} Vt and Idsat must both be filled or both be blank")
                vt_unit = _cell(row, headers, f"{vt_header}unit") or header_units.get(vt_header, "V")
                ids_unit = _cell(row, headers, f"{ids_header}unit") or header_units.get(ids_header, "uA")
                mos = MosWat(
                    _positive(_excel_number(raw_vt, vt_unit, _VOLTAGE_UNITS, "V", f"Excel row {row_number} {mos_name} Vt"), "Vt"),
                    _positive(_excel_number(raw_ids, ids_unit, _CURRENT_TO_UA, "uA", f"Excel row {row_number} {mos_name} Idsat"), "Idsat"),
                )
                register(lot, vdd, mos_name, mos)

    parsed: list[ExcelWatSweepPoint] = []
    required = {"pu1", "pu2", "pg1", "pg2", "pd1", "pd2"}
    for (lot, vdd), mos_measurements in sorted(measurements.items(), key=lambda item: (item[0][0], item[0][1])):
        missing = required - {name for name, values in mos_measurements.items() if values}
        if missing:
            raise ValueError(f"Excel {lot} at {vdd:.3f} V is missing: {', '.join(sorted(missing))}")
        devices: dict[str, MosWat] = {}
        statistics: dict[str, ExcelMosStatistics] = {}
        for mos_name in sorted(required):
            values = mos_measurements[mos_name]
            vt_values = [item.vt for item in values]
            ids_values = [item.ids for item in values]
            devices[mos_name] = MosWat(statlib.fmean(vt_values), statlib.fmean(ids_values))
            statistics[mos_name] = ExcelMosStatistics(
                valid_count=len(values),
                total_count=total_rows[(lot, vdd)].get(mos_name, len(values)),
                vt_mean=statlib.fmean(vt_values),
                vt_median=statlib.median(vt_values),
                vt_stdev=statlib.stdev(vt_values) if len(vt_values) > 1 else 0.0,
                vt_min=min(vt_values),
                vt_max=max(vt_values),
                ids_mean=statlib.fmean(ids_values),
                ids_median=statlib.median(ids_values),
                ids_stdev=statlib.stdev(ids_values) if len(ids_values) > 1 else 0.0,
                ids_min=min(ids_values),
                ids_max=max(ids_values),
            )
        parsed.append(ExcelWatSweepPoint(lot, vdd, SixTWatCell(lot, **devices), statistics))
    if not parsed:
        raise ValueError("Excel contains no analyzable model VDD above 0 V")
    return parsed


def read_wat_excel(path: str | os.PathLike[str], default_model_vdd_v: float = 0.90,
                   sheet_name: str | None = None) -> list[ExcelWatSweepPoint]:
    """Read a six-MOS WAT Excel workbook (.xlsx) and normalize all units."""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("Excel import requires openpyxl. Run: python -m pip install -r requirements.txt") from exc
    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        if sheet_name:
            worksheets = [workbook[sheet_name]]
        else:
            grouped = {
                _normalized_excel_key(sheet.title): sheet
                for sheet in workbook.worksheets
                if _normalized_excel_key(sheet.title) in {"pu", "pg", "pd", "puwat", "pgwat", "pdwat"}
            }
            selected = []
            for family in ("pu", "pg", "pd"):
                sheet = grouped.get(family) or grouped.get(f"{family}wat")
                if sheet is not None:
                    selected.append(sheet)
            worksheets = selected if len(selected) == 3 else [workbook.active]

        headers_raw: list[object] | None = None
        data_rows: list[list[object]] = []
        normalized_headers: list[str] | None = None
        for worksheet in worksheets:
            rows = list(worksheet.iter_rows(values_only=True))
            if not rows:
                raise ValueError(f"Excel worksheet '{worksheet.title}' is empty")
            current_headers = list(rows[0])
            current_normalized = [_normalized_excel_key(value) for value in current_headers]
            if normalized_headers is not None and current_normalized != normalized_headers:
                raise ValueError("PU/PG/PD worksheets must use the same column headers")
            headers_raw = headers_raw or current_headers
            normalized_headers = normalized_headers or current_normalized
            data_rows.extend([list(row) for row in rows[1:]])
        if headers_raw is None:
            raise ValueError("Excel contains no readable worksheet")
        return _read_wat_excel_rows(headers_raw, data_rows, default_model_vdd_v)
    finally:
        workbook.close()


def load_gui_state(state_path: str | os.PathLike[str] | None = None) -> dict[str, object]:
    """Load portable GUI inputs; malformed state is safely ignored."""
    path = Path(state_path) if state_path else Path.cwd() / GUI_STATE_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_gui_state(state: dict[str, object], state_path: str | os.PathLike[str] | None = None) -> None:
    """Persist only user-entered GUI values next to the application workspace."""
    path = Path(state_path) if state_path else Path.cwd() / GUI_STATE_FILENAME
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


VALIDATION_COLUMNS = (
    "lot_wafer", "pu_vt", "pu_idsat", "pg_vt", "pg_idsat", "pd_vt", "pd_idsat",
    "scan4n_vmin", "select_write_vmin", "select_read_vmin",
)


def read_validation_csv(path: str | os.PathLike[str]) -> list[dict]:
    """Read lot/wafer history. Blank WAT fields are allowed because WT is often full-test."""
    aliases = {"corner": "lot_wafer", "pu_ids": "pu_idsat", "pg_ids": "pg_idsat",
               "pd_ids": "pd_idsat", "scan4n": "scan4n_vmin",
               "select_write": "select_write_vmin", "select_read": "select_read_vmin"}
    records: list[dict] = []
    with open(path, newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        headers = {aliases.get(name.strip().lower(), name.strip().lower())
                   for name in (reader.fieldnames or [])}
        required = {"lot_wafer", "scan4n_vmin", "select_write_vmin", "select_read_vmin"}
        if missing := required - headers:
            raise ValueError("Validation CSV missing columns: " + ", ".join(sorted(missing)))
        for line, raw in enumerate(reader, 2):
            canonical = {aliases.get(str(key).strip().lower(), str(key).strip().lower()): value
                         for key, value in raw.items() if key is not None}
            record: dict[str, str | float | None] = {
                "lot_wafer": str(canonical.get("lot_wafer", "")).strip() or f"row_{line}"
            }
            for name in VALIDATION_COLUMNS[1:]:
                value = str(canonical.get(name, "") or "").strip()
                if not value:
                    record[name] = None
                    continue
                try:
                    number = float(value)
                except ValueError as exc:
                    raise ValueError(f"Validation CSV line {line}: {name} is not numeric") from exc
                if not math.isfinite(number) or number <= 0:
                    raise ValueError(f"Validation CSV line {line}: {name} must be positive")
                record[name] = number
            records.append(record)
    return records


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    dx, dy = [x - mx for x in xs], [y - my for y in ys]
    den = math.sqrt(sum(x*x for x in dx) * sum(y*y for y in dy))
    return None if den == 0 else sum(x*y for x, y in zip(dx, dy)) / den


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index + 1
        while end < len(order) and values[order[end]] == values[order[index]]:
            end += 1
        average_rank = (index + 1 + end) / 2
        for position in order[index:end]:
            ranks[position] = average_rank
        index = end
    return ranks


def _validation_record(cell: SixTWatCell | ThreeTWatCell, measured: ManualVmin) -> dict:
    wat = cell.representative()
    return {"lot_wafer": cell.corner, "pu_vt": wat.pu_vt, "pu_idsat": wat.pu_ids,
            "pg_vt": wat.pg_vt, "pg_idsat": wat.pg_ids, "pd_vt": wat.pd_vt,
            "pd_idsat": wat.pd_ids, "scan4n_vmin": measured.scan4n,
            "select_write_vmin": measured.select_write, "select_read_vmin": measured.select_read}


def validate_datasheet_wat_target(cell: SixTWatCell | ThreeTWatCell,
                                  measured: ManualVmin,
                                  targets: DatasheetTargets,
                                  settings: TargetValidationSettings,
                                  historical_rows: list[dict] | None = None) -> dict:
    """Validate whether closeness to WAT targets is associated with acceptable measured WT."""
    current = _validation_record(cell, measured)
    history = [dict(row) for row in (historical_rows or [])
               if str(row.get("lot_wafer", "")) != cell.corner]
    records = history + [current]
    target_values = {
        "pu_vt": targets.pu.vt, "pu_idsat": targets.pu.ids,
        "pg_vt": targets.pg.vt, "pg_idsat": targets.pg.ids,
        "pd_vt": targets.pd.vt, "pd_idsat": targets.pd.ids,
    }


def _attach_target_model(result: dict, targets: DatasheetTargets | None, cfg: Config) -> None:
    """Attach a same-condition 6T model built from datasheet PU/PG/PD targets."""
    if targets is None:
        result["target_6t"] = result["baseline_6t"]
        result["snm_target_comparison"] = []
        analytical = result["baseline_6t"]["analytical_read_snm_eq_3_36"]
        result["analytical_read_snm_comparison"] = {
            "method": "High-Speed CMOS Circuit Technology, Section 3.4.2, Equation 3.36",
            "current": analytical, "target": analytical,
            "current_snm_mv": analytical["snm_mv"], "target_snm_mv": analytical["snm_mv"],
            "delta_mv": 0.0 if analytical["snm_mv"] is not None else None,
            "delta_pct": 0.0 if analytical["snm_mv"] is not None else None,
        }
        return
    target_wat = WatPoint(
        corner="WAT Target",
        pu_vt=targets.pu.vt, pu_ids=targets.pu.ids,
        pg_vt=targets.pg.vt, pg_ids=targets.pg.ids,
        pd_vt=targets.pd.vt, pd_ids=targets.pd.ids,
    )
    target = analyze(target_wat, cfg)["baseline_6t"]
    result["target_6t"] = target
    current_metrics = result["baseline_6t"]["metrics"]
    result["snm_target_comparison"] = [
        {"mode": label, "current_snm_mv": current_metrics[key],
         "target_snm_mv": target["metrics"][key],
         "delta_mv": current_metrics[key] - target["metrics"][key],
         "delta_pct": ((current_metrics[key] / target["metrics"][key] - 1) * 100
                       if target["metrics"][key] else None)}
        for label, key in (("Read SNM", "read_snm_mv"),
                           ("Write SNM Proxy", "write_snm_proxy_mv"))
    ]
    current_analytical = result["baseline_6t"]["analytical_read_snm_eq_3_36"]
    target_analytical = target["analytical_read_snm_eq_3_36"]
    current_value, target_value = current_analytical["snm_mv"], target_analytical["snm_mv"]
    result["analytical_read_snm_comparison"] = {
        "method": "High-Speed CMOS Circuit Technology, Section 3.4.2, Equation 3.36",
        "current": current_analytical,
        "target": target_analytical,
        "current_snm_mv": current_value,
        "target_snm_mv": target_value,
        "delta_mv": (current_value - target_value
                     if current_value is not None and target_value is not None else None),
        "delta_pct": ((current_value / target_value - 1.0) * 100.0
                      if current_value is not None and target_value not in (None, 0) else None),
    }
    return
    limits = {"scan4n_vmin": settings.scan4n_vmin_max,
              "select_write_vmin": settings.select_write_vmin_max,
              "select_read_vmin": settings.select_read_vmin_max}

    analyzed: list[dict] = []
    for record in records:
        wt_complete = all(record.get(name) is not None for name in limits)
        wat_complete = all(record.get(name) is not None for name in target_values)
        wt_pass = (all(float(record[name]) <= limit for name, limit in limits.items())
                   if wt_complete else None)
        worst_normalized = (max(float(record[name]) / limit for name, limit in limits.items())
                            if wt_complete else None)
        deviations: list[float] = []
        within_flags: list[bool] = []
        if wat_complete:
            for name, target in target_values.items():
                value = float(record[name])
                if name.endswith("_vt"):
                    normalized = abs(value - target) * 1000 / settings.vt_tolerance_mv
                else:
                    normalized = abs(value / target - 1) * 100 / settings.idsat_tolerance_pct
                deviations.append(normalized)
                within_flags.append(normalized <= 1.0)
        distance = math.sqrt(sum(x*x for x in deviations) / len(deviations)) if deviations else None
        target_band_pass = all(within_flags) if within_flags else None
        if target_band_pass is None or wt_pass is None:
            consistency = "N/A"
        elif target_band_pass and wt_pass:
            consistency = "TRUE ACCEPT"
        elif target_band_pass and not wt_pass:
            consistency = "FALSE ACCEPT"
        elif not target_band_pass and wt_pass:
            consistency = "FALSE REJECT"
        else:
            consistency = "TRUE REJECT"
        analyzed.append({**record, "wat_complete": wat_complete, "wt_complete": wt_complete,
                         "target_distance": distance, "target_band_pass": target_band_pass,
                         "wt_pass": wt_pass, "worst_normalized_vmin": worst_normalized,
                         "consistency": consistency})

    paired = [row for row in analyzed if row["wat_complete"] and row["wt_complete"]]
    inside = [row for row in paired if row["target_band_pass"]]
    outside = [row for row in paired if not row["target_band_pass"]]
    pass_rate = lambda group: (100 * sum(bool(row["wt_pass"]) for row in group) / len(group)
                               if group else None)
    inside_rate, outside_rate = pass_rate(inside), pass_rate(outside)
    gap = None if inside_rate is None or outside_rate is None else inside_rate - outside_rate
    xs = [float(row["target_distance"]) for row in paired]
    ys = [float(row["worst_normalized_vmin"]) for row in paired]
    pearson = _pearson(xs, ys)
    spearman = _pearson(_ranks(xs), _ranks(ys)) if len(xs) >= 3 else None

    enough = (len(paired) >= settings.minimum_statistical_n and len(inside) >= 3 and len(outside) >= 3)
    if not enough:
        verdict = "INSUFFICIENT DATA"
        explanation = (f"Need at least {settings.minimum_statistical_n} paired rows and at least 3 rows "
                       "inside and outside the WAT target band.")
    elif gap is not None and gap >= 10 and spearman is not None and spearman >= 0.20:
        verdict = "SUPPORTED"
        explanation = "Target-band lots show a materially higher WT pass rate and target deviation tracks worse Vmin."
    elif gap is not None and gap <= -10:
        verdict = "CONTRADICTED"
        explanation = "Target-band lots have a lower WT pass rate; review target center, tolerance, and sampling alignment."
    else:
        verdict = "INCONCLUSIVE"
        explanation = "Available data does not show enough separation to prove this WAT target is an effective WT screen."

    parameter_evidence = []
    parameter_labels = {"pu_vt": "PU Vt", "pu_idsat": "PU Isat", "pg_vt": "PG Vt",
                        "pg_idsat": "PG Isat", "pd_vt": "PD Vt", "pd_idsat": "PD Isat"}
    for name, target in target_values.items():
        feature_rows = [row for row in analyzed if row.get(name) is not None and row["wt_complete"]]
        tolerance = (settings.vt_tolerance_mv / 1000 if name.endswith("_vt")
                     else target * settings.idsat_tolerance_pct / 100)
        feature_distance = [abs(float(row[name]) - target) / tolerance for row in feature_rows]
        feature_worst = [float(row["worst_normalized_vmin"]) for row in feature_rows]
        feature_inside = [row for row, distance in zip(feature_rows, feature_distance) if distance <= 1]
        feature_outside = [row for row, distance in zip(feature_rows, feature_distance) if distance > 1]
        feature_inside_rate, feature_outside_rate = pass_rate(feature_inside), pass_rate(feature_outside)
        feature_lift = (None if feature_inside_rate is None or feature_outside_rate is None
                        else feature_inside_rate - feature_outside_rate)
        feature_spearman = (_pearson(_ranks(feature_distance), _ranks(feature_worst))
                            if len(feature_rows) >= 3 else None)
        feature_enough = (len(feature_rows) >= settings.minimum_statistical_n and
                          len(feature_inside) >= 3 and len(feature_outside) >= 3)
        if not feature_enough:
            feature_verdict = "INSUFFICIENT DATA"
        elif feature_lift is not None and feature_lift >= 10 and feature_spearman is not None and feature_spearman >= .20:
            feature_verdict = "SUPPORTED"
        elif feature_lift is not None and feature_lift <= -10:
            feature_verdict = "CONTRADICTED"
        else:
            feature_verdict = "INCONCLUSIVE"
        parameter_evidence.append({"parameter": parameter_labels[name], "field": name,
                                   "target": target, "paired_n": len(feature_rows),
                                   "inside_n": len(feature_inside), "outside_n": len(feature_outside),
                                   "inside_wt_pass_rate_pct": feature_inside_rate,
                                   "outside_wt_pass_rate_pct": feature_outside_rate,
                                   "pass_rate_lift_pct_points": feature_lift,
                                   "deviation_vs_worst_vmin_spearman": feature_spearman,
                                   "verdict": feature_verdict})

    return {
        "objective": "Validate WAT targets using measured WT Vmin outcomes",
        "verdict": verdict, "explanation": explanation, "settings": asdict(settings),
        "counts": {"all_rows": len(analyzed), "wt_complete": sum(row["wt_complete"] for row in analyzed),
                   "paired_wat_wt": len(paired), "inside_target_band": len(inside),
                   "outside_target_band": len(outside)},
        "statistics": {"inside_wt_pass_rate_pct": inside_rate,
                       "outside_wt_pass_rate_pct": outside_rate,
                       "pass_rate_lift_pct_points": gap,
                       "target_distance_vs_worst_vmin_pearson": pearson,
                       "target_distance_vs_worst_vmin_spearman": spearman},
        "current_row": analyzed[-1], "rows": analyzed, "parameter_evidence": parameter_evidence,
        "interpretation": "Positive correlation means farther from the WAT target tends to accompany higher (worse) WT Vmin.",
    }


def analyze_six_mos(cell: SixTWatCell, cfg: Config,
                    datasheet_targets: DatasheetTargets | None = None) -> dict:
    """Full cell-level report for an OO six-device cell."""
    result = analyze(cell.representative(), cfg)
    result["object_mode"] = "6T Independent"
    result["cell"] = {
        "corner": cell.corner,
        "mos": {DISPLAY_MOS_NAMES[name]: asdict(getattr(cell, name))
                for name in ("pu1","pu2","pg1","pg2","pd1","pd2")},
        "method": "asymmetric cross-coupled 6T Read butterfly; cell RSNM uses the smaller state margin",
    }
    half_cell = cell_metric(cell, cfg)
    asymmetric = AsymmetricSram6T(cell, cfg).read_butterfly(
        cfg.nominal_vdd, max(1201, cfg.grid_points))
    baseline = result["baseline_6t"]
    baseline.update(asymmetric)
    butterfly = asymmetric["read_butterfly"]
    baseline["metrics"].update({
        "read_snm_mv": butterfly["snm_mv"],
        "read_snm_upper_left_mv": butterfly["snm_upper_left_mv"],
        "read_snm_lower_right_mv": butterfly["snm_lower_right_mv"],
        "read_snm_delta_mv": butterfly["delta_snm_mv"],
        "read_snm_mismatch_index_pct": butterfly["mismatch_index_pct"],
        "write_snm_proxy_mv": half_cell["write_snm_proxy_mv"],
        "cell_ratio_beta": half_cell["cell_ratio_beta"],
        "pull_up_ratio_beta": half_cell["pull_up_ratio_beta"],
        "cell_ratio_ids_proxy": half_cell["cell_ratio_ids_proxy"],
        "pull_up_ratio_ids_proxy": half_cell["pull_up_ratio_ids_proxy"],
    })
    result["cell"]["baseline_metrics"] = baseline["metrics"]
    result["cell"]["read_snm_state_definition"] = butterfly["coordinate_definition"]
    result["datasheet_targets"] = asdict(datasheet_targets) if datasheet_targets else None
    result["target_comparisons"] = _target_comparisons(cell, datasheet_targets)
    _attach_target_model(result, datasheet_targets, cfg)
    return result


def analyze_three_mos(cell: ThreeTWatCell, cfg: Config,
                      datasheet_targets: DatasheetTargets | None = None) -> dict:
    """Analyze a shared PU/PG/PD object set mapped onto the physical 6T cell."""
    result = analyze(cell.representative(), cfg)
    result["object_mode"] = "3T Merged"
    result["cell"] = {
        "corner": cell.corner,
        "mos": {name.upper(): asdict(getattr(cell, name)) for name in ("pu", "pg", "pd")},
        "method": "three shared objects mapped symmetrically to the physical 6T bitcell",
        "baseline_metrics": result["baseline_6t"]["metrics"],
    }
    result["datasheet_targets"] = asdict(datasheet_targets) if datasheet_targets else None
    result["target_comparisons"] = _target_comparisons(cell, datasheet_targets)
    _attach_target_model(result, datasheet_targets, cfg)
    return result


def validate_config(cfg: Config) -> None:
    positive = ("wat_vdd", "nominal_vdd", "vt_step", "ids_step_pct", "vmin_step",
                "read_snm_limit", "technology_node_nm", "channel_length_nm",
                "pu_width_nm", "pg_width_nm", "pd_width_nm",
                "read_wordline_over_vdd", "read_bitline_over_vdd",
                "write_wordline_over_vdd", "write_high_bitline_over_vdd")
    for name in positive:
        value = getattr(cfg, name)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be greater than zero")
    if cfg.ids_step_pct >= 100:
        raise ValueError("ids_step_pct must be below 100")
    if not math.isfinite(cfg.nominal_temperature_c) or not -100 <= cfg.nominal_temperature_c <= 200:
        raise ValueError("nominal_temperature_c must be between -100 and 200")
    if (cfg.read_wordline_over_vdd > 1.5 or cfg.read_bitline_over_vdd > 1.5 or
            cfg.write_wordline_over_vdd > 1.5 or cfg.write_high_bitline_over_vdd > 1.5):
        raise ValueError("Read/write WL and BL ratios must not exceed 1.5")
    if not math.isfinite(cfg.write_low_bitline_over_vdd) or not 0 <= cfg.write_low_bitline_over_vdd <= 1.5:
        raise ValueError("write_low_bitline_over_vdd must be between 0 and 1.5")
    if cfg.vmin_start <= 0 or cfg.vmin_stop < cfg.vmin_start:
        raise ValueError("Vmin range must satisfy 0 < start <= stop")


COLORS = ["#111827", "#2563eb", "#dc2626", "#7c3aed", "#059669"]
# Chart-only scale. This does not alter the electrical VDD used by the model.
SNM_PLOT_AXIS_MAX_V = 1.20


def _read_vtc_pair(data: dict) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Return both curves in common (Vin, Vout) plot coordinates."""
    direct = data["read_vtc"]
    mirrored = data.get("read_vtc_mirrored")
    if mirrored is None:
        mirrored = _inverse_vtc(direct)
    return direct, mirrored


def _fmt(value: float | None, digits: int = 3) -> str:
    return "N/A" if value is None else f"{value:.{digits}f}"


def _interpolate_rsnm_vcc_point(low: RsnmVccPoint, high: RsnmVccPoint,
                                vcc_v: float) -> RsnmVccPoint:
    """Linearly interpolate measured electrical inputs between two VDD rows."""
    if high.vcc_v <= low.vcc_v:
        raise ValueError("VDD interpolation requires increasing endpoints")
    fraction = (vcc_v - low.vcc_v) / (high.vcc_v - low.vcc_v)

    def mos(a: MosWat, b: MosWat) -> MosWat:
        return MosWat(a.vt + fraction * (b.vt - a.vt),
                      a.ids + fraction * (b.ids - a.ids))

    return RsnmVccPoint(vcc_v, mos(low.pu, high.pu), mos(low.pg, high.pg),
                        mos(low.pd, high.pd))


def analyze_rsnm_vcc_curve(points: list[RsnmVccPoint], cfg: Config,
                           fit_points: int = 801) -> dict:
    """Calculate grouped-6T Read SNM at each manually entered VDD point.

    Each Idsat value is calibrated at its own row VDD.  Eye closure is only
    estimated when the supplied rows bracket an invalid-to-valid butterfly-eye
    transition; electrical values inside that bracket are linearly interpolated.
    """
    if len(points) < 2:
        raise ValueError("Enter at least two VDD rows")
    fit_points = max(201, int(fit_points))
    ordered = sorted(points, key=lambda point: point.vcc_v)
    for index, point in enumerate(ordered):
        if not math.isfinite(point.vcc_v) or not 0 < point.vcc_v <= SNM_PLOT_AXIS_MAX_V:
            raise ValueError(f"Row {index + 1}: VDD must be > 0 and <= {SNM_PLOT_AXIS_MAX_V:.2f} V")
        if index and abs(point.vcc_v - ordered[index - 1].vcc_v) < 1e-12:
            raise ValueError(f"Duplicate VDD row: {point.vcc_v:.6g} V")
        for name, mos in (("PU", point.pu), ("PG", point.pg), ("PD", point.pd)):
            if not math.isfinite(mos.vt) or mos.vt <= 0:
                raise ValueError(f"Row {index + 1} {name}: Vt must be greater than zero")
            if not math.isfinite(mos.ids) or mos.ids < 0:
                raise ValueError(f"Row {index + 1} {name}: Idsat must be zero or greater")

    def evaluate(point: RsnmVccPoint) -> dict:
        point_cfg = replace(cfg, nominal_vdd=point.vcc_v, wat_vdd=point.vcc_v,
                            grid_points=fit_points)
        wat = WatPoint(
            f"VDD_{point.vcc_v:.6g}", point.pu.vt, point.pu.ids,
            point.pg.vt, point.pg.ids, point.pd.vt, point.pd.ids,
        )
        butterfly = Sram6T(wat, point_cfg).butterfly_squares(
            point.vcc_v, "read", points=fit_points)
        valid = bool(butterfly.get("valid") and butterfly.get("snm_mv") is not None and
                     butterfly["snm_mv"] > 0)
        return {"valid": valid, "snm_mv": butterfly.get("snm_mv") if valid else None,
                "reason": butterfly.get("reason", ""), "butterfly": butterfly}

    evaluated = []
    for point in ordered:
        result = evaluate(point)
        evaluated.append({
            "vcc_v": point.vcc_v,
            "pu_vt_v": point.pu.vt, "pu_idsat_ua": point.pu.ids,
            "pg_vt_v": point.pg.vt, "pg_idsat_ua": point.pg.ids,
            "pd_vt_v": point.pd.vt, "pd_idsat_ua": point.pd.ids,
            "rsnm_mv": result["snm_mv"], "valid_eye": result["valid"],
            "status": "VALID" if result["valid"] else "NO VALID EYE",
            "reason": result["reason"],
        })

    closure = None
    for index in range(len(ordered) - 1):
        if evaluated[index]["valid_eye"] or not evaluated[index + 1]["valid_eye"]:
            continue
        low_point, high_point = ordered[index], ordered[index + 1]
        low_v, high_v = low_point.vcc_v, high_point.vcc_v
        for _ in range(16):
            middle_v = (low_v + high_v) / 2.0
            middle_point = _interpolate_rsnm_vcc_point(low_point, high_point, middle_v)
            if evaluate(middle_point)["valid"]:
                high_v = middle_v
            else:
                low_v = middle_v
        closure = {
            "estimated_vcc_v": (low_v + high_v) / 2.0,
            "lower_invalid_vcc_v": low_v,
            "upper_valid_vcc_v": high_v,
            "source_low_vcc_v": low_point.vcc_v,
            "source_high_vcc_v": high_point.vcc_v,
            "method": "Bisection with linear interpolation of Vt and Idsat between bracketing rows",
        }
        break

    return {
        "rows": evaluated,
        "eye_closure": closure,
        "axis_max_v": SNM_PLOT_AXIS_MAX_V,
        "fit_points": fit_points,
        "definition": "Estimated eye-closure VDD is a compact-model boundary, not measured WT Vmin",
    }


def rsnm_vcc_curve_svg(analysis: dict, width: int = 1280, height: int = 720) -> str:
    """Render Read SNM versus manually supplied operating VDD values."""
    rows = analysis["rows"]
    left, top, plot_w, plot_h = 110, 105, 1050, 470
    max_rsnm = max((row["rsnm_mv"] for row in rows if row["rsnm_mv"] is not None), default=50.0)
    y_max = max(50.0, math.ceil(max_rsnm / 50.0) * 50.0)

    def xy(vcc_v: float, rsnm_mv: float) -> tuple[float, float]:
        return (left + vcc_v / SNM_PLOT_AXIS_MAX_V * plot_w,
                top + (1.0 - rsnm_mv / y_max) * plot_h)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Estimated Read SNM versus Model VDD" style="font-family:Calibri,Microsoft JhengHei,Arial,sans-serif">',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        '<text x="52" y="54" fill="#1D1D1F" font-size="38" font-weight="700">Estimated RSNM versus Model VDD</text>',
        '<path d="M52 79 h38" stroke="#007AFF" stroke-width="4"/><text x="101" y="85" fill="#3A3A3C" font-size="16">Manual VDD / PU / PG / PD WAT inputs</text>',
    ]
    for step in range(7):
        voltage = step * 0.2
        x, _ = xy(voltage, 0.0)
        parts += [f'<path d="M{x:.1f} {top} V{top+plot_h}" stroke="#E5E5EA" stroke-width="1"/>',
                  f'<text x="{x:.1f}" y="{top+plot_h+27}" text-anchor="middle" fill="#6E6E73" font-size="14">{voltage:.1f}</text>']
    for step in range(6):
        value = y_max * step / 5.0
        _, y = xy(0.0, value)
        parts += [f'<path d="M{left} {y:.1f} H{left+plot_w}" stroke="#E5E5EA" stroke-width="1"/>',
                  f'<text x="{left-12}" y="{y+5:.1f}" text-anchor="end" fill="#6E6E73" font-size="14">{value:.0f}</text>']

    valid_rows = [row for row in rows if row["rsnm_mv"] is not None]
    curve_points = []
    closure = analysis.get("eye_closure")
    if closure:
        curve_points.append(xy(closure["estimated_vcc_v"], 0.0))
    curve_points.extend(xy(row["vcc_v"], row["rsnm_mv"]) for row in valid_rows)
    for row in valid_rows:
        x, y = xy(row["vcc_v"], row["rsnm_mv"])
        parts.append(
            f'<path data-vdd-guide="{row["vcc_v"]:.2f}" d="M{x:.1f} {y+6:.1f} V{top+plot_h:.1f}" '
            'stroke="#B9D7FF" stroke-width="1.5" stroke-dasharray="4 5"/>'
        )
    if curve_points:
        points_text = " ".join(f"{x:.1f},{y:.1f}" for x, y in curve_points)
        parts.append(f'<polyline points="{points_text}" fill="none" stroke="#007AFF" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>')
    for index, row in enumerate(valid_rows):
        x, y = xy(row["vcc_v"], row["rsnm_mv"])
        vcc = row["vcc_v"]
        if vcc <= .375:
            label_dx, label_dy, label_anchor = (-18, -36, "end")
        elif vcc <= .385:
            label_dx, label_dy, label_anchor = (20, -52, "start")
        elif vcc <= .42:
            label_dx, label_dy, label_anchor = (22, 26, "start")
        elif vcc <= .47:
            label_dx, label_dy, label_anchor = (0, 42, "middle")
        elif vcc <= .53:
            label_dx, label_dy, label_anchor = (0, 38, "middle")
        elif vcc <= .65:
            label_dx, label_dy, label_anchor = (0, -40, "middle")
        else:
            label_dx, label_dy, label_anchor = (0, -30 if index % 2 == 0 else 28, "middle")
        parts += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#FFFFFF" stroke="#007AFF" stroke-width="3"/>',
                  f'<text x="{x+label_dx:.1f}" y="{y+label_dy:.1f}" text-anchor="{label_anchor}" fill="#1D1D1F" font-size="14" font-weight="700">{row["rsnm_mv"]:.1f} mV</text>']
    for row in rows:
        if row["valid_eye"]:
            continue
        x, y = xy(row["vcc_v"], 0.0)
        parts.append(f'<path d="M{x-5:.1f} {y-5:.1f} L{x+5:.1f} {y+5:.1f} M{x+5:.1f} {y-5:.1f} L{x-5:.1f} {y+5:.1f}" stroke="#8E8E93" stroke-width="2"/>')
    if closure:
        boundary_x, boundary_y = xy(closure["estimated_vcc_v"], 0.0)
        parts += [f'<path d="M{boundary_x:.1f} {top} V{top+plot_h}" stroke="#FF9500" stroke-width="3" stroke-dasharray="8 6"/>',
                  f'<circle cx="{boundary_x:.1f}" cy="{boundary_y:.1f}" r="7" fill="#FFFFFF" stroke="#FF9500" stroke-width="3"/>',
                  f'<text x="{boundary_x+12:.1f}" y="{top+28}" fill="#C56A00" font-size="16" font-weight="700">Estimated eye-closure VDD {closure["estimated_vcc_v"]:.4f} V</text>']
    else:
        parts.append(f'<text x="{left+plot_w-8}" y="{top+28}" text-anchor="end" fill="#C56A00" font-size="16" font-weight="700">Eye-closure VDD not bracketed by the entered rows</text>')
    parts += [
        f'<text x="{left+plot_w/2}" y="{height-62}" text-anchor="middle" fill="#1D1D1F" font-size="18">Model VDD (V)</text>',
        f'<text x="38" y="{top+plot_h/2}" transform="rotate(-90 38 {top+plot_h/2})" text-anchor="middle" fill="#1D1D1F" font-size="18">Read SNM (mV)</text>',
        f'<text x="{width/2}" y="{height-18}" text-anchor="middle" fill="#6E6E73" font-size="14">X = no valid butterfly eye. Boundary is a compact-model estimate and is not measured WT Vmin.</text>',
        '</svg>',
    ]
    return "".join(parts)


def write_rsnm_vcc_curve_outputs(analysis: dict, out_dir: str | os.PathLike[str]) -> Path:
    """Write CSV, SVG, PNG and an HTML report for manual RSNM/VDD analysis."""
    out = Path(out_dir)
    image_dir = out / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    svg_path = image_dir / "01_rsnm_vs_model_vcc.svg"
    png_path = image_dir / "01_rsnm_vs_model_vcc.png"
    svg_path.write_text(rsnm_vcc_curve_svg(analysis), encoding="utf-8")
    try:
        from reportlab.graphics import renderPM
        from svglib.svglib import svg2rlg
    except ImportError as exc:
        raise RuntimeError("PNG export packages are missing. Run: python -m pip install -r requirements.txt") from exc
    drawing = svg2rlg(str(svg_path))
    if drawing is None:
        raise RuntimeError("Could not render RSNM versus VDD chart")
    renderPM.drawToFile(drawing, str(png_path), fmt="PNG", dpi=180, backend="rlPyCairo")

    csv_fields = ["vcc_v", "pu_vt_v", "pu_idsat_ua", "pg_vt_v", "pg_idsat_ua",
                  "pd_vt_v", "pd_idsat_ua", "rsnm_mv", "valid_eye", "status", "reason"]
    with open(out / "rsnm_vcc_curve.csv", "w", newline="", encoding="utf-8-sig") as source:
        writer = csv.DictWriter(source, fieldnames=csv_fields)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in csv_fields} for row in analysis["rows"])
    (out / "rsnm_vcc_curve.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")

    closure = analysis.get("eye_closure")
    closure_text = (f'{closure["estimated_vcc_v"]:.4f} V' if closure else "Not bracketed")
    table_rows = "".join(
        f'<tr><td>{row["vcc_v"]:.3f}</td><td>{row["pu_vt_v"]:.4f}</td><td>{row["pu_idsat_ua"]:.3f}</td>'
        f'<td>{row["pg_vt_v"]:.4f}</td><td>{row["pg_idsat_ua"]:.3f}</td>'
        f'<td>{row["pd_vt_v"]:.4f}</td><td>{row["pd_idsat_ua"]:.3f}</td>'
        f'<td>{_fmt(row["rsnm_mv"], 2)}</td><td>{row["status"]}</td></tr>'
        for row in analysis["rows"])
    document = f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HV28 SRAM Analysis - RSNM vs VDD</title>
    <style>:root{{font:100%/1.5 Calibri,"Microsoft JhengHei",Arial,sans-serif;color:#1d1d1f;background:#f5f5f7}}*{{box-sizing:border-box}}body{{margin:0;padding:2rem}}main{{max-width:1500px;margin:auto}}h1{{font-size:2.6rem;letter-spacing:-.03em}}section{{background:#fff;border-radius:1.25rem;padding:1.5rem;margin:1rem 0}}img{{display:block;width:100%;height:auto;border:1px solid #e5e5ea;border-radius:1rem}}table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}th,td{{padding:.7rem;border-bottom:1px solid #e5e5ea;text-align:right}}th:first-child,td:first-child{{text-align:left}}.note{{color:#6e6e73}}</style></head><body><main>
    <h1>HV28 SRAM Analysis</h1><p>Manual VDD / PU / PG / PD WAT curve analysis</p>
    <section><h2>Estimated RSNM versus Model VDD</h2><p><b>Estimated eye-closure VDD:</b> {closure_text}</p><img src="images/{png_path.name}" alt="Estimated Read SNM versus Model VDD"><p class="note">This is a compact-model estimate derived from manually entered Vt and Idsat. It is not measured WT Vmin.</p></section>
    <section><h2>Input and calculated values</h2><table><thead><tr><th>VDD (V)</th><th>PU Vt</th><th>PU Isat</th><th>PG Vt</th><th>PG Isat</th><th>PD Vt</th><th>PD Isat</th><th>RSNM (mV)</th><th>Status</th></tr></thead><tbody>{table_rows}</tbody></table></section>
    </main></body></html>'''
    report = out / "rsnm_vcc_report.html"
    report.write_text(document, encoding="utf-8")
    return report


def butterfly_svg(items: list[dict], vdd: float, width: int = 900, height: int = 590) -> str:
    left, top, right, bottom = 62, 25, 18, 55
    pw, ph = width-left-right, height-top-bottom
    def xy(x: float, y: float) -> tuple[float, float]:
        return left + x/vdd*pw, top + (1-y/vdd)*ph
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Read butterfly curve" style="font-family:Calibri,Arial,sans-serif">',
             '<rect width="100%" height="100%" fill="white"/>']
    for i in range(6):
        val = vdd*i/5; x, y = xy(val, val)
        parts.append(f'<path d="M {x:.1f} {top} V {top+ph} M {left} {y:.1f} H {left+pw}" stroke="#e5e7eb" stroke-width="1"/>')
        parts.append(f'<text x="{x:.1f}" y="{top+ph+25}" text-anchor="middle" font-size="15">{val:.2f}</text>')
        parts.append(f'<text x="{left-9}" y="{y+5:.1f}" text-anchor="end" font-size="15">{val:.2f}</text>')
    for idx, item in enumerate(items):
        pts = item["read_vtc"]
        p1 = " ".join(f"{xy(x,y)[0]:.1f},{xy(x,y)[1]:.1f}" for x,y in pts)
        p2 = " ".join(f"{xy(y,x)[0]:.1f},{xy(y,x)[1]:.1f}" for x,y in pts)
        c = COLORS[idx]
        parts.append(f'<polyline points="{p1}" fill="none" stroke="{c}" stroke-width="1.7"/>')
        parts.append(f'<polyline points="{p2}" fill="none" stroke="{c}" stroke-width="1.7" opacity=".82"/>')
        lx = left+10+(idx%2)*235; ly = top+17+(idx//2)*20
        parts.append(f'<path d="M {lx} {ly-5} h 25" stroke="{c}" stroke-width="4"/><text x="{lx+32}" y="{ly}" font-size="15">{html.escape(item["label"])}</text>')
    parts += [f'<text x="{left+pw/2}" y="{height-8}" text-anchor="middle" font-size="17">Q (V)</text>',
              f'<text x="18" y="{top+ph/2}" transform="rotate(-90 18 {top+ph/2})" text-anchor="middle" font-size="17">QB (V)</text>', '</svg>']
    return "".join(parts)


def bar_svg(items: list[dict], key: str, title: str, unit: str, width: int = 900, height: int = 380) -> str:
    vals = [x["metrics"][key] for x in items]
    finite = [v for v in vals if v is not None]
    ymax = max(finite or [1.0]) * 1.18 or 1.0
    left, top, bottom = 55, 45, 55
    pw, ph = width-left-15, height-top-bottom
    barw = pw/len(items)*0.58
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" style="font-family:Calibri,Arial,sans-serif"><rect width="100%" height="100%" fill="white"/>',
         f'<text x="{width/2}" y="30" text-anchor="middle" font-size="19" font-weight="650">{html.escape(title)}</text>',
         f'<path d="M {left} {top} V {top+ph} H {left+pw}" fill="none" stroke="#374151"/>']
    for i, (item, val) in enumerate(zip(items, vals)):
        x = left + (i+.5)*pw/len(items); h = 0 if val is None else val/ymax*ph
        p.append(f'<rect x="{x-barw/2:.1f}" y="{top+ph-h:.1f}" width="{barw:.1f}" height="{h:.1f}" fill="{COLORS[i]}" opacity=".86"/>')
        p.append(f'<text x="{x:.1f}" y="{top+ph-h-7:.1f}" text-anchor="middle" font-size="15">{_fmt(val)}</text>')
        p.append(f'<text x="{x:.1f}" y="{top+ph+22}" transform="rotate(18 {x:.1f} {top+ph+22})" text-anchor="start" font-size="14">{html.escape(item["label"])}</text>')
    p.append(f'<text x="18" y="{top+ph/2}" transform="rotate(-90 18 {top+ph/2})" text-anchor="middle" font-size="15">{unit}</text></svg>')
    return "".join(p)


def snm_overview_svg(result: dict, width: int = 1440, height: int = 720) -> str:
    """Read VTC comparison using Vin on X and Vout on Y, both in volts."""
    current = result["baseline_6t"]
    target = result.get("target_6t", current)
    vdd = result["config"]["nominal_vdd"]
    axis_max = SNM_PLOT_AXIS_MAX_V
    lot_label_raw = str(result["wat"]["corner"])
    lot_label = html.escape(lot_label_raw)
    analytical = result.get("analytical_read_snm_comparison", {})
    plot_left, plot_top, plot_w, plot_h = 145, 145, 1140, 455

    def xy(vin: float, vout: float) -> tuple[float, float]:
        return (plot_left + vin / axis_max * plot_w,
                plot_top + (1.0 - vout / axis_max) * plot_h)

    current_value = current["metrics"]["read_snm_mv"]
    target_value = target["metrics"]["read_snm_mv"]
    delta = current_value - target_value
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Read SNM Lot/Wafer versus target VTC comparison" style="font-family:Calibri,Arial,sans-serif">',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        '<text x="54" y="48" fill="#1D1D1F" font-size="30" font-weight="700">Read SNM Target Comparison</text>',
        f'<path d="M54 82 h34" stroke="#007AFF" stroke-width="4"/><text x="98" y="88" fill="#3A3A3C" font-size="17">{lot_label} WAT VTC</text>',
        f'<path d="M270 82 h34" stroke="#007AFF" stroke-width="4" stroke-dasharray="10 7"/><text x="314" y="88" fill="#3A3A3C" font-size="17">{lot_label} mirrored VTC</text>',
        '<path d="M570 82 h34" stroke="#FF9500" stroke-width="4"/><text x="614" y="88" fill="#3A3A3C" font-size="17">WAT Target VTC</text>',
        '<path d="M842 82 h34" stroke="#FF9500" stroke-width="4" stroke-dasharray="10 7"/><text x="886" y="88" fill="#3A3A3C" font-size="17">WAT Target mirrored VTC</text>',
    ]
    for voltage in (0.0, 0.30, 0.60, 0.90, axis_max):
        px, py = xy(voltage, voltage)
        parts += [
            f'<path d="M{px:.1f} {plot_top} V{plot_top+plot_h} M{plot_left} {py:.1f} H{plot_left+plot_w}" stroke="#E5E5EA" stroke-width="1"/>',
            f'<text x="{px:.1f}" y="{plot_top+plot_h+27}" text-anchor="middle" fill="#6E6E73" font-size="15">{voltage:.2f}</text>',
            f'<text x="{plot_left-12}" y="{py+5:.1f}" text-anchor="end" fill="#6E6E73" font-size="15">{voltage:.2f}</text>',
        ]
    for data, color in ((current, "#007AFF"), (target, "#FF9500")):
        direct_curve, mirrored_curve = _read_vtc_pair(data)
        direct = " ".join(f'{xy(vin,vout)[0]:.1f},{xy(vin,vout)[1]:.1f}' for vin, vout in direct_curve)
        mirrored = " ".join(f'{xy(vin,vout)[0]:.1f},{xy(vin,vout)[1]:.1f}' for vin, vout in mirrored_curve)
        parts += [f'<polyline points="{direct}" fill="none" stroke="{color}" stroke-width="4"/>',
                  f'<polyline points="{mirrored}" fill="none" stroke="{color}" stroke-width="4" stroke-dasharray="10 7" opacity=".9"/>']
    analytical_current = analytical.get("current_snm_mv")
    analytical_target = analytical.get("target_snm_mv")
    analytical_text = (f'Analytical RSNM: {lot_label_raw} {analytical_current:.1f} mV · WAT Target {analytical_target:.1f} mV'
                       if analytical_current is not None and analytical_target is not None
                       else 'Analytical RSNM: N/A for this input set')
    parts += [
        f'<text x="{plot_left+plot_w-8}" y="{plot_top+30}" text-anchor="end" fill="#007AFF" font-size="18" font-weight="700">{lot_label} {current_value:.1f} mV</text>',
        f'<text x="{plot_left+plot_w-8}" y="{plot_top+58}" text-anchor="end" fill="#C56A00" font-size="18" font-weight="700">WAT Target {target_value:.1f} mV</text>',
        f'<text x="{plot_left+plot_w-8}" y="{plot_top+86}" text-anchor="end" fill="#1D1D1F" font-size="17" font-weight="700">Delta {delta:+.1f} mV</text>',
        f'<text x="{plot_left+plot_w-8}" y="{plot_top+114}" text-anchor="end" fill="#5856D6" font-size="16" font-weight="700">{html.escape(analytical_text)}</text>',
        f'<text x="{plot_left+plot_w/2}" y="{height-50}" text-anchor="middle" fill="#1D1D1F" font-size="19">Vin (V)</text>',
        f'<text x="48" y="{plot_top+plot_h/2}" transform="rotate(-90 48 {plot_top+plot_h/2})" text-anchor="middle" fill="#1D1D1F" font-size="19">Vout (V)</text>',
        '<text x="720" y="700" text-anchor="middle" fill="#6E6E73" font-size="15">Read bias uses the configured WL and BL/BLB levels. Axis values are actual volts.</text></svg>',
    ]
    return "".join(parts)


def write_snm_overview_svg(result: dict, width: int = 1440, height: int = 720) -> str:
    """Write-condition VTC comparison: BL low side versus mirrored BLB high side."""
    current = result["baseline_6t"]
    target = result.get("target_6t", current)
    cfg, wat = result["config"], result["wat"]
    vdd, lot_raw = cfg["nominal_vdd"], str(wat["corner"])
    axis_max = SNM_PLOT_AXIS_MAX_V
    lot_label = html.escape(lot_raw)
    left, top, pw, ph = 145, 145, 1140, 455

    def xy(vin: float, vout: float) -> tuple[float, float]:
        return left + vin / axis_max * pw, top + (1.0 - vout / axis_max) * ph

    current_value = current["metrics"]["write_snm_proxy_mv"]
    target_value = target["metrics"]["write_snm_proxy_mv"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Write SNM target comparison" style="font-family:Calibri,Arial,sans-serif">',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        '<text x="54" y="48" fill="#1D1D1F" font-size="30" font-weight="700">Write SNM Target Comparison</text>',
        f'<path d="M54 82 h34" stroke="#007AFF" stroke-width="4"/><text x="98" y="88" fill="#3A3A3C" font-size="17">{lot_label} BL-low VTC</text>',
        f'<path d="M270 82 h34" stroke="#007AFF" stroke-width="4" stroke-dasharray="10 7"/><text x="314" y="88" fill="#3A3A3C" font-size="17">{lot_label} mirrored BLB-high VTC</text>',
        '<path d="M650 82 h34" stroke="#FF9500" stroke-width="4"/><text x="694" y="88" fill="#3A3A3C" font-size="17">WAT Target write VTC pair</text>',
    ]
    for voltage in (0.0, 0.30, 0.60, 0.90, axis_max):
        px, py = xy(voltage, voltage)
        parts += [f'<path d="M{px:.1f} {top} V{top+ph} M{left} {py:.1f} H{left+pw}" stroke="#E5E5EA" stroke-width="1"/>',
                  f'<text x="{px:.1f}" y="{top+ph+27}" text-anchor="middle" fill="#6E6E73" font-size="15">{voltage:.2f}</text>',
                  f'<text x="{left-12}" y="{py+5:.1f}" text-anchor="end" fill="#6E6E73" font-size="15">{voltage:.2f}</text>']
    for data, color in ((current, "#007AFF"), (target, "#FF9500")):
        low = " ".join(f'{xy(x, y)[0]:.1f},{xy(x, y)[1]:.1f}' for x, y in data["write_vtc_low"])
        high = " ".join(f'{xy(y, x)[0]:.1f},{xy(y, x)[1]:.1f}' for x, y in data["write_vtc_high"])
        parts += [f'<polyline points="{low}" fill="none" stroke="{color}" stroke-width="4"/>',
                  f'<polyline points="{high}" fill="none" stroke="{color}" stroke-width="4" stroke-dasharray="10 7" opacity=".9"/>']
    parts += [
        f'<text x="{left+pw-8}" y="{top+30}" text-anchor="end" fill="#007AFF" font-size="18" font-weight="700">{lot_label} WSNM proxy {current_value:.1f} mV</text>',
        f'<text x="{left+pw-8}" y="{top+58}" text-anchor="end" fill="#C56A00" font-size="18" font-weight="700">WAT Target {target_value:.1f} mV</text>',
        f'<text x="{left+pw-8}" y="{top+86}" text-anchor="end" fill="#1D1D1F" font-size="17" font-weight="700">Delta {current_value-target_value:+.1f} mV</text>',
        f'<text x="{left+pw/2}" y="{height-50}" text-anchor="middle" fill="#1D1D1F" font-size="19">Vin (V)</text>',
        f'<text x="48" y="{top+ph/2}" transform="rotate(-90 48 {top+ph/2})" text-anchor="middle" fill="#1D1D1F" font-size="19">Vout (V)</text>',
        f'<text x="720" y="700" text-anchor="middle" fill="#6E6E73" font-size="15">Write bias: WL={cfg["write_wordline_over_vdd"]:.2f}×VDD, BL={cfg["write_low_bitline_over_vdd"]:.2f}×VDD, BLB={cfg["write_high_bitline_over_vdd"]:.2f}×VDD. WSNM is a WAT-calibrated bitline-noise proxy.</text>',
        '</svg>',
    ]
    return "".join(parts)


def read_snm_butterfly_svg(result: dict, width: int = 1440, height: int = 820) -> str:
    """Read butterfly plots with independently fitted maximum squares."""
    current = result["baseline_6t"]
    target = result.get("target_6t", current)
    analytical = result.get("analytical_read_snm_comparison", {})
    lot_label = str(result["wat"]["corner"])
    vdd = result["config"]["nominal_vdd"]
    axis_max = SNM_PLOT_AXIS_MAX_V
    panels = (
        (lot_label, current, "#007AFF", analytical.get("current_snm_mv")),
        ("WAT Target", target, "#FF9500", analytical.get("target_snm_mv")),
    )
    margin_x, gap = 60, 64
    panel_w = (width - 2 * margin_x - gap) / 2
    plot_top, plot_h = 158, 480
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Read SNM butterfly maximum squares" style="font-family:Calibri,Arial,sans-serif">',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        '<text x="54" y="48" fill="#1D1D1F" font-size="30" font-weight="700">Read SNM Butterfly Analysis</text>',
        '<path d="M54 82 h34" stroke="#3A3A3C" stroke-width="4"/><text x="98" y="88" fill="#3A3A3C" font-size="17">Right inverter VTC</text>',
        '<path d="M285 82 h34" stroke="#3A3A3C" stroke-width="4" stroke-dasharray="10 7"/><text x="329" y="88" fill="#3A3A3C" font-size="17">Inverse left inverter VTC</text>',
        '<rect x="600" y="68" width="24" height="24" fill="none" stroke="#34C759" stroke-width="3"/><text x="636" y="88" fill="#3A3A3C" font-size="17">Maximum squares 1 and 2</text>',
        '<text x="970" y="88" fill="#5856D6" font-size="17">Analytical RSNM shown as an independent reference</text>',
    ]
    for index, (title, data, color, analytical_value) in enumerate(panels):
        x0 = margin_x + index * (panel_w + gap)
        # Equal X/Y pixel scale keeps a voltage square visually square.
        plot_left, plot_w = x0 + 95, 480

        def xy(x_value: float, y_value: float) -> tuple[float, float]:
            return (plot_left + x_value / axis_max * plot_w,
                    plot_top + (1.0 - y_value / axis_max) * plot_h)

        parts += [f'<text x="{x0:.1f}" y="126" fill="#1D1D1F" font-size="25" font-weight="700">{html.escape(title)}</text>',
                  f'<text x="{x0+panel_w:.1f}" y="126" text-anchor="end" fill="{color}" font-size="18" font-weight="700">Geometric RSNM {data["read_butterfly"]["snm_mv"]:.1f} mV</text>']
        for voltage in (0.0, 0.30, 0.60, 0.90, axis_max):
            px, py = xy(voltage, voltage)
            parts += [f'<path d="M{px:.1f} {plot_top} V{plot_top+plot_h} M{plot_left} {py:.1f} H{plot_left+plot_w}" stroke="#E5E5EA" stroke-width="1"/>',
                      f'<text x="{px:.1f}" y="{plot_top+plot_h+26}" text-anchor="middle" fill="#6E6E73" font-size="15">{voltage:.2f}</text>',
                      f'<text x="{plot_left-10}" y="{py+5:.1f}" text-anchor="end" fill="#6E6E73" font-size="15">{voltage:.2f}</text>']
        direct_curve, mirrored_curve = _read_vtc_pair(data)
        direct = " ".join(f'{xy(x,y)[0]:.1f},{xy(x,y)[1]:.1f}' for x, y in direct_curve)
        mirrored = " ".join(f'{xy(x,y)[0]:.1f},{xy(x,y)[1]:.1f}' for x, y in mirrored_curve)
        parts += [f'<polyline points="{direct}" fill="none" stroke="{color}" stroke-width="4"/>',
                  f'<polyline points="{mirrored}" fill="none" stroke="{color}" stroke-width="4" stroke-dasharray="10 7"/>']

        squares = data["read_butterfly"]["squares"]
        limiting = min(square["side_v"] for square in squares)
        for square in squares:
            left, top = xy(square["x_v"], square["y_v"] + square["side_v"])
            side_px_x = square["side_v"] / axis_max * plot_w
            side_px_y = square["side_v"] / axis_max * plot_h
            stroke_width = 4 if abs(square["side_v"] - limiting) < 1e-12 else 3
            state_label = "QB=0 / Q=1" if square["lobe"] == 1 else "QB=1 / Q=0"
            value_label = f'{square["side_mv"]:.1f} mV'
            parts += [f'<rect x="{left:.1f}" y="{top:.1f}" width="{side_px_x:.1f}" height="{side_px_y:.1f}" fill="#EFFAF2" stroke="#34C759" stroke-width="{stroke_width}"/>',
                      f'<text x="{left+side_px_x/2:.1f}" y="{top+side_px_y/2-2:.1f}" text-anchor="middle" fill="#1D1D1F" font-size="15" font-weight="700">{state_label}</text>',
                      f'<text x="{left+side_px_x/2:.1f}" y="{top+side_px_y/2+17:.1f}" text-anchor="middle" fill="#1D1D1F" font-size="14" font-weight="700">{value_label}</text>']
            arrow_x = min(left + side_px_x + 16, plot_left + plot_w - 8)
            parts += [f'<path d="M{arrow_x:.1f} {top+3:.1f} V{top+side_px_y-3:.1f} M{arrow_x-5:.1f} {top+3:.1f} H{arrow_x+5:.1f} M{arrow_x-5:.1f} {top+side_px_y-3:.1f} H{arrow_x+5:.1f}" stroke="#34C759" stroke-width="2"/>']

        eq_text = f'Analytical RSNM {analytical_value:.1f} mV' if analytical_value is not None else 'Analytical RSNM N/A'
        butterfly = data["read_butterfly"]
        state_text = (f'Upper {butterfly.get("snm_upper_left_mv", squares[0]["side_mv"]):.1f} mV · '
                      f'Lower {butterfly.get("snm_lower_right_mv", squares[1]["side_mv"]):.1f} mV · '
                      f'Asymmetry {_fmt(butterfly.get("mismatch_index_pct"), 1)}%')
        parts += [f'<text x="{x0:.1f}" y="{plot_top+plot_h+60}" fill="#3A3A3C" font-size="15" font-weight="700">{state_text}</text>',
                  f'<text x="{x0+panel_w:.1f}" y="{plot_top+plot_h+82}" text-anchor="end" fill="#5856D6" font-size="16" font-weight="700">{eq_text}</text>',
                  f'<text x="{plot_left+plot_w/2:.1f}" y="{height-62}" text-anchor="middle" fill="#1D1D1F" font-size="18">Vin (V)</text>',
                  f'<text x="{x0+45:.1f}" y="{plot_top+plot_h/2:.1f}" transform="rotate(-90 {x0+45:.1f} {plot_top+plot_h/2:.1f})" text-anchor="middle" fill="#1D1D1F" font-size="18">Vout (V)</text>']
    parts.append(f'<text x="720" y="{height-14}" text-anchor="middle" fill="#6E6E73" font-size="15">Upper and lower margins represent opposite stored states; cell RSNM is the smaller value.</text></svg>')
    return "".join(parts)


def model_vdd_butterfly_svg(entries: list[dict], width: int = 1440) -> str:
    """Small-multiple Read butterflies with measured-WAT and WAT-target curves."""
    columns = 3
    rows = max(1, math.ceil(len(entries) / columns))
    panel_w, panel_h = 450, 405
    height = 125 + rows * panel_h + 45
    axis_max = SNM_PLOT_AXIS_MAX_V
    has_target = any(item["result"].get("datasheet_targets") for item in entries)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Read SNM butterflies by model VDD" style="font-family:Calibri,Arial,sans-serif">',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        '<text x="54" y="48" fill="#1D1D1F" font-size="30" font-weight="700">Read SNM Butterfly by Model VDD</text>',
        '<path d="M54 82 h32" stroke="#007AFF" stroke-width="4"/><text x="96" y="88" fill="#3A3A3C" font-size="15">WAT Inverter VTC</text>',
        '<path d="M247 82 h32" stroke="#007AFF" stroke-width="4" stroke-dasharray="10 7"/><text x="289" y="88" fill="#3A3A3C" font-size="15">WAT Mirrored VTC</text>',
        '<rect x="452" y="69" width="21" height="21" fill="#EFFAF2" stroke="#34C759" stroke-width="3"/><text x="484" y="88" fill="#3A3A3C" font-size="15">WAT SNM square</text>',
    ]
    if has_target:
        parts += [
            '<path d="M650 82 h32" stroke="#FF9500" stroke-width="4"/><text x="692" y="88" fill="#3A3A3C" font-size="15">Target Inverter VTC</text>',
            '<path d="M865 82 h32" stroke="#FF9500" stroke-width="4" stroke-dasharray="10 7"/><text x="907" y="88" fill="#3A3A3C" font-size="15">Target Mirrored VTC</text>',
            '<rect x="1095" y="69" width="21" height="21" fill="none" stroke="#FF9500" stroke-width="3" stroke-dasharray="6 4"/><text x="1127" y="88" fill="#3A3A3C" font-size="15">Target SNM square</text>',
        ]
    for index, entry in enumerate(entries):
        col, row = index % columns, index // columns
        x0, y0 = 38 + col * 468, 125 + row * panel_h
        left, top, size = x0 + 52, y0 + 72, 250
        data = entry["result"]["baseline_6t"]
        target = entry["result"].get("target_6t", data)
        show_target = bool(entry["result"].get("datasheet_targets"))

        def xy(x: float, y: float) -> tuple[float, float]:
            return left + x / axis_max * size, top + (1 - y / axis_max) * size

        measured_snm = data["metrics"]["read_snm_mv"]
        target_snm = target["metrics"]["read_snm_mv"]
        parts += [f'<text x="{x0}" y="{y0+22}" fill="#1D1D1F" font-size="19" font-weight="700">Operating VDD = {entry["model_vdd_v"]:.3f} V</text>']
        if show_target:
            delta = measured_snm - target_snm
            parts.append(f'<text x="{x0}" y="{y0+47}" fill="#3A3A3C" font-size="14"><tspan fill="#007AFF" font-weight="700">WAT {measured_snm:.1f} mV</tspan><tspan> · </tspan><tspan fill="#FF9500" font-weight="700">Target {target_snm:.1f} mV</tspan><tspan> · Δ {delta:+.1f} mV</tspan></text>')
        else:
            parts.append(f'<text x="{x0}" y="{y0+47}" fill="#007AFF" font-size="14" font-weight="700">WAT RSNM {measured_snm:.1f} mV</text>')
        for voltage in (0.0, 0.60, axis_max):
            px, py = xy(voltage, voltage)
            parts += [f'<path d="M{px:.1f} {top} V{top+size} M{left} {py:.1f} H{left+size}" stroke="#E5E5EA" stroke-width="1"/>',
                      f'<text x="{px:.1f}" y="{top+size+19}" text-anchor="middle" fill="#6E6E73" font-size="12">{voltage:.2f}</text>',
                      f'<text x="{left-7}" y="{py+4:.1f}" text-anchor="end" fill="#6E6E73" font-size="12">{voltage:.2f}</text>']
        direct_curve, mirrored_curve = _read_vtc_pair(data)
        direct = " ".join(f"{xy(x, y)[0]:.1f},{xy(x, y)[1]:.1f}" for x, y in direct_curve)
        mirrored = " ".join(f"{xy(x, y)[0]:.1f},{xy(x, y)[1]:.1f}" for x, y in mirrored_curve)
        parts += [f'<polyline points="{direct}" fill="none" stroke="#007AFF" stroke-width="3"/>',
                  f'<polyline points="{mirrored}" fill="none" stroke="#007AFF" stroke-width="3" stroke-dasharray="8 5"/>']
        if show_target:
            target_direct_curve, target_mirrored_curve = _read_vtc_pair(target)
            target_direct = " ".join(f"{xy(x, y)[0]:.1f},{xy(x, y)[1]:.1f}" for x, y in target_direct_curve)
            target_mirrored = " ".join(f"{xy(x, y)[0]:.1f},{xy(x, y)[1]:.1f}" for x, y in target_mirrored_curve)
            parts += [f'<polyline points="{target_direct}" fill="none" stroke="#FF9500" stroke-width="2.5"/>',
                      f'<polyline points="{target_mirrored}" fill="none" stroke="#FF9500" stroke-width="2.5" stroke-dasharray="8 5"/>']
        for square in data["read_butterfly"]["squares"]:
            sx, sy = xy(square["x_v"], square["y_v"] + square["side_v"])
            side = square["side_v"] / axis_max * size
            label_y = sy - 7 if square["lobe"] == 1 else sy + side + 15
            parts += [f'<rect x="{sx:.1f}" y="{sy:.1f}" width="{side:.1f}" height="{side:.1f}" fill="#EFFAF2" stroke="#34C759" stroke-width="3"/>',
                      f'<text x="{sx+side/2:.1f}" y="{label_y:.1f}" text-anchor="middle" fill="#248A3D" font-size="11" font-weight="700">WAT {square["side_mv"]:.1f} mV</text>']
        if show_target:
            for square in target["read_butterfly"]["squares"]:
                sx, sy = xy(square["x_v"], square["y_v"] + square["side_v"])
                side = square["side_v"] / axis_max * size
                label_y = sy - 20 if square["lobe"] == 1 else sy + side + 28
                parts += [f'<rect x="{sx:.1f}" y="{sy:.1f}" width="{side:.1f}" height="{side:.1f}" fill="none" stroke="#FF9500" stroke-width="2.5" stroke-dasharray="6 4"/>',
                          f'<text x="{sx+side/2:.1f}" y="{label_y:.1f}" text-anchor="middle" fill="#C56A00" font-size="11" font-weight="700">Target {square["side_mv"]:.1f} mV</text>']
        parts += [f'<text x="{left+size/2:.1f}" y="{top+size+48}" text-anchor="middle" fill="#1D1D1F" font-size="14">Vin (V)</text>',
                  f'<text x="{x0+16}" y="{top+size/2:.1f}" transform="rotate(-90 {x0+16} {top+size/2:.1f})" text-anchor="middle" fill="#1D1D1F" font-size="14">Vout (V)</text>']
    parts.append(f'<text x="{width/2}" y="{height-14}" text-anchor="middle" fill="#6E6E73" font-size="14">Each panel is a complete 6T Read butterfly. Vin/Vout axes are fixed at 0 to 1.20 V.</text></svg>')
    return "".join(parts)


def all_model_vdd_butterfly_overlay_svg(entries: list[dict], width: int = 1440, height: int = 920) -> str:
    """Overlay every analyzed operating VDD on one Vin/Vout chart."""
    left, top, plot_size = 125, 145, 660
    axis_max = SNM_PLOT_AXIS_MAX_V
    palette = ("#007AFF", "#34C759", "#AF52DE", "#FF9500", "#FF3B30", "#00A7A7")
    has_target = any(item["result"].get("datasheet_targets") for item in entries)

    def xy(x: float, y: float) -> tuple[float, float]:
        return left + x / axis_max * plot_size, top + (1 - y / axis_max) * plot_size

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="All operating VDD Read SNM butterfly overlay" style="font-family:Calibri,Arial,sans-serif">',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        '<text x="54" y="50" fill="#1D1D1F" font-size="30" font-weight="700">All Operating Voltages — Read SNM Butterfly Overlay</text>',
        '<path d="M55 83 h42" stroke="#1D1D1F" stroke-width="4"/><text x="108" y="89" fill="#3A3A3C" font-size="16">Measured WAT</text>',
    ]
    if has_target:
        parts += ['<path d="M260 83 h42" stroke="#1D1D1F" stroke-width="3" stroke-dasharray="10 7"/><text x="313" y="89" fill="#3A3A3C" font-size="16">WAT Target</text>']
    for step in range(7):
        voltage = step * 0.2
        px, py = xy(voltage, voltage)
        parts += [f'<path d="M{px:.1f} {top} V{top+plot_size} M{left} {py:.1f} H{left+plot_size}" stroke="#E5E5EA" stroke-width="1"/>',
                  f'<text x="{px:.1f}" y="{top+plot_size+28}" text-anchor="middle" fill="#6E6E73" font-size="15">{voltage:.1f} V</text>',
                  f'<text x="{left-12}" y="{py+5:.1f}" text-anchor="end" fill="#6E6E73" font-size="15">{voltage:.1f} V</text>']
    legend_x, legend_y = 925, 160
    for index, entry in enumerate(entries):
        color = palette[index % len(palette)]
        data = entry["result"]["baseline_6t"]
        target = entry["result"].get("target_6t", data)
        show_target = bool(entry["result"].get("datasheet_targets"))
        for points in _read_vtc_pair(data):
            path = " ".join(f"{xy(x, y)[0]:.1f},{xy(x, y)[1]:.1f}" for x, y in points)
            parts.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="3.5" opacity="0.88"/>')
        if show_target:
            for points in _read_vtc_pair(target):
                path = " ".join(f"{xy(x, y)[0]:.1f},{xy(x, y)[1]:.1f}" for x, y in points)
                parts.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2.5" stroke-dasharray="10 7" opacity="0.88"/>')
        y_pos = legend_y + index * 64
        measured_snm = data["metrics"]["read_snm_mv"]
        target_snm = target["metrics"]["read_snm_mv"]
        parts += [f'<path d="M{legend_x} {y_pos} h42" stroke="{color}" stroke-width="5"/>',
                  f'<text x="{legend_x+55}" y="{y_pos+6}" fill="#1D1D1F" font-size="18" font-weight="700">VDD {entry["model_vdd_v"]:.1f} V</text>',
                  f'<text x="{legend_x+55}" y="{y_pos+29}" fill="#007AFF" font-size="14">WAT RSNM {measured_snm:.1f} mV</text>']
        if show_target:
            parts.append(f'<text x="{legend_x+220}" y="{y_pos+29}" fill="#FF9500" font-size="14">Target {target_snm:.1f} mV · Δ {measured_snm-target_snm:+.1f}</text>')
    parts += [
        f'<text x="{left+plot_size/2}" y="{height-38}" text-anchor="middle" fill="#1D1D1F" font-size="19">Vin (V) — operating VDD identified by curve color</text>',
        f'<text x="42" y="{top+plot_size/2}" transform="rotate(-90 42 {top+plot_size/2})" text-anchor="middle" fill="#1D1D1F" font-size="19">Vout (V) — 0.0 to 1.2 V</text>',
        '<text x="925" y="120" fill="#6E6E73" font-size="15">Color = operating VDD · Solid = measured WAT · Dashed = target</text>',
        '</svg>',
    ]
    return "".join(parts)


def snm_by_model_vdd_svg(entries: list[dict], width: int = 1100, height: int = 540) -> str:
    """Independent SNM versus model-VDD trend chart for an Excel import."""
    left, top, plot_w, plot_h = 115, 110, 880, 320
    axis_max = SNM_PLOT_AXIS_MAX_V
    has_target = any(item["result"].get("datasheet_targets") for item in entries)
    datasets = [item["result"]["baseline_6t"] for item in entries]
    if has_target:
        datasets.extend(item["result"].get("target_6t", item["result"]["baseline_6t"]) for item in entries)
    max_snm = max(data["metrics"][key] for data in datasets
                  for key in ("read_snm_mv", "write_snm_proxy_mv"))
    y_max = max(50.0, math.ceil(max_snm / 50.0) * 50.0)

    def xy(vdd: float, snm: float) -> tuple[float, float]:
        return left + vdd / axis_max * plot_w, top + (1 - snm / y_max) * plot_h

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="SNM versus model VDD" style="font-family:Calibri,Arial,sans-serif">',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        '<text x="52" y="48" fill="#1D1D1F" font-size="29" font-weight="700">SNM versus Model VDD</text>',
        '<path d="M52 80 h32" stroke="#007AFF" stroke-width="4"/><text x="94" y="86" fill="#3A3A3C" font-size="16">Read SNM</text>',
        '<path d="M230 80 h32" stroke="#FF9500" stroke-width="4"/><text x="272" y="86" fill="#3A3A3C" font-size="16">Write SNM proxy</text>',
    ]
    if has_target:
        parts += ['<path d="M455 80 h32" stroke="#1D1D1F" stroke-width="3"/><text x="497" y="86" fill="#3A3A3C" font-size="16">Measured WAT</text>',
                  '<path d="M650 80 h32" stroke="#1D1D1F" stroke-width="3" stroke-dasharray="9 6"/><text x="692" y="86" fill="#3A3A3C" font-size="16">WAT Target</text>']
    for voltage in (0.0, 0.30, 0.60, 0.90, axis_max):
        x, _ = xy(voltage, 0)
        parts += [f'<path d="M{x:.1f} {top} V{top+plot_h}" stroke="#E5E5EA" stroke-width="1"/>',
                  f'<text x="{x:.1f}" y="{top+plot_h+25}" text-anchor="middle" fill="#6E6E73" font-size="14">{voltage:.2f}</text>']
    for snm in (0.0, y_max / 2, y_max):
        _, y = xy(0, snm)
        parts += [f'<path d="M{left} {y:.1f} H{left+plot_w}" stroke="#E5E5EA" stroke-width="1"/>',
                  f'<text x="{left-10}" y="{y+5:.1f}" text-anchor="end" fill="#6E6E73" font-size="14">{snm:.0f}</text>']
    for key, color in (("read_snm_mv", "#007AFF"), ("write_snm_proxy_mv", "#FF9500")):
        points = [xy(item["model_vdd_v"], item["result"]["baseline_6t"]["metrics"][key]) for item in entries]
        parts.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}" fill="none" stroke="{color}" stroke-width="4"/>')
        for x, y in points:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#FFFFFF" stroke="{color}" stroke-width="3"/>')
        if has_target:
            target_points = [xy(item["model_vdd_v"], item["result"].get("target_6t", item["result"]["baseline_6t"])["metrics"][key]) for item in entries]
            parts.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in target_points)}" fill="none" stroke="{color}" stroke-width="3" stroke-dasharray="9 6"/>')
            for x, y in target_points:
                parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#FFFFFF" stroke="{color}" stroke-width="2"/>')
    parts += [f'<text x="{left+plot_w/2}" y="{height-47}" text-anchor="middle" fill="#1D1D1F" font-size="18">Model VDD (V)</text>',
              f'<text x="38" y="{top+plot_h/2}" transform="rotate(-90 38 {top+plot_h/2})" text-anchor="middle" fill="#1D1D1F" font-size="18">SNM (mV)</text>',
              '<text x="550" y="512" text-anchor="middle" fill="#6E6E73" font-size="14">VDD axis is fixed at 0 to 1.20 V. Zero-VDD Excel rows are omitted because SNM is undefined at 0 V.</text></svg>']
    return "".join(parts)


def wat_electrical_snm_rows(result: dict) -> list[dict]:
    """Flatten WAT-measurable electrical inputs and derived SNM outputs."""
    cfg = result["config"]
    current_wat = result["wat"]
    datasets = [("Lot/Wafer", {
        "pu_vt": current_wat["pu_vt"], "pu_ids": current_wat["pu_ids"],
        "pg_vt": current_wat["pg_vt"], "pg_ids": current_wat["pg_ids"],
        "pd_vt": current_wat["pd_vt"], "pd_ids": current_wat["pd_ids"],
    }, result["baseline_6t"],
                 result.get("analytical_read_snm_comparison", {}).get("current", {}))]
    targets = result.get("datasheet_targets")
    if targets:
        datasets.append(("WAT Target", {
            "pu_vt": targets["pu"]["vt"], "pu_ids": targets["pu"]["ids"],
            "pg_vt": targets["pg"]["vt"], "pg_ids": targets["pg"]["ids"],
            "pd_vt": targets["pd"]["vt"], "pd_ids": targets["pd"]["ids"],
        }, result["target_6t"],
                         result.get("analytical_read_snm_comparison", {}).get("target", {})))

    def beta_proxy(vt: float, ids: float) -> float:
        overdrive = max(cfg["wat_vdd"] - abs(vt), 0.05)
        return 2.0 * ids / (overdrive * overdrive)

    rows = []
    for dataset, values, modeled, analytical in datasets:
        beta_pu = beta_proxy(values["pu_vt"], values["pu_ids"])
        beta_pg = beta_proxy(values["pg_vt"], values["pg_ids"])
        beta_pd = beta_proxy(values["pd_vt"], values["pd_ids"])
        rows.append({
            "dataset": dataset,
            "wat_vdd_v": cfg["wat_vdd"],
            "sram_vdd_v": cfg["nominal_vdd"],
            "pu_vt_v": values["pu_vt"], "pu_idsat_ua": values["pu_ids"],
            "pg_vt_v": values["pg_vt"], "pg_idsat_ua": values["pg_ids"],
            "pd_vt_v": values["pd_vt"], "pd_idsat_ua": values["pd_ids"],
            "beta_pu_proxy_ua_per_v2": beta_pu,
            "beta_pg_proxy_ua_per_v2": beta_pg,
            "beta_pd_proxy_ua_per_v2": beta_pd,
            "q_beta_pu_over_pg": beta_pu / beta_pg,
            "r_beta_pd_over_pg": beta_pd / beta_pg,
            "idsat_pd_over_pg": values["pd_ids"] / values["pg_ids"],
            "idsat_pg_over_pu": values["pg_ids"] / values["pu_ids"],
            "read_snm_geometric_mv": modeled["metrics"]["read_snm_mv"],
            "write_snm_proxy_mv": modeled["metrics"]["write_snm_proxy_mv"],
            "read_snm_eq_3_36_mv": analytical.get("snm_mv"),
            "eq_3_36_status": "VALID" if analytical.get("valid") else "N/A",
            "eq_3_36_reason": analytical.get("reason", ""),
            "evidence_scope": "WAT Vt + Idsat; no PDK/model-card-only parameters",
        })
    return rows


def generic_28nm_assumption_rows(result: dict) -> list[dict]:
    """Describe every non-WAT default and whether it affects active SNM math."""
    cfg, tech = result["config"], result["technology"]
    return [
        {"parameter": "Technology node", "value": tech["node_nm"], "unit": "nm",
         "source": "Editable model parameter; generic default 28 nm", "used_by": "Model identity", "active": "YES"},
        {"parameter": "Process family", "value": tech["process_family"], "unit": "-",
         "source": "Generic engineering assumption", "used_by": "Scope label", "active": "YES"},
        {"parameter": "SRAM VDD", "value": cfg["nominal_vdd"], "unit": "V",
         "source": "Model setting; default 0.90 V", "used_by": "Read VTC and analytical RSNM", "active": "YES"},
        {"parameter": "WAT calibration VDD", "value": cfg["wat_vdd"], "unit": "V",
         "source": "Model setting; default 0.90 V", "used_by": "Beta proxy calibration", "active": "YES"},
        {"parameter": "Channel length L", "value": tech["channel_length_nm"], "unit": "nm",
         "source": "Editable model parameter; generic default 28 nm", "used_by": "Architecture reference only", "active": "NO"},
        {"parameter": "PU width", "value": tech["pu_width_nm"], "unit": "nm",
         "source": "Editable model parameter; generic default 70 nm", "used_by": "Architecture reference only", "active": "NO"},
        {"parameter": "PG width", "value": tech["pg_width_nm"], "unit": "nm",
         "source": "Editable model parameter; generic default 100 nm", "used_by": "Architecture reference only", "active": "NO"},
        {"parameter": "PD width", "value": tech["pd_width_nm"], "unit": "nm",
         "source": "Editable model parameter; generic default 140 nm", "used_by": "Architecture reference only", "active": "NO"},
        {"parameter": "Nominal temperature", "value": tech["nominal_temperature_c"], "unit": "°C",
         "source": "Editable model parameter; generic default 25 °C", "used_by": "Reference; no temperature coefficients", "active": "NO"},
        {"parameter": "Read WL", "value": tech["read_wordline_over_vdd"], "unit": "×VDD",
         "source": "Editable read-bias parameter; generic default 1.0", "used_by": "Read VTC", "active": "YES"},
        {"parameter": "Read BL / BLB precharge", "value": tech["read_bitline_over_vdd"], "unit": "×VDD",
         "source": "Editable read-bias parameter; generic default 1.0", "used_by": "Read VTC", "active": "YES"},
        {"parameter": "Write WL", "value": tech["write_wordline_over_vdd"], "unit": "×VDD",
         "source": "Editable write-bias parameter; generic default 1.0", "used_by": "Write VTC and WSNM proxy", "active": "YES"},
        {"parameter": "Write BL", "value": tech["write_low_bitline_over_vdd"], "unit": "×VDD",
         "source": "Editable write-bias parameter; generic default 0.0", "used_by": "Write VTC and WSNM proxy", "active": "YES"},
        {"parameter": "Write BLB", "value": tech["write_high_bitline_over_vdd"], "unit": "×VDD",
         "source": "Editable write-bias parameter; generic default 1.0", "used_by": "Write VTC", "active": "YES"},
        {"parameter": "Beta", "value": tech["beta_policy"], "unit": "µA/V²",
         "source": "Derived from WAT Vt and Idsat", "used_by": "All compact VTC equations", "active": "YES"},
        {"parameter": "Common VTH", "value": tech["common_vth_policy"], "unit": "V",
         "source": "Mapped from three measured WAT Vt values", "used_by": "Analytical Read SNM only", "active": "YES"},
        {"parameter": "Cox / mobility / tox / lambda", "value": "Not required", "unit": "-",
         "source": "Intentionally not guessed", "used_by": "Replaced by WAT beta calibration", "active": "NO"},
    ]


def architecture_svg(width: int = 1080, height: int = 445) -> str:
    """Self-contained schematic-style view of the fixed 28 nm 6T topology."""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 500" width="{width}" height="{height}" role="img" aria-label="28 nm 6T SRAM architecture" style="font-family:Calibri,Arial,sans-serif">
    <rect width="100%" height="100%" fill="white"/>
    <text x="450" y="28" text-anchor="middle" font-size="19" font-weight="700">Generic 28 nm 6T SRAM bitcell</text>
    <path d="M95 72 H805 M125 72 V418 M775 72 V418" stroke="#94a3b8" stroke-width="2" fill="none"/>
    <text x="450" y="62" text-anchor="middle" font-size="17" font-weight="700">WL</text>
    <text x="125" y="447" text-anchor="middle" font-size="17" font-weight="700">BLB</text>
    <text x="775" y="447" text-anchor="middle" font-size="17" font-weight="700">BL</text>
    <path d="M315 105 H585 M450 105 V82 M315 390 H585 M450 390 V414" stroke="#334155" stroke-width="2.5" fill="none"/>
    <text x="450" y="97" text-anchor="middle" font-size="15" font-weight="700">VDD</text>
    <text x="450" y="438" text-anchor="middle" font-size="15" font-weight="700">GND</text>
    <path d="M315 105 V145 M585 105 V145 M315 205 V255 M585 205 V255 M315 315 V390 M585 315 V390" stroke="#334155" stroke-width="2.5"/>
    <rect x="270" y="145" width="90" height="60" rx="12" fill="#fff1f0" stroke="#ff3b30" stroke-width="2"/>
    <rect x="540" y="145" width="90" height="60" rx="12" fill="#fff1f0" stroke="#ff3b30" stroke-width="2"/>
    <rect x="270" y="255" width="90" height="60" rx="12" fill="#eef6ff" stroke="#007aff" stroke-width="2"/>
    <rect x="540" y="255" width="90" height="60" rx="12" fill="#eef6ff" stroke="#007aff" stroke-width="2"/>
    <rect x="145" y="220" width="100" height="60" rx="12" fill="#effcf2" stroke="#34c759" stroke-width="2"/>
    <rect x="655" y="220" width="100" height="60" rx="12" fill="#effcf2" stroke="#34c759" stroke-width="2"/>
    <text x="315" y="171" text-anchor="middle" font-size="15" font-weight="700">PUL</text><text x="315" y="190" text-anchor="middle" font-size="12">PMOS</text>
    <text x="585" y="171" text-anchor="middle" font-size="15" font-weight="700">PUR</text><text x="585" y="190" text-anchor="middle" font-size="12">PMOS</text>
    <text x="315" y="281" text-anchor="middle" font-size="15" font-weight="700">PDL</text><text x="315" y="300" text-anchor="middle" font-size="12">NMOS</text>
    <text x="585" y="281" text-anchor="middle" font-size="15" font-weight="700">PDR</text><text x="585" y="300" text-anchor="middle" font-size="12">NMOS</text>
    <text x="195" y="246" text-anchor="middle" font-size="15" font-weight="700">PGL</text><text x="195" y="265" text-anchor="middle" font-size="12">ACCESS</text>
    <text x="705" y="246" text-anchor="middle" font-size="15" font-weight="700">PGR</text><text x="705" y="265" text-anchor="middle" font-size="12">ACCESS</text>
    <path d="M125 250 H145 M245 250 H315 M585 250 H655 M755 250 H775" stroke="#334155" stroke-width="2.5" fill="none"/>
    <circle cx="315" cy="250" r="6" fill="#1d1d1f"/><circle cx="585" cy="250" r="6" fill="#1d1d1f"/>
    <text x="294" y="238" font-size="15" font-weight="700">QB</text><text x="598" y="238" font-size="15" font-weight="700">Q</text>
    <path d="M315 250 H420 V175 H540 M585 250 H480 V285 H360" stroke="#af52de" stroke-width="2" fill="none" stroke-dasharray="7 5"/>
    <path d="M195 220 V72 M705 220 V72" stroke="#34c759" stroke-width="2" fill="none"/>
    <text x="450" y="475" text-anchor="middle" font-size="12" fill="#64748b">L=28 nm · WPU=70 nm · WPG=100 nm · WPD=140 nm</text>
    </svg>'''


def export_pdf_report(result: dict, out: Path, image_dir: Path) -> Path:
    """Build a sharp, browser-independent PDF from the analysis and SVG charts."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                       Spacer, Table, TableStyle)
        from svglib.svglib import svg2rlg
    except ImportError as exc:
        raise RuntimeError("PDF export packages are missing. Run: python -m pip install -r requirements.txt") from exc

    pdf_dir = out / "pdf"; pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / "HV28_SRAM_Analysis.pdf"
    regular, bold = "Helvetica", "Helvetica-Bold"
    calibri = Path(r"C:\Windows\Fonts\calibri.ttf")
    calibrib = Path(r"C:\Windows\Fonts\calibrib.ttf")
    if calibri.exists() and calibrib.exists():
        pdfmetrics.registerFont(TTFont("Calibri", str(calibri)))
        pdfmetrics.registerFont(TTFont("Calibri-Bold", str(calibrib)))
        regular, bold = "Calibri", "Calibri-Bold"

    styles = getSampleStyleSheet()
    title = ParagraphStyle("HVTitle", parent=styles["Title"], fontName=bold, fontSize=29,
                           leading=32, textColor=colors.HexColor("#1D1D1F"), spaceAfter=5*mm)
    h1 = ParagraphStyle("HVH1", parent=styles["Heading1"], fontName=bold, fontSize=18,
                        leading=21, textColor=colors.HexColor("#1D1D1F"), spaceBefore=3*mm, spaceAfter=3*mm)
    h2 = ParagraphStyle("HVH2", parent=styles["Heading2"], fontName=bold, fontSize=12,
                        leading=14, textColor=colors.HexColor("#1D1D1F"), spaceBefore=2*mm, spaceAfter=2*mm)
    body = ParagraphStyle("HVBody", parent=styles["BodyText"], fontName=regular, fontSize=9.5,
                          leading=13, textColor=colors.HexColor("#3A3A3C"))
    small = ParagraphStyle("HVSmall", parent=body, fontSize=8, leading=10)

    def svg_drawing(filename: str, max_width: float, max_height: float):
        drawing = svg2rlg(str(image_dir / filename))
        if drawing is None or not drawing.width or not drawing.height:
            raise RuntimeError(f"Could not render SVG chart: {filename}")
        factor = min(max_width / drawing.width, max_height / drawing.height)
        drawing.width *= factor; drawing.height *= factor
        drawing.scale(factor, factor)
        return drawing

    table_header = colors.HexColor("#EEF4FC")
    border = colors.HexColor("#D2D2D7")
    def data_table(data: list[list], widths=None, font_size: float = 8):
        table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), bold), ("FONTNAME", (0, 1), (-1, -1), regular),
            ("FONTSIZE", (0, 0), (-1, -1), font_size), ("LEADING", (0, 0), (-1, -1), font_size+2),
            ("BACKGROUND", (0, 0), (-1, 0), table_header), ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1D1D1F")),
            ("GRID", (0, 0), (-1, -1), .35, border), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"), ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return table

    def footer(canvas, doc):
        canvas.saveState(); canvas.setFont(regular, 8); canvas.setFillColor(colors.HexColor("#6E6E73"))
        canvas.drawString(12*mm, 7*mm, "HV28 SRAM Analysis")
        canvas.drawRightString(landscape(A4)[0]-12*mm, 7*mm, f"Page {doc.page}")
        canvas.restoreState()

    cfg, wat = result["config"], result["wat"]
    ratios = result.get("strength_ratios", Sram6T(WatPoint(**wat), Config(**cfg)).strength_ratios())
    doc = SimpleDocTemplate(str(pdf_path), pagesize=landscape(A4), leftMargin=11*mm, rightMargin=11*mm,
                            topMargin=10*mm, bottomMargin=12*mm, title="HV28 SRAM Analysis")
    story = [Paragraph("HV28 SRAM Analysis", title),
             Paragraph(f'Lot/Wafer: <b>{html.escape(wat["corner"])}</b> &nbsp;&nbsp; Object mode: <b>{html.escape(result.get("object_mode", "Grouped"))}</b> &nbsp;&nbsp; SRAM VDD: <b>{cfg["nominal_vdd"]:.3f} V</b>', body),
             Spacer(1, 4*mm), svg_drawing("00_28nm_6t_bitcell_architecture.svg", 250*mm, 116*mm),
             Spacer(1, 3*mm), Paragraph("Generic 28 nm 6T compact model. WAT targets are comparison references; model estimates remain calibrated from measured WAT inputs.", small)]

    comparisons = result.get("target_comparisons", [])
    if comparisons:
        story += [PageBreak(), Paragraph("WAT Target vs WAT Measured", h1)]
        rows = [["Object", "Type", "Target Vt (V)", "WAT Vt (V)", "Delta Vt (mV)",
                 "Target Isat (uA)", "WAT Isat (uA)", "Delta Isat (uA)", "Delta Isat (%)"]]
        rows += [[x["object"], x["device"], f'{x["target_vt_v"]:.3f}', f'{x["measured_vt_v"]:.3f}',
                  f'{x["delta_vt_mv"]:+.1f}', f'{x["target_isat_ua"]:.2f}', f'{x["measured_isat_ua"]:.2f}',
                  f'{x["delta_isat_ua"]:+.2f}', f'{x["delta_isat_pct"]:+.2f}%'] for x in comparisons]
        story += [data_table(rows, font_size=7.5), Spacer(1, 5*mm)]

    if result.get("wt_test_0bit"):
        story += [Paragraph("WT Test 0-Bit Vmin", h1)]
        rows = [["WT mode", "Measured Vmin (V)", "Source"]] + [
            [x["test"], _fmt(x["vmin_v"], 3), x.get("source", "Model estimate")] for x in result["wt_test_0bit"]]
        story += [data_table(rows, widths=[55*mm, 48*mm, 82*mm]), Spacer(1, 4*mm),
                  Paragraph(f'WT sweep setup: Start={cfg["vmin_start"]:.3f} V, Stop={cfg["vmin_stop"]:.3f} V, Step={cfg["vmin_step"]:.3f} V.', body)]

    if result.get("cell"):
        story += [Paragraph(f'{result.get("object_mode", "6T Independent")} WAT Objects', h1)]
        rows = [["MOS object", "Vt (V)", "Isat / Ids (uA)"]] + [
            [name, f'{values["vt"]:.3f}', f'{values["ids"]:.2f}'] for name, values in result["cell"]["mos"].items()]
        story += [data_table(rows, widths=[50*mm, 40*mm, 50*mm])]

    story += [PageBreak(), Paragraph("WAT Vt / Isat Overview", h1)]
    rows = [["Device", "Vt (V)", "Isat / Ids (uA)"]] + [
        [dev, f'{wat[dev.lower()+"_vt"]:.3f}', f'{wat[dev.lower()+"_ids"]:.2f}'] for dev in ("PU", "PG", "PD")]
    ratio_rows = [["Ratio", "Model beta (Vt-aware)", "WAT Isat proxy", "Definition"],
                  ["Cell Ratio", f'{ratios["cell_ratio_beta"]:.3f}', f'{ratios["cell_ratio_ids_proxy"]:.3f}', "PD / PG"],
                  ["Pull-up Ratio", f'{ratios["pull_up_ratio_beta"]:.3f}', f'{ratios["pull_up_ratio_ids_proxy"]:.3f}', "PG / PU"]]
    story += [data_table(rows, widths=[50*mm, 40*mm, 50*mm]), Spacer(1, 4*mm),
              Paragraph("Cell Strength Ratios", h2),
              data_table(ratio_rows, widths=[48*mm, 48*mm, 45*mm, 40*mm]), Spacer(1, 3*mm),
              Paragraph(f'Model beta ratios include Vt through WAT calibration; Isat ratios are direct measured-current proxies. Assumed sensitivity sweep: Vt +/-{cfg["vt_step"]*1000:.0f} mV and Isat +/-{cfg["ids_step_pct"]:g}%.', body)]

    judgment = result.get("judgment")
    if judgment:
        story += [Spacer(1, 5*mm), Paragraph(f'Model Judgment: {judgment["overall_status"]}', h1)]
        judgment_rows = [["Parameter", "Model value", "Design target", "Margin", "Status", "Recommended action"]]
        for item in judgment["items"]:
            unit = item["unit"]
            digits = 3 if unit == "ratio" else 2
            suffix = "" if unit == "ratio" else f" {unit}"
            judgment_rows.append([
                item["label"], f'{item["value"]:.{digits}f}{suffix}',
                f'{item["target"]:.{digits}f}{suffix}', f'{item["margin"]:+.{digits}f}{suffix}',
                item["status"], Paragraph(item["recommended_action"], small)])
        story += [data_table(judgment_rows, widths=[31*mm, 30*mm, 30*mm, 28*mm, 24*mm, 116*mm], font_size=7),
                  Spacer(1, 2*mm), Paragraph(f'MARGINAL band: {judgment["marginal_band_pct"]:.1f}% below the user-entered target. These limits are design/datasheet inputs, not universal 28 nm criteria.', small)]

    for index, (dev, items) in enumerate(result["groups"].items(), 1):
        story += [PageBreak(), Paragraph(f"{dev} Sensitivity", h1)]
        prefix = f"{index:02d}_{dev.lower()}"
        charts = [[svg_drawing(f"{prefix}_read_butterfly.svg", 125*mm, 48*mm),
                   svg_drawing(f"{prefix}_read_snm_sensitivity.svg", 125*mm, 48*mm)],
                  [svg_drawing(f"{prefix}_hold_snm_sensitivity.svg", 125*mm, 39*mm),
                   svg_drawing(f"{prefix}_write_snm_sensitivity.svg", 125*mm, 39*mm)],
                  [svg_drawing(f"{prefix}_model_read_vmin_sensitivity.svg", 125*mm, 39*mm),
                   svg_drawing(f"{prefix}_model_write_vmin_sensitivity.svg", 125*mm, 39*mm)]]
        chart_table = Table(charts, colWidths=[132*mm, 132*mm], hAlign="CENTER")
        chart_table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("ALIGN", (0,0), (-1,-1), "CENTER"),
                                         ("LEFTPADDING", (0,0), (-1,-1), 2), ("RIGHTPADDING", (0,0), (-1,-1), 2),
                                         ("TOPPADDING", (0,0), (-1,-1), 2), ("BOTTOMPADDING", (0,0), (-1,-1), 2)]))
        baseline = items[0]["metrics"]
        rows = [["Scenario", "Cell ratio", "PU ratio", "Hold SNM", "Read SNM", "Write SNM*", "Delta WSNM", "Read Vmin", "Write Vmin"]]
        for item in items:
            m = item["metrics"]; w = item["wat"]
            rows.append([item["label"], f'{m["cell_ratio_beta"]:.3f}', f'{m["pull_up_ratio_beta"]:.3f}',
                         f'{m["hold_snm_mv"]:.2f}', f'{m["read_snm_mv"]:.2f}', f'{m["write_snm_mv"]:.2f}', f'{m["write_snm_mv"]-baseline["write_snm_mv"]:+.2f}',
                         _fmt(m["read_vmin_v"], 2), _fmt(m["write_vmin_v"], 2)])
        story += [chart_table, Spacer(1, 1*mm), data_table(rows, font_size=6.5),
                  Paragraph("* Write SNM is the compact-model bitline-noise margin proxy, not foundry/SPICE sign-off WSNM.", small)]

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return pdf_path


def _legacy_write_outputs(result: dict, out_dir: str | os.PathLike[str]) -> Path:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    image_dir = out/"images"; image_dir.mkdir(parents=True, exist_ok=True)
    image_manifest: list[dict[str, str]] = []

    def save_image(filename: str, svg: str, description: str, device: str = "CELL") -> str:
        """Publish a scalable SVG and a high-resolution PNG for the HTML report."""
        try:
            from reportlab.graphics import renderPM
            from svglib.svglib import svg2rlg
        except ImportError as exc:
            raise RuntimeError("PNG export packages are missing. Run: python -m pip install -r requirements.txt") from exc
        svg_path = image_dir/filename
        svg_path.write_text(svg, encoding="utf-8")
        png_name = str(Path(filename).with_suffix(".png"))
        png_path = image_dir/png_name
        drawing = svg2rlg(str(svg_path))
        if drawing is None:
            raise RuntimeError(f"Could not render chart image: {filename}")
        renderPM.drawToFile(drawing, str(png_path), fmt="PNG", dpi=180, backend="rlPyCairo")
        image_manifest.extend([
            {"filename": png_name, "format": "PNG", "role": "Primary image",
             "device": device, "description": description},
            {"filename": filename, "format": "SVG", "role": "Scalable chart source",
             "device": device, "description": description},
        ])
        return f'images/{png_name}'

    rows = [{"device": "6T Cell", "scenario": "Baseline", **result["wat"],
             **result["baseline_6t"]["metrics"]}]
    with open(out/"sram_wat_results.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    with open(out/"sram_wat_results.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    if "wt_test_0bit" in result:
        wt_rows=[]
        for item in result["wt_test_0bit"]:
            wt_rows.append({"test":item["test"],"vmin_v":item["vmin_v"],
                            "source":item.get("source", "Model estimate"),
                            "zero_bit_pass":item.get("zero_bit_pass", ""),
                            "failed_phase_count":item.get("failed_phase_count", ""),
                            "phases_at_vmin":"; ".join(k for k,v in item.get("phases_at_vmin",{}).items() if v)})
        with open(out/"wt_test_0bit_vmin.csv","w",newline="",encoding="utf-8-sig") as f:
            writer=csv.DictWriter(f,fieldnames=list(wt_rows[0])); writer.writeheader(); writer.writerows(wt_rows)
    comparisons = result.get("target_comparisons", [])
    if comparisons:
        with open(out/"wat_target_comparison.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(comparisons[0]))
            writer.writeheader(); writer.writerows(comparisons)
    if result.get("judgment"):
        judgment_rows = result["judgment"]["items"]
        with open(out/"parameter_judgment.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(judgment_rows[0]))
            writer.writeheader(); writer.writerows(judgment_rows)
    if result.get("target_validation"):
        validation = result["target_validation"]
        with open(out/"wat_target_validation_rows.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(validation["rows"][0]))
            writer.writeheader(); writer.writerows(validation["rows"])
        summary_row = {"verdict": validation["verdict"], **validation["counts"], **validation["statistics"]}
        with open(out/"wat_target_validation_summary.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(summary_row))
            writer.writeheader(); writer.writerow(summary_row)
        with open(out/"wat_target_parameter_evidence.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(validation["parameter_evidence"][0]))
            writer.writeheader(); writer.writerows(validation["parameter_evidence"])

    cfg = result["config"]; wat = result["wat"]; tech = result["technology"]
    sections = []
    ordered_groups = list(result["groups"].items())
    for group_index, (dev, items) in enumerate(ordered_groups, 1):
        baseline = items[0]["metrics"]
        trs = "".join("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in
            [x["label"], f'{x["wat"][dev.lower()+"_vt"]:.3f}', f'{x["wat"][dev.lower()+"_ids"]:.2f}',
             f'{x["metrics"]["hold_snm_mv"]:.2f}', f'{x["metrics"]["read_snm_mv"]:.2f}',
             f'{x["metrics"]["read_snm_mv"]-baseline["read_snm_mv"]:+.2f}',
             f'{x["metrics"]["write_snm_mv"]:.2f}',
             f'{x["metrics"]["write_snm_mv"]-baseline["write_snm_mv"]:+.2f}',
             f'{x["metrics"]["cell_ratio_beta"]:.3f}', f'{x["metrics"]["pull_up_ratio_beta"]:.3f}',
             _fmt(x["metrics"]["read_vmin_v"],2),
             "N/A" if x["metrics"]["read_vmin_v"] is None or baseline["read_vmin_v"] is None else f'{x["metrics"]["read_vmin_v"]-baseline["read_vmin_v"]:+.2f}',
             _fmt(x["metrics"]["write_vmin_v"],2),
             "N/A" if x["metrics"]["write_vmin_v"] is None or baseline["write_vmin_v"] is None else f'{x["metrics"]["write_vmin_v"]-baseline["write_vmin_v"]:+.2f}']) + "</tr>" for x in items)
        prefix = f"{group_index:02d}_{dev.lower()}"
        butterfly_path = save_image(f"{prefix}_read_butterfly.svg", butterfly_svg(items,cfg["nominal_vdd"]),
                                    f"{dev} read butterfly and SNM curves", dev)
        snm_path = save_image(f"{prefix}_read_snm_sensitivity.svg", bar_svg(items,"read_snm_mv",dev+" Read SNM","mV"),
                              f"{dev} read SNM sensitivity", dev)
        hold_snm_path = save_image(f"{prefix}_hold_snm_sensitivity.svg", bar_svg(items,"hold_snm_mv",dev+" Hold SNM","mV"),
                                   f"{dev} hold SNM sensitivity", dev)
        write_snm_path = save_image(f"{prefix}_write_snm_sensitivity.svg", bar_svg(items,"write_snm_mv",dev+" Write SNM (bitline-noise proxy)","mV"),
                                    f"{dev} compact-model write SNM sensitivity", dev)
        read_vmin_path = save_image(f"{prefix}_model_read_vmin_sensitivity.svg", bar_svg(items,"read_vmin_v",dev+" Model Read Vmin","V"),
                                    f"{dev} model-estimated read Vmin sensitivity", dev)
        write_vmin_path = save_image(f"{prefix}_model_write_vmin_sensitivity.svg", bar_svg(items,"write_vmin_v",dev+" Model Write Vmin","V"),
                                     f"{dev} model-estimated write Vmin sensitivity", dev)
        sections.append(f'''<section><h2>{dev} sensitivity</h2>
        <div class="grid"><div><img src="{butterfly_path}" alt="{dev} read butterfly"></div>
        <div><img src="{snm_path}" alt="{dev} read SNM sensitivity"></div>
        <div><img src="{hold_snm_path}" alt="{dev} hold SNM sensitivity"></div>
        <div><img src="{write_snm_path}" alt="{dev} write SNM sensitivity"></div>
        <div><img src="{read_vmin_path}" alt="{dev} model read Vmin sensitivity"></div>
        <div><img src="{write_vmin_path}" alt="{dev} model write Vmin sensitivity"></div></div>
        <table><thead><tr><th>Scenario</th><th>Vt (V)</th><th>Ids (uA)</th><th>Hold SNM (mV)</th><th>Read SNM (mV)</th><th>ΔRSNM</th><th>Write SNM* (mV)</th><th>ΔWSNM</th><th>Cell ratio β</th><th>Pull-up ratio β</th><th>Model Read Vmin (V)</th><th>ΔRVmin</th><th>Model Write Vmin (V)</th><th>ΔWVmin</th></tr></thead><tbody>{trs}</tbody></table>
        <p><small>* Compact-model bitline-noise margin proxy; not foundry/SPICE sign-off WSNM.</small></p></section>''')
    ratios = result.get("strength_ratios", Sram6T(WatPoint(**wat), Config(**cfg)).strength_ratios())
    ratio = (f'Model β Cell Ratio (PD/PG)={ratios["cell_ratio_beta"]:.3f}; '
             f'Model β Pull-up Ratio (PG/PU)={ratios["pull_up_ratio_beta"]:.3f}; '
             f'WAT Isat proxies={ratios["cell_ratio_ids_proxy"]:.3f} / {ratios["pull_up_ratio_ids_proxy"]:.3f}')
    wat_rows = "".join(f'<tr><td>{d}</td><td>{wat[d.lower()+"_vt"]:.3f}</td><td>{wat[d.lower()+"_ids"]:.2f}</td><td>{wat[d.lower()+"_ids"]/sum(wat[x+"_ids"] for x in ("pu","pg","pd")):.3f}</td></tr>' for d in ("PU","PG","PD"))
    individual_section = ""
    if "cell" in result:
        mos_rows = "".join(f'<tr><td>{name}</td><td>{values["vt"]:.3f}</td><td>{values["ids"]:.2f}</td></tr>' for name,values in result["cell"]["mos"].items())
        sensitivity_blocks = []
        for name, items in result.get("mos_sensitivity", {}).items():
            base = items[0]["metrics"]
            body = "".join("<tr>"+"".join(f"<td>{html.escape(str(v))}</td>" for v in [
                x["label"], f'{x["metrics"]["hold_snm_mv"]:.2f}', f'{x["metrics"]["read_snm_mv"]:.2f}',
                f'{x["metrics"]["write_snm_mv"]:.2f}', f'{x["metrics"]["cell_ratio_beta"]:.3f}',
                f'{x["metrics"]["pull_up_ratio_beta"]:.3f}',
                _fmt(x["metrics"]["read_vmin_v"],2), _fmt(x["metrics"]["write_vmin_v"],2)])+"</tr>" for x in items)
            sensitivity_blocks.append(f'<div><h3>{name}</h3><table><thead><tr><th>Scenario</th><th>Hold SNM</th><th>Read SNM</th><th>Write SNM*</th><th>Cell ratio β</th><th>Pull-up ratio β</th><th>Read Vmin</th><th>Write Vmin</th></tr></thead><tbody>{body}</tbody></table></div>')
        object_mode = result.get("object_mode", "6T Independent")
        individual_section = f'''<section><h2>{object_mode} objects</h2>
        <p>Object mode: {object_mode}. Model-estimated margins are kept separate from manually entered WT Vmin.</p>
        <table><thead><tr><th>MOS object</th><th>Vt (V)</th><th>Ids (µA)</th></tr></thead><tbody>{mos_rows}</tbody></table>
        <div class="grid">{''.join(sensitivity_blocks)}</div></section>'''
    target_section = ""
    if comparisons:
        target_rows = "".join(
            f'<tr><td>{x["object"]}</td><td>{x["device"]}</td><td>{x["target_vt_v"]:.3f}</td>'
            f'<td>{x["measured_vt_v"]:.3f}</td><td>{x["delta_vt_mv"]:+.1f}</td>'
            f'<td>{x["target_isat_ua"]:.2f}</td><td>{x["measured_isat_ua"]:.2f}</td>'
            f'<td>{x["delta_isat_ua"]:+.2f}</td><td>{x["delta_isat_pct"]:+.2f}%</td></tr>'
            for x in comparisons)
        target_section = f'''<section><h2>WAT Target vs WAT Measured</h2>
        <p>Target values are comparison references only. The compact model remains calibrated from the measured WAT values.</p>
        <table><thead><tr><th>MOS object</th><th>Type</th><th>Target Vt (V)</th><th>WAT Vt (V)</th><th>ΔVt (mV)</th><th>Target Isat (µA)</th><th>WAT Isat (µA)</th><th>ΔIsat (µA)</th><th>ΔIsat (%)</th></tr></thead><tbody>{target_rows}</tbody></table></section>'''
    wt_section = ""
    if "wt_test_0bit" in result:
        wt_defs={
            "Scan4N":"W0 → R0/W1 → R1/W0 → R0；全部 phase 通過",
            "Select_Write":"Write-0 與 Write-1 都達到 20%/80% rail",
            "Select_Read":"Read-0 與 Read-1 都保持 35%/65% rail 且符合 SNM 下限",
        }
        manual_source = result.get("vmin_source") == "manual"
        wt_rows="".join(f'<tr><td>{x["test"]}</td><td>{_fmt(x["vmin_v"],3)}</td><td>{html.escape(x.get("source", "Model estimate"))}</td><td>{html.escape(wt_defs[x["test"]])}</td></tr>' for x in result["wt_test_0bit"])
        wt_section=f'''<section><h2>WT Test 0-Bit Vmin</h2>
        <p>{"Values below were entered manually after WT measurement; the Python model did not generate them." if manual_source else "Model-estimated values; no manual WT values were supplied."}</p>
        <table><thead><tr><th>WT mode</th><th>Measured Vmin (V)</th><th>Source</th><th>Test definition</th></tr></thead><tbody>{wt_rows}</tbody></table></section>'''
    judgment_section = ""
    if result.get("judgment"):
        judgment = result["judgment"]
        judgment_rows = "".join(
            f'<tr><td>{item["label"]}</td><td>{item["value"]:.3f} {"" if item["unit"] == "ratio" else item["unit"]}</td>'
            f'<td>{item["target"]:.3f} {"" if item["unit"] == "ratio" else item["unit"]}</td>'
            f'<td>{item["margin"]:+.3f}</td><td><b>{item["status"]}</b></td>'
            f'<td>{html.escape(item["recommended_action"])}</td></tr>' for item in judgment["items"])
        judgment_section = f'''<section class="judgment"><h2>Parameter Judgment: {judgment["overall_status"]}</h2>
        <p>PASS requires the model value to meet the user-entered design target. MARGINAL means it is within {judgment["marginal_band_pct"]:.1f}% below target. These are not universal 28 nm limits.</p>
        <table><thead><tr><th>Parameter</th><th>Model value</th><th>Design target</th><th>Margin</th><th>Status</th><th>Recommended action</th></tr></thead><tbody>{judgment_rows}</tbody></table></section>'''
    validation_section = ""
    if result.get("target_validation"):
        validation = result["target_validation"]
        counts, stats, current = validation["counts"], validation["statistics"], validation["current_row"]
        stat_rows = "".join(
            f'<tr><td>{label}</td><td>{value}</td><td>{meaning}</td></tr>' for label, value, meaning in (
                ("WT-complete rows", counts["wt_complete"], "All three measured WT Vmin values are present"),
                ("Paired WAT + WT rows", counts["paired_wat_wt"], "Rows usable for target validation"),
                ("Inside / outside target band", f'{counts["inside_target_band"]} / {counts["outside_target_band"]}', "Both groups need at least 3 rows"),
                ("WT pass rate inside band", _fmt(stats["inside_wt_pass_rate_pct"], 1) + "%" if stats["inside_wt_pass_rate_pct"] is not None else "N/A", "Higher is better"),
                ("WT pass rate outside band", _fmt(stats["outside_wt_pass_rate_pct"], 1) + "%" if stats["outside_wt_pass_rate_pct"] is not None else "N/A", "Comparison population"),
                ("Pass-rate lift", (_fmt(stats["pass_rate_lift_pct_points"], 1) + " pp") if stats["pass_rate_lift_pct_points"] is not None else "N/A", "Inside minus outside"),
                ("Spearman correlation", _fmt(stats["target_distance_vs_worst_vmin_spearman"], 3), "Positive: farther from target tends to worsen Vmin"),
                ("Pearson correlation", _fmt(stats["target_distance_vs_worst_vmin_pearson"], 3), "Linear association; use with Spearman"),
            ))
        parameter_rows = "".join(
            f'<tr><td>{item["parameter"]}</td><td>{item["paired_n"]}</td>'
            f'<td>{item["inside_n"]} / {item["outside_n"]}</td>'
            f'<td>{(_fmt(item["pass_rate_lift_pct_points"], 1) + " pp") if item["pass_rate_lift_pct_points"] is not None else "N/A"}</td>'
            f'<td>{_fmt(item["deviation_vs_worst_vmin_spearman"], 3)}</td><td><b>{item["verdict"]}</b></td></tr>'
            for item in validation["parameter_evidence"])
        settings = validation["settings"]
        verdict_class = "ok" if validation["verdict"] == "SUPPORTED" else ("bad" if validation["verdict"] == "CONTRADICTED" else "warn")
        validation_section = f'''<section class="validation"><div class="section-heading"><div><h2>WAT Target Validation</h2><p>Primary decision: do lots near the WAT target actually achieve acceptable measured WT Vmin?</p></div><span class="verdict {verdict_class}">{validation["verdict"]}</span></div>
        <div class="validation-cards"><div><span>Current Lot/Wafer</span><b>{html.escape(str(current["lot_wafer"]))}</b></div><div><span>WAT band result</span><b>{"IN BAND" if current["target_band_pass"] else "OUT OF BAND"}</b></div><div><span>WT result</span><b>{"PASS" if current["wt_pass"] else "FAIL"}</b></div><div><span>Screen outcome</span><b>{current["consistency"]}</b></div></div>
        <p><b>Conclusion:</b> {html.escape(validation["explanation"])}</p>
        <p>Target band: Vt ±{settings["vt_tolerance_mv"]:.1f} mV and Isat ±{settings["idsat_tolerance_pct"]:.1f}%. WT pass limits: Scan4N ≤ {settings["scan4n_vmin_max"]:.3f} V, Select_Write ≤ {settings["select_write_vmin_max"]:.3f} V, Select_Read ≤ {settings["select_read_vmin_max"]:.3f} V.</p>
        <table><thead><tr><th>Evidence</th><th>Value</th><th>Interpretation</th></tr></thead><tbody>{stat_rows}</tbody></table>
        <h3>Individual WAT Target Evidence</h3><p>Each parameter uses every row where that WAT item and all three WT Vmin results are available; other missing WAT items do not discard the row.</p>
        <table><thead><tr><th>WAT target</th><th>Paired N</th><th>Inside / outside N</th><th>Pass-rate lift</th><th>Deviation–Vmin Spearman</th><th>Evidence</th></tr></thead><tbody>{parameter_rows}</tbody></table>
        <p class="footnote">WT-only rows remain in coverage counts, while correlation and band discrimination use paired WAT+WT rows only. Association supports screening usefulness but does not prove transistor-level causality.</p></section>'''
    snm_overview_path = save_image("01_6t_cell_snm_butterfly.svg", snm_overview_svg(result),
                                   "6T cell Hold, Read and Write SNM butterfly analysis", "6T CELL")
    snm_metrics = result["baseline_6t"]["metrics"]
    snm_section = f'''<section><h2>6T Cell SNM Analysis</h2>
    <p>SNM is evaluated at complete-cell level. PU, PG and PD are not assigned separate SNM values.</p>
    <p>Both axes span 0 to VDD, so the butterfly is entirely in the first quadrant. Hold and Read SNM evaluate the largest square in each lobe and use the smaller result. The two squares coincide in size for this symmetric 3T-merged input. Write is displayed as a write-noise proxy bracket because it is not calculated by the same symmetric butterfly-square rule.</p>
    <img src="{snm_overview_path}" alt="6T SRAM Hold Read Write SNM butterfly analysis">
    <table><thead><tr><th>Cell-level metric</th><th>Value (mV)</th><th>Operating condition</th></tr></thead><tbody>
    <tr><td>Hold SNM</td><td>{snm_metrics["hold_snm_mv"]:.2f}</td><td>WL off; data retention</td></tr>
    <tr><td>Read SNM</td><td>{snm_metrics["read_snm_mv"]:.2f}</td><td>WL on; BL and BLB precharged</td></tr>
    <tr><td>Write SNM*</td><td>{snm_metrics["write_snm_mv"]:.2f}</td><td>BL=0 / BLB=VDD compact-model proxy</td></tr>
    </tbody></table></section>'''
    with open(image_dir/"image_manifest.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["filename", "format", "role", "device", "description"])
        writer.writeheader(); writer.writerows(sorted(image_manifest, key=lambda row: row["filename"]))

    doc = f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HV28 SRAM Analysis</title>
    <style>
    :root{{font:100%/1.5 Calibri,"Microsoft JhengHei",Arial,sans-serif;color:#1d1d1f;background:#f5f5f7}}
    *{{box-sizing:border-box}} body{{margin:0;padding:clamp(1.25rem,4vw,3rem);background:radial-gradient(circle at 85% 0,#e8f2ff 0,transparent 28rem),#f5f5f7}}
    main{{max-width:1760px;margin:auto}} h1{{font-size:clamp(2.2rem,4vw,4rem);line-height:1.05;letter-spacing:-.035em;margin:.5rem 0 .35rem}} h2{{font-size:1.7rem;letter-spacing:-.018em;margin-top:0}} h3{{font-size:1.3rem;letter-spacing:-.01em}}
    .note,.summary,section{{background:rgba(255,255,255,.82);backdrop-filter:blur(22px) saturate(160%);border:1px solid rgba(255,255,255,.75);border-radius:1.25rem;box-shadow:0 1px 2px #0000000a,0 12px 36px #0000000d;padding:clamp(1rem,2.4vw,1.65rem);margin:1rem 0}}
    .note{{border-left:4px solid #007aff}} .summary{{color:#3a3a3c;font-size:1.05rem}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,42rem),1fr));gap:1.5rem}}
    .target-badge{{display:inline-block;vertical-align:middle;margin-left:.55rem;padding:.28rem .55rem;border-radius:999px;background:#e5f1ff;color:#007aff;font-size:.72rem;letter-spacing:.04em}}
    .section-heading{{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem}} .section-heading h2{{margin-bottom:.2rem}} .section-heading p{{margin:.1rem 0;color:#6e6e73}}
    .verdict{{white-space:nowrap;border-radius:999px;padding:.45rem .75rem;font-weight:700;font-size:.82rem}} .verdict.ok{{background:#eaf8ee;color:#167a36}} .verdict.warn{{background:#fff4df;color:#946200}} .verdict.bad{{background:#ffebe9;color:#c5221f}}
    .validation-cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(12rem,1fr));gap:.75rem;margin:1rem 0}} .validation-cards>div{{background:#f5f5f7;border-radius:.85rem;padding:.85rem 1rem}} .validation-cards span{{display:block;color:#6e6e73;font-size:.85rem;margin-bottom:.25rem}} .validation-cards b{{font-size:1.05rem}} .footnote{{color:#6e6e73;font-size:.9rem}}
    svg,img{{display:block;width:100%;min-height:24rem;height:auto;border:1px solid #e5e5ea;border-radius:1rem;overflow:hidden}} table{{border-collapse:separate;border-spacing:0;width:100%;margin-top:1rem;font-size:1rem;line-height:1.55;overflow:hidden}}
    th,td{{padding:.9rem .8rem;border-bottom:1px solid #e5e5ea;text-align:right;font-variant-numeric:tabular-nums}} th{{color:#6e6e73;font-size:.9rem;font-weight:680;letter-spacing:.015em}} th:first-child,td:first-child{{text-align:left}} tbody tr:last-child td{{border-bottom:0}} code{{background:#f2f2f7;padding:.18rem .38rem;border-radius:.35rem}}
    @media(prefers-reduced-transparency:reduce){{.note,.summary,section{{background:#fff;backdrop-filter:none;border-color:#d2d2d7}}}}
    @media(prefers-contrast:more){{.note,.summary,section{{background:#fff;border:2px solid #1d1d1f}}}}
    @page{{size:A4 landscape;margin:11mm}}
    @media print{{
      :root{{font-size:10pt}} body{{padding:0;background:#fff}} main{{max-width:none}}
      h1{{font-size:28pt}} h2{{font-size:18pt;break-after:avoid}} h3{{font-size:14pt;break-after:avoid}}
      .note,.summary,section{{background:#fff;border:1px solid #d2d2d7;box-shadow:none;backdrop-filter:none;margin:7mm 0;padding:6mm}}
      .summary,.note,table,.grid>div{{break-inside:avoid}} .grid{{grid-template-columns:1fr 1fr;gap:6mm}}
      svg,img{{min-height:0;max-height:155mm;object-fit:contain;border-radius:3mm}}
      table{{font-size:8.5pt;line-height:1.25}} th,td{{padding:5pt 4pt}}
    }}
    </style></head><body><main>
    <h1>HV28 SRAM Analysis</h1><p>Lot/Wafer: <b>{html.escape(wat["corner"])}</b> · Object mode: <b>{html.escape(result.get("object_mode","Grouped"))}</b> · SRAM VDD={cfg["nominal_vdd"]:.3f} V</p>
    <div class="summary"><b>Data meaning:</b> PU / PG / PD WAT Target Vt and Isat are references compared with measured WAT values. Scan4N, Select_Write and Select_Read Vmin are manually entered measured values. Model Read/Write Vmin is kept separate.</div>
    <div class="summary"><b>WT sweep setup:</b> Start={cfg["vmin_start"]:.3f} V · Stop={cfg["vmin_stop"]:.3f} V · Step={cfg["vmin_step"]:.3f} V. These are actual tester recipe inputs and also define the model search range and resolution.</div>
    {target_section}
    {wt_section}
    {validation_section}
    {judgment_section}
    {snm_section}
    {individual_section}
    <section><h2>WAT Vt / Ids comparison</h2><table><thead><tr><th>Device</th><th>Vt (V)</th><th>Ids (uA)</th><th>Normalized Ids</th></tr></thead><tbody>{wat_rows}</tbody></table><p>{ratio}</p></section>
    <p>Raw data: <code>sram_wat_results.csv</code>, <code>wat_target_comparison.csv</code>, <code>wat_target_validation_rows.csv</code>, <code>wat_target_validation_summary.csv</code>, <code>wat_target_parameter_evidence.csv</code>, <code>sram_wat_results.json</code>. Standalone charts: <code>images/</code> with <code>image_manifest.csv</code>.</p></main></body></html>'''
    report = out/"sram_wat_report.html"; report.write_text(doc, encoding="utf-8")
    return report


def write_outputs(result: dict, out_dir: str | os.PathLike[str]) -> Path:
    """Write the focused Read SNM current-versus-target HTML report."""
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    image_dir = out / "images"; image_dir.mkdir(parents=True, exist_ok=True)

    # These files belonged to the removed WT/Vmin workflow; do not leave stale results behind.
    for name in ("sram_wat_results.csv", "wt_test_0bit_vmin.csv", "parameter_judgment.csv",
                 "wat_target_validation_rows.csv", "wat_target_validation_summary.csv",
                 "wat_target_parameter_evidence.csv", "analytical_read_snm_eq_3_36.csv"):
        path = out / name
        if path.exists():
            path.unlink()
    for path in image_dir.iterdir():
        if path.is_file() and path.suffix.lower() in {".png", ".svg", ".csv"}:
            path.unlink()

    try:
        from reportlab.graphics import renderPM
        from svglib.svglib import svg2rlg
    except ImportError as exc:
        raise RuntimeError("PNG export packages are missing. Run: python -m pip install -r requirements.txt") from exc

    svg_name = "01_read_snm_target_comparison.svg"
    png_name = "01_read_snm_target_comparison.png"
    butterfly_svg_name = "02_read_snm_butterfly.svg"
    butterfly_png_name = "02_read_snm_butterfly.png"
    write_svg_name = "03_write_snm_target_comparison.svg"
    write_png_name = "03_write_snm_target_comparison.png"
    svg_path = image_dir / svg_name
    svg_path.write_text(snm_overview_svg(result), encoding="utf-8")
    drawing = svg2rlg(str(svg_path))
    if drawing is None:
        raise RuntimeError("Could not render Read SNM target comparison")
    renderPM.drawToFile(drawing, str(image_dir / png_name), fmt="PNG", dpi=180, backend="rlPyCairo")
    butterfly_svg_path = image_dir / butterfly_svg_name
    butterfly_svg_path.write_text(read_snm_butterfly_svg(result), encoding="utf-8")
    butterfly_drawing = svg2rlg(str(butterfly_svg_path))
    if butterfly_drawing is None:
        raise RuntimeError("Could not render Read SNM butterfly chart")
    renderPM.drawToFile(butterfly_drawing, str(image_dir / butterfly_png_name),
                        fmt="PNG", dpi=180, backend="rlPyCairo")
    write_svg_path = image_dir / write_svg_name
    write_svg_path.write_text(write_snm_overview_svg(result), encoding="utf-8")
    write_drawing = svg2rlg(str(write_svg_path))
    if write_drawing is None:
        raise RuntimeError("Could not render Write SNM target comparison")
    renderPM.drawToFile(write_drawing, str(image_dir / write_png_name), fmt="PNG", dpi=180,
                        backend="rlPyCairo")

    comparison_rows = result.get("snm_target_comparison", [])
    if not comparison_rows:
        metrics = result["baseline_6t"]["metrics"]
        comparison_rows = [
            {"mode": label, "current_snm_mv": metrics[key], "target_snm_mv": metrics[key],
             "delta_mv": 0.0, "delta_pct": 0.0}
            for label, key in (("Read SNM", "read_snm_mv"),
                               ("Write SNM Proxy", "write_snm_proxy_mv"))
        ]
    comparison_export_rows = [{
        "mode": row["mode"],
        "lot_wafer_snm_mv": row["current_snm_mv"],
        "target_snm_mv": row["target_snm_mv"],
        "lot_wafer_minus_target_mv": row["delta_mv"],
        "difference_pct": row["delta_pct"],
    } for row in comparison_rows]
    with open(out / "snm_target_comparison.csv", "w", newline="", encoding="utf-8-sig") as target_file:
        writer = csv.DictWriter(target_file, fieldnames=list(comparison_export_rows[0]))
        writer.writeheader(); writer.writerows(comparison_export_rows)

    state_rows = []
    for label, data in (("Lot/Wafer", result["baseline_6t"]),
                        ("WAT Target", result.get("target_6t", result["baseline_6t"]))):
        butterfly = data["read_butterfly"]
        squares = butterfly["squares"]
        upper = butterfly.get("snm_upper_left_mv", squares[0]["side_mv"])
        lower = butterfly.get("snm_lower_right_mv", squares[1]["side_mv"])
        mean = (upper + lower) / 2.0
        state_rows.append({
            "dataset": label,
            "upper_left_state_snm_mv": upper,
            "lower_right_state_snm_mv": lower,
            "cell_read_snm_min_mv": min(upper, lower),
            "upper_minus_lower_mv": upper - lower,
            "mismatch_index_pct": abs(upper - lower) / mean * 100.0 if mean > 0 else None,
        })
    with open(out / "read_snm_state_mismatch.csv", "w", newline="", encoding="utf-8-sig") as state_file:
        writer = csv.DictWriter(state_file, fieldnames=list(state_rows[0]))
        writer.writeheader(); writer.writerows(state_rows)

    analytical = result.get("analytical_read_snm_comparison", {})
    analytical_rows = []
    for dataset in ("current", "target"):
        values = analytical.get(dataset, {})
        analytical_rows.append({
            "dataset": "Lot/Wafer" if dataset == "current" else "WAT Target",
            "valid": values.get("valid"),
            "reason": values.get("reason"),
            "snm_mv": values.get("snm_mv"),
            "vdd_v": values.get("vdd_v"),
            "vth_eff_v": values.get("vth_eff_v"),
            "q_beta_p_over_beta_a": values.get("q_beta_p_over_beta_a"),
            "r_beta_d_over_beta_a": values.get("r_beta_d_over_beta_a"),
            "vs_v": values.get("vs_v"),
            "vr_v": values.get("vr_v"),
            "k": values.get("k"),
            "term_a_v": values.get("term_a_v"),
            "term_b_v": values.get("term_b_v"),
        })
    with open(out / "analytical_read_snm.csv", "w", newline="", encoding="utf-8-sig") as analytical_file:
        writer = csv.DictWriter(analytical_file, fieldnames=list(analytical_rows[0]))
        writer.writeheader(); writer.writerows(analytical_rows)

    electrical_snm_rows = wat_electrical_snm_rows(result)
    with open(out / "wat_electrical_snm_table.csv", "w", newline="", encoding="utf-8-sig") as electrical_file:
        writer = csv.DictWriter(electrical_file, fieldnames=list(electrical_snm_rows[0]))
        writer.writeheader(); writer.writerows(electrical_snm_rows)

    assumption_rows = generic_28nm_assumption_rows(result)
    with open(out / "generic_28nm_assumptions.csv", "w", newline="", encoding="utf-8-sig") as assumption_file:
        writer = csv.DictWriter(assumption_file, fieldnames=list(assumption_rows[0]))
        writer.writeheader(); writer.writerows(assumption_rows)

    comparisons = result.get("target_comparisons", [])
    if comparisons:
        with open(out / "wat_target_comparison.csv", "w", newline="", encoding="utf-8-sig") as target_file:
            writer = csv.DictWriter(target_file, fieldnames=list(comparisons[0]))
            writer.writeheader(); writer.writerows(comparisons)
    with open(out / "sram_wat_results.json", "w", encoding="utf-8") as json_file:
        json.dump(result, json_file, ensure_ascii=False, indent=2)

    manifest_rows = [
        {"filename": png_name, "format": "PNG", "role": "Primary image", "device": "6T CELL",
         "description": "Read SNM Lot/Wafer WAT versus WAT Target curves"},
        {"filename": svg_name, "format": "SVG", "role": "Scalable chart source", "device": "6T CELL",
         "description": "Read SNM Lot/Wafer WAT versus WAT Target curves"},
        {"filename": butterfly_png_name, "format": "PNG", "role": "Read SNM butterfly", "device": "6T CELL",
         "description": "Read VTC with maximum squares 1 and 2 for Lot/Wafer and Target"},
        {"filename": butterfly_svg_name, "format": "SVG", "role": "Scalable Read SNM butterfly source", "device": "6T CELL",
         "description": "Read VTC with maximum squares 1 and 2 for Lot/Wafer and Target"},
        {"filename": write_png_name, "format": "PNG", "role": "Write SNM comparison", "device": "6T CELL",
         "description": "Write VTC pair and bitline-noise WSNM proxy for Lot/Wafer and WAT Target"},
        {"filename": write_svg_name, "format": "SVG", "role": "Scalable Write SNM comparison source", "device": "6T CELL",
         "description": "Write VTC pair and bitline-noise WSNM proxy for Lot/Wafer and WAT Target"},
    ]
    with open(image_dir / "image_manifest.csv", "w", newline="", encoding="utf-8-sig") as manifest:
        writer = csv.DictWriter(manifest, fieldnames=list(manifest_rows[0]))
        writer.writeheader(); writer.writerows(manifest_rows)

    target_rows = "".join(
        f'<tr><td>{x["object"]}</td><td>{x["device"]}</td><td>{x["measured_vt_v"]:.3f}</td>'
        f'<td>{x["target_vt_v"]:.3f}</td><td>{x["delta_vt_mv"]:+.1f}</td>'
        f'<td>{x["measured_isat_ua"]:.2f}</td><td>{x["target_isat_ua"]:.2f}</td>'
        f'<td>{x["delta_isat_ua"]:+.2f}</td><td>{x["delta_isat_pct"]:+.2f}%</td></tr>'
        for x in comparisons)
    target_section = (f'''<section><h2>PU / PG / PD WAT vs WAT Target</h2>
    <p>The target values build a separate 6T model at the same SRAM VDD and WAT bias; they are not substituted into the Lot/Wafer WAT model.</p>
    <table><thead><tr><th>MOS object</th><th>Type</th><th>Lot/Wafer Vt (V)</th><th>WAT Target Vt (V)</th><th>ΔVt (mV)</th><th>Lot/Wafer Isat (µA)</th><th>WAT Target Isat (µA)</th><th>ΔIsat (µA)</th><th>ΔIsat (%)</th></tr></thead><tbody>{target_rows}</tbody></table></section>'''
                      if comparisons else "")

    snm_rows = "".join(
        f'<tr><td>{row["mode"]}</td><td>{row["current_snm_mv"]:.2f}</td>'
        f'<td>{row["target_snm_mv"]:.2f}</td><td>{row["delta_mv"]:+.2f}</td>'
        f'<td>{_fmt(row["delta_pct"], 2)}%</td></tr>' for row in comparison_rows)
    state_table_rows = "".join(
        f'<tr><td>{row["dataset"]}</td><td>{row["upper_left_state_snm_mv"]:.2f}</td>'
        f'<td>{row["lower_right_state_snm_mv"]:.2f}</td><td>{row["cell_read_snm_min_mv"]:.2f}</td>'
        f'<td>{row["upper_minus_lower_mv"]:+.2f}</td><td>{_fmt(row["mismatch_index_pct"], 2)}%</td></tr>'
        for row in state_rows)
    analytical_table_rows = "".join(
        f'<tr><td>{row["dataset"]}</td><td>{"VALID" if row["valid"] else "N/A"}</td>'
        f'<td>{_fmt(row["snm_mv"], 2)}</td><td>{_fmt(row["vth_eff_v"], 4)}</td>'
        f'<td>{_fmt(row["q_beta_p_over_beta_a"], 4)}</td><td>{_fmt(row["r_beta_d_over_beta_a"], 4)}</td>'
        f'<td>{_fmt(row["k"], 4)}</td><td>{html.escape(str(row["reason"] or ""))}</td></tr>'
        for row in analytical_rows)
    analytical_section = f'''<section><h2>Analytical Read SNM Reference</h2>
    <p>This independent analytical estimate uses WAT-derived strength ratios and a common effective threshold mapped from PU/PG/PD Vt. It is reported separately from the geometric butterfly result.</p>
    <table><thead><tr><th>Dataset</th><th>Status</th><th>Analytical RSNM (mV)</th><th>VTH,eff (V)</th><th>q = βPU/βPG</th><th>r = βPD/βPG</th><th>k</th><th>Applicability</th></tr></thead><tbody>{analytical_table_rows}</tbody></table>
    <p>If the analytical square-root domain is non-positive, the result is shown as N/A instead of forcing a complex value.</p></section>'''
    electrical_snm_table_rows = "".join(
        f'<tr><td>{html.escape(row["dataset"])}</td>'
        f'<td>{row["pu_vt_v"]:.3f}</td><td>{row["pu_idsat_ua"]:.2f}</td>'
        f'<td>{row["pg_vt_v"]:.3f}</td><td>{row["pg_idsat_ua"]:.2f}</td>'
        f'<td>{row["pd_vt_v"]:.3f}</td><td>{row["pd_idsat_ua"]:.2f}</td>'
        f'<td>{row["q_beta_pu_over_pg"]:.4f}</td><td>{row["r_beta_pd_over_pg"]:.4f}</td>'
        f'<td>{row["idsat_pd_over_pg"]:.4f}</td><td>{row["idsat_pg_over_pu"]:.4f}</td>'
        f'<td>{row["read_snm_geometric_mv"]:.2f}</td>'
        f'<td>{row["write_snm_proxy_mv"]:.2f}</td>'
        f'<td>{_fmt(row["read_snm_eq_3_36_mv"], 2)}</td><td>{row["eq_3_36_status"]}</td></tr>'
        for row in electrical_snm_rows)
    electrical_snm_section = f'''<section><h2>WAT Electrical Parameters → SNM Table</h2>
    <p>This table uses only electrical values that can be entered from WAT measurement: PU/PG/PD Vt and Idsat at the stated WAT VDD. The β values, q/r and current ratios are mathematical proxies derived from those measurements. No W/L, Cox, mobility, BSIM coefficients, parasitics or other PDK/model-card-only parameters are required.</p>
    <div class="table-scroll"><table><thead><tr><th>Dataset</th><th>PU Vt<br>(V)</th><th>PU Idsat<br>(µA)</th><th>PG Vt<br>(V)</th><th>PG Idsat<br>(µA)</th><th>PD Vt<br>(V)</th><th>PD Idsat<br>(µA)</th><th>q<br>βPU/βPG</th><th>r<br>βPD/βPG</th><th>Idsat<br>PD/PG</th><th>Idsat<br>PG/PU</th><th>Read SNM<br>(mV)</th><th>Write SNM<br>Proxy (mV)</th><th>Analytical<br>RSNM (mV)</th><th>Analytical<br>Status</th></tr></thead><tbody>{electrical_snm_table_rows}</tbody></table></div>
    <p><b>Interpretation:</b> geometric Read SNM comes from the WAT-calibrated butterfly curves; the analytical RSNM is an independent long-channel reference. These are correlation and trend metrics, not foundry sign-off values.</p></section>'''
    assumption_table_rows = "".join(
        f'<tr><td>{html.escape(str(row["parameter"]))}</td>'
        f'<td>{html.escape(str(row["value"]))}</td><td>{html.escape(str(row["unit"]))}</td>'
        f'<td>{html.escape(str(row["source"]))}</td><td>{html.escape(str(row["used_by"]))}</td>'
        f'<td>{row["active"]}</td></tr>' for row in assumption_rows)
    assumption_section = f'''<section><h2>Generic 28 nm Default Assumptions</h2>
    <p>Measured WAT Vt and Idsat always take priority. Parameters unavailable from WAT are filled only when the present formula needs them. Geometry and temperature are shown as generic references but are not silently used to replace WAT calibration.</p>
    <table><thead><tr><th>Parameter</th><th>Default / policy</th><th>Unit</th><th>Source class</th><th>Used by</th><th>Active in SNM</th></tr></thead><tbody>{assumption_table_rows}</tbody></table>
    <p><b>Important:</b> Cox, carrier mobility, oxide thickness and channel-length modulation are not guessed because the active compact model obtains effective beta directly from WAT Vt/Idsat. Adding arbitrary values for them would double-count device strength and make the result less defensible.</p></section>'''
    object_section = ""
    if "cell" in result:
        mos_rows = "".join(
            f'<tr><td>{name}</td><td>{values["vt"]:.3f}</td><td>{values["ids"]:.2f}</td></tr>'
            for name, values in result["cell"]["mos"].items())
        object_section = f'''<section><h2>{html.escape(result.get("object_mode", "6T"))} WAT Objects</h2>
        <table><thead><tr><th>MOS object</th><th>Vt (V)</th><th>Isat / Ids (µA)</th></tr></thead><tbody>{mos_rows}</tbody></table></section>'''

    cfg, wat = result["config"], result["wat"]
    document = f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HV28 SRAM Analysis</title>
    <style>
    :root{{font:100%/1.5 Calibri,"Microsoft JhengHei",Arial,sans-serif;color:#1d1d1f;background:#f5f5f7}}
    *{{box-sizing:border-box}} body{{margin:0;padding:clamp(1.25rem,4vw,3rem);background:radial-gradient(circle at 85% 0,#e8f2ff 0,transparent 28rem),#f5f5f7}}
    main{{max-width:1760px;margin:auto}} h1{{font-size:clamp(2.2rem,4vw,4rem);line-height:1.05;letter-spacing:-.035em;margin:.5rem 0 .35rem}} h2{{font-size:1.7rem;letter-spacing:-.018em;margin-top:0}}
    .summary,section{{background:rgba(255,255,255,.84);backdrop-filter:blur(22px) saturate(160%);border:1px solid rgba(255,255,255,.78);border-radius:1.25rem;box-shadow:0 1px 2px #0000000a,0 12px 36px #0000000d;padding:clamp(1rem,2.4vw,1.65rem);margin:1rem 0}}
    .summary{{color:#3a3a3c;font-size:1.05rem;border-left:4px solid #007aff}} img{{display:block;width:100%;height:auto;border:1px solid #e5e5ea;border-radius:1rem}} .formula{{background:#f2f2f7;border-radius:.8rem;padding:1rem;line-height:1.8;overflow:auto}} .table-scroll{{overflow-x:auto}}
    table{{border-collapse:separate;border-spacing:0;width:100%;margin-top:1rem;font-size:1rem;line-height:1.55}} th,td{{padding:.9rem .8rem;border-bottom:1px solid #e5e5ea;text-align:right;font-variant-numeric:tabular-nums}} th{{color:#6e6e73;font-size:.9rem;font-weight:680}} th:first-child,td:first-child{{text-align:left}} tbody tr:last-child td{{border-bottom:0}} code{{background:#f2f2f7;padding:.18rem .38rem;border-radius:.35rem}}
    @media(prefers-reduced-transparency:reduce){{.summary,section{{background:#fff;backdrop-filter:none;border-color:#d2d2d7}}}} @media(prefers-contrast:more){{.summary,section{{background:#fff;border:2px solid #1d1d1f}}}}
    </style></head><body><main>
    <h1>HV28 SRAM Analysis</h1><p>Lot/Wafer: <b>{html.escape(wat["corner"])}</b> · Object mode: <b>{html.escape(result.get("object_mode", "Grouped"))}</b> · SRAM VDD={cfg["nominal_vdd"]:.3f} V</p>
    <div class="summary"><b>Analysis scope:</b> Read SNM plus write-condition SNM proxy. Blue curves use the measured Lot/Wafer WAT inputs; orange curves use the entered PU/PG/PD WAT targets.</div>
    <section><h2>Read SNM Target Comparison</h2><p>The plotted curves use the WAT-calibrated compact VTC model. X-axis is Vin and Y-axis is Vout, both expressed in volts and fixed at 0 to 1.20 V. Limiting squares are not drawn in this comparison view.</p>
    <img src="images/{png_name}" alt="Read SNM Lot/Wafer WAT versus WAT Target comparison">
    <table><thead><tr><th>Mode</th><th>Lot/Wafer SNM (mV)</th><th>WAT Target SNM (mV)</th><th>Lot/Wafer − WAT Target (mV)</th><th>Difference (%)</th></tr></thead><tbody>{snm_rows}</tbody></table></section>
    <section><h2>Read SNM Butterfly and Left/Right Mismatch</h2><p>The measured 6T model keeps PUL/PGL/PDL and PUR/PGR/PDR independent. The upper-left and lower-right eyes represent opposite stored states. Cell RSNM is the smaller state margin; a larger difference or mismatch index indicates stronger left/right imbalance. X-axis is Vin and Y-axis is Vout, both expressed in volts and fixed at 0 to 1.20 V.</p>
    <img src="images/{butterfly_png_name}" alt="Asymmetric Read SNM butterfly with two state margins">
    <table><thead><tr><th>Dataset</th><th>Upper-left state SNM (mV)</th><th>Lower-right state SNM (mV)</th><th>Cell RSNM = min (mV)</th><th>Upper - Lower (mV)</th><th>Mismatch index</th></tr></thead><tbody>{state_table_rows}</tbody></table></section>
    <section><h2>Write SNM Target Comparison</h2><p>Write condition uses the configured WL, a low write bitline and a high complementary bitline. The two VTCs are intentionally asymmetric. Vin and Vout axes are fixed at 0 to 1.20 V. Write SNM is reported as the maximum tolerated rise on the nominally-low write bitline while the access device can still overcome the pull-up device; it is a WAT-calibrated write-margin proxy, not a geometric butterfly-square or sign-off WSNM.</p>
    <img src="images/{write_png_name}" alt="Write SNM target comparison"></section>
    {electrical_snm_section}
    {assumption_section}
    {analytical_section}
    {target_section}
    <section><h2>Model Settings</h2><table><tbody><tr><td>Technology node</td><td>28 nm generic compact model</td></tr><tr><td>SRAM analysis VDD</td><td>{cfg["nominal_vdd"]:.3f} V</td></tr><tr><td>WAT calibration VDD</td><td>{cfg["wat_vdd"]:.3f} V</td></tr></tbody></table></section>
    {object_section}
    <p>Raw data: <code>snm_target_comparison.csv</code>, <code>read_snm_state_mismatch.csv</code>, <code>wat_electrical_snm_table.csv</code>, <code>generic_28nm_assumptions.csv</code>, <code>analytical_read_snm.csv</code>, <code>wat_target_comparison.csv</code>, <code>sram_wat_results.json</code>. Standalone charts: <code>images/{png_name}</code>, <code>images/{butterfly_png_name}</code> and <code>images/{write_png_name}</code>.</p>
    </main></body></html>'''
    report = out / "sram_wat_report.html"
    report.write_text(document, encoding="utf-8")
    return report


def write_excel_sweep_outputs(entries: list[dict], out_dir: str | os.PathLike[str]) -> Path:
    """Write one HTML report plus combined butterfly and SNM trend plots for Excel VDD sweeps."""
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    image_dir = out / "images"; image_dir.mkdir(parents=True, exist_ok=True)
    try:
        from reportlab.graphics import renderPM
        from svglib.svglib import svg2rlg
    except ImportError as exc:
        raise RuntimeError("PNG export packages are missing. Run: python -m pip install -r requirements.txt") from exc

    analyzed_entries = [item for item in entries if item.get("result") is not None]
    if not analyzed_entries:
        raise ValueError("Excel contains no Model VDD above the imported MOS threshold voltages")
    charts = [
        ("04_model_vdd_read_snm_butterfly", model_vdd_butterfly_svg(analyzed_entries), "Read SNM butterfly panels by model VDD"),
        ("05_snm_vs_model_vdd", snm_by_model_vdd_svg(analyzed_entries), "Read and Write SNM versus model VDD"),
        ("06_all_vdd_read_snm_overlay", all_model_vdd_butterfly_overlay_svg(analyzed_entries), "All operating VDD Read SNM butterfly overlay"),
    ]
    manifest_rows = []
    for stem, svg, description in charts:
        svg_path = image_dir / f"{stem}.svg"
        png_path = image_dir / f"{stem}.png"
        svg_path.write_text(svg, encoding="utf-8")
        drawing = svg2rlg(str(svg_path))
        if drawing is None:
            raise RuntimeError(f"Could not render {stem}")
        renderPM.drawToFile(drawing, str(png_path), fmt="PNG", dpi=180, backend="rlPyCairo")
        manifest_rows.extend([
            {"filename": png_path.name, "format": "PNG", "description": description},
            {"filename": svg_path.name, "format": "SVG", "description": description},
        ])
    rows = []
    for item in entries:
        result = item.get("result")
        metrics = result["baseline_6t"]["metrics"] if result is not None else {}
        has_target = bool(result and result.get("datasheet_targets"))
        target_metrics = result["target_6t"]["metrics"] if has_target else {}
        read_snm = metrics.get("read_snm_mv")
        target_read_snm = target_metrics.get("read_snm_mv")
        rows.append({
            "lot_wafer": item["lot_wafer"], "model_vdd_v": item["model_vdd_v"],
            "read_snm_mv": read_snm,
            "read_snm_upper_left_mv": metrics.get("read_snm_upper_left_mv"),
            "read_snm_lower_right_mv": metrics.get("read_snm_lower_right_mv"),
            "read_snm_state_delta_mv": metrics.get("read_snm_delta_mv"),
            "read_snm_mismatch_index_pct": metrics.get("read_snm_mismatch_index_pct"),
            "target_read_snm_mv": target_read_snm,
            "read_snm_delta_mv": (read_snm - target_read_snm
                                  if read_snm is not None and target_read_snm is not None else None),
            "write_snm_proxy_mv": metrics.get("write_snm_proxy_mv"),
            "target_write_snm_proxy_mv": target_metrics.get("write_snm_proxy_mv"),
            "analytical_read_snm_mv": metrics.get("analytical_read_snm_mv"),
            "analysis_status": item.get("analysis_status", "Analyzed"),
        })
    with open(out / "excel_model_vdd_snm.csv", "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    statistics_rows = []
    for item in entries:
        for mos_name in ("pu1", "pu2", "pg1", "pg2", "pd1", "pd2"):
            stats = item.get("statistics", {}).get(mos_name)
            if stats is None:
                continue
            statistics_rows.append({
                "lot_wafer": item["lot_wafer"],
                "model_vdd_v": item["model_vdd_v"],
                "mos": DISPLAY_MOS_NAMES[mos_name],
                "valid_count": stats.valid_count,
                "total_count": stats.total_count,
                "coverage_pct": 100.0 * stats.valid_count / stats.total_count if stats.total_count else 0.0,
                "vt_mean_v": stats.vt_mean,
                "vt_median_v": stats.vt_median,
                "vt_stdev_v": stats.vt_stdev,
                "vt_min_v": stats.vt_min,
                "vt_max_v": stats.vt_max,
                "idsat_mean_ua": stats.ids_mean,
                "idsat_median_ua": stats.ids_median,
                "idsat_stdev_ua": stats.ids_stdev,
                "idsat_min_ua": stats.ids_min,
                "idsat_max_ua": stats.ids_max,
            })
    if statistics_rows:
        with open(out / "excel_wat_site_statistics.csv", "w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=list(statistics_rows[0])); writer.writeheader(); writer.writerows(statistics_rows)
    with open(image_dir / "excel_sweep_image_manifest.csv", "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(manifest_rows[0])); writer.writeheader(); writer.writerows(manifest_rows)
    table_rows = "".join(
        f'<tr><td>{html.escape(row["lot_wafer"])}</td><td>{row["model_vdd_v"]:.3f}</td>'
        f'<td>{_fmt(row["read_snm_upper_left_mv"], 2)}</td><td>{_fmt(row["read_snm_lower_right_mv"], 2)}</td>'
        f'<td>{_fmt(row["read_snm_mv"], 2)}</td><td>{_fmt(row["read_snm_state_delta_mv"], 2)}</td>'
        f'<td>{_fmt(row["read_snm_mismatch_index_pct"], 2)}</td><td>{_fmt(row["target_read_snm_mv"], 2)}</td><td>{_fmt(row["read_snm_delta_mv"], 2)}</td>'
        f'<td>{_fmt(row["write_snm_proxy_mv"], 2)}</td><td>{_fmt(row["target_write_snm_proxy_mv"], 2)}</td>'
        f'<td>{_fmt(row["analytical_read_snm_mv"], 2)}</td><td>{html.escape(row["analysis_status"])}</td></tr>' for row in rows)
    statistics_table_rows = "".join(
        f'<tr><td>{html.escape(row["lot_wafer"])}</td><td>{row["model_vdd_v"]:.3f}</td><td>{row["mos"]}</td>'
        f'<td>{row["valid_count"]}/{row["total_count"]}</td><td>{row["coverage_pct"]:.1f}%</td>'
        f'<td>{row["vt_mean_v"]:.4f}</td><td>{row["vt_stdev_v"]:.4f}</td>'
        f'<td>{row["idsat_mean_ua"]:.3f}</td><td>{row["idsat_stdev_ua"]:.3f}</td></tr>'
        for row in statistics_rows)
    statistics_section = (f'''<section><h2>Wafer-Site WAT Statistics</h2>
    <p>The 6T model uses the arithmetic mean of all valid sites at each Lot/Wafer, model VDD and physical MOS. Median, standard deviation, minimum and maximum are retained in <code>excel_wat_site_statistics.csv</code>.</p>
    <table><thead><tr><th>Lot/Wafer</th><th>Model VDD (V)</th><th>MOS</th><th>Valid/Total</th><th>Coverage</th><th>Mean Vt (V)</th><th>Vt σ (V)</th><th>Mean Idsat (µA)</th><th>Idsat σ (µA)</th></tr></thead><tbody>{statistics_table_rows}</tbody></table></section>'''
        if statistics_rows else "")
    document = f'''<!doctype html><html><head><meta charset="utf-8"><title>HV28 SRAM Excel VDD Sweep</title>
    <style>body{{font-family:Calibri,Arial,sans-serif;background:#F5F5F7;color:#1D1D1F;margin:0}}main{{max-width:1500px;margin:auto;padding:42px}}section{{background:#fff;border-radius:18px;padding:24px;margin:16px 0}}img{{width:100%;height:auto;border:1px solid #E5E5EA;border-radius:12px}}table{{border-collapse:collapse;width:100%}}th,td{{padding:10px;border-bottom:1px solid #E5E5EA;text-align:left}}th{{color:#6E6E73}}.note{{color:#6E6E73}}</style></head><body><main>
    <h1>HV28 SRAM Excel Model-VDD Sweep</h1><p class="note">PU, PG and PD worksheets are combined by Lot/Wafer and model VDD. Repeated wafer-site records are converted to V/uA and averaged per physical MOS before 6T analysis.</p>
    <section><h2>Read SNM Butterfly by Operating VDD</h2><p>Each panel compares measured-WAT and WAT-target VTCs, mirrored VTCs and SNM squares under the same operating voltage. Vin/Vout axes are fixed at 0 to 1.20 V.</p><img src="images/04_model_vdd_read_snm_butterfly.png" alt="Measured and target Read SNM butterflies by model VDD"></section>
    <section><h2>Independent SNM Trend</h2><p>Measured and target Read SNM / Write SNM proxy are plotted against imported model VDD.</p><img src="images/05_snm_vs_model_vdd.png" alt="Measured and target SNM versus model VDD"></section>
    <section><h2>All Operating Voltages Overlay</h2><p>All analyzed VDD butterfly curves are integrated in one Vin/Vout plot. Curve color identifies operating VDD; solid lines are measured WAT and dashed lines are WAT Target.</p><img src="images/06_all_vdd_read_snm_overlay.png" alt="All operating VDD Read SNM butterfly overlay"></section>
    <section><h2>Model-VDD Results</h2><table><thead><tr><th>Lot/Wafer</th><th>Model VDD (V)</th><th>Upper-left RSNM (mV)</th><th>Lower-right RSNM (mV)</th><th>Cell RSNM (mV)</th><th>State delta (mV)</th><th>Mismatch (%)</th><th>Target RSNM (mV)</th><th>WAT - Target (mV)</th><th>WAT Write Proxy (mV)</th><th>Target Write Proxy (mV)</th><th>Analytical RSNM (mV)</th><th>Status</th></tr></thead><tbody>{table_rows}</tbody></table></section>
    {statistics_section}
    <p class="note">Raw results: <code>excel_model_vdd_snm.csv</code> and <code>excel_wat_site_statistics.csv</code>; images: <code>images/04_model_vdd_read_snm_butterfly.png</code>, <code>images/05_snm_vs_model_vdd.png</code> and <code>images/06_all_vdd_read_snm_overlay.png</code>.</p></main></body></html>'''
    report = out / "excel_wat_sweep_report.html"
    report.write_text(document, encoding="utf-8")
    return report


def analyze_excel_wat_sweep(path: str | os.PathLike[str], out_dir: str | os.PathLike[str], cfg: Config,
                            targets: DatasheetTargets | None = None,
                            archive_run: bool = False) -> Path:
    """Analyze each imported model voltage and create one combined Excel sweep report."""
    validate_config(cfg)
    samples = read_wat_excel(path, cfg.nominal_vdd)
    entries = []
    for sample in samples:
        highest_vt = max(getattr(sample.cell, name).vt for name in ("pu1", "pu2", "pg1", "pg2", "pd1", "pd2"))
        if sample.model_vdd_v <= highest_vt:
            result = None
            status = f"WAT statistics only: VDD ≤ highest MOS Vt ({highest_vt:.3f} V)"
        else:
            model_cfg = replace(cfg, nominal_vdd=sample.model_vdd_v)
            result = analyze_six_mos(sample.cell, model_cfg, targets) if targets else analyze_six_mos(sample.cell, model_cfg, None)
            status = "Analyzed"
        entries.append({"lot_wafer": sample.lot_wafer, "model_vdd_v": sample.model_vdd_v,
                        "statistics": sample.statistics, "analysis_status": status, "result": result})
    if archive_run:
        wafer_ids = sorted({sample.lot_wafer for sample in samples})
        wafer_label = (wafer_ids[0] if len(wafer_ids) == 1 else
                       f"{wafer_ids[0]}_plus_{len(wafer_ids) - 1}")
        out_dir = create_run_output_dir(out_dir, wafer_label, "excel_vdd_sweep")
    return write_excel_sweep_outputs(entries, out_dir)


def run_analysis(csv_path: str, out_dir: str, cfg: Config, corner: str | None = None,
                 targets: DatasheetTargets | None = None) -> list[Path]:
    validate_config(cfg)
    if Path(csv_path).suffix.lower() == ".xlsx":
        if corner:
            raise ValueError("--corner is not used for Excel model-VDD sweep import")
        return [analyze_excel_wat_sweep(csv_path, out_dir, cfg, targets, archive_run=True)]
    points = read_wat_csv(csv_path)
    if corner:
        points = [p for p in points if p.corner.lower() == corner.lower()]
        if not points:
            raise ValueError(f"corner not found: {corner}")
    reports = []
    for p in points:
        if targets:
            cell = ThreeTWatCell(p.corner, MosWat(p.pu_vt, p.pu_ids),
                                 MosWat(p.pg_vt, p.pg_ids), MosWat(p.pd_vt, p.pd_ids))
            result = analyze_three_mos(cell, cfg, targets)
        else:
            result = analyze(p, cfg)
        target = create_run_output_dir(out_dir, p.corner, "6t_analysis")
        reports.append(write_outputs(result, target))
    return reports


def _launch_legacy_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk(); root.title("HV28 SRAM Analysis"); root.geometry("820x720"); root.minsize(760, 680)
    saved_state = load_gui_state()
    def saved_text(group: str, key: str, default: object) -> str:
        return str(saved_state.get(group, {}).get(key, default)) if isinstance(saved_state.get(group), dict) else str(default)

    values = {"out": tk.StringVar(value=saved_text("values", "out", Path.cwd()/"output")),
              "corner": tk.StringVar(value=saved_text("values", "corner", "DEMO28_TT_W01")),
              "excel_input": tk.StringVar(value=saved_text("values", "excel_input", ""))}
    defaults={"pu":("0.385","44"),"pg":("0.365","82"),"pd":("0.355","124")}
    wat_values={}
    for dev in ("pu1","pu2","pg1","pg2","pd1","pd2"):
        vt,ids=defaults[dev[:2]]
        wat_values[f"{dev}_vt"]=tk.StringVar(value=vt); wat_values[f"{dev}_ids"]=tk.StringVar(value=ids)
    numeric = {k: tk.StringVar(value=str(v)) for k,v in asdict(Config()).items() if k != "grid_points"}
    frame = ttk.Frame(root, padding=18); frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="HV28 SRAM Analysis", font=("Calibri", 17, "bold")).grid(row=0,column=0,columnspan=4,sticky="w")
    ttk.Label(frame, text="固定 28 nm 6T 架構；手動輸入 WAT，Python 產生 SNM 與 R/W Vmin（不使用 SPICE）",
              foreground="#475569").grid(row=1,column=0,columnspan=4,sticky="w",pady=(3,14))

    wat_box = ttk.LabelFrame(frame, text="WAT 手動輸入", padding=12)
    wat_box.grid(row=2,column=0,columnspan=4,sticky="ew",pady=5)
    ttk.Label(wat_box,text="Lot / Wafer ID").grid(row=0,column=0,sticky="w",padx=(0,8))
    ttk.Entry(wat_box,textvariable=values["corner"],width=16).grid(row=0,column=1,sticky="w")
    ttk.Label(wat_box,text="Q side",font=("Calibri",10,"bold")).grid(row=1,column=0,columnspan=3,pady=(10,3))
    ttk.Label(wat_box,text="QB side",font=("Calibri",10,"bold")).grid(row=1,column=3,columnspan=3,pady=(10,3))
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
            run_dir=create_run_output_dir(values["out"].get(),point.corner,"6t_analysis")
            report=write_outputs(analyze_six_mos(point,cfg),run_dir)
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
    from tkinter import filedialog, font as tkfont, messagebox, ttk

    BG, CARD, TEXT, SECONDARY = "#F5F5F7", "#FFFFFF", "#1D1D1F", "#6E6E73"
    BLUE, BLUE_DARK, BORDER, GREEN, RED = "#007AFF", "#0062CC", "#D2D2D7", "#34C759", "#FF3B30"
    root = tk.Tk()
    root.title("HV28 SRAM Analysis")
    root.geometry("1180x940")
    root.minsize(1080, 860)
    root.configure(bg=BG)
    if sys.platform == "win32":
        root.after(0, lambda: root.state("zoomed"))
    for named_font in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                       "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
        try:
            tkfont.nametofont(named_font).configure(family="Calibri")
        except tk.TclError:
            pass

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Root.TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD, relief="flat")
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Calibri", 24, "bold"))
    style.configure("Subtitle.TLabel", background=BG, foreground=SECONDARY, font=("Calibri", 10))
    style.configure("Section.TLabel", background=CARD, foreground=TEXT, font=("Calibri", 13, "bold"))
    style.configure("ChartTitle.TLabel", background=CARD, foreground=TEXT,
                    font=("Calibri", 18, "bold"))
    style.configure("Body.TLabel", background=CARD, foreground=TEXT, font=("Calibri", 10))
    style.configure("Meta.TLabel", background=CARD, foreground=SECONDARY, font=("Calibri", 9))
    style.configure("Apple.TEntry", fieldbackground="#F2F2F7", foreground=TEXT, bordercolor="#E5E5EA",
                    lightcolor="#E5E5EA", darkcolor="#E5E5EA", padding=(8, 6))
    style.map("Apple.TEntry", bordercolor=[("focus", BLUE)])
    style.configure("Accent.TButton", background=BLUE, foreground="white", borderwidth=0,
                    font=("Calibri", 11, "bold"), padding=(18, 11))
    style.map("Accent.TButton", background=[("pressed", BLUE_DARK), ("active", "#1689FF"), ("disabled", "#A7CFFF")])
    style.configure("Quiet.TButton", background="#E9E9ED", foreground=TEXT, borderwidth=0, padding=(10, 7))
    style.map("Quiet.TButton", background=[("pressed", "#D8D8DC"), ("active", "#E2E2E7")])
    style.configure("Apple.Horizontal.TProgressbar", background=BLUE, troughcolor="#E5E5EA", borderwidth=0)
    style.configure("Apple.TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 10))
    style.configure("Apple.TNotebook.Tab", background="#E9E9ED", foreground=TEXT,
                    borderwidth=0, padding=(18, 9), font=("Calibri", 10, "bold"))
    style.map("Apple.TNotebook.Tab", background=[("selected", CARD), ("active", "#F0F0F4")],
              foreground=[("selected", BLUE)],
              font=[("selected", ("Calibri", 12, "bold")),
                    ("!selected", ("Calibri", 10, "bold"))],
              padding=[("selected", (22, 12)), ("!selected", (18, 9))])

    saved_state = load_gui_state()

    def saved_text(group: str, key: str, default: object) -> str:
        group_values = saved_state.get(group, {})
        return str(group_values.get(key, default)) if isinstance(group_values, dict) else str(default)

    values = {
        "out": tk.StringVar(value=saved_text("values", "out", Path.cwd() / "output")),
        "corner": tk.StringVar(value=saved_text("values", "corner", "DEMO28_TT_W01")),
        "excel_input": tk.StringVar(value=saved_text("values", "excel_input", "")),
    }
    defaults = {"pu": ("0.385", "44"), "pg": ("0.365", "82"), "pd": ("0.355", "124")}
    target_defaults = {"pu": ("0.380", "45"), "pg": ("0.370", "80"), "pd": ("0.360", "120")}
    wat_values: dict[str, tk.StringVar] = {}
    for dev in ("pu1", "pu2", "pg1", "pg2", "pd1", "pd2"):
        vt, ids = defaults[dev[:2]]
        wat_values[f"{dev}_vt"] = tk.StringVar(value=saved_text("wat", f"{dev}_vt", vt))
        wat_values[f"{dev}_ids"] = tk.StringVar(value=saved_text("wat", f"{dev}_ids", ids))
    target_values: dict[str, tk.StringVar] = {}
    for dev in ("pu", "pg", "pd"):
        vt, ids = target_defaults[dev]
        target_values[f"{dev}_vt"] = tk.StringVar(value=saved_text("targets", f"{dev}_vt", vt))
        target_values[f"{dev}_ids"] = tk.StringVar(value=saved_text("targets", f"{dev}_ids", ids))
    config_defaults = Config()
    numeric = {
        "nominal_vdd": tk.StringVar(value=saved_text("numeric", "nominal_vdd", config_defaults.nominal_vdd)),
        "wat_vdd": tk.StringVar(value=saved_text("numeric", "wat_vdd", config_defaults.wat_vdd)),
    }
    assumption_specs = (
        ("technology_node_nm", "Technology node", "nm"),
        ("channel_length_nm", "Channel length L", "nm"),
        ("pu_width_nm", "PU width", "nm"),
        ("pg_width_nm", "PG width", "nm"),
        ("pd_width_nm", "PD width", "nm"),
        ("nominal_temperature_c", "Nominal temperature", "°C"),
        ("read_wordline_over_vdd", "Read WL / VDD", "×VDD"),
        ("read_bitline_over_vdd", "Read BL / VDD", "×VDD"),
        ("write_wordline_over_vdd", "Write WL / VDD", "×VDD"),
        ("write_low_bitline_over_vdd", "Write BL / VDD", "×VDD"),
        ("write_high_bitline_over_vdd", "Write BLB / VDD", "×VDD"),
    )
    assumption_values = {
        key: tk.StringVar(value=saved_text("assumptions", key, getattr(config_defaults, key)))
        for key, _label, _unit in assumption_specs
    }

    shell = ttk.Frame(root, style="Root.TFrame", padding=(28, 22, 28, 24)); shell.pack(fill="both", expand=True)
    header = ttk.Frame(shell, style="Root.TFrame"); header.pack(fill="x", pady=(0, 18))
    ttk.Label(header, text="HV28 SRAM Analysis", style="Title.TLabel").pack(side="left")
    badge = tk.Label(header, text="  WAT STUDIO  ", bg="#E5F1FF", fg=BLUE,
                     font=("Calibri", 9, "bold"), padx=7, pady=4)
    badge.pack(side="left", padx=12, pady=(7, 0))
    ttk.Label(header, text="Object-oriented 6T bitcell analysis", style="Subtitle.TLabel").pack(side="right", pady=(10, 0))

    notebook = ttk.Notebook(shell, style="Apple.TNotebook")
    notebook.pack(fill="both", expand=True)
    bitcell_tab = ttk.Frame(notebook, style="Root.TFrame")
    curve_tab = ttk.Frame(notebook, style="Root.TFrame")
    notebook.add(bitcell_tab, text="6T Bitcell Analysis")
    notebook.add(curve_tab, text="RSNM vs VDD Curve")

    content = ttk.Frame(bitcell_tab, style="Root.TFrame"); content.pack(fill="both", expand=True)
    content.columnconfigure(0, weight=7); content.columnconfigure(1, weight=4); content.rowconfigure(0, weight=1)
    left = ttk.Frame(content, style="Card.TFrame", padding=18); left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
    right_card = ttk.Frame(content, style="Card.TFrame"); right_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
    # Keep the primary action visible while the analysis inputs scroll independently.
    right_footer = ttk.Frame(right_card, style="Card.TFrame", padding=(18, 8, 18, 18))
    right_footer.pack(side="bottom", fill="x")
    right_view = tk.Canvas(right_card, bg=CARD, highlightthickness=0, bd=0)
    right_scroll = ttk.Scrollbar(right_card, orient="vertical", command=right_view.yview)
    right_scroll.pack(side="right", fill="y", pady=(12, 6))
    right_view.pack(side="left", fill="both", expand=True)
    right_view.configure(yscrollcommand=right_scroll.set)
    right = ttk.Frame(right_view, style="Card.TFrame", padding=(18, 18, 12, 8))
    right_window = right_view.create_window((0, 0), window=right, anchor="nw")

    def sync_right_scroll(_event=None) -> None:
        right_view.configure(scrollregion=right_view.bbox("all"))

    def fit_right_width(event) -> None:
        right_view.itemconfigure(right_window, width=event.width)

    def scroll_right(event) -> None:
        if event.delta:
            right_view.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def enable_right_scroll(_event) -> None:
        root.bind_all("<MouseWheel>", scroll_right)

    def disable_right_scroll(_event) -> None:
        root.unbind_all("<MouseWheel>")

    right.bind("<Configure>", sync_right_scroll)
    right_view.bind("<Configure>", fit_right_width)
    right_view.bind("<Enter>", enable_right_scroll)
    right_view.bind("<Leave>", disable_right_scroll)
    root.after_idle(sync_right_scroll)

    ttk.Label(left, text="Bitcell WAT", style="Section.TLabel").pack(anchor="w")
    ttk.Label(left, text="Enter Vt and Ids beside each physical MOS.", style="Meta.TLabel").pack(anchor="w", pady=(2, 8))
    top_fields = ttk.Frame(left, style="Card.TFrame"); top_fields.pack(fill="x", pady=(0, 4))
    ttk.Label(top_fields, text="Lot / Wafer", style="Body.TLabel").pack(side="left")
    ttk.Entry(top_fields, textvariable=values["corner"], width=14, style="Apple.TEntry").pack(side="left", padx=10)
    tk.Label(top_fields, text="  6T INDEPENDENT  ", bg="#EEF6FF", fg=BLUE,
             font=("Calibri", 9, "bold"), padx=8, pady=5).pack(side="right")

    excel_row = ttk.Frame(left, style="Card.TFrame"); excel_row.pack(fill="x", pady=(2, 7))
    ttk.Label(excel_row, text="Excel 6T WAT", style="Body.TLabel").pack(side="left")
    excel_label = ttk.Label(excel_row, textvariable=values["excel_input"], style="Meta.TLabel")
    excel_label.pack(side="left", fill="x", expand=True, padx=(10, 6))

    def pick_excel() -> None:
        selected = filedialog.askopenfilename(
            title="Import 6T WAT Excel", filetypes=[("Excel workbook", "*.xlsx"), ("All files", "*.*")])
        if not selected:
            return
        try:
            samples = read_wat_excel(selected, float(numeric["nominal_vdd"].get()))
            first = samples[0]
            values["excel_input"].set(selected)
            values["corner"].set(first.lot_wafer)
            numeric["nominal_vdd"].set(f"{first.model_vdd_v:.3f}")
            for mos_name in ("pu1", "pu2", "pg1", "pg2", "pd1", "pd2"):
                mos = getattr(first.cell, mos_name)
                wat_values[f"{mos_name}_vt"].set(f"{mos.vt:.6g}")
                wat_values[f"{mos_name}_ids"].set(f"{mos.ids:.6g}")
            status.set(f"Excel loaded: {len(samples)} model-VDD point(s); first point copied to the 6T inputs")
            status_label.configure(fg=GREEN)
        except Exception as exc:
            messagebox.showerror("Excel import", str(exc))

    ttk.Button(excel_row, text="Import Excel…", style="Quiet.TButton", command=pick_excel).pack(side="right")

    schematic = tk.Canvas(left, bg=CARD, highlightthickness=0, height=540)
    schematic.pack(fill="both", expand=True)
    # Standard 6T arrangement: WL above, BLB/BL at the sides, cross-coupled Q/QB.
    schematic.create_line(45, 48, 605, 48, fill=BORDER, width=2)
    schematic.create_text(325, 30, text="WL", fill=TEXT, font=("Calibri", 12, "bold"))
    schematic.create_line(68, 48, 68, 474, fill=BORDER, width=2)
    schematic.create_line(582, 48, 582, 474, fill=BORDER, width=2)
    schematic.create_text(68, 498, text="BLB", fill=SECONDARY, font=("Calibri", 10, "bold"))
    schematic.create_text(582, 498, text="BL", fill=SECONDARY, font=("Calibri", 10, "bold"))
    schematic.create_line(205, 105, 445, 105, fill=BORDER, width=2)
    schematic.create_text(325, 91, text="VDD", fill=SECONDARY, font=("Calibri", 9, "bold"))
    schematic.create_line(205, 442, 445, 442, fill=BORDER, width=2)
    schematic.create_text(325, 463, text="GND", fill=SECONDARY, font=("Calibri", 9, "bold"))
    schematic.create_line(265, 105, 265, 257, fill=BORDER, width=2)
    schematic.create_line(385, 105, 385, 257, fill=BORDER, width=2)
    schematic.create_line(265, 277, 265, 442, fill=BORDER, width=2)
    schematic.create_line(385, 277, 385, 442, fill=BORDER, width=2)
    schematic.create_line(68, 267, 202, 267, fill=GREEN, width=2)
    schematic.create_line(448, 267, 582, 267, fill=GREEN, width=2)
    schematic.create_line(265, 215, 385, 318, fill="#AF52DE", width=2, dash=(5, 4))
    schematic.create_line(385, 215, 265, 318, fill="#AF52DE", width=2, dash=(5, 4))
    schematic.create_oval(255, 257, 275, 277, fill=TEXT, outline="")
    schematic.create_oval(375, 257, 395, 277, fill=TEXT, outline="")
    schematic.create_text(265, 245, text="QB", fill=TEXT, font=("Calibri", 11, "bold"))
    schematic.create_text(385, 245, text="Q", fill=TEXT, font=("Calibri", 11, "bold"))

    def mos_panel(name: str, accent: str, source: dict[str, tk.StringVar]) -> ttk.Frame:
        panel = tk.Frame(schematic, bg="#FAFAFC", highlightbackground=BORDER, highlightthickness=1, padx=7, pady=6)
        display_name = DISPLAY_MOS_NAMES.get(name, name.upper())
        tk.Label(panel, text=display_name, bg="#FAFAFC", fg=accent,
                 font=("Calibri", 9, "bold")).grid(row=0, column=0, columnspan=4, sticky="w")
        tk.Label(panel, text="Vt", bg="#FAFAFC", fg=SECONDARY, font=("Calibri", 8)).grid(row=1, column=0)
        ttk.Entry(panel, textvariable=source[f"{name}_vt"], width=6, style="Apple.TEntry").grid(row=1, column=1, padx=(3, 6), pady=(4, 0))
        tk.Label(panel, text="Ids", bg="#FAFAFC", fg=SECONDARY, font=("Calibri", 8)).grid(row=1, column=2)
        ttk.Entry(panel, textvariable=source[f"{name}_ids"], width=6, style="Apple.TEntry").grid(row=1, column=3, padx=(3, 0), pady=(4, 0))
        return panel

    positions = {"pu1": (135, 120), "pu2": (350, 120), "pg1": (2, 230), "pg2": (475, 230),
                 "pd1": (135, 335), "pd2": (350, 335)}
    for name, (x, y) in positions.items():
        accent = RED if name.startswith("pu") else GREEN if name.startswith("pg") else BLUE
        schematic.create_window(x, y, anchor="nw", window=mos_panel(name, accent, wat_values))
    schematic.create_text(325, 526, text="Vt in V · Isat / Ids in µA", fill=SECONDARY, font=("Calibri", 8))

    ttk.Label(right, text="Analysis", style="Section.TLabel").pack(anchor="w")
    ttk.Label(right, text="Compare the entered Lot/Wafer WAT with WAT Target Read SNM curves.", style="Meta.TLabel").pack(anchor="w", pady=(2, 12))

    ttk.Label(right, text="WAT Target", style="Body.TLabel").pack(anchor="w")
    ttk.Label(right, text="Comparison reference only; the model remains calibrated from measured WAT.",
              style="Meta.TLabel", wraplength=350).pack(anchor="w", pady=(2, 5))
    target_grid = ttk.Frame(right, style="Card.TFrame"); target_grid.pack(fill="x", pady=(0, 12))
    ttk.Label(target_grid, text="Type", style="Meta.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Label(target_grid, text="Vt (V)", style="Meta.TLabel").grid(row=0, column=1, sticky="w", padx=(8, 0))
    ttk.Label(target_grid, text="Isat (µA)", style="Meta.TLabel").grid(row=0, column=2, sticky="w", padx=(8, 0))
    for row, (name, color) in enumerate((("PU", RED), ("PG", GREEN), ("PD", BLUE)), 1):
        tk.Label(target_grid, text=name, bg=CARD, fg=color,
                 font=("Calibri", 10, "bold")).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(target_grid, textvariable=target_values[f"{name.lower()}_vt"], width=9,
                  style="Apple.TEntry").grid(row=row, column=1, padx=(8, 0), pady=3)
        ttk.Entry(target_grid, textvariable=target_values[f"{name.lower()}_ids"], width=10,
                  style="Apple.TEntry").grid(row=row, column=2, padx=(8, 0), pady=3)

    ttk.Label(right, text="Model settings", style="Body.TLabel").pack(anchor="w", pady=(2, 0))
    config_grid = ttk.Frame(right, style="Card.TFrame"); config_grid.pack(fill="x")
    labels = [("nominal_vdd", "SRAM VDD", "V"), ("wat_vdd", "WAT VDD", "V")]
    for row, (key, label, unit) in enumerate(labels):
        ttk.Label(config_grid, text=label, style="Body.TLabel").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(config_grid, textvariable=numeric[key], width=10, style="Apple.TEntry").grid(row=row, column=1, sticky="e", padx=(12, 5))
        ttk.Label(config_grid, text=unit, style="Meta.TLabel").grid(row=row, column=2, sticky="w")
    config_grid.columnconfigure(0, weight=1)

    assumption_header = ttk.Frame(right, style="Card.TFrame")
    assumption_header.pack(fill="x", pady=(15, 0))
    ttk.Label(assumption_header, text="Generic 28 nm Default Assumptions",
              style="Body.TLabel").pack(side="left")

    def restore_assumption_defaults() -> None:
        for key, _label, _unit in assumption_specs:
            assumption_values[key].set(str(getattr(config_defaults, key)))

    ttk.Button(assumption_header, text="Restore Defaults", style="Quiet.TButton",
               command=restore_assumption_defaults).pack(side="right")
    ttk.Label(right,
              text="Replace a value when verified process information is available. A blank field uses the displayed generic default.",
              style="Meta.TLabel", wraplength=350).pack(anchor="w", pady=(3, 5))
    assumption_grid = ttk.Frame(right, style="Card.TFrame")
    assumption_grid.pack(fill="x")
    for row, (key, label, unit) in enumerate(assumption_specs):
        ttk.Label(assumption_grid, text=label, style="Body.TLabel").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(assumption_grid, textvariable=assumption_values[key], width=10,
                  style="Apple.TEntry").grid(row=row, column=1, sticky="e", padx=(12, 5), pady=2)
        ttk.Label(assumption_grid, text=unit, style="Meta.TLabel").grid(row=row, column=2, sticky="w")
    assumption_grid.columnconfigure(0, weight=1)
    ttk.Label(right,
              text="Output: Read SNM butterfly plus write-condition VTC and WSNM proxy comparisons against WAT Target.",
              style="Meta.TLabel", wraplength=350).pack(anchor="w", pady=(7, 0))

    ttk.Separator(right).pack(fill="x", pady=16)
    ttk.Label(right, text="Report destination", style="Body.TLabel").pack(anchor="w")
    out_row = ttk.Frame(right, style="Card.TFrame"); out_row.pack(fill="x", pady=(6, 14))
    ttk.Entry(out_row, textvariable=values["out"], style="Apple.TEntry").pack(side="left", fill="x", expand=True)
    def pick_out() -> None:
        selected = filedialog.askdirectory()
        if selected: values["out"].set(selected)
    ttk.Button(out_row, text="Choose…", style="Quiet.TButton", command=pick_out).pack(side="left", padx=(7, 0))
    ttk.Label(right,
              text="Each run is archived as YYYY-MM-DD / WaferID / HHMMSS_analysis.",
              style="Meta.TLabel", wraplength=350).pack(anchor="w", pady=(0, 10))

    status = tk.StringVar(value="Ready to analyze")
    status_label = tk.Label(right, textvariable=status, bg=CARD, fg=SECONDARY,
                            font=("Calibri", 9), anchor="w", justify="left", wraplength=330)
    status_label.pack(fill="x", pady=(0, 7))
    progress = ttk.Progressbar(right, mode="indeterminate", style="Apple.Horizontal.TProgressbar")
    progress.pack(fill="x", pady=(0, 12))
    result_queue: queue.Queue = queue.Queue()

    def collect_inputs() -> tuple[SixTWatCell, Config, DatasheetTargets]:
        resolved_assumptions: dict[str, float] = {}
        for key, _label, _unit in assumption_specs:
            raw = assumption_values[key].get().strip()
            resolved_assumptions[key] = getattr(config_defaults, key) if not raw else float(raw)
        cfg = Config(
            nominal_vdd=float(numeric["nominal_vdd"].get()),
            wat_vdd=float(numeric["wat_vdd"].get()),
            **resolved_assumptions,
        )
        validate_config(cfg)
        corner = values["corner"].get().strip() or "Manual"
        mos6 = {name: MosWat(_positive(wat_values[f"{name}_vt"].get(), f"{name}_vt"),
                             _positive(wat_values[f"{name}_ids"].get(), f"{name}_ids"))
                for name in ("pu1", "pu2", "pg1", "pg2", "pd1", "pd2")}
        cell = SixTWatCell(corner, **mos6)
        targets = DatasheetTargets(**{
            name: MosWat(_positive(target_values[f"{name}_vt"].get(), f"{name} target Vt"),
                         _positive(target_values[f"{name}_ids"].get(), f"{name} target Isat"))
            for name in ("pu", "pg", "pd")
        })
        return cell, cfg, targets

    def worker(cell: SixTWatCell, cfg: Config,
               targets: DatasheetTargets, out_path: str) -> None:
        try:
            result = analyze_six_mos(cell, cfg, targets)
            run_dir = create_run_output_dir(out_path, cell.corner, "6t_analysis")
            report = write_outputs(result, run_dir)
            result_queue.put((True, cell, report))
        except Exception as exc:
            result_queue.put((False, None, exc))

    def excel_worker(excel_path: str, cfg: Config, targets: DatasheetTargets, out_path: str) -> None:
        try:
            report = analyze_excel_wat_sweep(
                excel_path, out_path, cfg, targets, archive_run=True)
            result_queue.put((True, None, report))
        except Exception as exc:
            result_queue.put((False, None, exc))

    def poll_result() -> None:
        try: ok, cell, payload = result_queue.get_nowait()
        except queue.Empty:
            root.after(80, poll_result); return
        progress.stop(); analyze_button.state(["!disabled"]); excel_analyze_button.state(["!disabled"])
        if ok:
            label = "Excel model-VDD sweep" if cell is None else cell.corner
            status.set(f"Complete - {label}; saved to {Path(payload).parent}")
            status_label.configure(fg=GREEN)
            webbrowser.open(Path(payload).resolve().as_uri())
        else:
            status.set("Analysis could not be completed")
            status_label.configure(fg=RED)
            messagebox.showerror("Analysis error", str(payload))

    def execute() -> None:
        try: cell, cfg, targets = collect_inputs()
        except Exception as exc:
            status.set("Check the highlighted input values")
            status_label.configure(fg=RED)
            messagebox.showerror("Invalid input", str(exc)); return
        status.set("Analyzing 6T Independent · Read/Write SNM target curves…")
        status_label.configure(fg=BLUE)
        analyze_button.state(["disabled"]); progress.start(10)
        threading.Thread(target=worker, args=(cell, cfg, targets, values["out"].get()), daemon=True).start()
        root.after(80, poll_result)

    def execute_excel_sweep() -> None:
        excel_path = values["excel_input"].get().strip()
        if not excel_path:
            messagebox.showerror("Excel import", "Choose a 6T WAT Excel workbook first.")
            return
        try:
            _cell, cfg, targets = collect_inputs()
        except Exception as exc:
            status.set("Check the input values")
            status_label.configure(fg=RED)
            messagebox.showerror("Invalid input", str(exc)); return
        status.set("Analyzing Excel model-VDD sweep…")
        status_label.configure(fg=BLUE)
        analyze_button.state(["disabled"]); excel_analyze_button.state(["disabled"]); progress.start(10)
        threading.Thread(target=excel_worker, args=(excel_path, cfg, targets, values["out"].get()), daemon=True).start()
        root.after(80, poll_result)

    ttk.Label(right_footer, text="Read / Write SNM · Lot/Wafer vs WAT Target", style="Meta.TLabel").pack(pady=(0, 7))
    analyze_button = ttk.Button(right_footer, text="Analyze & Open HTML", style="Accent.TButton", command=execute)
    analyze_button.pack(fill="x")
    excel_analyze_button = ttk.Button(right_footer, text="Analyze Excel VDD Sweep", style="Quiet.TButton", command=execute_excel_sweep)
    excel_analyze_button.pack(fill="x", pady=(7, 0))

    # Dedicated manual VDD / grouped PU-PG-PD curve-analysis tab.
    curve_tab.columnconfigure(0, weight=6)
    curve_tab.columnconfigure(1, weight=7)
    curve_tab.rowconfigure(0, weight=1)
    curve_input_card = ttk.Frame(curve_tab, style="Card.TFrame", padding=18)
    curve_input_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
    curve_chart_card = ttk.Frame(curve_tab, style="Card.TFrame", padding=18)
    curve_chart_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
    curve_chart_card.columnconfigure(0, weight=1)
    curve_chart_card.rowconfigure(3, weight=1)

    ttk.Label(curve_input_card, text="Manual VDD Sweep Inputs", style="Section.TLabel").pack(anchor="w")
    ttk.Label(curve_input_card,
              text="Enter grouped PU / PG / PD Vt and Isat measured at each VDD. Blank rows are ignored.",
              style="Meta.TLabel", wraplength=560).pack(anchor="w", pady=(2, 10))

    curve_columns = (
        ("vcc", "VDD", TEXT),
        ("pu_vt", "PU Vt", RED), ("pu_ids", "PU Isat", RED),
        ("pg_vt", "PG Vt", GREEN), ("pg_ids", "PG Isat", GREEN),
        ("pd_vt", "PD Vt", BLUE), ("pd_ids", "PD Isat", BLUE),
    )
    curve_row_vars: list[dict[str, tk.StringVar]] = []
    curve_table = ttk.Frame(curve_input_card, style="Card.TFrame")
    curve_table.pack(fill="x")
    curve_table.columnconfigure(0, minsize=30)
    for column in range(1, len(curve_columns) + 1):
        curve_table.columnconfigure(column, minsize=72, weight=1, uniform="curve_data")
    tk.Label(curve_table, text="#", bg=CARD, fg=SECONDARY,
             font=("Calibri", 9, "bold")).grid(
                 row=0, column=0, padx=(0, 3), pady=(0, 3), sticky="ew")
    for column, (_key, label, color) in enumerate(curve_columns, 1):
        unit = "(V)" if _key == "vcc" or _key.endswith("_vt") else "(uA)"
        tk.Label(curve_table, text=f"{label}\n{unit}", bg=CARD, fg=color,
                 font=("Calibri", 8, "bold")).grid(
                     row=0, column=column, padx=3, pady=(0, 3), sticky="ew")
    curve_row_widgets: list[tk.Widget] = []

    def default_curve_rows() -> list[dict[str, str]]:
        base_vt = {"pu": .385, "pg": .365, "pd": .355}
        base_ids = {"pu": 44.0, "pg": 82.0, "pd": 124.0}
        calibration_vcc = .90

        def scaled_ids(kind: str, vcc_v: float) -> float:
            denominator = calibration_vcc - base_vt[kind]
            overdrive = max(vcc_v - base_vt[kind], 0.0)
            return base_ids[kind] * (overdrive / denominator) ** 2

        result = []
        for vcc_v in (.30, .34, .35, .36, .37, .38, .40, .45, .50, .60, .80, .90, 1.00, 1.20):
            row = {"vcc": f"{vcc_v:.2f}"}
            for kind in ("pu", "pg", "pd"):
                row[f"{kind}_vt"] = f"{base_vt[kind]:.3f}"
                row[f"{kind}_ids"] = f"{scaled_ids(kind, vcc_v):.4g}"
            result.append(row)
        return result

    def rebuild_curve_rows() -> None:
        for child in curve_row_widgets:
            child.destroy()
        curve_row_widgets.clear()
        for row_index, variables in enumerate(curve_row_vars):
            number_label = tk.Label(curve_table, text=str(row_index + 1), bg=CARD, fg=SECONDARY,
                                    font=("Calibri", 8))
            number_label.grid(row=row_index + 1, column=0, padx=(0, 3), pady=2, sticky="ew")
            curve_row_widgets.append(number_label)
            for column, (key, _label, _color) in enumerate(curve_columns, 1):
                entry = ttk.Entry(curve_table, textvariable=variables[key], width=7,
                                  style="Apple.TEntry")
                entry.grid(row=row_index + 1, column=column, padx=3, pady=2, sticky="ew")
                curve_row_widgets.append(entry)

    def append_curve_row(data: dict[str, object] | None = None) -> None:
        if len(curve_row_vars) >= 20:
            messagebox.showinfo("VDD sweep", "A maximum of 20 manual rows is supported.")
            return
        data = data or {}
        curve_row_vars.append({key: tk.StringVar(value=str(data.get(key, "")))
                               for key, _label, _color in curve_columns})
        rebuild_curve_rows()

    def remove_curve_row() -> None:
        if len(curve_row_vars) <= 2:
            messagebox.showinfo("VDD sweep", "Keep at least two input rows.")
            return
        curve_row_vars.pop()
        rebuild_curve_rows()

    def restore_curve_example() -> None:
        curve_row_vars.clear()
        for data in default_curve_rows():
            append_curve_row(data)
        curve_status.set("Example restored; edit the values and analyze")
        curve_status_label.configure(fg=SECONDARY)

    saved_curve_rows = saved_state.get("rsnm_vcc_rows", [])
    initial_curve_rows = (saved_curve_rows if isinstance(saved_curve_rows, list) and
                          len(saved_curve_rows) >= 2 else default_curve_rows())
    for saved_row in initial_curve_rows[:20]:
        append_curve_row(saved_row if isinstance(saved_row, dict) else {})

    curve_controls = ttk.Frame(curve_input_card, style="Card.TFrame")
    curve_controls.pack(fill="x", pady=(10, 7))
    ttk.Button(curve_controls, text="+ Add Row", style="Quiet.TButton",
               command=append_curve_row).pack(side="left")
    ttk.Button(curve_controls, text="Remove Last", style="Quiet.TButton",
               command=remove_curve_row).pack(side="left", padx=(7, 0))
    ttk.Button(curve_controls, text="Restore Example", style="Quiet.TButton",
               command=restore_curve_example).pack(side="right")
    ttk.Label(curve_input_card,
              text="Isat may be 0 uA below threshold. For accurate eye-closure VDD, include rows on both sides of the expected boundary.",
              style="Meta.TLabel", wraplength=560).pack(anchor="w", pady=(0, 9))

    curve_status = tk.StringVar(value="Ready to analyze the VDD sweep")
    curve_status_label = tk.Label(curve_input_card, textvariable=curve_status, bg=CARD, fg=SECONDARY,
                                  font=("Calibri", 9), anchor="w", justify="left", wraplength=560)
    curve_status_label.pack(fill="x", pady=(3, 6))
    curve_progress = ttk.Progressbar(curve_input_card, mode="indeterminate",
                                     style="Apple.Horizontal.TProgressbar")
    curve_progress.pack(fill="x", pady=(0, 9))

    ttk.Label(curve_chart_card, text="Estimated Read SNM Curve", style="ChartTitle.TLabel").grid(
        row=0, column=0, sticky="w")
    ttk.Label(curve_chart_card,
              text="X: Model VDD (V)  /  Y: RSNM (mV). Vertical guides map each point to VDD; X marks indicate no valid butterfly eye.",
              style="Meta.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 6))
    curve_summary = tk.StringVar(value="Analyze at least two VDD rows to display the curve.")
    curve_summary_label = tk.Label(curve_chart_card, textvariable=curve_summary, bg=CARD, fg=SECONDARY,
                                   font=("Calibri", 10, "bold"), anchor="w", justify="left")
    curve_summary_label.grid(row=2, column=0, sticky="ew", pady=(0, 6))
    curve_canvas = tk.Canvas(curve_chart_card, bg=CARD, highlightthickness=0, bd=0,
                             width=620, height=550)
    curve_canvas.grid(row=3, column=0, sticky="nsew")
    curve_result: dict | None = None
    curve_report_path: Path | None = None

    def draw_curve_chart(_event=None) -> None:
        curve_canvas.delete("all")
        width = max(curve_canvas.winfo_width(), 520)
        height = max(curve_canvas.winfo_height(), 420)
        left_margin, right_margin, top_margin, bottom_margin = 72, 24, 42, 68
        plot_width = width - left_margin - right_margin
        plot_height = height - top_margin - bottom_margin
        if not curve_result:
            curve_canvas.create_text(width / 2, height / 2, text="RSNM curve will appear here",
                                     fill=SECONDARY, font=("Calibri", 13))
            return
        rows = curve_result["rows"]
        valid_rows = [row for row in rows if row["rsnm_mv"] is not None]
        max_rsnm = max((row["rsnm_mv"] for row in valid_rows), default=50.0)
        y_max = max(50.0, math.ceil(max_rsnm / 50.0) * 50.0)

        def xy(vcc_v: float, rsnm_mv: float) -> tuple[float, float]:
            return (left_margin + vcc_v / SNM_PLOT_AXIS_MAX_V * plot_width,
                    top_margin + (1.0 - rsnm_mv / y_max) * plot_height)

        for step in range(7):
            value = step * .2
            x, _ = xy(value, 0)
            curve_canvas.create_line(x, top_margin, x, top_margin + plot_height, fill="#E5E5EA")
            curve_canvas.create_text(x, top_margin + plot_height + 20, text=f"{value:.1f}",
                                     fill=SECONDARY, font=("Calibri", 9))
        for step in range(6):
            value = y_max * step / 5
            _, y = xy(0, value)
            curve_canvas.create_line(left_margin, y, left_margin + plot_width, y, fill="#E5E5EA")
            curve_canvas.create_text(left_margin - 10, y, text=f"{value:.0f}", anchor="e",
                                     fill=SECONDARY, font=("Calibri", 9))
        curve_canvas.create_line(left_margin, top_margin, left_margin,
                                 top_margin + plot_height, fill=TEXT, width=2)
        curve_canvas.create_line(left_margin, top_margin + plot_height,
                                 left_margin + plot_width, top_margin + plot_height, fill=TEXT, width=2)

        display_points: list[tuple[float, float]] = []
        closure = curve_result.get("eye_closure")
        if closure:
            display_points.append(xy(closure["estimated_vcc_v"], 0.0))
        display_points.extend(xy(row["vcc_v"], row["rsnm_mv"]) for row in valid_rows)
        baseline_y = top_margin + plot_height
        for row in valid_rows:
            guide_x, guide_y = xy(row["vcc_v"], row["rsnm_mv"])
            curve_canvas.create_line(guide_x, guide_y + 5, guide_x, baseline_y,
                                     fill="#B9D7FF", width=1, dash=(3, 4))
        if len(display_points) >= 2:
            curve_canvas.create_line(*[coordinate for point in display_points for coordinate in point],
                                     fill=BLUE, width=3, smooth=False)

        placed_label_boxes: list[tuple[int, int, int, int]] = []

        def boxes_overlap(first: tuple[int, int, int, int],
                          second: tuple[int, int, int, int], padding: int = 5) -> bool:
            return not (first[2] + padding < second[0] or
                        first[0] - padding > second[2] or
                        first[3] + padding < second[1] or
                        first[1] - padding > second[3])

        label_candidates = (
            (-11, -9, "se"), (11, -9, "sw"),
            (-11, 9, "ne"), (11, 9, "nw"),
            (0, -15, "s"), (0, 15, "n"),
            (-16, 0, "e"), (16, 0, "w"),
        )
        for index, row in enumerate(valid_rows):
            x, y = xy(row["vcc_v"], row["rsnm_mv"])
            curve_canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=CARD, outline=BLUE, width=2)
            ordered_candidates = label_candidates[index % 4:] + label_candidates[:index % 4]
            chosen_item = None
            for label_dx, label_dy, label_anchor in ordered_candidates:
                label_item = curve_canvas.create_text(
                    x + label_dx, y + label_dy,
                    text=f'{row["rsnm_mv"]:.1f} mV', fill=TEXT,
                    anchor=label_anchor, font=("Calibri", 10, "bold"))
                bbox = curve_canvas.bbox(label_item)
                within_plot = bool(bbox and bbox[0] >= left_margin + 3 and
                                   bbox[2] <= left_margin + plot_width - 3 and
                                   bbox[1] >= top_margin + 3 and
                                   bbox[3] <= baseline_y - 3)
                collision = bool(bbox and any(boxes_overlap(bbox, used)
                                              for used in placed_label_boxes))
                if bbox and within_plot and not collision:
                    placed_label_boxes.append(bbox)
                    chosen_item = label_item
                    break
                curve_canvas.delete(label_item)
            if chosen_item is None:
                fallback_y = max(top_margin + 12, min(y - 14, baseline_y - 12))
                chosen_item = curve_canvas.create_text(
                    x, fallback_y, text=f'{row["rsnm_mv"]:.1f} mV', fill=TEXT,
                    anchor="s", font=("Calibri", 10, "bold"))
                fallback_bbox = curve_canvas.bbox(chosen_item)
                if fallback_bbox:
                    placed_label_boxes.append(fallback_bbox)
        for row in rows:
            if row["valid_eye"]:
                continue
            x, y = xy(row["vcc_v"], 0.0)
            curve_canvas.create_line(x - 4, y - 4, x + 4, y + 4, fill=SECONDARY, width=2)
            curve_canvas.create_line(x + 4, y - 4, x - 4, y + 4, fill=SECONDARY, width=2)
        if closure:
            x, y = xy(closure["estimated_vcc_v"], 0.0)
            curve_canvas.create_line(x, top_margin, x, top_margin + plot_height,
                                     fill="#FF9500", width=2, dash=(6, 4))
            curve_canvas.create_text(x + 8, top_margin + 12,
                                     text=f'Eye-closure VDD {closure["estimated_vcc_v"]:.4f} V',
                                     anchor="w", fill="#C56A00", font=("Calibri", 12, "bold"))
        curve_canvas.create_text(left_margin + plot_width / 2, height - 25, text="Model VDD (V)",
                                 fill=TEXT, font=("Calibri", 10, "bold"))
        curve_canvas.create_text(18, top_margin + plot_height / 2, text="Read SNM (mV)", angle=90,
                                 fill=TEXT, font=("Calibri", 10, "bold"))

    curve_canvas.bind("<Configure>", draw_curve_chart)
    draw_curve_chart()

    curve_result_queue: queue.Queue = queue.Queue()

    def collect_curve_inputs() -> tuple[list[RsnmVccPoint], Config]:
        points: list[RsnmVccPoint] = []
        for row_number, variables in enumerate(curve_row_vars, 1):
            raw = {key: variable.get().strip() for key, variable in variables.items()}
            if not any(raw.values()):
                continue
            missing = [label for key, label, _color in curve_columns if not raw[key]]
            if missing:
                raise ValueError(f'Row {row_number}: missing {", ".join(missing)}')
            points.append(RsnmVccPoint(
                float(raw["vcc"]),
                MosWat(float(raw["pu_vt"]), float(raw["pu_ids"])),
                MosWat(float(raw["pg_vt"]), float(raw["pg_ids"])),
                MosWat(float(raw["pd_vt"]), float(raw["pd_ids"])),
            ))
        resolved_assumptions = {}
        for key, _label, _unit in assumption_specs:
            raw = assumption_values[key].get().strip()
            resolved_assumptions[key] = getattr(config_defaults, key) if not raw else float(raw)
        cfg = Config(nominal_vdd=float(numeric["nominal_vdd"].get()),
                     wat_vdd=float(numeric["wat_vdd"].get()), **resolved_assumptions)
        validate_config(cfg)
        return points, cfg

    def curve_worker(points: list[RsnmVccPoint], cfg: Config,
                     out_path: Path, wafer_id: str) -> None:
        try:
            analysis = analyze_rsnm_vcc_curve(points, cfg)
            run_dir = create_run_output_dir(out_path, wafer_id, "rsnm_vdd_curve")
            report = write_rsnm_vcc_curve_outputs(analysis, run_dir)
            curve_result_queue.put((True, analysis, report))
        except Exception as exc:
            curve_result_queue.put((False, None, exc))

    def poll_curve_result() -> None:
        nonlocal curve_result, curve_report_path
        try:
            ok, analysis, payload = curve_result_queue.get_nowait()
        except queue.Empty:
            root.after(80, poll_curve_result)
            return
        curve_progress.stop()
        curve_analyze_button.state(["!disabled"])
        if ok:
            curve_result = analysis
            curve_report_path = Path(payload)
            closure = analysis.get("eye_closure")
            if closure:
                summary = f'Estimated eye-closure VDD: {closure["estimated_vcc_v"]:.4f} V'
                curve_summary_label.configure(fg="#C56A00")
            else:
                summary = "Eye-closure VDD not bracketed by the entered rows"
                curve_summary_label.configure(fg=SECONDARY)
            curve_summary.set(summary)
            curve_status.set(
                f"Complete - {len(analysis['rows'])} VDD point(s); saved to {Path(payload).parent}")
            curve_status_label.configure(fg=GREEN)
            curve_open_button.state(["!disabled"])
            draw_curve_chart()
        else:
            curve_status.set("RSNM curve analysis could not be completed")
            curve_status_label.configure(fg=RED)
            messagebox.showerror("RSNM vs VDD analysis", str(payload))

    def execute_curve_analysis() -> None:
        try:
            points, cfg = collect_curve_inputs()
        except Exception as exc:
            curve_status.set("Check the VDD sweep input values")
            curve_status_label.configure(fg=RED)
            messagebox.showerror("Invalid VDD sweep input", str(exc))
            return
        curve_status.set("Calculating Read SNM at each VDD point...")
        curve_status_label.configure(fg=BLUE)
        curve_analyze_button.state(["disabled"])
        curve_open_button.state(["disabled"])
        curve_progress.start(10)
        wafer_id = values["corner"].get().strip() or "Manual"
        output_path = Path(values["out"].get())
        threading.Thread(target=curve_worker,
                         args=(points, cfg, output_path, wafer_id), daemon=True).start()
        root.after(80, poll_curve_result)

    def open_curve_report() -> None:
        if curve_report_path and curve_report_path.exists():
            webbrowser.open(curve_report_path.resolve().as_uri())

    curve_action_row = ttk.Frame(curve_input_card, style="Card.TFrame")
    curve_action_row.pack(side="bottom", fill="x", pady=(8, 0))
    curve_analyze_button = ttk.Button(curve_action_row, text="Analyze RSNM vs VDD",
                                      style="Accent.TButton", command=execute_curve_analysis)
    curve_analyze_button.pack(fill="x")
    curve_open_button = ttk.Button(curve_action_row, text="Open HTML Result",
                                   style="Quiet.TButton", command=open_curve_report)
    curve_open_button.pack(fill="x", pady=(7, 0))
    curve_open_button.state(["disabled"])

    def persist_and_close() -> None:
        state = {
            "values": {key: variable.get() for key, variable in values.items()},
            "wat": {key: variable.get() for key, variable in wat_values.items()},
            "targets": {key: variable.get() for key, variable in target_values.items()},
            "numeric": {key: variable.get() for key, variable in numeric.items()},
            "assumptions": {key: variable.get() for key, variable in assumption_values.items()},
            "rsnm_vcc_rows": [
                {key: variable.get() for key, variable in row.items()}
                for row in curve_row_vars
            ],
        }
        try:
            save_gui_state(state)
        except OSError:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", persist_and_close)
    root.mainloop()


def parse_args(argv: list[str]) -> argparse.Namespace:
    p=argparse.ArgumentParser(description="6T SRAM / WAT cell-level SNM analyzer")
    p.add_argument("--input",help="WAT CSV or six-MOS WAT Excel (.xlsx); omit to open GUI")
    p.add_argument("--output",default="output",help="output directory")
    p.add_argument("--corner",help="analyze only this corner")
    p.add_argument("--vdd",type=float,default=.80,help="nominal SRAM VDD")
    p.add_argument("--wat-vdd",type=float,default=1.20,help="WAT Ids test voltage")
    p.add_argument("--pu-target-vt",type=float,default=.380)
    p.add_argument("--pu-target-ids",type=float,default=45.0)
    p.add_argument("--pg-target-vt",type=float,default=.370)
    p.add_argument("--pg-target-ids",type=float,default=80.0)
    p.add_argument("--pd-target-vt",type=float,default=.360)
    p.add_argument("--pd-target-ids",type=float,default=120.0)
    return p.parse_args(argv)


def main(argv: list[str] | None=None) -> int:
    args=parse_args(sys.argv[1:] if argv is None else argv)
    if not args.input:
        launch_gui(); return 0
    cfg=Config(wat_vdd=args.wat_vdd, nominal_vdd=args.vdd)
    targets = DatasheetTargets(MosWat(args.pu_target_vt, args.pu_target_ids),
                               MosWat(args.pg_target_vt, args.pg_target_ids),
                               MosWat(args.pd_target_vt, args.pd_target_ids))
    try:
        reports=run_analysis(args.input,args.output,cfg,args.corner,targets)
        for report in reports: print(report.resolve())
        return 0
    except Exception as exc:
        print(f"error: {exc}",file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
