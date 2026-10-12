"""Ver la SQL real de _same_identity y probar cada condicion."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "tbs-webui")
os.environ["TBS_DATA_DIR"] = os.path.abspath(".localdata-debug")
os.environ["TBS_MOCK"] = "1"

from sqlalchemy import select  # noqa: E402

from app.database import get_sessionmaker, init_db  # noqa: E402
from app.models import Transponder  # noqa: E402

init_db()
session = get_sessionmaker()()
session.query(Transponder).delete()
session.commit()
first = Transponder(delivery_system="DVB-S2", frequency_khz=10970000, polarization="V", symbol_rate_ksps=27900)
second = Transponder(delivery_system="DVB-S2", frequency_khz=12000000, polarization="V", symbol_rate_ksps=27500)
session.add_all([first, second])
session.commit()

print("todos:", [(t.id, t.delivery_system, t.frequency_khz, t.polarization, t.symbol_rate_ksps) for t in session.query(Transponder)])

conditions = [
    ("id != exclude", Transponder.id != second.id),
    ("delivery_system", Transponder.delivery_system == "DVB-S2"),
    ("frequency_khz", Transponder.frequency_khz == 10970000),
    ("polarization", Transponder.polarization == "V"),
    ("symbol_rate_ksps", Transponder.symbol_rate_ksps == 27500),
    ("stream_id is null", Transponder.stream_id.is_(None)),
    ("plp_id is null", Transponder.plp_id.is_(None)),
]
for label, condition in conditions:
    stmt = select(Transponder).where(condition)
    rows = session.execute(stmt).scalars().all()
    print(f"{label:22} -> {[r.id for r in rows]}")

stmt = select(Transponder)
for _label, condition in conditions:
    stmt = stmt.where(condition)
print("SQL:", str(stmt))
print("resultado:", [r.id for r in session.execute(stmt).scalars().all()])

session.query(Transponder).delete()
session.commit()
session.close()
