from datetime import datetime, timezone

import pandas as pd

from frecuencias.config import ZONA_HORARIA


def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def a_zona(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize(ZONA_HORARIA) if t.tzinfo is None else t.tz_convert(ZONA_HORARIA)


def a_local(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t if t.tzinfo is None else t.tz_convert(ZONA_HORARIA).tz_localize(None)
