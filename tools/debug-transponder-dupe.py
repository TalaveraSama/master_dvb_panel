"""Depura la deteccion de identidad duplicada de transponders."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "tbs-webui")
os.environ["TBS_DATA_DIR"] = os.path.abspath(".localdata-debug")
os.environ["TBS_MOCK"] = "1"

from app.database import get_sessionmaker, init_db  # noqa: E402
from app.models import Transponder  # noqa: E402
from app.services import channels_service  # noqa: E402

init_db()
session = get_sessionmaker()()
session.query(Transponder).delete()
session.commit()

first = Transponder(delivery_system="DVB-S2", frequency_khz=10970000, polarization="V", symbol_rate_ksps=27900)
second = Transponder(delivery_system="DVB-S2", frequency_khz=12000000, polarization="V", symbol_rate_ksps=27500)
session.add_all([first, second])
session.commit()
print("ids:", first.id, second.id)

candidate = {
    "delivery_system": "DVB-S2",
    "frequency_khz": 10970000,
    "polarization": "V",
    "symbol_rate_ksps": 27500,
    "stream_id": None,
    "plp_id": None,
}
found = channels_service._same_identity(session, candidate, exclude_id=second.id)
print("duplicado detectado:", found.id if found else None, "(esperado", first.id, ")")

print("update_transponder lanza ValueError?")
try:
    channels_service.update_transponder(session, second.id, {"frequency_khz": 10970000})
    print("  NO lanzo -> ", session.get(Transponder, second.id).frequency_khz)
except ValueError as exc:
    print("  OK, lanzo ValueError:", exc)
session.query(Transponder).delete()
session.commit()
session.close()
