import re
import pandas as pd
import numpy as np
from dateutil import parser as dateparser
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pandas")

# ------------------- HELPERS: parsing + debug -------------------
def _try_parse_numeric_series(s: pd.Series, debug=False):
    snum = pd.to_numeric(s, errors="coerce")
    maxv = snum.max() if not snum.empty else np.nan
    methods = []
    result = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")

    # heuristik epoch ms / s
    if pd.notna(maxv):
        if maxv > 1e12:  # likely milliseconds
            try:
                result = pd.to_datetime(snum, unit='ms', errors='coerce')
                methods.append("epoch_ms")
            except Exception:
                pass
        elif maxv > 1e9:  # likely seconds
            try:
                result = pd.to_datetime(snum, unit='s', errors='coerce')
                methods.append("epoch_s")
            except Exception:
                pass
        # Excel serial days (roughly between 29500..50000)
        if (snum.between(29500, 50000).sum() > 0) and "epoch_ms" not in methods and "epoch_s" not in methods:
            try:
                base = pd.to_datetime("1899-12-30")
                # gunakan float, agar jam desimal ikut terkonversi
                result_excel = base + pd.to_timedelta(snum.fillna(0), unit='D')
                # hanya ambil tanggal yang masuk akal
                result = result_excel.where(result_excel.dt.year.between(1900, 9999))
                methods.append("excel_serial_float")
            except Exception:
                pass
            
    # fallback: try pandas default interpretation (may return NaT)
    if result.isna().sum() > 0:
        try:
            parsed = pd.to_datetime(s, errors='coerce')
            if parsed.notna().sum() > result.notna().sum():
                result = parsed
                methods.append("pd_to_datetime_infer")
        except Exception:
            pass

    if debug:
        print(f"    [numeric] max={maxv}, methods tried: {methods}, parsed={result.notna().sum()}/{len(s)}")

    return result, methods

COMMON_FORMATS = [
    "%m/%d/%y %H:%M", "%m/%d/%Y %H:%M", "%d-%b-%Y %H:%M", "%d-%b-%y %H:%M",
    "%d/%m/%Y %H:%M", "%d/%m/%y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
    "%d-%m-%y", "%Y/%m/%d", "%d.%m.%Y", "%d.%m.%y"
]

def _try_parse_string_series(s: pd.Series, debug=False):
    result = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    methods_used = []

    # Parse string yang mirip Excel serial date (float)
    excel_serial_mask = s.str.match(r"^\d{5}(\.\d+)?$")
    if excel_serial_mask.any():
        try:
            base = pd.to_datetime("1899-12-30")
            serial_float = pd.to_numeric(s[excel_serial_mask], errors="coerce")
            parsed_excel = base + pd.to_timedelta(serial_float, unit="D")
            # hanya ambil tanggal yang masuk akal
            valid_mask = parsed_excel.dt.year.between(1900, 9999)
            result.loc[s[excel_serial_mask].index[valid_mask]] = parsed_excel[valid_mask]
            methods_used.append(("excel_serial_float_str", valid_mask.sum()))
        except Exception:
            pass

    # 1) fast try: pandas infer_datetime_format dayfirst=False
    try:
        parsed = pd.to_datetime(s, errors='coerce', dayfirst=False)
        parsed_count = parsed.notna().sum()
        result.update(parsed)
        methods_used.append(("pandas_infer_dayfirst=False", parsed_count))
    except Exception:
        pass

    # 2) try dayfirst=True if it helps
    try:
        parsed_df = pd.to_datetime(s, errors='coerce', dayfirst=True)
        if parsed_df.notna().sum() > result.notna().sum():
            result.update(parsed_df)
            methods_used.append(("pandas_infer_dayfirst=True", parsed_df.notna().sum()))
    except Exception:
        pass

    # 3) try a list of explicit formats, fill progressively
    remaining_mask = result.isna() & s.notna()
    for fmt in COMMON_FORMATS:
        if not remaining_mask.any():
            break
        try:
            parsed_fmt = pd.to_datetime(s[remaining_mask], format=fmt, errors='coerce')
            ok_mask = parsed_fmt.notna()
            if ok_mask.any():
                result.loc[remaining_mask.index[remaining_mask]] = parsed_fmt
                remaining_mask = result.isna() & s.notna()
                methods_used.append((f"format:{fmt}", ok_mask.sum()))
        except Exception:
            pass

    # 4) fallback: dateutil parser (slower) for remaining
    remaining_mask = result.isna() & s.notna()
    if remaining_mask.any():
        def _parse_val(x):
            try:
                # allow fuzzy parsing for weird strings
                return dateparser.parse(str(x), fuzzy=True, dayfirst=False)
            except Exception:
                try:
                    return dateparser.parse(str(x), fuzzy=True, dayfirst=True)
                except Exception:
                    return pd.NaT
        parsed_fallback = s[remaining_mask].apply(_parse_val)
        # convert to Timestamp where possible
        parsed_fallback = pd.to_datetime(parsed_fallback, errors='coerce')
        if parsed_fallback.notna().sum() > 0:
            result.loc[parsed_fallback.index] = parsed_fallback
            methods_used.append(("dateutil_fallback", parsed_fallback.notna().sum()))

    if debug:
        print(f"    [string] methods summary: {methods_used}, parsed={result.notna().sum()}/{len(s)}")

    return result, methods_used

