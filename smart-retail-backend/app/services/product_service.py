from sqlalchemy.orm import Session
from typing import List, Optional

from app.models.product import Product
from app.schemas.schemas import ProductCreate

class ProductService:
    \"\"\"Service layer for product operations\"\"\"
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_product(self, product_data: ProductCreate) -> Product:
        \"\"\"Create new product\"\"\"
        db_product = Product(**product_data.dict())
        self.db.add(db_product)
        self.db.commit()
        self.db.refresh(db_product)
        return db_product
    
    def get_product_by_id(self, product_id: int) -> Optional[Product]:
        \"\"\"Retrieve product by ID\"\"\"
        return self.db.query(Product).filter(Product.id == product_id).first()
    
    def get_product_by_sku(self, sku: str) -> Optional[Product]:
        \"\"\"Retrieve product by SKU\"\"\"
        return self.db.query(Product).filter(Product.sku == sku).first()
    
    def get_all_products(self, skip: int = 0, limit: int = 100) -> List[Product]:
        \"\"\"Retrieve all products with pagination\"\"\"
        return self.db.query(Product).offset(skip).limit(limit).all()
    
    def get_products_by_category(self, category: str) -> List[Product]:
        \"\"\"Retrieve products by category\"\"\"
        return self.db.query(Product).filter(Product.category == category).all()
    
    def delete_product(self, product_id: int) -> bool:
        \"\"\"Delete product by ID\"\"\"
        product = self.get_product_by_id(product_id)
        if product:
            self.db.delete(product)
            self.db.commit()
            return True
        return False
