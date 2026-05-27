from sqlalchemy.orm import Session
from typing import List, Optional
from passlib.context import CryptContext

from app.models.supplier import Supplier
from app.schemas.schemas import SupplierCreate

pwd_context = CryptContext(schemes=[\"bcrypt\"], deprecated=\"auto\")

class SupplierService:
    \"\"\"Service layer for supplier operations\"\"\"
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_supplier(self, supplier_data: SupplierCreate) -> Supplier:
        \"\"\"Create new supplier\"\"\"
        db_supplier = Supplier(**supplier_data.dict())
        self.db.add(db_supplier)
        self.db.commit()
        self.db.refresh(db_supplier)
        return db_supplier
    
    def get_supplier_by_id(self, supplier_id: int) -> Optional[Supplier]:
        \"\"\"Retrieve supplier by ID\"\"\"
        return self.db.query(Supplier).filter(Supplier.id == supplier_id).first()
    
    def get_all_suppliers(self, skip: int = 0, limit: int = 100) -> List[Supplier]:
        \"\"\"Retrieve all suppliers with pagination\"\"\"
        return self.db.query(Supplier).offset(skip).limit(limit).all()
    
    def delete_supplier(self, supplier_id: int) -> bool:
        \"\"\"Delete supplier by ID\"\"\"
        supplier = self.get_supplier_by_id(supplier_id)
        if supplier:
            self.db.delete(supplier)
            self.db.commit()
            return True
        return False