def try_parse_column(series: pd.Series, col_name: str, debug=False):
    """
    Try multiple strategies to parse a single pandas Series that should contain dates.
    Returns parsed Series (datetime64[ns]) and a dict with debug info.
    """
    s = series.copy()
    debug_info = {"column": col_name, "initial_non_null": int(s.notna().sum()), "steps": []}

    # Quick sample preview
    if debug:
        sample_vals = s.dropna().astype(str).head(8).tolist()
        print(f"\n>> Parsing column '{col_name}': dtype={s.dtype}, non-null={debug_info['initial_non_null']}")
        if sample_vals:
            print("    sample values:", sample_vals)

    parsed = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")

    # Numeric-based heuristics
    if pd.api.types.is_numeric_dtype(s) or s.dropna().apply(lambda x: re.fullmatch(r"\d+(\.\d+)?", str(x)) is not None).all():
        parsed_numeric, methods = _try_parse_numeric_series(s, debug=debug)
        parsed.update(parsed_numeric)
        debug_info["steps"].append({"strategy": "numeric", "methods": methods, "parsed": int(parsed_numeric.notna().sum())})

    # Generic text-based parsing
    # convert everything to string for consistent parsing attempts if not numeric-only
    s_str = s.astype(str).where(s.notna(), None)
    parsed_str, methods_str = _try_parse_string_series(s_str, debug=debug)
    # keep parsed values (prefer already parsed numeric values)
    mask_to_update = parsed.isna() & parsed_str.notna()
    if mask_to_update.any():
        parsed.loc[mask_to_update] = parsed_str.loc[mask_to_update]
    debug_info["steps"].append({"strategy": "string", "methods": methods_str, "parsed": int(parsed_str.notna().sum())})

    # Final stats
    parsed_count = int(parsed.notna().sum())
    debug_info["parsed_non_null"] = parsed_count
    debug_info["parsed_pct"] = round(parsed_count / (debug_info["initial_non_null"] or 1) * 100, 2)

    if debug:
        print(f"    => parsed {parsed_count}/{debug_info['initial_non_null']} ({debug_info['parsed_pct']}%)")
        # show up to 10 failed examples
        failed_examples = series[parsed.isna() & series.notna()].astype(str).unique()[:10].tolist()
        if failed_examples:
            print("    failed examples (up to 10):", failed_examples)
        # show up to 5 successful examples
        success_examples = parsed[parsed.notna()].head(5).astype(str).tolist()
        if success_examples:
            print("    success examples (head):", success_examples)

    return parsed, debug_info

def normalize_all_dates(df: pd.DataFrame, debug=False) -> pd.DataFrame:
    """
    Normalisasi semua kolom yang berisi 'tgl', 'date', atau 'eta',
    kecuali kolom yang ada di exclude_cols.
    Jika debug=True: print laporan parsing untuk tiap kolom.
    """
    df = df.copy()
    
    # cari kolom yang "mirip tanggal"
    date_like_cols = [col for col in df.columns if any(k in col.lower() for k in ["tgl", "date", "eta"])]
    
    # daftar kolom yang tidak boleh di-parse
    exclude_cols = ["status_pod_update"]
    date_like_cols = [col for col in date_like_cols if col.lower() not in exclude_cols]
    
    parse_reports = []

    for col in date_like_cols:
        try:
            parsed_series, report = try_parse_column(df[col], col, debug=debug)
            df[col] = parsed_series  # replace dengan parsed datetimes
            parse_reports.append(report)
        except Exception as e:
            print(f"[!] Error saat parse kolom {col}: {e}")

    # summary print
    if debug and parse_reports:
        print("\n--- PARSE SUMMARY (per column) ---")
        for r in parse_reports:
            print(f"  {r['column']}: non-null={r['initial_non_null']}, parsed={r['parsed_non_null']} ({r['parsed_pct']}%)")
        print("--- END SUMMARY ---\n")

    return df