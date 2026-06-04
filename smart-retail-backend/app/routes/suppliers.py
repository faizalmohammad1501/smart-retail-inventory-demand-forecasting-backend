from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List

from app.database.connection import get_db
from app.models.user import User
from app.schemas.schemas import SupplierCreate, SupplierResponse
from app.services.supplier_service import SupplierService
from app.core.dependencies import get_current_active_user, require_roles

router = APIRouter(prefix="/api/suppliers", tags=["Suppliers"])

@router.post("/", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED)
def create_supplier(
    supplier: SupplierCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "manager")),
):
    """Create new supplier"""
    try:
        service = SupplierService(db)
        return service.create_supplier(supplier)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/", response_model=List[SupplierResponse])
def get_all_suppliers(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """Retrieve all suppliers with pagination"""
    service = SupplierService(db)
    return service.get_all_suppliers(skip=skip, limit=limit)

@router.get("/{supplier_id}", response_model=SupplierResponse)
def get_supplier(supplier_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_active_user)):
    """Retrieve supplier by ID"""
    service = SupplierService(db)
    supplier = service.get_supplier_by_id(supplier_id)
    if not supplier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found"
        )
    return supplier

@router.delete("/{supplier_id}")
def delete_supplier(supplier_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    """Delete supplier by ID"""
    service = SupplierService(db)
    if not service.delete_supplier(supplier_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found"
        )
    return {"message": "Supplier deleted successfully"}