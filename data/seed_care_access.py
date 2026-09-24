# data/seed_care_access.py
"""
Seeds the care-access module (facilities, patients, a few referrals/queue
tickets/follow-ups) so the new screens have realistic data to show on
startup, the same way golden_batches.py seeds intake. Idempotent — safe
to call on every app startup.

Does NOT seed triage_results or teleconsult_sessions: those require a
live Gemini call / are meant to be demonstrated live on camera, not
pre-baked, so the demo video shows the actual agent reasoning happening.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from shared.database import get_connection, init_db
from shared.schemas import Facility, FacilityLevel, Patient, QueueStatus, Referral, ReferralStatus, RiskCategory, UrgencyBand
from services.facilities.service import ensure_facilities_schema, insert_facility, get_facility
from services.patients.service import ensure_patients_schema, register_patient, get_patient
from services.referrals.service import ensure_referrals_schema
from services.queue.service import ensure_queue_schema
from services.followups.service import ensure_followups_schema, create_follow_up, list_follow_ups

DISTRICT = "Nabarangpur"

_FACILITIES = [
    # (facility_id, name, level, village, staff, beds, occupied, teleconsult)
    ("SC-BADIM", "Sub-Centre Badimela", FacilityLevel.SUB_CENTRE, "Badimela", 2, 0, 0, False),
    ("SC-JHARI", "Sub-Centre Jharigaon", FacilityLevel.SUB_CENTRE, "Jharigaon", 2, 0, 0, False),
    ("PHC-UMER", "PHC Umerkote", FacilityLevel.PHC, "Umerkote", 6, 10, 4, True),
    ("PHC-RAIGH", "PHC Raighar", FacilityLevel.PHC, "Raighar", 5, 8, 3, True),
    ("CHC-NABAR", "CHC Nabarangpur", FacilityLevel.CHC, "Nabarangpur Town", 18, 30, 14, True),
    ("DH-NABAR", "District Hospital Nabarangpur", FacilityLevel.DISTRICT_HOSPITAL, "Nabarangpur Town", 45, 120, 61, True),
]

_PATIENTS = [
    # (name, age, gender, village, home_facility_id, risk_category)
    ("Sunita Majhi", 27, "F", "Badimela", "SC-BADIM", RiskCategory.MATERNAL),
    ("Raju Nayak", 4, "M", "Badimela", "SC-BADIM", RiskCategory.CHILD),
    ("Kamala Gouda", 58, "F", "Jharigaon", "SC-JHARI", RiskCategory.CHRONIC),
    ("Bijay Sabar", 34, "M", "Umerkote", "PHC-UMER", RiskCategory.NONE),
    ("Anita Behera", 30, "F", "Raighar", "PHC-RAIGH", RiskCategory.MATERNAL),
    ("Suresh Muduli", 62, "M", "Nabarangpur Town", "CHC-NABAR", RiskCategory.CHRONIC),
    ("Priya Dei", 2, "F", "Jharigaon", "SC-JHARI", RiskCategory.CHILD),
    ("Manoj Podh", 45, "M", "Umerkote", "PHC-UMER", RiskCategory.NONE),
]


def generate_care_access_demo(conn) -> None:
    print("Generating care-access demo data (facilities, patients, referrals, queue, follow-ups)...")
    ensure_facilities_schema(conn)
    ensure_patients_schema(conn)
    ensure_referrals_schema(conn)
    ensure_queue_schema(conn)
    ensure_followups_schema(conn)

    for facility_id, name, level, village, staff, beds, occupied, tele in _FACILITIES:
        if get_facility(conn, facility_id) is None:
            insert_facility(conn, Facility(
                facility_id=facility_id, name=name, level=level, village_or_area=village,
                district=DISTRICT, staff_count=staff, beds_total=beds, beds_occupied=occupied,
                has_teleconsult=tele,
            ))
            print(f"  Facility {facility_id} ({name})")

    existing = conn.execute("SELECT COUNT(*) AS c FROM patients").fetchone()["c"]
    if existing == 0:
        registered_ids = []
        for name, age, gender, village, facility_id, risk in _PATIENTS:
            patient = register_patient(
                conn, name=name, age=age, gender=gender, village=village,
                home_facility_id=facility_id, registered_by="ASHA-DEMO", risk_category=risk,
            )
            registered_ids.append((patient.patient_id, facility_id, risk))
            print(f"  Patient {patient.patient_id} ({name})")

        # A couple of pending referrals so the referral tracker has something to show
        conn.execute(
            """
            INSERT INTO referrals (referral_id, patient_id, from_facility_id, to_facility_id,
                reason, urgency, status, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "REF-DEMO0001", registered_ids[0][0], "SC-BADIM", "PHC-UMER",
                "Routine antenatal check-up, referred for facility-level ANC screening",
                UrgencyBand.SOON.value, ReferralStatus.CREATED.value, "ASHA-DEMO",
                datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.execute(
            """
            INSERT INTO referrals (referral_id, patient_id, from_facility_id, to_facility_id,
                reason, urgency, status, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "REF-DEMO0002", registered_ids[2][0], "SC-JHARI", "CHC-NABAR",
                "Chronic condition follow-up requiring specialist review",
                UrgencyBand.SOON.value, ReferralStatus.ACCEPTED.value, "ASHA-DEMO",
                datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

        # A few queue tickets at PHC Umerkote so the queue screen isn't empty
        today = date.today().isoformat()
        for idx, (pid, facility_id, _risk) in enumerate([p for p in registered_ids if p[1] == "PHC-UMER"]):
            conn.execute(
                """
                INSERT INTO queue_tickets (ticket_id, facility_id, patient_id, token_number,
                    status, priority, queue_date, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    f"TKT-DEMO000{idx+1}", facility_id, pid, idx + 1, QueueStatus.WAITING.value,
                    today, datetime.now(timezone.utc).isoformat(),
                ),
            )
        conn.commit()

        # Follow-ups for the maternal/child/chronic patients, some overdue
        # so the dashboard's "overdue" count is non-zero out of the box.
        due_offsets = [-3, 5, -1, None, 10]  # negative = overdue
        for (pid, facility_id, risk), offset in zip(registered_ids, due_offsets):
            if risk == RiskCategory.NONE or offset is None:
                continue
            due = date.today() + timedelta(days=offset)
            existing_fu = list_follow_ups(conn, patient_id=pid)
            if not existing_fu:
                create_follow_up(
                    conn, patient_id=pid, risk_category=risk,
                    reason=f"{risk.value.title()} follow-up check",
                    due_date=due, created_by="ASHA-DEMO",
                )
                print(f"  Follow-up for {pid} due {due.isoformat()}")

        print("Referrals, queue tickets and follow-ups seeded.")
    else:
        print(f"{existing} patients already exist, skipping patient/referral/queue/follow-up seed.")

    print("Care-access demo data generated successfully.")


if __name__ == "__main__":
    init_db()
    conn = get_connection()
    generate_care_access_demo(conn)
