# data/seed.py
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from faker import Faker
from datetime import datetime

# Local SQLite database configuration
DATABASE_URL = "sqlite:///./medtrust.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define SQLAlchemy Table Models locally
class SupplierModel(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    location = Column(String)
    contact_email = Column(String)

class BatchModel(Base):
    __tablename__ = "batches"
    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String, unique=True, index=True)
    medication_name = Column(String)
    supplier_name = Column(String)
    status = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)

fake = Faker()

def generate_suppliers(db):
    print("Generating synthetic suppliers...")
    for _ in range(5):
        supplier = SupplierModel(
            name=fake.company(),
            location=fake.city(),
            contact_email=fake.company_email()
        )
        db.add(supplier)
    print("Suppliers generated successfully.")

def generate_batches(db):
    print("Generating synthetic medication batches...")
    for _ in range(10):
        batch = BatchModel(
            batch_id=f"BTH-{fake.random_number(digits=5)}",
            medication_name=fake.random_element(elements=["Paracetamol", "Amoxicillin", "Ibuprofen", "Aspirin", "Metformin"]),
            supplier_name=fake.company(),
            status=fake.random_element(elements=["PENDING", "ACCEPTED", "REJECTED", "HOLD"])
        )
        db.add(batch)
    print("Batches generated successfully.")

def insert_golden_batches(db):
    print("Inserting golden reference batches...")
    golden = BatchModel(
        batch_id="GOLDEN-REF-001",
        medication_name="Standard Reference Vaccine",
        supplier_name="Global MedCorp",
        status="ACCEPTED"
    )
    db.add(golden)
    print("Golden batches inserted successfully.")

def run_seed():
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        generate_suppliers(db)
        generate_batches(db)
        insert_golden_batches(db)
        db.commit()
        print("Seed complete successfully!")
    except Exception as e:
        db.rollback()
        print(f"Error during seeding: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    run_seed()