from app.database.connection import engine, Base
from app.models.user import User
from app.models.supplier import Supplier
from app.models.product import Product
from app.models.inventory import Inventory
from app.models.sales import Order
from app.models.notification import Notification

def init_db():
    """Initialize database tables"""
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully")

def drop_db():
    """Drop all database tables"""
    Base.metadata.drop_all(bind=engine)
    print("Database tables dropped successfully")