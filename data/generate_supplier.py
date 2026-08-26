# data/generate_suppliers.py
from faker import Faker

fake = Faker()

def generate_suppliers(db):
    print("Generating synthetic suppliers...")
    # Add your supplier generation logic here using db.add()
    # Example:
    # supplier = Supplier(name=fake.company(), location=fake.city())
    # db.add(supplier)
    print("Suppliers generated successfully.")
    